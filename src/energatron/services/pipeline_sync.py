"""`pipeline sync`: bring the workspace's generated pipeline files in line with the installed tool (D3 §10.1).

- `dvc.yaml` is `emit_dvc_yaml(STAGES)`, written atomically only when its bytes differ.
- `params.yaml` is written with the current version's defaults when missing, and rewritten through the migration
  chain when older, keeping every value (comments are lost). DVC reads declared params from the file itself, so an
  in-memory upgrade isn't enough (D3 §10.2). A newer file → `E_SCHEMA_TOO_NEW`.
- The region's values are copied from `ctx.region` (P2 spec §5.3): `meter.local_tz` is the region's, so a file whose
  zone differs (or is `null`) is rewritten with only that value changed; `validation.timebase.windows` and the first
  `analysis.tou.window_sets` entry's windows (P3 spec §4.3) are replaced only while `null`, because the owner may edit
  them. `offers.retailers` and `offers.distributor` (P4 spec §4.3) and `offers.gas_distributor` (P5 spec §4.3) are
  likewise filled only while `null`. Every other
  value is the owner's and is kept.
- Every source without a catalog gets an empty one, and the directories created are tracked (`dvc add`).
- The local offer-rules directory `config/offer_rules.local/` (a `normalise_offers` dependency, P4 spec §6) is created
  with a `README.md` when it is missing. It is git-tracked config, never DVC. So is `config/bundle_rules.local/` (a
  `bundles` dependency, P6 spec §3.4).
- `data/runs/` and `data/decisions/` (the run manifests and decisions, P6 spec §3.4) are created with a `README.md`
  when missing and tracked with `dvc add`; the readme keeps each directory non-empty.
- Nothing is committed: the result lists the pointer files to commit.

`check` writes nothing and fails with `E_PIPELINE_NOT_SYNCED` naming the drift; `land` and the workspace's
`pipeline-sync` commit hook run it. The model for `params` is looked up in the kind registry on every call, so a
newer `Params` version (and its migration) is picked up without changing this module.
"""

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, Final, Literal, cast

import yaml

from energatron.core.canonical import JSONValue
from energatron.core.errors import InvalidInputError, PipelineNotSyncedError, StorageError
from energatron.core.models.catalog import SourceType
from energatron.core.models.params import Params
from energatron.core.models.region import RegionProfile
from energatron.core.models.storage import RAW_PREFIX
from energatron.core.persisted import Persisted, registered_kinds
from energatron.services.context import ServiceContext, ServiceResult
from energatron.services.files import atomic_write_text, make_dirs
from energatron.services.stages.dvc_yaml import emit_dvc_yaml
from energatron.services.stages.registry import STAGES

_log = logging.getLogger(__name__)

DVC_YAML: Final = "dvc.yaml"
PARAMS_YAML: Final = "params.yaml"
RAW_GITIGNORE: Final = f"{RAW_PREFIX}.gitignore"
RULES_DIR: Final = "config/offer_rules.local"
RULES_README: Final = f"{RULES_DIR}/README.md"
_RULES_README_TEXT: Final = """\
# Local offer rules

One YAML file per rule (`kind: offer_rule`, `schema_version: 1`), read by the `normalise_offers` stage after the
curated rules that ship with energatron. A rule here with the same `id` as a curated one replaces it.

```yaml
kind: offer_rule
schema_version: 1
id: my-rule                 # kebab-case, unique
description: Why this rule exists.
match: {brand: <brand>, plan_id: null, from: null, to: null}   # brand or plan_id (a regex) is required
effect: {approximate: <reason>}   # or {exclude: <reason>} or {note: <text>}
verified_on: "YYYY-MM-DD"
cites: []                   # offer snapshot IDs the rule was derived from
```

Offer details are public; never put an identifier or a figure about this household here.
"""

BUNDLE_RULES_DIR: Final = "config/bundle_rules.local"
BUNDLE_RULES_README: Final = f"{BUNDLE_RULES_DIR}/README.md"
_BUNDLE_RULES_README_TEXT: Final = """\
# Local bundle rules

One YAML file per rule (`kind: bundle_rule`, `schema_version: 1`), read by the `bundles` stage after the curated rules
that ship with energatron. A rule here with the same `id` as a curated one replaces it.

```yaml
kind: bundle_rule
schema_version: 1
id: my-dual-fuel-rule       # kebab-case, unique
description: Why this rule exists.
brand: <brand>              # both tariffs' brand, case-insensitive
electricity_plan: null      # a regex on the electricity plan ID, or null for any
gas_plan: null              # a regex on the gas plan ID, or null for any
effect: {annual_credit: "<dollars a year>"}   # or {percent_off: "<percent>"}
verified_on: "YYYY-MM-DD"
cites: []                   # offer snapshot IDs the rule was read from
```

Offer details are public; never put an identifier or a figure about this household here.
"""
RUNS_DIR: Final = "data/runs"
DECISIONS_DIR: Final = "data/decisions"
_RECORD_READMES: Final = {
    RUNS_DIR: """\
# Run manifests

One `run-<UTC time>-<head>.json` (`kind: run_manifest`) per `energatron run`: the stages that ran, the datasets and
offer batches it saw, and the recommendation it produced (P6 spec §8.2). Written by `energatron run`; never edited.
""",
    DECISIONS_DIR: """\
# Decisions

One `DEC-<date>-<n>.json` (`kind: decision`) per `energatron decide`: the recommendation acted on, the plans chosen
with their projections, and the switch date (P6 spec §9.2). Written by `energatron decide`; never edited.
""",
}

