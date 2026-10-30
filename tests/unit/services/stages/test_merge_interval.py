"""The `merge_interval` stage (P2 spec §7.2, §13.3): one day from the newest `UpdateDateTime`, the conflicts report and
the summary.

Every value is synthetic. The expected numbers come from the generator specs and the independent oracle, never from
the stage's own reader. The stage's input is the real `parse_nem12` output for the fixture raw zone.
"""

import contextlib
import json
import math
import shutil
from collections.abc import Callable
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from energatron.core.canonical import JSONValue, content_hash, sha256_hex
from energatron.core.errors import CheckFailedError, InvalidInputError, WarningCode
from energatron.core.models.catalog import SourceType
from energatron.core.models.meter import MergeSummary
from energatron.core.models.provenance import StageCode, StageProvenance
from energatron.services.stages import merge_interval
from energatron.services.stages.registry import get_stage
from energatron.services.stages.runner import StageResult, prepare_stage, run_stage
from tests.fixtures import oracle
from tests.fixtures.make_nem12 import Nem12Spec, make_nem12, sapn_filename
from tests.fixtures.pipeline_workspace import (
    INTERVAL_MINUTES,
    Dataset,
    RawZone,
    build_raw_zone,
    empty_raw_zone,
    land_blob,
)
from tests.fixtures.synthetic import SYNTHETIC_ACCOUNT, SYNTHETIC_NMI, SYNTHETIC_NMI11, SYNTHETIC_SERIAL

CHECKS = ["key_unique", "one_interval_length"]
PER_DAY = 1440 // INTERVAL_MINUTES
INTERVALS_SCHEMA = pa.schema(
    [
        ("suffix", pa.string()),
        ("meter_date", pa.date32()),
        ("meter_start", pa.timestamp("us")),
        ("interval_minutes", pa.int16()),
        ("kwh", pa.float64()),
        ("quality", pa.string()),
        ("quality_flag", pa.string()),
        ("reason_code", pa.int16()),
        ("update_dt", pa.timestamp("us")),
        ("dataset_id", pa.string()),
    ]
)
CONFLICTS_SCHEMA = pa.schema(
    [
        ("suffix", pa.string()),
        ("meter_date", pa.date32()),
        ("kept_dataset_id", pa.string()),
        ("dropped_dataset_id", pa.string()),
        ("kept_update_dt", pa.timestamp("us")),
        ("dropped_update_dt", pa.timestamp("us")),
        ("intervals_differing", pa.int16()),
        ("quality_differing", pa.int16()),
        ("kept_kwh", pa.float64()),
        ("dropped_kwh", pa.float64()),
        ("kept_flags", pa.string()),
        ("dropped_flags", pa.string()),
    ]
)
PARAMS = "kind: params\nschema_version: 1\n"


def _code(name: str) -> StageCode:
    source = sha256_hex(name.encode())
    digest = content_hash({"source_sha256": source, "packages": {}, "python": "3.14.0"})
    return StageCode(stage_sha256=digest, source_sha256=source, python="3.14.0", packages={}, files=["__init__.py"])


def run_named(name: str, root: Path, **overrides: Path) -> StageResult:
    spec = get_stage(name)
    prepared = prepare_stage(spec, root=root, params_file=root / "params.yaml", input_overrides=overrides)
    code = _code(name)
    return run_stage(
        spec, prepared.inputs, prepared.params, prepared.outputs, code=code, recorded_code=code.stage_sha256
    )


def parse(root: Path) -> None:
    """Run `parse_nem12`; a failed check there still leaves its tables, which is how an unread dataset is made."""
    with contextlib.suppress(CheckFailedError):
        run_named("parse_nem12", root)


def merge(root: Path, **overrides: Path) -> StageResult:
    return run_named("merge_interval", root, **overrides)


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> RawZone:
    """The fixture raw zone with `parse_nem12` already run."""
    root = tmp_path_factory.mktemp("template")
    zone = build_raw_zone(root)
    (root / "params.yaml").write_text(PARAMS)
    parse(root)
    return zone


@pytest.fixture
def zone(template: RawZone, tmp_path: Path) -> RawZone:
    root = tmp_path / "workspace"
    shutil.copytree(template.root, root)
    return replace(template, root=root)


