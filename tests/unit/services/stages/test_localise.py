"""The `localise` stage (P2 spec §10, §13.3): meter time to local time, behind the time-basis gate.

Every value is synthetic. The stage's inputs are the real `parse_nem12`, `merge_interval` and `validate_timebase`
outputs for the fixture zone, with the usage exports generated under the basis a test needs. Expected instants and
local clocks come from the independent oracle (`tests/fixtures/oracle.py`), not from `core.timebase`.
"""

import contextlib
import json
import math
import shutil
from collections import Counter
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from energatron.core.canonical import canonical_json, content_hash, sha256_hex
from energatron.core.errors import CheckFailedError, InvalidInputError
from energatron.core.models.catalog import SourceType
from energatron.core.models.provenance import StageCode, StageProvenance
from energatron.services.stages import localise
from energatron.services.stages.registry import STAGES, get_stage
from energatron.services.stages.runner import StageResult, prepare_stage, run_stage
from tests.fixtures import oracle
from tests.fixtures.make_sources import make_retailer_usage_from_nem12
from tests.fixtures.params_text import params_v2_text
from tests.fixtures.pipeline_workspace import LABELS, WINDOWS, RawZone, build_raw_zone, land_blob
from tests.fixtures.synthetic import SYNTHETIC_ACCOUNT, SYNTHETIC_NMI, SYNTHETIC_NMI11, SYNTHETIC_SERIAL

CHECKS = ["timebase_gate", "energy_conserved", "key_unique", "local_placeable", "local_day_lengths"]
COLUMNS = [
    "suffix",
    "utc_start",
    "local_start",
    "local_date",
    "utc_offset_minutes",
    "meter_start",
    "meter_date",
    "interval_minutes",
    "kwh",
    "quality",
    "quality_flag",
    "reason_code",
    "dataset_id",
]


def day(month: int, number: int = 1) -> datetime:
    return datetime(2025, month, number)


WINTER = [day(5), day(6), day(7), day(8), day(9), day(10)]
MIXED = [day(4, 3), day(4, 4), day(4, 5), *WINTER, day(10, 5), day(10, 6), day(10, 7), day(10, 8)]


def _code(name: str) -> StageCode:
    source = sha256_hex(name.encode())
    digest = content_hash({"source_sha256": source, "packages": {}, "python": "3.14.0"})
    return StageCode(stage_sha256=digest, source_sha256=source, python="3.14.0", packages={}, files=["__init__.py"])


def run_named(name: str, root: Path) -> StageResult:
    spec = get_stage(name)
    prepared = prepare_stage(spec, root=root, params_file=root / "params.yaml")
    code = _code(name)
    return run_stage(
        spec, prepared.inputs, prepared.params, prepared.outputs, code=code, recorded_code=code.stage_sha256
    )


def set_params(root: Path, **changes: object) -> None:
    (root / "params.yaml").write_text(params_v2_text(region=True, **changes))  # type: ignore[arg-type]


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> RawZone:
    """The fixture zone, parsed and merged, with its own usage exports removed: each test lands what it needs."""
    root = tmp_path_factory.mktemp("template")
    zone = build_raw_zone(root)
    usage = root / "data" / "raw" / "retailer_usage"
    shutil.rmtree(usage)
    usage.mkdir()
    (usage / "_catalog.jsonl").write_bytes(b"")
    set_params(root)
    run_named("parse_nem12", root)
    run_named("merge_interval", root)
    return zone


@pytest.fixture
def zone(template: RawZone, tmp_path: Path) -> RawZone:
    root = tmp_path / "workspace"
    shutil.copytree(template.root, root)
    return replace(template, root=root)


def interval_dir(root: Path) -> Path:
    return root / "data" / "interim" / "elec_interval_nem"


def timebase_dir(root: Path) -> Path:
    return root / "data" / "reports" / "validation" / "timebase"


def local_dir(root: Path) -> Path:
    return root / "data" / "interim" / "elec_interval_local"


def land_usage(zone: RawZone, reads: list[datetime], basis: str) -> None:
    data = make_retailer_usage_from_nem12(
        zone.truth, basis=basis, windows=WINDOWS, reads=reads, labels=LABELS, account=SYNTHETIC_ACCOUNT
    )
    land_blob(
        zone.root,
        SourceType.RETAILER_USAGE,
        "synthetic_usage.csv",
        data,
        file_mtime=datetime(2026, 1, 1, tzinfo=UTC),
    )


def prepare(zone: RawZone, reads: list[datetime], generated: str, configured: str | None = None) -> None:
    """Land an export made under `generated`, configure `configured` (default: the same) and run `validate_timebase`.

    A `contradicted` verdict fails that stage's check, but the report is written first, so the gate can read it."""
    land_usage(zone, reads, generated)
    set_params(zone.root, basis=configured or generated)
    with contextlib.suppress(CheckFailedError):
        run_named("validate_timebase", zone.root)


def table(root: Path) -> pa.Table:
    return pq.read_table(local_dir(root) / "intervals.parquet")


