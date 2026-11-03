"""The stage contract: `StageSpec`, `prepare_stage`, `StageRun` and `run_stage` (D1 §9.1-9.3, §10).

Everything that decides what a stage receives lives here: reading `params.yaml`, picking the declared dotted keys and
resolving input and output paths. This module is in every stage's module set (D1 §11.1), so it never imports the
registry, and with it every other stage's code. It follows the stage determinism rules (D1 §9.4): no clock, no
randomness, no environment, no current directory, and no absolute path in anything it writes.
"""

import importlib.resources
import re
import shutil
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import ClassVar, Final, Literal, cast
from zoneinfo import ZoneInfo

import yaml
from pydantic import Field

from energatron.core.canonical import JSONValue, content_hash, sha256_hex, tree_hash
from energatron.core.errors import (
    CheckFailedError,
    EnergatronError,
    FingerprintStaleError,
    InternalError,
    InvalidInputError,
    PipelineNotSyncedError,
    ResultWarning,
    UsageError,
    WarningCode,
)
from energatron.core.models._common import ZONE_KEY, StageName
from energatron.core.models.catalog import CATALOG_FILENAME, SourceType, catalog_lines
from energatron.core.models.params import REGION_FIELDS, Params
from energatron.core.models.provenance import (
    PROVENANCE_FILENAME,
    CheckResult,
    ProvenanceFile,
    ProvenanceInput,
    ProvenanceParams,
    StageCode,
    StageProvenance,
)
from energatron.core.models.storage import RAW_PREFIX
from energatron.core.persisted import Persisted
from energatron.services.files import fsync_directory, make_dirs, parquet_rows, read_json, write_json

_IGNORED: Final = frozenset({".DS_Store"})
_NEW_SUFFIX: Final = ".energatron-new"
_OLD_SUFFIX: Final = ".energatron-old"
_FIELD_PART: Final = re.compile(r"([^.\[\]]+)|\[([0-9]+)\]")


@dataclass(frozen=True)
class StageSpec:
    """A stage: its inputs, params and outputs, and its body (D1 §9.2). Each stage module defines its own `SPEC`."""

    name: str
    inputs: Mapping[str, str]  # input name → default workspace-relative path
    params: tuple[str, ...]  # dotted keys into params.yaml, e.g. "meter.timebase"
    outputs: Mapping[str, str]  # output name → workspace-relative directory
    impl: Callable[[StageRun], None]  # the stage body
    package_data: tuple[str, ...] = ()  # package-relative files or directories it reads, e.g. "regions/sa.yaml"


def per_source_inputs(prefix: str, filename: str) -> dict[str, str]:
    """One input per source type, `<prefix>.<source>` → `data/raw/<source>/<filename>`, in sorted order (D1 §9.2).

    Built from `SourceType`, never by hand, so a new source type reaches every stage that reads them all.
    """
    return {f"{prefix}.{source.value}": f"{RAW_PREFIX}{source.value}/{filename}" for source in sorted(SourceType)}


@dataclass(frozen=True)
class PreparedStage:
    """What `run_stage` takes: absolute input and output paths, and exactly the declared params."""

    inputs: Mapping[str, Path]
    params: Mapping[str, JSONValue]
    outputs: Mapping[str, Path]


class StageResult(Persisted):
    """`stage_result` v1: what a stage run produced (D1 §5, §9.3 step 8; fields set in P1.3.3).

    Per output, its files with their `sha256`, byte counts and row counts; the checks. No paths beyond output-relative
    ones: an override may sit outside the workspace. The warnings are returned, but, as in every service result, left
    out of the dump: the CLI moves them to the envelope's own `warnings` (D1 §8.5).
    """

    KIND: ClassVar[str] = "stage_result"
    SCHEMA_VERSION: ClassVar[int] = 1
    kind: Literal["stage_result"] = "stage_result"
    schema_version: Literal[1] = 1

    stage: StageName
    outputs: dict[str, list[ProvenanceFile]]
    checks: list[CheckResult]
    warnings: list[ResultWarning] = Field(default_factory=list, exclude=True)


# prepare_stage (D1 §9.1)


