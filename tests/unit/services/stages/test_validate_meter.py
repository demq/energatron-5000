"""The `validate_meter` stage (P2 spec §8, §13.3): the merged meter intervals against the SAPN SUMMARY totals, the gap
list and the quality shares.

Every value is synthetic. The expected numbers come from the generator specs and the independent oracle, never from
the stage's own reader. The stage's inputs are the real `parse_nem12` and `merge_interval` outputs for the fixture raw
zone, or for small zones the tests build.
"""

import contextlib
import json
import math
import shutil
from collections import Counter
from collections.abc import Callable
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from energatron.core.canonical import JSONValue, canonical_json, content_hash, sha256_hex
from energatron.core.errors import CheckFailedError, InvalidInputError, WarningCode
from energatron.core.models.catalog import SourceType
from energatron.core.models.meter import MeterReport
from energatron.core.models.provenance import StageCode, StageProvenance
from energatron.services.stages import validate_meter
from energatron.services.stages.registry import STAGES, get_stage
from energatron.services.stages.runner import StageResult, prepare_stage, run_stage
from tests.fixtures import oracle
from tests.fixtures.make_nem12 import Nem12Spec, make_nem12, sapn_filename
from tests.fixtures.make_sources import SummaryRow, make_pdf, make_sapn_summary_csv
from tests.fixtures.params_text import params_v2_text
from tests.fixtures.pipeline_workspace import (
    INTERVAL_MINUTES,
    Dataset,
    RawZone,
    build_raw_zone,
    empty_raw_zone,
    land_blob,
)
from tests.fixtures.synthetic import SYNTHETIC_ACCOUNT, SYNTHETIC_NMI, SYNTHETIC_NMI11, SYNTHETIC_SERIAL

CHECKS = ["blobs_match_catalog", "detector_known", "monthly_totals_match", "summary_rows_unique"]
PER_DAY = 1440 // INTERVAL_MINUTES
PARAMS = "kind: params\nschema_version: 1\n"
COMPARE_SCHEMA = pa.schema(
    [
        ("dataset_id", pa.string()),
        ("period_start", pa.date32()),
        ("period_end", pa.date32()),
        ("summary_quality", pa.string()),
        ("summary_kwh", pa.float64()),
        ("meter_kwh", pa.float64()),
        ("diff_kwh", pa.float64()),
        ("status", pa.string()),
        ("summary_disagreement", pa.bool_()),
    ]
)
GAPS_SCHEMA = pa.schema(
    [("suffix", pa.string()), ("gap_start", pa.date32()), ("gap_end", pa.date32()), ("days", pa.int32())]
)
QUALITY_SCHEMA = pa.schema(
    [
        ("suffix", pa.string()),
        ("month", pa.string()),
        ("quality_flag", pa.string()),
        ("intervals", pa.int32()),
        ("kwh", pa.float64()),
        ("share", pa.float64()),
    ]
)
DAY1 = date(2025, 6, 1)


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


def prepare(root: Path) -> None:
    """Run `parse_nem12` and `merge_interval`: the stage under test reads the merged table."""
    with contextlib.suppress(CheckFailedError):
        run_named("parse_nem12", root)
    run_named("merge_interval", root)


def validate(root: Path, **overrides: Path) -> StageResult:
    return run_named("validate_meter", root, **overrides)


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> RawZone:
    """The fixture raw zone with the stages before `validate_meter` already run."""
    root = tmp_path_factory.mktemp("template")
    zone = build_raw_zone(root)
    (root / "params.yaml").write_text(PARAMS)
    prepare(root)
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


def interval_dir(root: Path) -> Path:
    return root / "data" / "interim" / "elec_interval_nem"


def report_dir(root: Path) -> Path:
    return root / "data" / "reports" / "validation" / "meter"


def compare(root: Path) -> pa.Table:
    return pq.read_table(report_dir(root) / "summary_compare.parquet")


def gaps(root: Path) -> pa.Table:
    return pq.read_table(report_dir(root) / "gaps.parquet")


def quality(root: Path) -> pa.Table:
    return pq.read_table(report_dir(root) / "quality_monthly.parquet")


def report(root: Path) -> MeterReport:
    return MeterReport.from_document(json.loads((report_dir(root) / "report.json").read_text()))


def checks(root: Path) -> dict[str, tuple[str, dict[str, JSONValue]]]:
    provenance = StageProvenance.from_document(json.loads((report_dir(root) / "_provenance.json").read_text()))
    return {check.name: (check.status, dict(check.details)) for check in provenance.checks}


def failing(root: Path) -> dict[str, dict[str, JSONValue]]:
    with pytest.raises(CheckFailedError):
        validate(root)
    return {name: details for name, (status, details) in checks(root).items() if status == "fail"}