def checks(root: Path) -> dict[str, tuple[str, dict[str, object]]]:
    provenance = StageProvenance.from_document(json.loads((local_dir(root) / "_provenance.json").read_text()))
    return {check.name: (check.status, dict(check.details)) for check in provenance.checks}


def failing(root: Path) -> dict[str, dict[str, object]]:
    with pytest.raises(CheckFailedError):
        run_named("localise", root)
    return {name: details for name, (status, details) in checks(root).items() if status == "fail"}


def rows_per_local_day(found: pa.Table, suffix: str = "E1") -> Counter[date]:
    return Counter(
        local_date
        for local_date, name in zip(found["local_date"].to_pylist(), found["suffix"].to_pylist(), strict=True)
        if name == suffix
    )


# --- the spec ----------------------------------------------------------------------------------------------------


def test_spec_reads_the_merged_intervals_and_the_timebase_report() -> None:
    spec = localise.SPEC
    assert spec.name == "localise"
    assert dict(spec.inputs) == {
        "interval": "data/interim/elec_interval_nem",
        "timebase": "data/reports/validation/timebase",
    }
    assert spec.params == ("meter.local_tz", "meter.timebase")
    assert dict(spec.outputs) == {"interval": "data/interim/elec_interval_local"}


def test_it_is_registered_right_after_validate_timebase() -> None:
    names = list(STAGES)
    assert names[names.index("validate_timebase") + 1] == "localise"


# --- a confirmed basis -------------------------------------------------------------------------------------------


def test_a_confirmed_run_writes_the_table_and_provenance_and_passes_every_check(zone: RawZone) -> None:
    prepare(zone, MIXED, "nem_aest")
    found = run_named("localise", zone.root)
    assert sorted(path.name for path in local_dir(zone.root).iterdir()) == ["_provenance.json", "intervals.parquet"]
    assert [(check.name, check.status) for check in found.checks] == [(name, "pass") for name in CHECKS]
    assert found.warnings == []
    out = table(zone.root)
    assert out.column_names == COLUMNS
    assert out.schema.field("utc_start").type == pa.timestamp("us", tz=UTC)
    assert out.schema.field("local_start").type == pa.timestamp("us")
    assert out.schema.field("local_date").type == pa.date32()
    assert out.schema.field("utc_offset_minutes").type == pa.int16()
    metadata = out.schema.metadata
    assert metadata[b"energatron.kind"] == b"elec_interval_local"
    assert metadata[b"energatron.schema_version"] == b"1"
    merged = pq.read_table(interval_dir(zone.root) / "intervals.parquet")
    assert out.num_rows == merged.num_rows


@pytest.mark.parametrize("basis", ["nem_aest", "local_standard"])
def test_every_row_is_placed_as_the_oracle_places_it(zone: RawZone, basis: str) -> None:
    prepare(zone, MIXED, basis)
    run_named("localise", zone.root)
    out = table(zone.root).to_pylist()
    assert out
    for row in out:
        meter_start = row["meter_start"]
        utc = oracle.utc_time(meter_start, basis)
        local = oracle.local_time(meter_start, basis)
        assert row["utc_start"].astimezone(UTC).replace(tzinfo=None) == utc
        assert row["local_start"] == local
        assert row["local_date"] == local.date()
        assert row["utc_offset_minutes"] == (local - utc) // timedelta(minutes=1)
    starts = [(row["suffix"], row["utc_start"]) for row in out]
    assert starts == sorted(starts)


@pytest.mark.parametrize("basis", ["nem_aest", "local_standard"])
def test_the_23_hour_and_25_hour_local_days_have_46_and_50_half_hours(zone: RawZone, basis: str) -> None:
    prepare(zone, MIXED, basis)
    run_named("localise", zone.root)
    april, october = oracle.sa_dst_dates(2025)
    counts = rows_per_local_day(table(zone.root))
    assert counts[october] == 46  # clocks go forward: a 23-hour day
    assert counts[april] == 50  # clocks go back: a 25-hour day
    ordinary = [count for local_date, count in counts.items() if local_date not in (april, october)]
    assert Counter(ordinary).most_common(1)[0][0] == 48
    status, details = checks(zone.root)["local_day_lengths"]
    assert status == "pass"
    assert details["dst_days"] == {"short": 1, "long": 1}


def test_the_utc_offset_tells_the_repeated_hour_apart(zone: RawZone) -> None:
    prepare(zone, MIXED, "nem_aest")
    run_named("localise", zone.root)
    april, _ = oracle.sa_dst_dates(2025)
    repeated = datetime.combine(april, datetime.min.time()) + timedelta(hours=2)
    found = [row for row in table(zone.root).to_pylist() if row["suffix"] == "E1" and row["local_start"] == repeated]
    assert sorted(row["utc_offset_minutes"] for row in found) == [570, 630]  # +9:30 and +10:30
    assert len({row["utc_start"] for row in found}) == 2