def prepare_stage(
    spec: StageSpec,
    *,
    root: Path,
    params_file: Path,
    input_overrides: Mapping[str, Path] | None = None,
    output_overrides: Mapping[str, Path] | None = None,
) -> PreparedStage:
    """Resolve a stage's inputs, params and outputs, ready for `run_stage` (D1 §9.1).

    Default paths are the spec's workspace-relative paths under `root`. An override replaces one by name; a relative
    override is taken under `root`. `params.yaml` is read with `yaml.safe_load` and validated whole through `Params`
    (D3 §10.2), then the declared dotted keys are picked out. A missing `params.yaml` → `E_PIPELINE_NOT_SYNCED`; an
    unknown override name or a declared key missing from the file → `E_USAGE`.
    """
    inputs = _resolve("input", spec.inputs, root, input_overrides or {})
    outputs = _resolve("output", spec.outputs, root, output_overrides or {})
    try:
        text = params_file.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise PipelineNotSyncedError(
            "params.yaml is missing: run `energatron pipeline sync`.", drift=["params.yaml"]
        ) from None
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError:
        raise InvalidInputError("params.yaml isn't valid YAML.", kind=Params.KIND) from None
    if not isinstance(doc, dict):
        raise InvalidInputError("params.yaml isn't a mapping.", kind=Params.KIND)
    values: dict[str, JSONValue] = Params.from_document(doc).model_dump(mode="json")
    params = {key: _pick(values, key) for key in spec.params}
    _refuse_unset_region_values(values, spec.params)
    return PreparedStage(
        inputs=MappingProxyType(inputs), params=MappingProxyType(params), outputs=MappingProxyType(outputs)
    )


def _resolve(what: str, defaults: Mapping[str, str], root: Path, overrides: Mapping[str, Path]) -> dict[str, Path]:
    unknown = sorted(set(overrides) - set(defaults))
    if unknown:
        raise UsageError(f"Unknown {what} name: {', '.join(unknown)}.", names=unknown)
    return {name: root / overrides.get(name, Path(default)) for name, default in defaults.items()}


def _refuse_unset_region_values(values: Mapping[str, JSONValue], declared: tuple[str, ...]) -> None:
    """A declared key that is, or holds, a region value still `null` → `E_INVALID_INPUT` (P2 spec §5.1).

    Only `pipeline sync` fills the zone and the TOU windows from the region profile, so a stage never sees `null`.
    A `[n]` in a field is a list index (the first analysis window set).
    """
    for field in REGION_FIELDS:
        if any(field == key or field.startswith((f"{key}.", f"{key}[")) for key in declared) and _is_null(
            values, field
        ):
            raise InvalidInputError(
                f"params.yaml has no value for {field} yet: run `energatron pipeline sync`.", field=field
            )


def _is_null(values: Mapping[str, JSONValue], field: str) -> bool:
    current: JSONValue = values
    for name, index in _FIELD_PART.findall(field):
        if name:
            if not isinstance(current, Mapping) or name not in current:
                return False
            current = current[name]
        else:
            if not isinstance(current, list):
                return False
            items = cast("list[JSONValue]", current)
            if int(index) >= len(items):
                return False
            current = items[int(index)]
    return current is None


def _pick(values: Mapping[str, JSONValue], key: str) -> JSONValue:
    current: JSONValue = values
    for part in key.split("."):
        if not isinstance(current, Mapping) or part not in current:
            raise UsageError(f"params.yaml has no {key}: run `energatron pipeline sync`.", key=key)
        current = current[part]
    return current


# StageRun (D1 §9.2)