def values(table: pa.Table, name: str) -> list[object]:
    return table.column(name).to_pylist()


def by_period(table: pa.Table) -> dict[tuple[date, date], dict[str, object]]:
    rows = zip(*(values(table, name) for name in table.column_names), strict=True)
    return {(row[1], row[2]): dict(zip(table.column_names, row, strict=True)) for row in rows}  # type: ignore[misc]


def land_spec(root: Path, spec: Nem12Spec, generated: datetime, **kw: object) -> Dataset:
    window = (spec.start, spec.start + timedelta(days=spec.days - 1))
    name = sapn_filename("DETAILED", SYNTHETIC_NMI, window, generated)
    return land_blob(root, SourceType.SAPN_INTERVAL, name, make_nem12(spec), **kw)  # type: ignore[arg-type]


def land_summary(
    root: Path, rows: list[SummaryRow], generated: datetime = datetime(2025, 7, 9, 9, 0, 0), **kw: object
) -> Dataset:
    window = (min(row.start for row in rows), max(row.end for row in rows))
    name = sapn_filename("SUMMARY", SYNTHETIC_NMI, window, generated)
    return land_blob(root, SourceType.SAPN_SUMMARY, name, make_sapn_summary_csv(rows), **kw)  # type: ignore[arg-type]


def small_spec(**changes: object) -> Nem12Spec:
    base = Nem12Spec(start=DAY1, days=10, interval_minutes=INTERVAL_MINUTES, seed=3)
    return replace(base, **changes)  # type: ignore[arg-type]


def summary_row(spec: Nem12Spec, first: date, last: date, delta: float = 0.0, *, quality_code: str = "A") -> SummaryRow:
    """A SUMMARY row for the NEM12 `spec`'s consumption registers, its total off by `delta`."""
    total = oracle.period_sum(make_nem12(spec), first, last, suffixes=["E1"]) + delta
    return SummaryRow(first, last, quality_code, (f"{total:.3f}", "2.000", "3.000", "0.500"))


def edit_record(root: Path, dataset: Dataset, change: Callable[[dict[str, JSONValue]], None]) -> None:
    catalog = root / "data" / "raw" / dataset.source.value / "_catalog.jsonl"
    out: list[bytes] = []
    for line in catalog.read_bytes().splitlines():
        doc = json.loads(line)
        if doc["dataset_id"] == dataset.dataset_id:
            change(doc)
        out.append(canonical_json(doc) + b"\n")
    catalog.write_bytes(b"".join(out))


def small_zone(root: Path, rows: list[SummaryRow] | None = None, **spec_changes: object) -> Nem12Spec:
    """One 10-day interval dataset (one register) and, if `rows` are given, one SUMMARY CSV; stages run up to merge."""
    spec = small_spec(**spec_changes)
    land_spec(root, spec, datetime(2025, 7, 1, 9, 0, 0))
    if rows is not None:
        land_summary(root, rows)
    prepare(root)
    return spec


# --- the spec ----------------------------------------------------------------------------------------------------


def test_spec_reads_the_merged_intervals_and_the_summary_source() -> None:
    spec = validate_meter.SPEC
    assert spec.name == "validate_meter"
    assert dict(spec.inputs) == {"interval": "data/interim/elec_interval_nem", "summary": "data/raw/sapn_summary"}
    assert spec.params == ("validation.meter",)
    assert dict(spec.outputs) == {"report": "data/reports/validation/meter"}


def test_it_is_registered_after_merge_interval() -> None:
    assert get_stage("validate_meter") is validate_meter.SPEC
    assert list(STAGES)[:4] == ["catalog_index", "parse_nem12", "merge_interval", "validate_meter"]


# --- the fixture zone --------------------------------------------------------------------------------------------


def test_it_writes_four_reports_and_provenance(zone: RawZone) -> None:
    validate(zone.root)
    assert sorted(path.name for path in report_dir(zone.root).iterdir()) == [
        "_provenance.json",
        "gaps.parquet",
        "quality_monthly.parquet",
        "report.json",
        "summary_compare.parquet",
    ]


def test_every_check_passes_and_nothing_warns_on_the_fixture_zone(zone: RawZone) -> None:
    result = validate(zone.root)
    results = checks(zone.root)
    assert list(results) == CHECKS
    assert {status for status, _ in results.values()} == {"pass"}
    assert [check.name for check in result.checks] == CHECKS
    assert result.warnings == []


def test_the_tables_have_the_columns_types_and_metadata(zone: RawZone) -> None:
    validate(zone.root)
    for table, schema, kind in (
        (compare(zone.root), COMPARE_SCHEMA, b"meter_summary_compare"),
        (gaps(zone.root), GAPS_SCHEMA, b"meter_gaps"),
        (quality(zone.root), QUALITY_SCHEMA, b"meter_quality_monthly"),
    ):
        assert table.schema.remove_metadata().equals(schema), kind
        assert table.schema.metadata == {b"energatron.kind": kind, b"energatron.schema_version": b"1"}


