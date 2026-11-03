"""The stage contract: `prepare_stage`, `run_stage` and `StageRun` (D1 §9.1-9.3, §10). Synthetic stages and values."""

import json
import zoneinfo
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pyarrow as pa
import pytest

from energatron.core.canonical import JSONValue, canonical_json, content_hash, sha256_hex, tree_hash
from energatron.core.errors import (
    CheckFailedError,
    FingerprintStaleError,
    InternalError,
    InvalidInputError,
    PipelineNotSyncedError,
    UsageError,
    WarningCode,
)
from energatron.core.ids import dataset_id
from energatron.core.models import _common
from energatron.core.models.provenance import CodeFingerprint, StageCode, StageProvenance
from energatron.services.files import write_parquet
from energatron.services.stages import registry, runner
from energatron.services.stages.runner import StageRun, StageSpec, prepare_stage, run_stage

PARAMS_V1 = "kind: params\nschema_version: 1\n"


def make_code(seed: str = "stage") -> StageCode:
    source = sha256_hex(seed.encode())
    packages = {"pandas": "0.0.0"}
    digest = content_hash({"source_sha256": source, "packages": packages, "python": "3.14.0"})
    return StageCode(
        stage_sha256=digest, source_sha256=source, python="3.14.0", packages=packages, files=["__init__.py"]
    )


CODE = make_code()


def _copy(run: StageRun) -> None:
    data = run.input("text").read_bytes()
    (run.output("result") / "copy.txt").write_bytes(data)
    run.check("non_empty", bool(data), size=len(data))


def spec_of(
    impl: Callable[[StageRun], None] = _copy,
    *,
    inputs: Mapping[str, str] | None = None,
    params: tuple[str, ...] = (),
    outputs: Mapping[str, str] | None = None,
    package_data: tuple[str, ...] = (),
) -> StageSpec:
    return StageSpec(
        name="synthetic",
        inputs={"text": "data/in/text.txt"} if inputs is None else inputs,
        params=params,
        outputs={"result": "data/out/result"} if outputs is None else outputs,
        impl=impl,
        package_data=package_data,
    )