@pytest.fixture
def empty(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    empty_raw_zone(root)
    (root / "params.yaml").write_text(PARAMS)
    return root


def staged_dir(root: Path) -> Path:
    return root / "data" / "staged" / "elec_interval"


def interval_dir(root: Path) -> Path:
    return root / "data" / "interim" / "elec_interval_nem"


def report_dir(root: Path) -> Path:
    return root / "data" / "reports" / "validation" / "merge"


def intervals(root: Path) -> pa.Table:
    return pq.read_table(interval_dir(root) / "intervals.parquet")


def conflicts(root: Path) -> pa.Table:
    return pq.read_table(report_dir(root) / "conflicts.parquet")


def summary(root: Path) -> MergeSummary:
    return MergeSummary.from_document(json.loads((report_dir(root) / "summary.json").read_text()))


def checks(root: Path, directory: Callable[[Path], Path] = interval_dir) -> dict[str, tuple[str, dict[str, JSONValue]]]:
    provenance = StageProvenance.from_document(json.loads((directory(root) / "_provenance.json").read_text()))
    return {check.name: (check.status, dict(check.details)) for check in provenance.checks}


def failing(root: Path) -> dict[str, dict[str, JSONValue]]:
    with pytest.raises(CheckFailedError):
        merge(root)
    return {name: details for name, (status, details) in checks(root).items() if status == "fail"}


def values(table: pa.Table, name: str) -> list[object]:
    return table.column(name).to_pylist()


def rows_of(table: pa.Table, **match: object) -> pa.Table:
    mask = [True] * table.num_rows
    for name, wanted in match.items():
        mask = [flag and cell == wanted for flag, cell in zip(mask, values(table, name), strict=True)]
    return table.filter(pa.array(mask, type=pa.bool_()))


def land_spec(root: Path, spec: Nem12Spec, generated: datetime, **kw: object) -> Dataset:
    window = (spec.start, spec.start + timedelta(days=spec.days - 1))
    name = sapn_filename("DETAILED", SYNTHETIC_NMI, window, generated)
    return land_blob(root, SourceType.SAPN_INTERVAL, name, make_nem12(spec), **kw)  # type: ignore[arg-type]


def small_spec(**changes: object) -> Nem12Spec:
    return replace(Nem12Spec(start=date(2025, 6, 1), days=3, interval_minutes=INTERVAL_MINUTES), **changes)  # type: ignore[arg-type]


def flat(
    value: str, *, start: date = date(2025, 6, 1), days: int = 3, minutes: int = INTERVAL_MINUTES
) -> dict[date, list[str]]:
    """Exact values for every day: one constant, so datasets differ in every interval."""
    return {start + timedelta(days=n): [value] * (1440 // minutes) for n in range(days)}


def updated(days: int) -> Callable[[date], datetime]:
    """An `UpdateDateTime` that many days after the meter date, at 05:00."""
    return lambda day: datetime.combine(day + timedelta(days=days), datetime.min.time()) + timedelta(hours=5)


def spec_days(spec: Nem12Spec) -> set[date]:
    return {spec.start + timedelta(days=n) for n in range(spec.days)}


def rewrite(path: Path, change: Callable[[pa.Table], pa.Table]) -> None:
    """Replace a Parquet file by `change` of its table, keeping the schema metadata."""
    table = pq.read_table(path)
    changed = change(table)
    pq.write_table(changed.replace_schema_metadata(table.schema.metadata), path, compression="zstd")


def staged_path(root: Path, dataset: Dataset) -> Path:
    return staged_dir(root) / f"{dataset.dataset_id}.parquet"


# --- the spec ----------------------------------------------------------------------------------------------------


def test_spec_reads_the_staged_zone_and_writes_the_interval_and_report_zones() -> None:
    spec = merge_interval.SPEC
    assert spec.name == "merge_interval"
    assert dict(spec.inputs) == {"staged": "data/staged/elec_interval"}
    assert spec.params == ()
    assert dict(spec.outputs) == {
        "interval": "data/interim/elec_interval_nem",
        "report": "data/reports/validation/merge",
    }


def test_it_is_registered_after_parse_nem12() -> None:
    from energatron.services.stages.registry import STAGES

    assert get_stage("merge_interval") is merge_interval.SPEC
    assert list(STAGES)[:3] == ["catalog_index", "parse_nem12", "merge_interval"]


# --- the fixture zone --------------------------------------------------------------------------------------------


def test_it_writes_the_tables_the_summary_and_provenance(zone: RawZone) -> None:
    merge(zone.root)
    assert sorted(path.name for path in interval_dir(zone.root).iterdir()) == ["_provenance.json", "intervals.parquet"]
    assert sorted(path.name for path in report_dir(zone.root).iterdir()) == [
        "_provenance.json",
        "conflicts.parquet",
        "summary.json",
    ]


def test_every_check_passes_and_nothing_warns_on_the_fixture_zone(zone: RawZone) -> None:
    result = merge(zone.root)
    results = checks(zone.root)
    assert list(results) == CHECKS
    assert {status for status, _ in results.values()} == {"pass"}
    assert [check.name for check in result.checks] == CHECKS
    assert result.warnings == []
    assert checks(zone.root, report_dir) == results


def test_the_tables_have_the_columns_types_and_metadata(zone: RawZone) -> None:
    merge(zone.root)
    table = intervals(zone.root)
    assert table.schema.remove_metadata().equals(INTERVALS_SCHEMA)
    assert table.schema.metadata == {b"energatron.kind": b"elec_interval_nem", b"energatron.schema_version": b"1"}
    found = conflicts(zone.root)
    assert found.schema.remove_metadata().equals(CONFLICTS_SCHEMA)
    assert found.schema.metadata == {b"energatron.kind": b"merge_conflicts", b"energatron.schema_version": b"1"}


def test_merged_rows_are_the_distinct_days_times_the_intervals_per_day(zone: RawZone) -> None:
    merge(zone.root)
    table = intervals(zone.root)
    specs = zone.specs
    distinct = spec_days(specs.a) | spec_days(specs.b)
    assert len(distinct) == specs.truth.days
    assert table.num_rows == len(distinct) * PER_DAY
    assert set(values(table, "meter_date")) == distinct
    assert set(values(table, "suffix")) == {"E1"}
    keys = list(zip(values(table, "suffix"), values(table, "meter_start"), strict=True))
    assert keys == sorted(keys)
    assert len(set(keys)) == table.num_rows


def test_the_merged_days_equal_the_truth_file_day_by_day(zone: RawZone) -> None:
    merge(zone.root)
    table = intervals(zone.root)
    expected = oracle.day_sums(zone.truth)
    per_day: dict[date, list[float]] = {}
    for day, kwh in zip(values(table, "meter_date"), values(table, "kwh"), strict=True):
        per_day.setdefault(day, []).append(kwh)  # type: ignore[arg-type]
    assert {day: math.fsum(kwh) for day, kwh in per_day.items()} == {day: total for (_, day), total in expected.items()}


def test_each_day_comes_from_the_dataset_with_the_newest_update(zone: RawZone) -> None:
    merge(zone.root)
    table = intervals(zone.root)
    a, b = zone.interval
    specs = zone.specs
    source_of = dict(zip(values(table, "meter_date"), values(table, "dataset_id"), strict=True))
    for day in spec_days(specs.a) - spec_days(specs.b):
        assert source_of[day] == a.dataset_id
    for day in spec_days(specs.b):
        assert source_of[day] == b.dataset_id  # the conflict day by its newer update, the rest by precedence
    assert len(set(values(rows_of(table, meter_date=specs.conflict_day), "dataset_id"))) == 1


def test_quality_and_update_time_are_those_of_the_kept_day(zone: RawZone) -> None:
    merge(zone.root)
    table = intervals(zone.root)
    specs = zone.specs
    assert set(values(rows_of(table, meter_date=specs.v_day), "quality")) == {"S14"}
    assert set(values(rows_of(table, meter_date=specs.e_day), "quality")) == {"E52"}
    stamp = datetime.combine(specs.conflict_day + timedelta(days=11), datetime.min.time()) + timedelta(hours=5)
    assert set(values(rows_of(table, meter_date=specs.conflict_day), "update_dt")) == {stamp}


def test_identical_overlapping_days_are_no_conflict_and_the_changed_one_is_reported(zone: RawZone) -> None:
    merge(zone.root)
    found = conflicts(zone.root)
    specs = zone.specs
    a, b = zone.interval
    overlap = (specs.overlap[1] - specs.overlap[0]).days + 1
    assert overlap > 1  # the other overlap days hold the same values in both files
    assert found.num_rows == 1
    row = {name: values(found, name)[0] for name in found.column_names}
    day = specs.conflict_day
    assert row == {
        "suffix": "E1",
        "meter_date": day,
        "kept_dataset_id": b.dataset_id,
        "dropped_dataset_id": a.dataset_id,
        "kept_update_dt": datetime.combine(day + timedelta(days=11), datetime.min.time()) + timedelta(hours=5),
        "dropped_update_dt": datetime.combine(day + timedelta(days=1), datetime.min.time()) + timedelta(hours=5),
        "intervals_differing": PER_DAY,
        "quality_differing": 0,
        "kept_kwh": oracle.day_sums(make_nem12(specs.b))["E1", day],
        "dropped_kwh": oracle.day_sums(make_nem12(specs.a))["E1", day],
        "kept_flags": "A",
        "dropped_flags": "A",
    }


def test_the_summary_counts_the_fixture_zone(zone: RawZone) -> None:
    merge(zone.root)
    found = summary(zone.root)
    a, b = zone.interval
    specs = zone.specs
    assert found.datasets_used == [b.dataset_id, a.dataset_id]  # highest precedence first
    assert found.datasets_skipped.superseded == []
    assert found.datasets_skipped.unread == []
    assert found.days == {"E1": specs.truth.days}
    assert found.overlap_days == (specs.overlap[1] - specs.overlap[0]).days + 1
    assert found.conflict_days == 1
    assert found.conflict_intervals == PER_DAY
    assert found.same_update_conflicts == 0
    document = json.loads((report_dir(zone.root) / "summary.json").read_text())
    assert (document["kind"], document["schema_version"]) == ("merge_summary", 1)


def test_provenance_names_the_staged_directory_and_its_datasets(zone: RawZone) -> None:
    merge(zone.root)
    for directory in (interval_dir, report_dir):
        path = directory(zone.root) / "_provenance.json"
        provenance = StageProvenance.from_document(json.loads(path.read_text()))
        (record,) = provenance.inputs
        assert (record.name, record.type, record.path) == ("staged", "dir", "data/staged/elec_interval")
        assert record.dataset_ids == sorted(dataset.dataset_id for dataset in zone.interval)


def test_running_twice_writes_the_same_bytes(zone: RawZone) -> None:
    def snapshot() -> dict[str, bytes]:
        return {
            f"{directory.name}/{path.name}": path.read_bytes()
            for directory in (interval_dir(zone.root), report_dir(zone.root))
            for path in directory.iterdir()
        }

    merge(zone.root)
    first = snapshot()
    merge(zone.root)
    assert snapshot() == first


def test_the_input_can_be_overridden(zone: RawZone, tmp_path: Path) -> None:
    other = tmp_path / "elsewhere"
    shutil.copytree(staged_dir(zone.root), other)
    shutil.rmtree(staged_dir(zone.root))  # the default location is gone
    merge(zone.root, staged=other)
    assert intervals(zone.root).num_rows == zone.specs.truth.days * PER_DAY


def test_no_nmi_serial_or_account_reaches_any_output(zone: RawZone) -> None:
    merge(zone.root)
    secrets = [SYNTHETIC_NMI, SYNTHETIC_NMI11, SYNTHETIC_SERIAL, SYNTHETIC_ACCOUNT]
    for directory in (interval_dir(zone.root), report_dir(zone.root)):
        for path in directory.iterdir():
            data = path.read_bytes()
            assert not any(secret.encode() in data for secret in secrets), path.name
            if path.suffix == ".parquet":
                table = pq.read_table(path)
                assert not any(name in table.column_names for name in ("nmi", "serial", "meter_serial", "account"))


# --- the rule ----------------------------------------------------------------------------------------------------


def test_equal_updates_are_decided_by_precedence_and_warn(empty: Path) -> None:
    older = land_spec(empty, small_spec(seed=1), datetime(2025, 7, 1, 9, 0, 0))
    newer = land_spec(empty, small_spec(seed=2), datetime(2025, 7, 2, 9, 0, 0))
    parse(empty)
    result = merge(empty)
    table = intervals(empty)
    assert set(values(table, "dataset_id")) == {newer.dataset_id}
    found = summary(empty)
    assert (found.overlap_days, found.conflict_days, found.same_update_conflicts) == (3, 3, 3)
    assert [warning.code for warning in result.warnings] == [WarningCode.W_MERGE_SAME_UPDATE]
    (warning,) = result.warnings
    assert warning.details == {"same_update_conflicts": 3}
    rows = conflicts(empty)
    assert set(values(rows, "dropped_dataset_id")) == {older.dataset_id}
    assert values(rows, "kept_update_dt") == values(rows, "dropped_update_dt")
    assert {status for status, _ in checks(empty).values()} == {"pass"}  # a conflict is a finding, not a failure


def test_the_newest_update_wins_over_a_higher_precedence(empty: Path) -> None:
    low = land_spec(empty, small_spec(seed=1, update_dt=updated(3)), datetime(2025, 7, 1, 9, 0, 0))
    land_spec(
        empty, small_spec(seed=2, update_dt=updated(1)), datetime(2025, 7, 2, 9, 0, 0)
    )  # newer file, older update
    parse(empty)
    result = merge(empty)
    assert set(values(intervals(empty), "dataset_id")) == {low.dataset_id}
    assert summary(empty).same_update_conflicts == 0
    assert result.warnings == []


def test_a_difference_in_quality_alone_is_a_conflict(empty: Path) -> None:
    day = date(2025, 6, 2)
    older = land_spec(empty, small_spec(seed=1, day_quality={day: "E52"}), datetime(2025, 7, 1, 9, 0, 0))
    newer = land_spec(empty, small_spec(seed=1, update_dt=updated(2)), datetime(2025, 7, 2, 9, 0, 0))
    parse(empty)
    merge(empty)
    rows = conflicts(empty)
    assert rows.num_rows == 1
    row = {name: values(rows, name)[0] for name in rows.column_names}
    assert row["meter_date"] == day
    assert (row["kept_dataset_id"], row["dropped_dataset_id"]) == (newer.dataset_id, older.dataset_id)
    assert (row["intervals_differing"], row["quality_differing"]) == (0, PER_DAY)
    assert (row["kept_flags"], row["dropped_flags"]) == ("A", "E")
    assert row["kept_kwh"] == row["dropped_kwh"]
    found = summary(empty)
    assert (found.conflict_days, found.conflict_intervals, found.same_update_conflicts) == (1, PER_DAY, 0)


def test_three_candidates_give_one_conflict_row_per_dropped_dataset(empty: Path) -> None:
    first = land_spec(empty, small_spec(day_values=flat("0.100"), update_dt=updated(5)), datetime(2025, 7, 1, 9, 0, 0))
    second = land_spec(empty, small_spec(day_values=flat("0.200"), update_dt=updated(1)), datetime(2025, 7, 2, 9, 0, 0))
    third = land_spec(empty, small_spec(day_values=flat("0.300"), update_dt=updated(1)), datetime(2025, 7, 3, 9, 0, 0))
    parse(empty)
    merge(empty)
    assert set(values(intervals(empty), "dataset_id")) == {first.dataset_id}
    rows = conflicts(empty)
    assert rows.num_rows == 6
    keys = list(
        zip(values(rows, "suffix"), values(rows, "meter_date"), values(rows, "dropped_dataset_id"), strict=True)
    )
    assert keys == sorted(keys)
    assert set(values(rows, "kept_dataset_id")) == {first.dataset_id}
    assert set(values(rows, "dropped_dataset_id")) == {second.dataset_id, third.dataset_id}
    assert set(values(rows, "intervals_differing")) == {PER_DAY}
    assert set(values(rows, "kept_kwh")) == {math.fsum([0.1] * PER_DAY)}
    found = summary(empty)
    assert found.datasets_used == [third.dataset_id, second.dataset_id, first.dataset_id]
    assert (found.overlap_days, found.conflict_days, found.conflict_intervals) == (3, 3, 6 * PER_DAY)
    assert found.same_update_conflicts == 0  # the dropped two tie with each other, not with the kept day


def test_a_superseded_dataset_is_skipped(empty: Path) -> None:
    old = land_spec(empty, small_spec(seed=1), datetime(2025, 7, 1, 9, 0, 0))
    new = land_spec(
        empty,
        small_spec(seed=2, start=date(2025, 6, 2)),
        datetime(2025, 7, 2, 9, 0, 0),
        supersedes=old.dataset_id,
    )
    parse(empty)
    merge(empty)
    table = intervals(empty)
    assert set(values(table, "dataset_id")) == {new.dataset_id}
    assert set(values(table, "meter_date")) == {date(2025, 6, 2), date(2025, 6, 3), date(2025, 6, 4)}  # not the old 1st
    found = summary(empty)
    assert found.datasets_used == [new.dataset_id]
    assert found.datasets_skipped.superseded == [old.dataset_id]
    assert found.datasets_skipped.unread == []
    assert (found.overlap_days, found.conflict_days) == (0, 0)
    assert conflicts(empty).num_rows == 0


def test_an_unread_dataset_is_skipped(zone: RawZone) -> None:
    target, other = zone.interval
    catalog = zone.root / "data" / "raw" / "sapn_interval" / "_catalog.jsonl"
    lines = [json.loads(line) for line in catalog.read_bytes().splitlines()]
    for doc in lines:
        if doc["dataset_id"] == target.dataset_id:
            doc["detector"]["version"] = 2  # a version `parse_nem12` doesn't read
    catalog.write_text("".join(json.dumps(doc, sort_keys=True, separators=(",", ":")) + "\n" for doc in lines))
    parse(zone.root)
    assert not staged_path(zone.root, target).exists()
    merge(zone.root)
    table = intervals(zone.root)
    assert set(values(table, "dataset_id")) == {other.dataset_id}
    assert table.num_rows == zone.specs.b.days * PER_DAY
    found = summary(zone.root)
    assert found.datasets_used == [other.dataset_id]
    assert found.datasets_skipped.unread == [target.dataset_id]
    assert found.datasets_skipped.superseded == []
    assert found.days == {"E1": zone.specs.b.days}
    assert {status for status, _ in checks(zone.root).values()} == {"pass"}


def test_each_suffix_is_merged_on_its_own(empty: Path) -> None:
    a = land_spec(empty, small_spec(seed=1, suffixes=("E1", "B1")), datetime(2025, 7, 1, 9, 0, 0))
    b = land_spec(
        empty, small_spec(seed=2, start=date(2025, 6, 3), suffixes=("E1", "B1")), datetime(2025, 7, 2, 9, 0, 0)
    )
    parse(empty)
    merge(empty)
    table = intervals(empty)
    assert table.num_rows == 2 * 5 * PER_DAY
    assert summary(empty).days == {"B1": 5, "E1": 5}
    assert summary(empty).overlap_days == 2  # one overlapping date, once per suffix
    mine = rows_of(table, suffix="B1", meter_date=date(2025, 6, 3))
    assert set(values(mine, "dataset_id")) == {b.dataset_id}
    assert set(values(rows_of(table, suffix="E1", meter_date=date(2025, 6, 1)), "dataset_id")) == {a.dataset_id}
    assert conflicts(empty).num_rows == 2  # B1 and E1 each differ on the shared day
    assert set(values(conflicts(empty), "suffix")) == {"B1", "E1"}


# --- the checks --------------------------------------------------------------------------------------------------


def test_one_interval_length_fails_when_candidates_disagree(empty: Path) -> None:
    land_spec(empty, small_spec(seed=1), datetime(2025, 7, 1, 9, 0, 0))
    land_spec(empty, small_spec(seed=2, start=date(2025, 6, 10), interval_minutes=15), datetime(2025, 7, 2, 9, 0, 0))
    parse(empty)
    failed = failing(empty)
    assert failed == {"one_interval_length": {"lengths": {"E1": [15, 30]}}}
    assert intervals(empty).num_rows == 3 * 48 + 3 * 96  # the table is still written
    assert (report_dir(empty) / "summary.json").is_file()


def test_one_interval_length_passes_for_a_dataset_that_is_skipped(empty: Path) -> None:
    old = land_spec(empty, small_spec(seed=1, interval_minutes=15), datetime(2025, 7, 1, 9, 0, 0))
    land_spec(empty, small_spec(seed=2), datetime(2025, 7, 2, 9, 0, 0), supersedes=old.dataset_id)
    parse(empty)
    merge(empty)
    assert checks(empty)["one_interval_length"][0] == "pass"


def test_a_dropped_interval_with_no_kept_partner_counts_as_differing(empty: Path) -> None:
    day = date(2025, 6, 1)
    older = land_spec(
        empty,
        small_spec(days=1, interval_minutes=15, day_values=flat("0.100", days=1, minutes=15)),
        datetime(2025, 7, 1, 9, 0, 0),
    )
    newer = land_spec(
        empty,
        small_spec(days=1, interval_minutes=30, day_values=flat("0.100", days=1, minutes=30)),
        datetime(2025, 7, 2, 9, 0, 0),
    )
    parse(empty)
    assert set(failing(empty)) == {"one_interval_length"}
    rows = conflicts(empty)
    assert rows.num_rows == 1
    row = {name: values(rows, name)[0] for name in rows.column_names}
    assert (row["meter_date"], row["kept_dataset_id"], row["dropped_dataset_id"]) == (
        day,
        newer.dataset_id,
        older.dataset_id,
    )
    # the 96 dropped quarter-hours: those on the half hour have a partner with equal value and quality; 48 have none
    assert (row["intervals_differing"], row["quality_differing"]) == (48, 48)
    assert summary(empty).conflict_intervals == 48


def test_key_unique_fails_for_a_repeated_interval(zone: RawZone) -> None:
    target = zone.interval[0]
    rewrite(staged_path(zone.root, target), lambda table: pa.concat_tables([table, table.slice(5, 1)]))
    failed = failing(zone.root)
    assert failed == {"key_unique": {"duplicates": 1}}


# --- the input ---------------------------------------------------------------------------------------------------


def test_an_empty_catalog_gives_empty_tables_and_every_check_passes(empty: Path) -> None:
    parse(empty)
    result = merge(empty)
    assert intervals(empty).num_rows == 0
    assert intervals(empty).schema.remove_metadata().equals(INTERVALS_SCHEMA)
    assert conflicts(empty).num_rows == 0
    assert conflicts(empty).schema.remove_metadata().equals(CONFLICTS_SCHEMA)
    found = summary(empty)
    assert found.datasets_used == []
    assert found.days == {}
    assert (found.overlap_days, found.conflict_days, found.conflict_intervals, found.same_update_conflicts) == (0,) * 4
    assert {status for status, _ in checks(empty).values()} == {"pass"}
    assert result.warnings == []


def test_a_missing_staged_table_is_invalid_input(zone: RawZone) -> None:
    staged_path(zone.root, zone.interval[0]).unlink()
    with pytest.raises(InvalidInputError):
        merge(zone.root)


def test_a_missing_index_is_invalid_input(zone: RawZone) -> None:
    (staged_dir(zone.root) / "index.parquet").unlink()
    with pytest.raises(InvalidInputError):
        merge(zone.root)


@pytest.mark.parametrize("name", ["index.parquet", "a-staged-table"])
def test_a_table_of_another_kind_is_invalid_input(zone: RawZone, name: str) -> None:
    path = staged_dir(zone.root) / (name if name == "index.parquet" else f"{zone.interval[1].dataset_id}.parquet")
    table = pq.read_table(path)
    pq.write_table(table.replace_schema_metadata({"energatron.kind": "other", "energatron.schema_version": "1"}), path)
    with pytest.raises(InvalidInputError):
        merge(zone.root)


def test_a_staged_table_holding_another_datasets_rows_is_invalid_input(zone: RawZone) -> None:
    a, b = zone.interval
    rewrite(
        staged_path(zone.root, a),
        lambda table: table.set_column(
            table.schema.get_field_index("dataset_id"), "dataset_id", pa.array([b.dataset_id] * table.num_rows)
        ),
    )
    with pytest.raises(InvalidInputError) as error:
        merge(zone.root)
    assert error.value.details["file"] == f"{a.dataset_id}.parquet"


def test_a_staged_table_with_an_id_the_index_doesnt_know_is_invalid_input(zone: RawZone) -> None:
    a = zone.interval[0]
    foreign = "sapn_interval-20250101T000000-ffffffff"
    rewrite(
        staged_path(zone.root, a),
        lambda table: table.set_column(
            table.schema.get_field_index("dataset_id"), "dataset_id", pa.array([foreign] * table.num_rows)
        ),
    )
    with pytest.raises(InvalidInputError):
        merge(zone.root)


def test_a_table_of_an_unknown_version_is_invalid_input(zone: RawZone) -> None:
    path = staged_dir(zone.root) / "index.parquet"
    table = pq.read_table(path)
    pq.write_table(
        table.replace_schema_metadata({"energatron.kind": "elec_interval_index", "energatron.schema_version": "2"}),
        path,
    )
    with pytest.raises(InvalidInputError):
        merge(zone.root)