def test_every_summary_period_matches_the_meter_total_on_the_fixture_zone(zone: RawZone) -> None:
    validate(zone.root)
    table = compare(zone.root)
    assert table.num_rows == len(zone.periods) > 1
    rows = by_period(table)
    assert sorted(rows) == sorted(zone.periods)
    starts: list[date] = table.column("period_start").to_pylist()
    assert starts == sorted(starts)
    for (first, last), row in rows.items():
        expected = oracle.period_sum(zone.truth, first, last, suffixes=["E1"])
        assert row["status"] == "match"
        assert row["meter_kwh"] == expected
        assert row["summary_kwh"] == float(f"{expected:.3f}")
        assert row["diff_kwh"] == pytest.approx(row["meter_kwh"] - row["summary_kwh"], abs=1e-9)  # type: ignore[operator]
        assert abs(row["diff_kwh"]) <= 0.01  # type: ignore[arg-type]
        assert row["dataset_id"] == zone.summary.dataset_id
        assert row["summary_quality"] == "A"
        assert row["summary_disagreement"] is False


def test_the_fixture_zone_has_no_gaps_and_the_report_counts_it(zone: RawZone) -> None:
    validate(zone.root)
    assert gaps(zone.root).num_rows == 0
    found = report(zone.root)
    truth = zone.specs.truth
    assert found.summary_rows.model_dump() == {"match": len(zone.periods), "mismatch": 0, "not_covered": 0}
    assert found.gap_days == 0
    assert found.intervals == {"E1": truth.days * PER_DAY}
    assert found.first_date == {"E1": truth.start}
    assert found.last_date == {"E1": truth.start + timedelta(days=truth.days - 1)}
    a_days = truth.days - 2  # the V day and the E52 day aren't flag A
    assert found.a_share == a_days * PER_DAY / (truth.days * PER_DAY)
    document = json.loads((report_dir(zone.root) / "report.json").read_text())
    assert (document["kind"], document["schema_version"]) == ("meter_report", 1)


def test_the_quality_shares_follow_the_flags_of_each_day(zone: RawZone) -> None:
    validate(zone.root)
    specs = zone.specs
    truth = specs.truth
    flag_of = {specs.v_day: "S", specs.e_day: "E"}  # the V day's 400 range is S14; the other day is E52
    days = [truth.start + timedelta(days=n) for n in range(truth.days)]
    counts: Counter[tuple[str, str]] = Counter()
    kwh: dict[tuple[str, str], list[float]] = {}
    sums = oracle.day_sums(zone.truth)
    for day in days:
        key = (f"{day:%Y-%m}", flag_of.get(day, "A"))
        counts[key] += PER_DAY
        kwh.setdefault(key, []).append(sums["E1", day])
    totals: Counter[str] = Counter()
    for (month, _), count in counts.items():
        totals[month] += count
    table = quality(zone.root)
    rows = list(zip(*(values(table, name) for name in table.column_names), strict=True))
    assert [row[:3] for row in rows] == sorted(("E1", month, flag) for month, flag in counts)
    assert len(rows) == len(counts) > 3
    for suffix, month, flag, intervals, total, share in rows:
        assert suffix == "E1"
        assert intervals == counts[month, flag]
        assert total == pytest.approx(math.fsum(kwh[month, flag]), rel=1e-9)
        assert share == intervals / totals[month]
    for month in totals:
        assert math.fsum(row[5] for row in rows if row[1] == month) == pytest.approx(1.0)  # type: ignore[misc]


def test_running_twice_writes_the_same_bytes(zone: RawZone) -> None:
    def snapshot() -> dict[str, bytes]:
        return {path.name: path.read_bytes() for path in report_dir(zone.root).iterdir()}

    validate(zone.root)
    first = snapshot()
    validate(zone.root)
    assert snapshot() == first


def test_provenance_names_the_interval_directory_and_the_summary_datasets(zone: RawZone) -> None:
    validate(zone.root)
    provenance = StageProvenance.from_document(json.loads((report_dir(zone.root) / "_provenance.json").read_text()))
    by_name = {record.name: record for record in provenance.inputs}
    assert set(by_name) == {"interval", "summary"}
    assert by_name["interval"].path == "data/interim/elec_interval_nem"
    assert by_name["interval"].dataset_ids == sorted(dataset.dataset_id for dataset in zone.interval)
    assert (by_name["summary"].type, by_name["summary"].path) == ("dir", "data/raw/sapn_summary")
    assert by_name["summary"].dataset_ids == [zone.summary.dataset_id]
    assert provenance.params.values == {"validation.meter": {"total_tolerance_kwh": 0.01}}