@pytest.fixture
def root(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    (workspace / "data" / "in").mkdir(parents=True)
    (workspace / "data" / "in" / "text.txt").write_bytes(b"synthetic text\n")
    (workspace / "params.yaml").write_text(PARAMS_V1)
    return workspace


def run(spec: StageSpec, root: Path) -> runner.StageResult:
    prepared = prepare_stage(spec, root=root, params_file=root / "params.yaml")
    return run_stage(
        spec, prepared.inputs, prepared.params, prepared.outputs, code=CODE, recorded_code=CODE.stage_sha256
    )


def provenance_of(directory: Path) -> StageProvenance:
    return StageProvenance.from_document(json.loads((directory / "_provenance.json").read_text()))


def leftovers(directory: Path) -> list[str]:
    return sorted(path.name for path in directory.iterdir() if path.name.startswith("."))


# prepare_stage (D1 §9.1)


class _AnyParams:
    """Stands in for a later `Params` version that has settings: keeps the document as it is."""

    def __init__(self, doc: dict[str, JSONValue]) -> None:
        self._doc = doc

    @classmethod
    def from_document(cls, doc: Mapping[str, JSONValue]) -> _AnyParams:
        return cls(dict(doc))

    def model_dump(self, *, mode: str) -> dict[str, JSONValue]:
        return self._doc


def test_prepare_stage_picks_exactly_the_declared_keys(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner, "Params", _AnyParams)
    (root / "params.yaml").write_text(
        "kind: params\nschema_version: 1\nmeter:\n  timebase: nem_aest\n  local_tz: Australia/Adelaide\n"
        "analysis:\n  window_days: 365\n"
    )
    prepared = prepare_stage(
        spec_of(params=("meter.timebase", "analysis.window_days")), root=root, params_file=root / "params.yaml"
    )
    assert dict(prepared.params) == {"meter.timebase": "nem_aest", "analysis.window_days": 365}


def test_prepare_stage_refuses_a_missing_declared_key(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner, "Params", _AnyParams)
    (root / "params.yaml").write_text("kind: params\nschema_version: 1\nmeter:\n  local_tz: Australia/Adelaide\n")
    with pytest.raises(UsageError) as caught:
        prepare_stage(spec_of(params=("meter.timebase",)), root=root, params_file=root / "params.yaml")
    assert caught.value.details["key"] == "meter.timebase"


def test_prepare_stage_validates_the_whole_params_file(root: Path) -> None:
    (root / "params.yaml").write_text(PARAMS_V1 + "unknown: 1\n")
    with pytest.raises(InvalidInputError):
        prepare_stage(spec_of(), root=root, params_file=root / "params.yaml")


def test_prepare_stage_without_params_yaml_asks_for_pipeline_sync(root: Path) -> None:
    (root / "params.yaml").unlink()
    with pytest.raises(PipelineNotSyncedError) as caught:
        prepare_stage(spec_of(), root=root, params_file=root / "params.yaml")
    assert caught.value.details["drift"] == ["params.yaml"]


def test_prepare_stage_resolves_default_paths_under_root(root: Path) -> None:
    prepared = prepare_stage(spec_of(), root=root, params_file=root / "params.yaml")
    assert dict(prepared.inputs) == {"text": root / "data" / "in" / "text.txt"}
    assert dict(prepared.outputs) == {"result": root / "data" / "out" / "result"}
    assert dict(prepared.params) == {}


def test_prepare_stage_applies_overrides(root: Path, tmp_path: Path) -> None:
    elsewhere = tmp_path / "elsewhere.txt"
    prepared = prepare_stage(
        spec_of(),
        root=root,
        params_file=root / "params.yaml",
        input_overrides={"text": elsewhere},
        output_overrides={"result": tmp_path / "out"},
    )
    assert prepared.inputs["text"] == elsewhere
    assert prepared.outputs["result"] == tmp_path / "out"


@pytest.mark.parametrize("which", ["input", "output"])
def test_prepare_stage_refuses_an_unknown_override_name(root: Path, tmp_path: Path, which: str) -> None:
    overrides = {"nope": tmp_path / "x"}
    kwargs = {"input_overrides": overrides} if which == "input" else {"output_overrides": overrides}
    with pytest.raises(UsageError):
        prepare_stage(spec_of(), root=root, params_file=root / "params.yaml", **kwargs)  # type: ignore[arg-type]


PARAMS_V2 = """\
kind: params
schema_version: 2
meter:
  timebase: nem_aest
  local_tz: Test/Zone
validation:
  meter:
    total_tolerance_kwh: 0.01
  timebase:
    windows:
      Alpha: ["08:00-20:00"]
      Beta: ["20:00-08:00"]
    register_tolerance_kwh: 0.05
    register_tolerance_pct: 0.1
    total_tolerance_kwh: 0.05
    min_fit_periods: 3
"""


def test_a_dict_valued_declared_param_reaches_the_stage_whole(root: Path) -> None:
    (root / "params.yaml").write_text(PARAMS_V2)
    received: dict[str, JSONValue] = {}

    def impl(run: StageRun) -> None:
        received.update(run.params)

    run(spec_of(impl, params=("validation.meter", "validation.timebase", "meter.local_tz")), root)
    assert received["validation.meter"] == {"total_tolerance_kwh": 0.01}
    assert received["validation.timebase"] == {
        "windows": {"Alpha": ["08:00-20:00"], "Beta": ["20:00-08:00"]},
        "register_tolerance_kwh": 0.05,
        "register_tolerance_pct": 0.1,
        "total_tolerance_kwh": 0.05,
        "min_fit_periods": 3,
    }
    assert received["meter.local_tz"] == "Test/Zone"


def test_a_dict_valued_param_is_recorded_in_provenance_with_its_hash(root: Path) -> None:
    (root / "params.yaml").write_text(PARAMS_V2)
    run(spec_of(params=("validation.meter",)), root)
    params = provenance_of(root / "data" / "out" / "result").params
    assert params.values == {"validation.meter": {"total_tolerance_kwh": 0.01}}
    assert params.sha256 == content_hash({"validation.meter": {"total_tolerance_kwh": 0.01}})


@pytest.mark.parametrize(
    ("keys", "field"),
    [
        (("meter.local_tz",), "meter.local_tz"),
        (("validation.timebase.windows",), "validation.timebase.windows"),
        (("validation.timebase",), "validation.timebase.windows"),
        (("validation",), "validation.timebase.windows"),
        (("meter", "validation.meter"), "meter.local_tz"),
    ],
)
def test_a_stage_that_declares_a_null_zone_or_windows_is_invalid_input(
    root: Path, keys: tuple[str, ...], field: str
) -> None:
    """A v1 file migrates to v2 with both `null` (P2 spec §5.1); the stage must not see them."""
    with pytest.raises(InvalidInputError) as caught:
        prepare_stage(spec_of(params=keys), root=root, params_file=root / "params.yaml")
    assert caught.value.details["field"] == field
    assert "pipeline sync" in caught.value.message


@pytest.mark.parametrize("keys", [(), ("meter.timebase",), ("validation.meter",)])
def test_declared_keys_that_are_set_pass_while_the_zone_is_null(root: Path, keys: tuple[str, ...]) -> None:
    prepared = prepare_stage(spec_of(params=keys), root=root, params_file=root / "params.yaml")
    assert set(prepared.params) == set(keys)


PARAMS_V3_NULL_SET = """\
kind: params
schema_version: 3
meter: {timebase: nem_aest, local_tz: Test/Zone}
validation:
  meter: {total_tolerance_kwh: 0.01}
  timebase:
    windows: {Alpha: ["08:00-20:00"], Beta: ["20:00-08:00"]}
    register_tolerance_kwh: 0.05
    register_tolerance_pct: 0.1
    total_tolerance_kwh: 0.05
    min_fit_periods: 3
analysis:
  reference_months: 12
  coverage_threshold: 0.95
  seasons: {DJF: [12, 1, 2], MAM: [3, 4, 5], JJA: [6, 7, 8], SON: [9, 10, 11]}
  weekend: [saturday, sunday]
  band_percentiles: [10, 50, 90]
  base_load: {from: "01:00", to: "05:00", percentile: 10}
  demand_minutes: 30
  tou:
    window_sets:
      - {effective_from: null, windows: null}
"""


@pytest.mark.parametrize("keys", [("analysis",), ("analysis.tou",), ("analysis.tou.window_sets",)])
def test_a_stage_that_declares_analysis_with_a_null_first_set_is_invalid_input(
    root: Path, keys: tuple[str, ...]
) -> None:
    """P3 spec §4.1: the first set's windows are region data; only `pipeline sync` fills them."""
    (root / "params.yaml").write_text(PARAMS_V3_NULL_SET)
    with pytest.raises(InvalidInputError) as caught:
        prepare_stage(spec_of(params=keys), root=root, params_file=root / "params.yaml")
    assert caught.value.details["field"] == "analysis.tou.window_sets[0].windows"
    assert "pipeline sync" in caught.value.message


@pytest.mark.parametrize("keys", [("analysis.reference_months",), ("analysis.base_load",), ("analysis.seasons",)])
def test_declared_analysis_keys_that_are_set_pass_while_the_first_set_is_null(
    root: Path, keys: tuple[str, ...]
) -> None:
    (root / "params.yaml").write_text(PARAMS_V3_NULL_SET)
    assert set(prepare_stage(spec_of(params=keys), root=root, params_file=root / "params.yaml").params) == set(keys)


def test_a_filled_first_set_passes_and_reaches_the_stage_whole(root: Path) -> None:
    filled = PARAMS_V3_NULL_SET.replace(
        "{effective_from: null, windows: null}",
        '{effective_from: null, windows: {Alpha: ["08:00-20:00"], Beta: ["20:00-08:00"]}}',
    )
    (root / "params.yaml").write_text(filled)
    prepared = prepare_stage(spec_of(params=("analysis",)), root=root, params_file=root / "params.yaml")
    analysis = prepared.params["analysis"]
    assert isinstance(analysis, dict)
    assert analysis["base_load"] == {"from": "01:00", "to": "05:00", "percentile": 10}
    assert analysis["tou"] == {
        "window_sets": [{"effective_from": None, "windows": {"Alpha": ["08:00-20:00"], "Beta": ["20:00-08:00"]}}]
    }


# StageRun.zone (P2 spec §6)


def make_run(root: Path) -> StageRun:
    return StageRun(spec_of(), {}, {}, {})


def test_zone_returns_the_tzdata_zone(root: Path) -> None:
    zone = make_run(root).zone("Australia/Adelaide")
    assert isinstance(zone, ZoneInfo)
    assert zone.key == "Australia/Adelaide"
    assert datetime(2025, 7, 1, 12, 0, tzinfo=zone).utcoffset() == timedelta(hours=9, minutes=30)
    assert datetime(2025, 1, 1, 12, 0, tzinfo=zone).utcoffset() == timedelta(hours=10, minutes=30)


def test_zone_reads_the_tzdata_distribution_not_the_system_database(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A wrong system zone file changes nothing: with the search path pointed at an empty folder, the zone still
    comes from the `tzdata` package."""
    monkeypatch.setattr(zoneinfo, "TZPATH", (str(root),))
    zone = make_run(root).zone("Australia/Adelaide")
    assert datetime(2025, 1, 1, 12, 0, tzinfo=zone).utcoffset() == timedelta(hours=10, minutes=30)


@pytest.mark.parametrize(
    "key",
    [
        "",
        " ",
        "Australia Adelaide",
        "../Adelaide",
        "Australia/../Adelaide",
        "/Australia/Adelaide",
        "Australia/Adelaide/",
        "Australia//Adelaide",
        "Australia/Adelaide\n",  # `$` would let a trailing newline through
        "Australia\\Adelaide",
        "Australia/Nowhere",  # well-formed, but no such file
        "Australia",  # a directory
        "zone.tab",
        "Nowhere/Nowhere/Nowhere",
    ],
)
def test_zone_refuses_a_malformed_or_unknown_key(root: Path, key: str) -> None:
    with pytest.raises(InvalidInputError) as caught:
        make_run(root).zone(key)
    assert caught.value.details["field"] == "meter.local_tz"


# The registry (D1 §9.2, §9.3 step 1)


def test_get_stage_refuses_an_unknown_name() -> None:
    with pytest.raises(UsageError):
        registry.get_stage("no_such_stage")


def test_get_stage_returns_the_registered_spec() -> None:
    assert registry.get_stage("catalog_index") is registry.STAGES["catalog_index"]


# run_stage (D1 §9.3)


@pytest.mark.parametrize("recorded", [None, make_code("other").stage_sha256])
def test_a_missing_or_differing_recorded_code_is_stale(root: Path, recorded: str | None) -> None:
    spec = spec_of()
    prepared = prepare_stage(spec, root=root, params_file=root / "params.yaml")
    with pytest.raises(FingerprintStaleError):
        run_stage(spec, prepared.inputs, prepared.params, prepared.outputs, code=CODE, recorded_code=recorded)
    assert not (root / "data" / "out").exists()


def test_only_this_stages_entry_counts(root: Path) -> None:
    recorded = CodeFingerprint(
        energatron_version="0.0.0",
        install="index",
        tool_commit=None,
        tool_dirty=False,
        stages={"synthetic": CODE, "another": make_code("changed")},
    )
    spec = spec_of()
    prepared = prepare_stage(spec, root=root, params_file=root / "params.yaml")
    run_stage(
        spec,
        prepared.inputs,
        prepared.params,
        prepared.outputs,
        code=CODE,
        recorded_code=recorded.stages["synthetic"].stage_sha256,
    )
    assert (root / "data" / "out" / "result" / "copy.txt").exists()


def test_a_missing_input_is_a_usage_error(root: Path) -> None:
    (root / "data" / "in" / "text.txt").unlink()
    with pytest.raises(UsageError):
        run(spec_of(), root)


@pytest.mark.parametrize("params", [{}, {"meter.timebase": "x", "extra.key": 1}])
def test_params_keys_must_equal_the_declared_ones(root: Path, params: dict[str, JSONValue]) -> None:
    spec = spec_of(params=("meter.timebase",))
    inputs = {"text": root / "data" / "in" / "text.txt"}
    outputs = {"result": root / "data" / "out" / "result"}
    with pytest.raises(UsageError):
        run_stage(spec, inputs, params, outputs, code=CODE, recorded_code=CODE.stage_sha256)


def test_unknown_input_or_output_names_are_usage_errors(root: Path) -> None:
    spec = spec_of()
    with pytest.raises(UsageError):
        run_stage(
            spec, {}, {}, {"result": root / "data" / "out" / "result"}, code=CODE, recorded_code=CODE.stage_sha256
        )
    with pytest.raises(UsageError):
        run_stage(spec, {"text": root / "data" / "in" / "text.txt"}, {}, {}, code=CODE, recorded_code=CODE.stage_sha256)


def test_declared_package_data_is_reachable_and_undeclared_is_internal(root: Path) -> None:
    seen: list[Path] = []

    def impl(run: StageRun) -> None:
        seen.append(run.package_data("core/canonical.py"))
        seen.append(run.package_data("core/models/catalog.py"))
        run.package_data("core/ids.py")

    spec = spec_of(impl, package_data=("core/canonical.py", "core/models"))
    with pytest.raises(InternalError):
        run(spec, root)
    assert [path.is_file() for path in seen] == [True, True]
    assert seen[0].name == "canonical.py"


def _write_then_raise(error: Exception) -> Callable[[StageRun], None]:
    def impl(run: StageRun) -> None:
        (run.output("result") / "partial.txt").write_text("partial\n")
        (run.output("report") / "partial.txt").write_text("partial\n")
        raise error

    return impl


@pytest.mark.parametrize(
    ("error", "expected"), [(ValueError("synthetic"), InternalError), (UsageError("synthetic"), UsageError)]
)
def test_a_raising_impl_leaves_targets_untouched_and_no_temporary_directory(
    root: Path, error: Exception, expected: type[Exception]
) -> None:
    outputs = {"result": "data/out/result", "report": "data/reports/report"}
    old = root / "data" / "out" / "result"
    old.mkdir(parents=True)
    (old / "old.txt").write_text("old\n")
    with pytest.raises(expected):
        run(spec_of(_write_then_raise(error), outputs=outputs), root)
    assert sorted(path.name for path in old.iterdir()) == ["old.txt"]
    assert leftovers(root / "data" / "out") == []
    assert not (root / "data" / "reports" / "report").exists()
    assert leftovers(root / "data" / "reports") == []


def test_a_failed_check_promotes_the_outputs_then_raises(root: Path) -> None:
    def impl(run: StageRun) -> None:
        (run.output("result") / "report.json").write_text("{}\n")
        run.check("passes", True)
        run.check("fails", False, count=2)

    with pytest.raises(CheckFailedError) as caught:
        run(spec_of(impl), root)
    assert caught.value.details["failed"] == ["fails"]
    target = root / "data" / "out" / "result"
    assert (target / "report.json").exists()
    checks = provenance_of(target).checks
    assert [(check.name, check.status, check.details) for check in checks] == [
        ("passes", "pass", {}),
        ("fails", "fail", {"count": 2}),
    ]


def test_outputs_replace_the_old_directory_and_carry_provenance(root: Path) -> None:
    old = root / "data" / "out" / "result"
    old.mkdir(parents=True)
    (old / "stale.txt").write_text("stale\n")
    result = run(spec_of(), root)
    assert sorted(path.name for path in old.iterdir()) == ["_provenance.json", "copy.txt"]
    assert leftovers(root / "data" / "out") == []
    data = b"synthetic text\n"
    provenance = provenance_of(old)
    assert provenance.stage == "synthetic"
    assert provenance.output == "result"
    assert provenance.code == CODE
    assert [item.model_dump() for item in provenance.files] == [
        {"path": "copy.txt", "sha256": sha256_hex(data), "bytes": len(data), "rows": None}
    ]
    (item,) = provenance.inputs
    assert (item.name, item.type, item.path, item.sha256, item.dataset_ids) == (
        "text",
        "file",
        "data/in/text.txt",
        sha256_hex(data),
        [],
    )
    assert provenance.params.values == {}
    assert result.stage == "synthetic"
    assert result.outputs == {"result": provenance.files}
    assert [check.name for check in result.checks] == ["non_empty"]
    assert result.warnings == []


def test_provenance_is_byte_identical_on_a_rerun(root: Path) -> None:
    run(spec_of(), root)
    first = (root / "data" / "out" / "result" / "_provenance.json").read_bytes()
    run(spec_of(), root)
    assert (root / "data" / "out" / "result" / "_provenance.json").read_bytes() == first
    assert first.endswith(b"}\n")
    assert b'  "code"' in first  # indent=2, sorted keys


def test_parquet_rows_and_nested_files_are_recorded(root: Path) -> None:
    def impl(run: StageRun) -> None:
        out = run.output("result")
        (out / "sub").mkdir()
        (out / "sub" / "b.txt").write_text("b\n")
        frame = pd.DataFrame({"key": ["x", "y"]})
        write_parquet(
            frame,
            out / "a.parquet",
            kind="synthetic_table",
            schema_version=1,
            sort_by=["key"],
            schema=pa.schema([("key", pa.string())]),
        )

    run(spec_of(impl), root)
    files = provenance_of(root / "data" / "out" / "result").files
    assert [(item.path, item.rows) for item in files] == [("a.parquet", 2), ("sub/b.txt", None)]


def test_params_are_recorded_with_their_hash(root: Path) -> None:
    spec = spec_of(params=("meter.timebase",))
    inputs = {"text": root / "data" / "in" / "text.txt"}
    outputs = {"result": root / "data" / "out" / "result"}
    run_stage(spec, inputs, {"meter.timebase": "nem_aest"}, outputs, code=CODE, recorded_code=CODE.stage_sha256)
    params = provenance_of(outputs["result"]).params
    assert params.values == {"meter.timebase": "nem_aest"}
    assert params.sha256 == content_hash({"meter.timebase": "nem_aest"})


def test_warnings_are_returned_not_recorded(root: Path) -> None:
    def impl(run: StageRun) -> None:
        run.output("result")
        run.warn(WarningCode.W_PREMISES_UNCHECKED, "synthetic warning", count=1)

    result = run(spec_of(impl), root)
    assert [(item.code, item.details) for item in result.warnings] == [(WarningCode.W_PREMISES_UNCHECKED, {"count": 1})]
    assert provenance_of(root / "data" / "out" / "result").checks == []


def test_an_input_override_outside_the_workspace_records_the_default_path(root: Path, tmp_path: Path) -> None:
    elsewhere = tmp_path / "outside.txt"
    elsewhere.write_bytes(b"outside\n")
    spec = spec_of()
    prepared = prepare_stage(spec, root=root, params_file=root / "params.yaml", input_overrides={"text": elsewhere})
    run_stage(spec, prepared.inputs, prepared.params, prepared.outputs, code=CODE, recorded_code=CODE.stage_sha256)
    (item,) = provenance_of(root / "data" / "out" / "result").inputs
    assert item.path == "data/in/text.txt"
    assert item.sha256 == sha256_hex(b"outside\n")
    assert str(tmp_path) not in (root / "data" / "out" / "result" / "_provenance.json").read_text()


# Inputs: directories, catalogs and lineage (D1 §9.2, §10)


def _record_line(source: str, payload: bytes) -> bytes:
    digest = sha256_hex(payload)
    ident = dataset_id(source, datetime(2026, 1, 2, 3, 4, 5), digest)
    doc: dict[str, JSONValue] = {
        "kind": "catalog_record",
        "schema_version": 1,
        "dataset_id": ident,
        "source": source,
        "sha256": digest,
        "bytes": len(payload),
        "stored_path": f"data/raw/{source}/2026/{ident}.csv",
        "original_filename": "synthetic.csv",
        "landed_at": "2026-01-02T00:00:00Z",
        "file_mtime": None,
        "source_ts": "2026-01-02T03:04:05",
        "coverage": None,
        "detector": {"name": source, "version": 1, "evidence": ["content"]},
        "extra": {},
        "supersedes": None,
        "notes": "",
    }
    return canonical_json(doc) + b"\n"


def _per_source_spec(impl: Callable[[StageRun], None]) -> StageSpec:
    return spec_of(
        impl,
        inputs={
            "catalog.sapn_summary": "data/raw/sapn_summary/_catalog.jsonl",
            "catalog.sapn_interval": "data/raw/sapn_interval/_catalog.jsonl",
        },
    )


def test_inputs_by_prefix_are_keyed_by_suffix_in_sorted_order(root: Path) -> None:
    for source in ("sapn_interval", "sapn_summary"):
        (root / "data" / "raw" / source).mkdir(parents=True)
        (root / "data" / "raw" / source / "_catalog.jsonl").write_bytes(b"")
    seen: list[list[str]] = []

    def impl(run: StageRun) -> None:
        run.output("result")
        seen.append(list(run.inputs("catalog")))

    run(_per_source_spec(impl), root)
    assert seen == [["sapn_interval", "sapn_summary"]]


def test_catalog_inputs_record_their_dataset_ids_and_an_empty_catalog_is_accepted(root: Path) -> None:
    (root / "data" / "raw" / "sapn_interval").mkdir(parents=True)
    (root / "data" / "raw" / "sapn_summary").mkdir(parents=True)
    lines = _record_line("sapn_interval", b"two") + b"not json\n" + _record_line("sapn_interval", b"one")
    (root / "data" / "raw" / "sapn_interval" / "_catalog.jsonl").write_bytes(lines)
    (root / "data" / "raw" / "sapn_summary" / "_catalog.jsonl").write_bytes(b"")

    run(_per_source_spec(lambda run: None), root)
    inputs = {item.name: item for item in provenance_of(root / "data" / "out" / "result").inputs}
    expected = sorted(json.loads(line)["dataset_id"] for line in lines.splitlines() if line.startswith(b"{"))
    assert inputs["catalog.sapn_interval"].dataset_ids == expected
    assert inputs["catalog.sapn_summary"].dataset_ids == []
    assert inputs["catalog.sapn_summary"].sha256 == sha256_hex(b"")


def test_a_directory_input_hashes_its_tree_and_inherits_upstream_dataset_ids(root: Path) -> None:
    (root / "data" / "raw" / "sapn_interval").mkdir(parents=True)
    line = _record_line("sapn_interval", b"one")
    (root / "data" / "raw" / "sapn_interval" / "_catalog.jsonl").write_bytes(line)

    def write_x(run: StageRun) -> None:
        (run.output("result") / "x.txt").write_text("x\n")

    upstream = spec_of(
        write_x,
        inputs={"catalog.sapn_interval": "data/raw/sapn_interval/_catalog.jsonl"},
    )
    run(upstream, root)
    upstream_dir = root / "data" / "out" / "result"
    (upstream_dir / ".DS_Store").write_bytes(b"finder")

    downstream = spec_of(lambda run: None, inputs={"upstream": "data/out/result"}, outputs={"result": "data/out/next"})
    run(downstream, root)
    (item,) = provenance_of(root / "data" / "out" / "next").inputs
    assert item.type == "dir"
    assert item.dataset_ids == [json.loads(line)["dataset_id"]]
    expected = tree_hash(
        (path.relative_to(upstream_dir).as_posix(), sha256_hex(path.read_bytes()))
        for path in upstream_dir.rglob("*")
        if path.is_file() and path.name != ".DS_Store"
    )
    assert item.sha256 == expected


def test_provenance_lists_inputs_in_declared_order_whatever_the_callers_order(root: Path) -> None:
    (root / "data" / "in" / "other.txt").write_bytes(b"other\n")
    spec = spec_of(lambda run: None, inputs={"text": "data/in/text.txt", "other": "data/in/other.txt"})
    prepared = prepare_stage(spec, root=root, params_file=root / "params.yaml")
    target = prepared.outputs["result"]
    contents: list[bytes] = []
    for inputs in (dict(prepared.inputs), dict(reversed(list(prepared.inputs.items())))):
        run_stage(spec, inputs, {}, prepared.outputs, code=CODE, recorded_code=CODE.stage_sha256)
        contents.append((target / "_provenance.json").read_bytes())
    assert contents[0] == contents[1]
    assert [item.name for item in provenance_of(target).inputs] == ["text", "other"]


def test_the_zone_key_pattern_is_the_one_core_defines() -> None:
    """One definition, in `core/models/_common.py`: the stage's `zone()` and the `ZoneKey` field type agree."""
    assert runner.ZONE_KEY is _common.ZONE_KEY
    assert _common.ZONE_KEY.pattern == r"^[A-Za-z0-9_+-]+(/[A-Za-z0-9_+-]+)*$"  # the P2 spec §6 pattern, verbatim