def test_energy_is_conserved(zone: RawZone) -> None:
    prepare(zone, MIXED, "nem_aest")
    run_named("localise", zone.root)
    merged = pq.read_table(interval_dir(zone.root) / "intervals.parquet")
    out = table(zone.root)
    assert math.fsum(out["kwh"].to_pylist()) == math.fsum(merged["kwh"].to_pylist())
    status, details = checks(zone.root)["energy_conserved"]
    assert status == "pass"
    assert details["rows"] == merged.num_rows


def test_no_identifier_reaches_the_output(zone: RawZone) -> None:
    prepare(zone, MIXED, "nem_aest")
    run_named("localise", zone.root)
    banned = [SYNTHETIC_NMI, SYNTHETIC_NMI11, SYNTHETIC_SERIAL, SYNTHETIC_ACCOUNT]
    for path in local_dir(zone.root).iterdir():
        assert not any(item.encode() in path.read_bytes() for item in banned)


# --- a meter on the wall clock ----------------------------------------------------------------------------------


def test_local_wall_across_a_daylight_saving_change_cannot_be_placed(zone: RawZone) -> None:
    prepare(zone, MIXED, "local_wall")
    failed = failing(zone.root)
    assert "local_placeable" in failed
    unplaceable = failed["local_placeable"]["unplaceable"]
    out = table(zone.root)
    assert isinstance(unplaceable, int)
    assert unplaceable > 0
    assert out["utc_start"].null_count == unplaceable
    assert out["local_start"].null_count == unplaceable
    status, _ = checks(zone.root)["energy_conserved"]
    assert status == "pass"


# --- the gate ---------------------------------------------------------------------------------------------------


def only_provenance(root: Path) -> bool:
    return sorted(path.name for path in local_dir(root).iterdir()) == ["_provenance.json"]


def test_an_unconfirmed_verdict_leaves_no_table(zone: RawZone) -> None:
    prepare(zone, WINTER, "local_wall")  # winter-only evidence can't tell local_wall from local_standard
    failed = failing(zone.root)
    assert failed == {
        "timebase_gate": {"verdict": "unconfirmed", "report_basis": "local_wall", "param_basis": "local_wall"}
    }
    assert only_provenance(zone.root)
    assert list(checks(zone.root)) == ["timebase_gate"]


def test_a_contradicted_verdict_leaves_no_table(zone: RawZone) -> None:
    prepare(zone, MIXED, "local_wall", configured="nem_aest")
    failed = failing(zone.root)
    assert failed["timebase_gate"]["verdict"] == "contradicted"
    assert only_provenance(zone.root)
    assert list(checks(zone.root)) == ["timebase_gate"]


def test_a_basis_the_report_did_not_judge_leaves_no_table(zone: RawZone) -> None:
    prepare(zone, MIXED, "nem_aest")
    set_params(zone.root, basis="local_standard")  # changed after the report was made
    failed = failing(zone.root)
    assert failed == {
        "timebase_gate": {"verdict": "confirmed", "report_basis": "nem_aest", "param_basis": "local_standard"}
    }
    assert only_provenance(zone.root)


def test_a_failed_gate_removes_the_table_an_earlier_run_left(zone: RawZone) -> None:
    prepare(zone, MIXED, "nem_aest")
    run_named("localise", zone.root)
    assert (local_dir(zone.root) / "intervals.parquet").is_file()
    set_params(zone.root, basis="local_wall")
    failing(zone.root)
    assert only_provenance(zone.root)


# --- bad inputs -------------------------------------------------------------------------------------------------


def test_a_missing_timebase_report_is_invalid_input(zone: RawZone) -> None:
    prepare(zone, MIXED, "nem_aest")
    (timebase_dir(zone.root) / "result.json").unlink()
    with pytest.raises(InvalidInputError) as caught:
        run_named("localise", zone.root)
    assert caught.value.details["input"] == "timebase"


def test_a_report_of_another_kind_is_invalid_input(zone: RawZone) -> None:
    prepare(zone, MIXED, "nem_aest")
    (timebase_dir(zone.root) / "result.json").write_bytes(canonical_json({"kind": "meter_report", "schema_version": 1}))
    with pytest.raises(InvalidInputError) as caught:
        run_named("localise", zone.root)
    assert caught.value.details["input"] == "timebase"


def test_an_interval_table_of_another_kind_is_invalid_input(zone: RawZone) -> None:
    prepare(zone, MIXED, "nem_aest")
    shutil.copy(
        zone.root / "data" / "staged" / "elec_interval" / "index.parquet", interval_dir(zone.root) / "intervals.parquet"
    )
    with pytest.raises(InvalidInputError) as caught:
        run_named("localise", zone.root)
    assert caught.value.details["input"] == "interval"


def test_a_null_zone_is_invalid_input(zone: RawZone) -> None:
    prepare(zone, MIXED, "nem_aest")
    (zone.root / "params.yaml").write_text(params_v2_text())
    with pytest.raises(InvalidInputError) as caught:
        run_named("localise", zone.root)
    assert caught.value.details["field"] == "meter.local_tz"