def test_the_inputs_can_be_overridden(zone: RawZone, tmp_path: Path) -> None:
    moved_intervals, moved_summary = tmp_path / "intervals", tmp_path / "summary"
    shutil.copytree(interval_dir(zone.root), moved_intervals)
    shutil.copytree(zone.root / "data" / "raw" / "sapn_summary", moved_summary)
    shutil.rmtree(interval_dir(zone.root))
    shutil.rmtree(zone.root / "data" / "raw" / "sapn_summary")
    validate(zone.root, interval=moved_intervals, summary=moved_summary)
    assert {row["status"] for row in by_period(compare(zone.root)).values()} == {"match"}


def test_no_nmi_serial_or_account_reaches_any_output(zone: RawZone) -> None:
    validate(zone.root)
    secrets = [SYNTHETIC_NMI, SYNTHETIC_NMI11, SYNTHETIC_SERIAL, SYNTHETIC_ACCOUNT]
    for path in report_dir(zone.root).iterdir():
        assert not any(secret.encode() in path.read_bytes() for secret in secrets), path.name
        if path.suffix == ".parquet":
            names = pq.read_table(path).column_names
            assert not any(name in names for name in ("nmi", "serial", "meter_serial", "account"))


# --- the comparison ----------------------------------------------------------------------------------------------


def test_match_within_the_tolerance_and_mismatch_just_outside_it(empty: Path) -> None:
    spec = small_spec()
    rows = [
        summary_row(spec, DAY1, DAY1 + timedelta(days=1), 0.009),
        summary_row(spec, DAY1 + timedelta(days=2), DAY1 + timedelta(days=3), -0.009),
        summary_row(spec, DAY1 + timedelta(days=4), DAY1 + timedelta(days=5), 0.011),
        summary_row(spec, DAY1 + timedelta(days=6), DAY1 + timedelta(days=7), -0.011),
        summary_row(spec, DAY1 + timedelta(days=8), DAY1 + timedelta(days=9), 0.0),
    ]
    small_zone(empty, rows)
    failed = failing(empty)
    assert failed == {"monthly_totals_match": {"mismatched": 2}}
    statuses = [row["status"] for _, row in sorted(by_period(compare(empty)).items())]
    assert statuses == ["match", "match", "mismatch", "mismatch", "match"]
    table = by_period(compare(empty))
    assert table[DAY1 + timedelta(days=4), DAY1 + timedelta(days=5)]["diff_kwh"] == pytest.approx(-0.011, abs=1e-9)
    assert table[DAY1 + timedelta(days=6), DAY1 + timedelta(days=7)]["diff_kwh"] == pytest.approx(0.011, abs=1e-9)
    assert report(empty).summary_rows.model_dump() == {"match": 3, "mismatch": 2, "not_covered": 0}


@pytest.mark.parametrize("seed", range(1, 7))
def test_a_difference_of_exactly_the_tolerance_is_a_match_on_both_sides(empty: Path, seed: int) -> None:
    spec = small_spec(seed=seed)
    days = [DAY1 + timedelta(days=n) for n in range(10)]
    plus = [summary_row(spec, day, day, 0.01) for day in days[:5]]  # the summary 0.010 below the meter total
    minus = [summary_row(spec, day, day, -0.01) for day in days[5:]]
    small_zone(empty, [*plus, *minus], seed=seed)
    validate(empty)  # no failed check: every period is a match
    found = by_period(compare(empty))
    assert {row["status"] for row in found.values()} == {"match"}
    assert sorted(float(row["diff_kwh"]) for row in found.values()) == [-0.01] * 5 + [0.01] * 5  # type: ignore[arg-type]


def test_a_difference_just_over_the_tolerance_is_a_mismatch_on_both_sides(empty: Path) -> None:
    spec = small_spec()
    days = [DAY1 + timedelta(days=n) for n in range(4)]
    rows = [summary_row(spec, day, day, delta) for day, delta in zip(days, (0.011, -0.011, 0.012, -0.012), strict=True)]
    small_zone(empty, rows)
    assert failing(empty) == {"monthly_totals_match": {"mismatched": 4}}


def test_the_tolerance_is_the_declared_param(empty: Path) -> None:
    spec = small_spec()
    rows = [
        summary_row(spec, DAY1, DAY1 + timedelta(days=4), 0.4),
        summary_row(spec, DAY1 + timedelta(days=5), DAY1 + timedelta(days=9), 0.6),
    ]
    small_zone(empty, rows)
    (empty / "params.yaml").write_text(params_v2_text(0.5))
    with pytest.raises(CheckFailedError):
        validate(empty)
    statuses = [row["status"] for _, row in sorted(by_period(compare(empty)).items())]
    assert statuses == ["match", "mismatch"]
    assert checks(empty)["monthly_totals_match"] == ("fail", {"mismatched": 1})