class StageRun:
    """What a stage body gets: its inputs, params, package data and temporary output directories."""

    def __init__(
        self,
        spec: StageSpec,
        inputs: Mapping[str, Path],
        params: Mapping[str, JSONValue],
        outputs: Mapping[str, Path],
    ) -> None:
        self._spec = spec
        self._inputs = inputs
        self._outputs = outputs
        self.params: Mapping[str, JSONValue] = MappingProxyType(dict(params))
        self.checks: list[CheckResult] = []
        self.warnings: list[ResultWarning] = []

    def input(self, name: str) -> Path:
        """The declared input `name`: an existing file or directory."""
        if name not in self._inputs:
            raise InternalError(f"Stage {self._spec.name} has no input {name}.")
        return self._inputs[name]

    def inputs(self, prefix: str) -> Mapping[str, Path]:
        """Every input named `<prefix>.<…>`, keyed by the part after the dot, in sorted order."""
        start = f"{prefix}."
        found = {name[len(start) :]: path for name, path in self._inputs.items() if name.startswith(start)}
        return MappingProxyType(dict(sorted(found.items())))

    def package_data(self, path: str) -> Path:
        """A file under the stage's declared `package_data`; any other path → `E_INTERNAL` (D1 §9.2)."""
        parts = PurePosixPath(path).parts
        declared = any(
            parts[: len(PurePosixPath(entry).parts)] == PurePosixPath(entry).parts for entry in self._spec.package_data
        )
        if not declared or ".." in parts or path.startswith("/"):
            raise InternalError(f"Stage {self._spec.name} didn't declare package data {path}.")
        return Path(str(importlib.resources.files("energatron"))).joinpath(*parts)

    def zone(self, key: str) -> ZoneInfo:
        """The time zone `key` (e.g. `meter.local_tz`) from the `tzdata` distribution, never the system database.

        A key that isn't tzdata path-like text, or names no zone in the installed `tzdata` → `E_INVALID_INPUT` with
        `details.field: "meter.local_tz"` (P2 spec §6). Stage code converts time zones only with zones from here.
        """
        if ZONE_KEY.fullmatch(key) is None:
            raise InvalidInputError("The time zone isn't a valid zone key.", field="meter.local_tz")
        resource = importlib.resources.files("tzdata").joinpath("zoneinfo", *key.split("/"))
        try:
            with resource.open("rb") as handle:
                return ZoneInfo.from_file(handle, key=key)
        except OSError, ValueError:  # a missing file or a directory; or bytes that aren't a zone file
            raise InvalidInputError("The time zone isn't in the installed tzdata.", field="meter.local_tz") from None

    def output(self, name: str) -> Path:
        """The temporary directory to write output `name` into."""
        if name not in self._outputs:
            raise InternalError(f"Stage {self._spec.name} has no output {name}.")
        return self._outputs[name]

    def check(self, name: str, passed: bool, **details: JSONValue) -> None:
        """Record a check. A failed one fails the stage after its outputs are promoted (D1 §9.3 step 7)."""
        if any(check.name == name for check in self.checks):
            raise InternalError(f"Stage {self._spec.name} ran check {name} twice.")
        self.checks.append(CheckResult(name=name, status="pass" if passed else "fail", details=details))

    def warn(self, code: WarningCode, message: str, **details: JSONValue) -> None:
        """Record a warning: returned in the `StageResult`, not written to provenance."""
        self.warnings.append(ResultWarning(code=code, message=message, details=details))


# run_stage (D1 §9.3)


def run_stage(
    spec: StageSpec,
    inputs: Mapping[str, Path],
    params: Mapping[str, JSONValue],
    outputs: Mapping[str, Path],
    *,
    code: StageCode,
    recorded_code: str | None,
) -> StageResult:
    """Run one stage: check its code entry and arguments, run its body into temporary directories, write
    provenance, promote the outputs, then fail on a failed check (D1 §9.3)."""
    if recorded_code != code.stage_sha256:
        raise FingerprintStaleError(
            f"Stage {spec.name}'s code changed since the fingerprint was recorded: run `energatron fingerprint`, "
            "or use `energatron run`.",
            stage=spec.name,
        )
    _check_names("input", inputs, spec.inputs)
    _check_names("output", outputs, spec.outputs)
    missing = sorted(name for name, path in inputs.items() if not path.exists())
    if missing:
        raise UsageError(
            f"Stage {spec.name} is missing inputs: {', '.join(missing)}.", stage=spec.name, missing=missing
        )
    if set(params) != set(spec.params):
        raise UsageError(f"Stage {spec.name} got params other than the declared keys.", stage=spec.name)

    temporary = {name: _sibling(target, _NEW_SUFFIX) for name, target in outputs.items()}
    try:
        for directory in temporary.values():
            make_dirs(directory.parent)
            if directory.exists():
                shutil.rmtree(directory)
            directory.mkdir()
        run = StageRun(spec, inputs, params, temporary)
        try:
            spec.impl(run)
        except EnergatronError:
            raise
        except Exception as error:
            raise InternalError(f"Stage {spec.name} failed: {type(error).__name__}.", stage=spec.name) from error
        records = [_input_record(spec, name, inputs[name]) for name in spec.inputs]  # declared order
        values = dict(params)
        provenance_params = ProvenanceParams(values=values, sha256=content_hash(values))
        files = {name: _output_files(directory) for name, directory in temporary.items()}
        for name, directory in temporary.items():
            provenance = StageProvenance(
                stage=spec.name,
                output=name,
                code=code,
                inputs=records,
                params=provenance_params,
                files=files[name],
                checks=run.checks,
            )
            write_json(directory / PROVENANCE_FILENAME, provenance.model_dump(mode="json"))
    except BaseException:
        for directory in temporary.values():
            shutil.rmtree(directory, ignore_errors=True)
        raise

    for name, target in outputs.items():
        _promote(temporary[name], target)

    failed = [check.name for check in run.checks if check.status == "fail"]
    if failed:
        raise CheckFailedError(f"Stage {spec.name} failed checks: {', '.join(failed)}.", stage=spec.name, failed=failed)
    return StageResult(stage=spec.name, outputs=files, checks=run.checks, warnings=run.warnings)