type ParamsState = Literal["missing", "older", "drifted", "current"]  # "older", "drifted": rewritten


class PipelineSyncResult(ServiceResult):
    """`pipeline_sync_result` v1 (D3 §10.1). In a dry run the flags and `catalogs_created` say what would happen."""

    KIND: ClassVar[str] = "pipeline_sync_result"
    SCHEMA_VERSION: ClassVar[int] = 1
    kind: Literal["pipeline_sync_result"] = "pipeline_sync_result"
    schema_version: Literal[1] = 1

    dvc_yaml_written: bool
    params_written: bool  # created
    params_rewritten: bool  # upgraded from an older version
    catalogs_created: list[str]  # sources, sorted
    pointer_files: list[str]  # workspace-relative files to commit: pointers, `data/raw/.gitignore`, the rules readme
    dry_run: bool


@dataclass(frozen=True)
class _Plan:
    """What `sync` would change, worked out before anything is written."""

    dvc_yaml: str | None  # the text to write, or None when the file is current
    params_state: ParamsState
    params_text: str | None  # the text to write, or None when the file is current
    missing: list[SourceType]
    rules_dir_missing: bool
    bundle_rules_dir_missing: bool
    records_missing: list[str]  # of RUNS_DIR, DECISIONS_DIR


def check(ctx: ServiceContext) -> None:
    """Write nothing; `E_PIPELINE_NOT_SYNCED` with `details.drift` (`dvc.yaml`, `params.yaml`, `catalogs`) on drift.

    An invalid `params.yaml` → `E_INVALID_INPUT`; a newer one → `E_SCHEMA_TOO_NEW`. Takes no lock (D3 §10.1).
    """
    plan = _plan(ctx)
    drift = [
        name
        for name, drifted in (
            (DVC_YAML, plan.dvc_yaml is not None),
            (PARAMS_YAML, plan.params_text is not None),
            ("catalogs", bool(plan.missing)),
            (RULES_DIR, plan.rules_dir_missing),
            (BUNDLE_RULES_DIR, plan.bundle_rules_dir_missing),
            *((directory, True) for directory in plan.records_missing),
        )
        if drifted
    ]
    if drift:
        raise PipelineNotSyncedError(
            f"The pipeline files aren't in sync with the tool ({', '.join(drift)}): run `energatron pipeline sync`.",
            drift=list(drift),
        )


def sync(ctx: ServiceContext, *, dry_run: bool = False) -> PipelineSyncResult:
    """Write `dvc.yaml` and `params.yaml`, create missing catalogs and track their directories; never commit.

    Everything is read and validated before the first write. The caller holds the workspace lock, also for a dry
    run, which writes and tracks nothing.
    """
    plan = _plan(ctx)
    root = ctx.paths.root
    created: list[str] = [source.value for source in plan.missing]
    pointers: list[str] = []
    if not dry_run:
        if plan.dvc_yaml is not None:
            _write(root / DVC_YAML, plan.dvc_yaml, DVC_YAML)
        if plan.params_text is not None:
            _write(root / PARAMS_YAML, plan.params_text, PARAMS_YAML)
        created = [source.value for source in plan.missing if ctx.catalog.initialise(source)]
        if created:
            ignore = root / RAW_GITIGNORE
            before = _read_bytes(ignore)
            pointers = list(ctx.vcs.track([f"{RAW_PREFIX}{source}" for source in created]))
            if _read_bytes(ignore) != before and RAW_GITIGNORE not in pointers:
                pointers.append(RAW_GITIGNORE)
        if plan.rules_dir_missing:
            _make_dir(root / RULES_DIR)
            _write(root / RULES_README, _RULES_README_TEXT, RULES_README)
            pointers.append(RULES_README)
        if plan.bundle_rules_dir_missing:
            _make_dir(root / BUNDLE_RULES_DIR)
            _write(root / BUNDLE_RULES_README, _BUNDLE_RULES_README_TEXT, BUNDLE_RULES_README)
            pointers.append(BUNDLE_RULES_README)
        if plan.records_missing:
            for directory in plan.records_missing:
                _make_dir(root / directory)
                _write(root / directory / "README.md", _RECORD_READMES[directory], f"{directory}/README.md")
            ignore = root / "data" / ".gitignore"
            before = _read_bytes(ignore)
            pointers.extend(ctx.vcs.track(plan.records_missing))
            if _read_bytes(ignore) != before and "data/.gitignore" not in pointers:
                pointers.append("data/.gitignore")
    result = PipelineSyncResult(
        dvc_yaml_written=plan.dvc_yaml is not None,
        params_written=plan.params_state == "missing",
        params_rewritten=plan.params_state in ("older", "drifted"),
        catalogs_created=created,
        pointer_files=pointers,
        dry_run=dry_run,
    )
    _log.info(
        "pipeline sync: dvc.yaml %s, params %s, %d catalogs created, dry run: %s",
        "written" if result.dvc_yaml_written else "current",
        plan.params_state,
        len(created),
        dry_run,
    )
    return result