def test_partial_first_and_last_periods_are_not_covered(empty: Path) -> None:
    spec = small_spec()
    rows = [
        summary_row(spec, DAY1 - timedelta(days=7), DAY1 + timedelta(days=2)),  # starts before the data
        summary_row(spec, DAY1 + timedelta(days=3), DAY1 + timedelta(days=9)),  # covered
        summary_row(spec, DAY1 + timedelta(days=7), DAY1 + timedelta(days=19)),  # ends after the data
    ]
    small_zone(empty, rows)
    result = validate(empty)
    found = by_period(compare(empty))
    first, middle, last = (found[key] for key in sorted(found))
    assert [row["status"] for row in (first, middle, last)] == ["not_covered", "match", "not_covered"]
    for row in (first, last):
        assert row["meter_kwh"] is None and row["diff_kwh"] is None
        assert row["summary_kwh"] is not None
    assert middle["meter_kwh"] is not None
    assert {status for status, _ in checks(empty).values()} == {"pass"}  # not covered is no failure
    assert result.warnings == []  # one period is covered, so the data is validated
    assert report(empty).summary_rows.model_dump() == {"match": 1, "mismatch": 0, "not_covered": 2}


def test_a_period_over_a_gap_is_not_covered(empty: Path) -> None:
    first = small_spec(days=3)
    second = small_spec(start=DAY1 + timedelta(days=5), days=3, seed=4)
    land_spec(empty, first, datetime(2025, 7, 1, 9, 0, 0))
    land_spec(empty, second, datetime(2025, 7, 2, 9, 0, 0))
    land_summary(
        empty, [SummaryRow(DAY1, DAY1 + timedelta(days=7)), summary_row(first, DAY1, DAY1 + timedelta(days=2))]
    )
    prepare(empty)
    validate(empty)
    statuses = [row["status"] for _, row in sorted(by_period(compare(empty)).items())]
    assert statuses == ["match", "not_covered"]  # (1st-3rd) matches; (1st-8th) has two missing dates


def test_only_consumption_registers_are_compared_and_each_must_cover_the_period(empty: Path) -> None:
    spec = small_spec(suffixes=("E1", "B1"))
    land_spec(empty, spec, datetime(2025, 7, 1, 9, 0, 0))
    both = summary_row(spec, DAY1, DAY1 + timedelta(days=4))  # E1's total only: B1 is a different register
    land_summary(empty, [both])
    prepare(empty)
    validate(empty)
    (row,) = by_period(compare(empty)).values()
    assert row["status"] == "match"
    assert row["meter_kwh"] == oracle.period_sum(make_nem12(spec), DAY1, DAY1 + timedelta(days=4), suffixes=["E1"])


def test_a_second_consumption_register_missing_a_date_leaves_the_period_not_covered(empty: Path) -> None:
    both = small_spec(days=4, suffixes=("E1", "E2"))
    only_e1 = small_spec(start=DAY1 + timedelta(days=4), days=2, seed=5)
    land_spec(empty, both, datetime(2025, 7, 1, 9, 0, 0))
    land_spec(empty, only_e1, datetime(2025, 7, 2, 9, 0, 0))
    total = oracle.period_sum(make_nem12(both), DAY1, DAY1 + timedelta(days=3), suffixes=["E1", "E2"])
    land_summary(
        empty,
        [
            SummaryRow(DAY1, DAY1 + timedelta(days=3), "A", (f"{total:.3f}", "2.000", "3.000", "0.500")),
            SummaryRow(DAY1, DAY1 + timedelta(days=5)),
        ],
    )
    prepare(empty)
    validate(empty)
    found = by_period(compare(empty))
    assert found[DAY1, DAY1 + timedelta(days=3)]["status"] == "match"  # E1 and E2 are both present, and both count
    assert found[DAY1, DAY1 + timedelta(days=5)]["status"] == "not_covered"  # E2 stops on the 4th


# --- the summary datasets ----------------------------------------------------------------------------------------


def test_the_newest_dataset_wins_and_a_disagreement_is_flagged(empty: Path) -> None:
    spec = small_spec()
    land_spec(empty, spec, datetime(2025, 7, 1, 9, 0, 0))
    periods = [(DAY1, DAY1 + timedelta(days=4)), (DAY1 + timedelta(days=5), DAY1 + timedelta(days=9))]
    older = land_summary(empty, [summary_row(spec, *period) for period in periods], datetime(2025, 7, 5, 9, 0, 0))
    newer = land_summary(
        empty,
        [summary_row(spec, periods[0][0], periods[0][1]), summary_row(spec, *periods[1], 0.5)],
        datetime(2025, 7, 9, 9, 0, 0),
    )
    prepare(empty)
    with pytest.raises(CheckFailedError):
        validate(empty)
    found = by_period(compare(empty))
    assert [found[period]["dataset_id"] for period in periods] == [newer.dataset_id] * 2
    assert [found[period]["summary_disagreement"] for period in periods] == [False, True]
    assert [found[period]["status"] for period in periods] == ["match", "mismatch"]  # the newer dataset's value counts
    assert older.dataset_id not in json.dumps(checks(empty))