def _check_names(what: str, given: Mapping[str, Path], declared: Mapping[str, str]) -> None:
    if set(given) != set(declared):
        raise UsageError(f"The {what} names don't match the stage's declared ones.", expected=sorted(declared))


def _sibling(target: Path, suffix: str) -> Path:
    """A hidden sibling of `target`, so a rename stays on the same file system."""
    return target.parent / f".{target.name}{suffix}"


def _promote(directory: Path, target: Path) -> None:
    """Move `directory` into place at `target`: set any old one aside, rename, then delete the old one."""
    old = _sibling(target, _OLD_SUFFIX)
    if old.exists():
        shutil.rmtree(old)
    if target.exists():
        target.rename(old)
    directory.rename(target)
    fsync_directory(target.parent)
    if old.exists():
        shutil.rmtree(old)


def _files_under(directory: Path) -> list[tuple[str, Path]]:
    """Every file under `directory` as (relative POSIX path, path), sorted by path."""
    found = [
        (path.relative_to(directory).as_posix(), path)
        for path in directory.rglob("*")
        if path.is_file() and path.name not in _IGNORED
    ]
    return sorted(found, key=lambda item: item[0])


def _output_files(directory: Path) -> list[ProvenanceFile]:
    files: list[ProvenanceFile] = []
    for relative, path in _files_under(directory):
        if relative == PROVENANCE_FILENAME:
            continue
        data = path.read_bytes()
        rows = parquet_rows(path) if path.suffix == ".parquet" else None
        files.append(ProvenanceFile(path=relative, sha256=sha256_hex(data), bytes=len(data), rows=rows))
    return files


def _input_record(spec: StageSpec, name: str, path: Path) -> ProvenanceInput:
    """An input's provenance: its declared default path, and the hash and lineage of what was actually read."""
    declared = spec.inputs[name]
    if path.is_dir():
        entries = [(relative, sha256_hex(file.read_bytes())) for relative, file in _files_under(path)]
        return ProvenanceInput(
            name=name,
            type="dir",
            path=declared,
            sha256=tree_hash(entries),
            dataset_ids=_dir_dataset_ids(declared, path),
        )
    data = path.read_bytes()
    ids = _catalog_dataset_ids(data) if _is_raw(declared) and PurePosixPath(declared).name == CATALOG_FILENAME else []
    return ProvenanceInput(name=name, type="file", path=declared, sha256=sha256_hex(data), dataset_ids=ids)


def _is_raw(declared: str) -> bool:
    return declared.startswith(RAW_PREFIX)


def _catalog_dataset_ids(data: bytes) -> list[str]:
    """The IDs of a catalog file's valid lines, sorted and unique (D1 §10; D3 §9)."""
    return sorted({line.record.dataset_id for line in catalog_lines(data) if line.record is not None})


def _dir_dataset_ids(declared: str, path: Path) -> list[str]:
    """A raw directory's catalog IDs, or the union an upstream output's provenance names (D1 §10)."""
    if _is_raw(declared):
        catalog = path / CATALOG_FILENAME
        return _catalog_dataset_ids(catalog.read_bytes()) if catalog.is_file() else []
    provenance_file = path / PROVENANCE_FILENAME
    if not provenance_file.is_file():
        return []
    try:
        doc = read_json(provenance_file)
        if not isinstance(doc, Mapping):
            raise ValueError("not an object")
        upstream = StageProvenance.from_document(doc)
    except ValueError, InvalidInputError:
        raise InvalidInputError(f"An input's {PROVENANCE_FILENAME} is invalid.", kind=StageProvenance.KIND) from None
    return sorted({dataset for item in upstream.inputs for dataset in item.dataset_ids})