# Planning


def _plan(ctx: ServiceContext) -> _Plan:
    root = ctx.paths.root
    expected = emit_dvc_yaml(STAGES)
    dvc_yaml = None if _read_bytes(root / DVC_YAML) == expected.encode("utf-8") else expected
    state, params_text = _params(_read_bytes(root / PARAMS_YAML), ctx.region)
    return _Plan(
        dvc_yaml=dvc_yaml,
        params_state=state,
        params_text=params_text,
        missing=ctx.catalog.missing_sources(),
        rules_dir_missing=not (root / RULES_DIR).is_dir(),
        bundle_rules_dir_missing=not (root / BUNDLE_RULES_DIR).is_dir(),
        records_missing=[directory for directory in (RUNS_DIR, DECISIONS_DIR) if not (root / directory).is_dir()],
    )


def _params(data: bytes | None, profile: RegionProfile) -> tuple[ParamsState, str | None]:
    """The file's state and the text to write (None when it's current). Invalid → `E_INVALID_INPUT`."""
    model = _params_model()
    if data is None:
        return "missing", _dump(model.defaults(profile))
    try:
        doc = yaml.safe_load(data.decode("utf-8"))
    except UnicodeDecodeError, yaml.YAMLError:
        raise InvalidInputError("params.yaml isn't valid UTF-8 YAML.", kind=Params.KIND) from None
    if not isinstance(doc, dict):
        raise InvalidInputError("params.yaml isn't a mapping.", kind=Params.KIND)
    document: dict[str, JSONValue] = {str(key): value for key, value in doc.items()}  # pyright: ignore[reportUnknownVariableType]
    params = model.from_document(document)  # E_INVALID_INPUT, or E_SCHEMA_TOO_NEW for a newer file
    filled = _with_region_values(model, params, profile)
    if document.get("schema_version") != model.SCHEMA_VERSION:
        return "older", _dump(filled)
    if filled != params:
        return "drifted", _dump(filled)
    return "current", None


def _with_region_values(model: type[Params], params: Params, profile: RegionProfile) -> Params:
    """`params` with the region's zone, and the region's windows, retailers and distributors where `params` has none
    (P2 §5.3, P3 §4.3, P4 §4.3, P5 §4.3)."""
    values = cast(dict[str, Any], params.model_dump(mode="json"))
    values["meter"]["local_tz"] = profile.local_tz
    region_windows = (
        None if profile.tou_windows is None else {label: list(found) for label, found in profile.tou_windows.items()}
    )
    if values["validation"]["timebase"]["windows"] is None:
        values["validation"]["timebase"]["windows"] = region_windows
    first_set = values["analysis"]["tou"]["window_sets"][0]
    if first_set["windows"] is None:
        first_set["windows"] = region_windows
    offers = values["offers"]
    if offers["retailers"] is None and profile.cdr_retailers is not None:
        offers["retailers"] = list(profile.cdr_retailers)
    if offers["distributor"] is None:
        offers["distributor"] = profile.distributor
    if offers["gas_distributor"] is None:
        offers["gas_distributor"] = profile.gas_distributor
    return model.model_validate(values)


def _params_model() -> type[Params]:
    model = registered_kinds().get(Params.KIND)
    if model is None:  # `Params` registers itself on import
        raise InvalidInputError("No params model is registered.", kind=Params.KIND)
    return cast(type[Params], model)


def _dump(params: Persisted) -> str:
    """`yaml.safe_dump(sort_keys=False)`, with `kind` and `schema_version` first (D3 §10.1 step 2)."""
    values: Mapping[str, JSONValue] = params.model_dump(mode="json")
    ordered = {"kind": values["kind"], "schema_version": values["schema_version"]}
    ordered.update((key, value) for key, value in values.items() if key not in ordered)
    return yaml.safe_dump(ordered, sort_keys=False, allow_unicode=True)


# Files


def _read_bytes(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError:
        raise StorageError(
            "A pipeline file can't be read.", location="workspace", hint="check the workspace's file permissions"
        ) from None


def _write(path: Path, text: str, name: str) -> None:
    try:
        atomic_write_text(path, text)
    except OSError:
        raise StorageError(
            f"{name} couldn't be written.",
            location="workspace",
            hint="check that the workspace is writable and the disk isn't full",
        ) from None


def _make_dir(path: Path) -> None:
    try:
        make_dirs(path)
    except OSError:
        raise StorageError(
            f"{path.name} couldn't be created.", location="workspace", hint="check that the workspace is writable"
        ) from None