def test_a_period_only_an_older_dataset_holds_still_counts(empty: Path) -> None:
    spec = small_spec()
    land_spec(empty, spec, datetime(2025, 7, 1, 9, 0, 0))
    older = land_summary(empty, [summary_row(spec, DAY1, DAY1 + timedelta(days=4))], datetime(2025, 7, 5, 9, 0, 0))
    newer = land_summary(
        empty, [summary_row(spec, DAY1 + timedelta(days=5), DAY1 + timedelta(days=9))], datetime(2025, 7, 9, 9, 0, 0)
    )
    prepare(empty)
    validate(empty)
    found = by_period(compare(empty))
    assert {row["dataset_id"] for row in found.values()} == {older.dataset_id, newer.dataset_id}
    assert {row["summary_disagreement"] for row in found.values()} == {False}


def test_a_repeated_period_in_one_file_fails_summary_rows_unique(empty: Path) -> None:
    spec = small_spec()
    land_spec(empty, spec, datetime(2025, 7, 1, 9, 0, 0))
    row = summary_row(spec, DAY1, DAY1 + timedelta(days=4))
    target = land_summary(empty, [row, summary_row(spec, DAY1 + timedelta(days=5), DAY1 + timedelta(days=9)), row])
    prepare(empty)
    failed = failing(empty)
    assert failed == {"summary_rows_unique": {"repeated": {target.dataset_id: 1}}}
    assert compare(empty).num_rows == 2  # the first of the two is used


def test_the_same_period_in_two_files_is_not_a_repeat(empty: Path) -> None:
    spec = small_spec()
    land_spec(empty, spec, datetime(2025, 7, 1, 9, 0, 0))
    row = summary_row(spec, DAY1, DAY1 + timedelta(days=4))
    land_summary(empty, [row], datetime(2025, 7, 5, 9, 0, 0))
    land_summary(empty, [row], datetime(2025, 7, 9, 9, 0, 0))
    prepare(empty)
    validate(empty)
    assert checks(empty)["summary_rows_unique"][0] == "pass"
    assert compare(empty).num_rows == 1


def test_the_pdf_dataset_is_ignored(empty: Path) -> None:
    spec = small_spec()
    land_spec(empty, spec, datetime(2025, 7, 1, 9, 0, 0))
    csv = land_summary(empty, [summary_row(spec, DAY1, DAY1 + timedelta(days=4))], datetime(2025, 7, 5, 9, 0, 0))
    name = sapn_filename(
        "SUMMARY", SYNTHETIC_NMI, (DAY1, DAY1 + timedelta(days=9)), datetime(2025, 7, 9, 9, 0, 0), "pdf"
    )
    pdf = land_blob(empty, SourceType.SAPN_SUMMARY, name, make_pdf(creation_date="D:20250709090000+09'30'"))
    prepare(empty)
    validate(empty)
    assert {row["dataset_id"] for row in by_period(compare(empty)).values()} == {csv.dataset_id}
    assert pdf.dataset_id not in json.dumps(checks(empty))


def test_a_superseded_summary_dataset_is_ignored(empty: Path) -> None:
    spec = small_spec()
    land_spec(empty, spec, datetime(2025, 7, 1, 9, 0, 0))
    old = land_summary(empty, [summary_row(spec, DAY1, DAY1 + timedelta(days=4))], datetime(2025, 7, 5, 9, 0, 0))
    new = land_summary(
        empty,
        [summary_row(spec, DAY1 + timedelta(days=5), DAY1 + timedelta(days=9))],
        datetime(2025, 7, 9, 9, 0, 0),
        supersedes=old.dataset_id,
    )
    prepare(empty)
    validate(empty)
    found = by_period(compare(empty))
    assert list(found) == [(DAY1 + timedelta(days=5), DAY1 + timedelta(days=9))]
    assert {row["dataset_id"] for row in found.values()} == {new.dataset_id}


def test_blobs_match_catalog_fails_for_a_tampered_summary_blob(zone: RawZone) -> None:
    target = zone.summary
    blob = zone.root / target.stored_path
    blob.write_bytes(blob.read_bytes() + b"\r\n")
    failed = failing(zone.root)
    assert failed["blobs_match_catalog"] == {"mismatched": [target.dataset_id]}
    assert compare(zone.root).num_rows == 0  # a dataset that doesn't match its record is not read


def test_blobs_match_catalog_fails_for_a_missing_summary_blob(zone: RawZone) -> None:
    (zone.root / zone.summary.stored_path).unlink()
    assert failing(zone.root)["blobs_match_catalog"] == {"mismatched": [zone.summary.dataset_id]}


def test_detector_known_fails_for_a_detector_version_the_stage_doesnt_know(zone: RawZone) -> None:
    edit_record(zone.root, zone.summary, lambda doc: doc["detector"].update({"version": 2}))  # type: ignore[union-attr]
    failed = failing(zone.root)
    assert failed["detector_known"] == {"unknown": [zone.summary.dataset_id]}
    assert compare(zone.root).num_rows == 0


def test_blobs_match_catalog_checks_the_pdf_and_superseded_datasets_too(empty: Path) -> None:
    spec = small_spec()
    land_spec(empty, spec, datetime(2025, 7, 1, 9, 0, 0))
    old = land_summary(empty, [summary_row(spec, DAY1, DAY1 + timedelta(days=4))], datetime(2025, 7, 5, 9, 0, 0))
    land_summary(
        empty,
        [summary_row(spec, DAY1 + timedelta(days=5), DAY1 + timedelta(days=9))],
        datetime(2025, 7, 9, 9, 0, 0),
        supersedes=old.dataset_id,
    )
    name = sapn_filename(
        "SUMMARY", SYNTHETIC_NMI, (DAY1, DAY1 + timedelta(days=9)), datetime(2025, 7, 10, 9, 0, 0), "pdf"
    )
    pdf = land_blob(empty, SourceType.SAPN_SUMMARY, name, make_pdf(creation_date="D:20250710090000+09'30'"))
    prepare(empty)
    validate(empty)  # intact blobs: every check passes
    assert checks(empty)["blobs_match_catalog"] == ("pass", {"mismatched": []})
    (empty / pdf.stored_path).write_bytes(b"tampered")
    (empty / old.stored_path).write_bytes(b"tampered")
    failed = failing(empty)
    assert sorted(failed["blobs_match_catalog"]["mismatched"]) == sorted([old.dataset_id, pdf.dataset_id])  # type: ignore[arg-type]
    assert [row["dataset_id"] == old.dataset_id for row in by_period(compare(empty)).values()] == [False]


def test_detector_known_checks_the_pdf_and_superseded_datasets_too(empty: Path) -> None:
    spec = small_spec()
    land_spec(empty, spec, datetime(2025, 7, 1, 9, 0, 0))
    old = land_summary(empty, [summary_row(spec, DAY1, DAY1 + timedelta(days=4))], datetime(2025, 7, 5, 9, 0, 0))
    land_summary(
        empty,
        [summary_row(spec, DAY1 + timedelta(days=5), DAY1 + timedelta(days=9))],
        datetime(2025, 7, 9, 9, 0, 0),
        supersedes=old.dataset_id,
    )
    name = sapn_filename(
        "SUMMARY", SYNTHETIC_NMI, (DAY1, DAY1 + timedelta(days=9)), datetime(2025, 7, 10, 9, 0, 0), "pdf"
    )
    pdf = land_blob(empty, SourceType.SAPN_SUMMARY, name, make_pdf(creation_date="D:20250710090000+09'30'"))
    for target in (old, pdf):
        edit_record(empty, target, lambda doc: doc["detector"].update({"version": 2}))  # type: ignore[union-attr]
    prepare(empty)
    failed = failing(empty)
    assert sorted(failed["detector_known"]["unknown"]) == sorted([old.dataset_id, pdf.dataset_id])  # type: ignore[arg-type]


# --- no summary --------------------------------------------------------------------------------------------------


def test_no_summary_warns_unvalidated_and_passes(empty: Path) -> None:
    small_zone(empty)
    result = validate(empty)
    assert [warning.code for warning in result.warnings] == [WarningCode.W_METER_UNVALIDATED]
    assert {status for status, _ in checks(empty).values()} == {"pass"}
    assert compare(empty).num_rows == 0
    assert compare(empty).schema.remove_metadata().equals(COMPARE_SCHEMA)
    assert report(empty).summary_rows.model_dump() == {"match": 0, "mismatch": 0, "not_covered": 0}


def test_a_summary_that_overlaps_nothing_warns_unvalidated(empty: Path) -> None:
    small_zone(empty, [SummaryRow(date(2024, 1, 1), date(2024, 1, 31))])
    result = validate(empty)
    assert [warning.code for warning in result.warnings] == [WarningCode.W_METER_UNVALIDATED]
    assert [row["status"] for row in by_period(compare(empty)).values()] == ["not_covered"]


def test_an_empty_zone_gives_empty_tables_and_only_the_unvalidated_warning(empty: Path) -> None:
    prepare(empty)
    result = validate(empty)
    assert [warning.code for warning in result.warnings] == [WarningCode.W_METER_UNVALIDATED]
    assert {status for status, _ in checks(empty).values()} == {"pass"}
    assert gaps(empty).num_rows == 0 and gaps(empty).schema.remove_metadata().equals(GAPS_SCHEMA)
    assert quality(empty).num_rows == 0 and quality(empty).schema.remove_metadata().equals(QUALITY_SCHEMA)
    found = report(empty)
    assert (found.intervals, found.first_date, found.last_date, found.gap_days) == ({}, {}, {}, 0)
    assert found.a_share is None


# --- the gap list ------------------------------------------------------------------------------------------------


def test_gaps_are_runs_of_missing_dates_per_register_and_warn(empty: Path) -> None:
    both = ("E1", "B1")
    land_spec(empty, small_spec(days=3, suffixes=both), datetime(2025, 7, 1, 9, 0, 0))  # 1st-3rd
    land_spec(empty, small_spec(start=date(2025, 6, 6), days=2, suffixes=both, seed=4), datetime(2025, 7, 2, 9, 0, 0))
    land_spec(empty, small_spec(start=date(2025, 6, 10), days=1, seed=5), datetime(2025, 7, 3, 9, 0, 0))  # E1 only
    prepare(empty)
    result = validate(empty)
    table = gaps(empty)
    rows = list(zip(*(values(table, name) for name in table.column_names), strict=True))
    assert rows == [
        ("B1", date(2025, 6, 4), date(2025, 6, 5), 2),
        ("E1", date(2025, 6, 4), date(2025, 6, 5), 2),
        ("E1", date(2025, 6, 8), date(2025, 6, 9), 2),
    ]
    gap_warning, _ = result.warnings  # the second is the unvalidated warning: no SUMMARY landed
    assert gap_warning.code == WarningCode.W_METER_GAPS
    assert gap_warning.details == {"gap_days": 6}
    assert {status for status, _ in checks(empty).values()} == {"pass"}  # a gap is a fact, not a failure
    found = report(empty)
    assert found.gap_days == 6
    assert found.first_date == {"B1": date(2025, 6, 1), "E1": date(2025, 6, 1)}
    assert found.last_date == {"B1": date(2025, 6, 7), "E1": date(2025, 6, 10)}  # no gap before a register's first date
    assert found.intervals == {"B1": 5 * PER_DAY, "E1": 6 * PER_DAY}


def test_a_gap_warns_alone_when_a_summary_period_is_covered(empty: Path) -> None:
    first = small_spec(days=2)
    land_spec(empty, first, datetime(2025, 7, 1, 9, 0, 0))
    land_spec(empty, small_spec(start=date(2025, 6, 5), days=2, seed=4), datetime(2025, 7, 2, 9, 0, 0))
    land_summary(empty, [summary_row(first, DAY1, DAY1 + timedelta(days=1))])
    prepare(empty)
    result = validate(empty)
    assert [warning.code for warning in result.warnings] == [WarningCode.W_METER_GAPS]


# --- the input ---------------------------------------------------------------------------------------------------


def test_a_missing_interval_table_is_invalid_input(zone: RawZone) -> None:
    (interval_dir(zone.root) / "intervals.parquet").unlink()
    with pytest.raises(InvalidInputError):
        validate(zone.root)


def test_a_table_of_another_kind_is_invalid_input(zone: RawZone) -> None:
    path = interval_dir(zone.root) / "intervals.parquet"
    table = pq.read_table(path)
    pq.write_table(table.replace_schema_metadata({"energatron.kind": "other", "energatron.schema_version": "1"}), path)
    with pytest.raises(InvalidInputError):
        validate(zone.root)


def test_a_table_of_an_unknown_version_is_invalid_input(zone: RawZone) -> None:
    path = interval_dir(zone.root) / "intervals.parquet"
    table = pq.read_table(path)
    pq.write_table(
        table.replace_schema_metadata({"energatron.kind": "elec_interval_nem", "energatron.schema_version": "2"}), path
    )
    with pytest.raises(InvalidInputError):
        validate(zone.root)


def test_a_table_without_the_expected_columns_is_invalid_input(zone: RawZone) -> None:
    path = interval_dir(zone.root) / "intervals.parquet"
    table = pq.read_table(path)
    pq.write_table(table.drop_columns(["quality"]).replace_schema_metadata(table.schema.metadata), path)
    with pytest.raises(InvalidInputError):
        validate(zone.root)


def test_a_missing_summary_catalog_is_invalid_input(zone: RawZone) -> None:
    (zone.root / "data" / "raw" / "sapn_summary" / "_catalog.jsonl").unlink()
    with pytest.raises(InvalidInputError):
        validate(zone.root)
