"""The `validate_timebase` stage (P2 spec §9, §13.3): the three time-basis hypotheses against the retailer's TOU export.

Every value is synthetic. Exports are generated from the fixture zone's "truth" NEM12 under a chosen basis, so the
expected verdict follows from how the export was made, and expected register sums come from the independent oracle.
The stage's inputs are the real `parse_nem12` and `merge_interval` outputs for the fixture zone.
"""

import contextlib
import json
import shutil
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import cast

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from energatron.core.canonical import JSONValue, canonical_json, content_hash, sha256_hex
from energatron.core.errors import CheckFailedError, InvalidInputError, WarningCode
from energatron.core.models.catalog import SourceType
from energatron.core.models.meter import TimebaseResult
from energatron.core.models.provenance import StageCode, StageProvenance
from energatron.services.stages import validate_timebase
from energatron.services.stages.registry import STAGES, get_stage
from energatron.services.stages.runner import StageResult, prepare_stage, run_stage
from tests.fixtures import oracle
from tests.fixtures.make_sources import make_retailer_usage_from_nem12
from tests.fixtures.params_text import params_v2_text
from tests.fixtures.pipeline_workspace import (
    LABELS,
    WINDOWS,
    Dataset,
    RawZone,
    build_raw_zone,
    land_blob,
)
from tests.fixtures.synthetic import SYNTHETIC_ACCOUNT, SYNTHETIC_NMI, SYNTHETIC_NMI11, SYNTHETIC_SERIAL

CHECKS = ["blobs_match_catalog", "detector_known", "labels_match", "windows_align", "configured_is_best"]
BASES = ("nem_aest", "local_wall", "local_standard")
OTHER_WINDOWS = {
    "Peak": ("07:00-10:00", "15:00-02:00"),
    "Off Peak": ("02:00-07:00",),
    "Shoulder": ("10:00-15:00",),
}
PERIODS_SCHEMA = pa.schema(
    [
        ("period_start", pa.date32()),
        ("period_end", pa.date32()),
        ("status", pa.string()),
        ("dataset_id", pa.string()),
        ("label", pa.string()),
        ("reported_kwh", pa.float64()),
        ("kwh_nem_aest", pa.float64()),
        ("kwh_local_wall", pa.float64()),
        ("kwh_local_standard", pa.float64()),
        ("fit_nem_aest", pa.bool_()),
        ("fit_local_wall", pa.bool_()),
        ("fit_local_standard", pa.bool_()),
    ]
)


def day(month: int, number: int = 1, hour: int = 0) -> datetime:
    return datetime(2025, month, number, hour)


WINTER = [day(5), day(6), day(7), day(8), day(9), day(10)]  # five winter periods
MIXED = [day(4, 3), day(4, 4), day(4, 5), *WINTER, day(10, 5), day(10, 6), day(10, 7), day(10, 8)]  # twelve periods


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


def validate(root: Path, **overrides: Path) -> StageResult:
    return run_named("validate_timebase", root, **overrides)


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
    with contextlib.suppress(CheckFailedError):
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


def report_dir(root: Path) -> Path:
    return root / "data" / "reports" / "validation" / "timebase"


def land_usage(
    zone: RawZone,
    reads: Sequence[datetime],
    basis: str,
    *,
    windows: Mapping[str, tuple[str, ...]] = WINDOWS,
    labels: Sequence[str] = LABELS,
    blank_last: bool = False,
    mtime: int = 1,
    account: str = SYNTHETIC_ACCOUNT,
    name: str = "usage",
) -> Dataset:
    data = make_retailer_usage_from_nem12(
        zone.truth, basis=basis, windows=windows, reads=reads, labels=labels, blank_last=blank_last, account=account
    )
    return land_blob(
        zone.root,
        SourceType.RETAILER_USAGE,
        f"synthetic_{name}.csv",
        data,
        file_mtime=datetime(2026, 1, mtime, tzinfo=UTC),
    )


def result(root: Path) -> TimebaseResult:
    return TimebaseResult.from_document(json.loads((report_dir(root) / "result.json").read_text()))


def periods(root: Path) -> pa.Table:
    return pq.read_table(report_dir(root) / "periods.parquet")


def rows(table: pa.Table) -> list[dict[str, object]]:
    return table.to_pylist()


def statuses(root: Path) -> Counter[str]:
    """Each period once, by status."""
    seen = {(row["period_start"], row["period_end"]): row["status"] for row in rows(periods(root))}
    return Counter(seen.values())  # type: ignore[arg-type]


def checks(root: Path) -> dict[str, tuple[str, dict[str, JSONValue]]]:
    provenance = StageProvenance.from_document(json.loads((report_dir(root) / "_provenance.json").read_text()))
    return {check.name: (check.status, dict(check.details)) for check in provenance.checks}


def failing(root: Path) -> dict[str, dict[str, JSONValue]]:
    with pytest.raises(CheckFailedError):
        validate(root)
    return {name: details for name, (status, details) in checks(root).items() if status == "fail"}


def codes(found: StageResult) -> list[WarningCode]:
    return [warning.code for warning in found.warnings]


def edit_record(root: Path, dataset: Dataset, change: object) -> None:
    catalog = root / "data" / "raw" / dataset.source.value / "_catalog.jsonl"
    out: list[bytes] = []
    for line in catalog.read_bytes().splitlines():
        doc = json.loads(line)
        if doc["dataset_id"] == dataset.dataset_id:
            change(doc)  # type: ignore[operator]
        out.append(canonical_json(doc) + b"\n")
    catalog.write_bytes(b"".join(out))


# --- the spec ----------------------------------------------------------------------------------------------------


def test_spec_reads_the_merged_intervals_and_the_usage_source() -> None:
    spec = validate_timebase.SPEC
    assert spec.name == "validate_timebase"
    assert dict(spec.inputs) == {"interval": "data/interim/elec_interval_nem", "usage": "data/raw/retailer_usage"}
    assert spec.params == ("meter.local_tz", "meter.timebase", "validation.timebase")
    assert dict(spec.outputs) == {"report": "data/reports/validation/timebase"}


def test_it_is_registered_after_validate_meter_and_before_localise() -> None:
    names = list(STAGES)
    assert names[names.index("validate_timebase") - 1] == "validate_meter"
    assert names[names.index("validate_timebase") + 1] == "localise"


# --- the verdict -------------------------------------------------------------------------------------------------


def test_a_confirmed_run_writes_two_files_provenance_and_no_warning(zone: RawZone) -> None:
    land_usage(zone, MIXED, "nem_aest")
    found = validate(zone.root)
    assert sorted(path.name for path in report_dir(zone.root).iterdir()) == [
        "_provenance.json",
        "periods.parquet",
        "result.json",
    ]
    assert found.warnings == []
    assert [(check.name, check.status) for check in found.checks] == [(name, "pass") for name in CHECKS]
    assert result(zone.root).verdict == "confirmed"


@pytest.mark.parametrize("generated", BASES)
def test_exports_under_each_basis_confirm_that_basis_and_contradict_the_others(zone: RawZone, generated: str) -> None:
    land_usage(zone, MIXED, generated)
    for configured in BASES:
        set_params(zone.root, basis=configured)
        if configured == generated:
            found = validate(zone.root)
            assert found.warnings == []
            outcome = result(zone.root)
            assert outcome.verdict == "confirmed"
            assert outcome.configured == configured
            fits = outcome.fits.model_dump()
            assert fits[generated] == 12
            assert all(count < 12 for basis, count in fits.items() if basis != generated)
            assert outcome.best == [generated]
            assert outcome.unexplained == 0
        else:
            failed = failing(zone.root)
            assert list(failed) == ["configured_is_best"]
            assert failed["configured_is_best"]["best"] == [generated]
            assert result(zone.root).verdict == "contradicted"


def test_winter_only_evidence_cannot_tell_local_wall_from_local_standard(zone: RawZone) -> None:
    land_usage(zone, WINTER, "local_wall")
    for configured in ("local_wall", "local_standard"):
        set_params(zone.root, basis=configured)
        found = validate(zone.root)
        outcome = result(zone.root)
        assert outcome.verdict == "unconfirmed"
        assert outcome.fits.local_wall == outcome.fits.local_standard == 5
        assert outcome.fits.nem_aest < 5
        assert outcome.best == ["local_standard", "local_wall"]
        assert codes(found) == [WarningCode.W_TIMEBASE_UNCONFIRMED]
        assert found.warnings[0].details["fits"] == outcome.fits.model_dump()
        assert all(status == "pass" for status, _ in checks(zone.root).values())


def test_winter_only_evidence_contradicts_nem_aest_when_two_bases_tie_ahead_of_it(zone: RawZone) -> None:
    land_usage(zone, WINTER, "local_wall")
    set_params(zone.root, basis="nem_aest")
    assert list(failing(zone.root)) == ["configured_is_best"]
    outcome = result(zone.root)
    assert outcome.verdict == "contradicted"
    assert outcome.best == ["local_standard", "local_wall"]


def test_winter_only_evidence_is_enough_to_confirm_nem_aest_over_the_other_two(zone: RawZone) -> None:
    land_usage(zone, WINTER, "nem_aest")
    found = validate(zone.root)
    assert result(zone.root).verdict == "confirmed"
    assert found.warnings == []


def test_a_basis_strictly_ahead_of_the_configured_one_contradicts_it(zone: RawZone) -> None:
    land_usage(zone, MIXED, "local_standard")
    set_params(zone.root, basis="local_wall")  # fits every winter period, but not the summer ones
    failed = failing(zone.root)
    assert failed["configured_is_best"]["best"] == ["local_standard"]
    fits = cast("dict[str, int]", failed["configured_is_best"]["fits"])
    assert fits["local_standard"] > fits["local_wall"]


def test_too_few_used_periods_is_unconfirmed_and_passes(zone: RawZone) -> None:
    land_usage(zone, [day(5), day(6), day(7)], "nem_aest")  # two periods; three are needed
    found = validate(zone.root)
    outcome = result(zone.root)
    assert outcome.verdict == "unconfirmed"
    assert outcome.fits.nem_aest == 2
    assert codes(found) == [WarningCode.W_TIMEBASE_UNCONFIRMED]
    assert all(status == "pass" for status, _ in checks(zone.root).values())


def test_min_fit_periods_is_the_declared_param(zone: RawZone) -> None:
    land_usage(zone, [day(5), day(6), day(7)], "nem_aest")
    set_params(zone.root, min_fit_periods=2)
    validate(zone.root)
    assert result(zone.root).verdict == "confirmed"


def test_no_usage_export_is_unconfirmed_with_an_empty_table(zone: RawZone) -> None:
    found = validate(zone.root)
    outcome = result(zone.root)
    assert outcome.verdict == "unconfirmed"
    assert outcome.fits.model_dump() == {"nem_aest": 0, "local_wall": 0, "local_standard": 0}
    assert outcome.best == []
    assert outcome.periods.model_dump() == {"used": 0, "not_covered": 0, "total_mismatch": 0}
    assert periods(zone.root).num_rows == 0
    assert codes(found) == [WarningCode.W_TIMEBASE_UNCONFIRMED]
    assert all(status == "pass" for status, _ in checks(zone.root).values())


def test_periods_that_fit_no_basis_are_unexplained_and_warn(zone: RawZone) -> None:
    first = make_retailer_usage_from_nem12(
        zone.truth, basis="nem_aest", windows=WINDOWS, reads=WINTER, labels=LABELS
    ).split(b"\r\n")
    second = make_retailer_usage_from_nem12(
        zone.truth, basis="nem_aest", windows=OTHER_WINDOWS, reads=WINTER, labels=LABELS
    ).split(b"\r\n")
    # the header and four reads on the real windows (the first opens the series, so three periods); then two on others
    spliced = b"\r\n".join(first[:5] + second[5:])
    land_blob(zone.root, SourceType.RETAILER_USAGE, "synthetic_usage.csv", spliced)
    found = validate(zone.root)
    outcome = result(zone.root)
    assert outcome.verdict == "confirmed"  # three periods fit, which is the minimum
    assert outcome.fits.nem_aest == 3
    assert outcome.unexplained == 2
    assert codes(found) == [WarningCode.W_TIMEBASE_UNEXPLAINED]
    assert found.warnings[0].details == {"periods": 2}
    assert statuses(zone.root) == {"used": 5}


# --- periods -----------------------------------------------------------------------------------------------------


def test_a_period_is_the_meter_dates_after_the_previous_read_up_to_the_new_one(zone: RawZone) -> None:
    land_usage(zone, [day(5), day(6), day(7)], "nem_aest")
    validate(zone.root)
    found = {(row["period_start"], row["period_end"]) for row in rows(periods(zone.root))}
    assert found == {(date(2025, 5, 2), date(2025, 6, 1)), (date(2025, 6, 2), date(2025, 7, 1))}
    assert statuses(zone.root) == {"used": 2}


def test_a_read_date_off_by_one_day_fails_the_total_check(zone: RawZone) -> None:
    data = make_retailer_usage_from_nem12(zone.truth, basis="nem_aest", windows=WINDOWS, reads=WINTER, labels=LABELS)
    for read in WINTER:  # every read one day later than the meter dates it covers
        data = data.replace(
            f"{read:%Y-%m-%d}T".encode(), f"{datetime(read.year, read.month, read.day + 1):%Y-%m-%d}T".encode()
        )
    land_blob(zone.root, SourceType.RETAILER_USAGE, "synthetic_usage.csv", data)
    found = validate(zone.root)
    assert statuses(zone.root) == {"total_mismatch": 5}
    assert result(zone.root).fits.model_dump() == {"nem_aest": 0, "local_wall": 0, "local_standard": 0}
    assert codes(found) == [WarningCode.W_TIMEBASE_UNCONFIRMED]


def test_the_table_has_one_row_per_period_and_label_with_the_oracles_sums(zone: RawZone) -> None:
    dataset = land_usage(zone, WINTER, "nem_aest")
    validate(zone.root)
    table = periods(zone.root)
    assert table.schema.remove_metadata().equals(PERIODS_SCHEMA)
    assert table.schema.metadata[b"energatron.kind"] == b"timebase_periods"
    assert table.schema.metadata[b"energatron.schema_version"] == b"1"
    found = rows(table)
    assert len(found) == 15
    assert [(row["period_end"], row["label"]) for row in found] == sorted(
        (row["period_end"], row["label"])
        for row in found  # type: ignore[type-var]
    )
    assert {row["dataset_id"] for row in found} == {dataset.dataset_id}
    assert {row["status"] for row in found} == {"used"}
    items = oracle.intervals(zone.truth)
    for row in found:
        start, end, label = cast("date", row["period_start"]), cast("date", row["period_end"]), str(row["label"])
        inside = [item for item in items if start <= item.meter_start.date() <= end]
        for basis in BASES:
            assert row[f"kwh_{basis}"] == pytest.approx(oracle.tou_sums(inside, WINDOWS, basis)[label], abs=1e-9)
        assert row["fit_nem_aest"] is True
        assert row["reported_kwh"] == pytest.approx(oracle.tou_sums(inside, WINDOWS, "nem_aest")[label], abs=1e-4)


def test_a_period_beyond_the_meter_data_is_not_covered(zone: RawZone) -> None:
    land_usage(zone, [day(3), day(5), day(6), day(10), day(10, 20)], "nem_aest")
    validate(zone.root)
    counted = statuses(zone.root)
    assert counted["not_covered"] == 2  # the first starts before the data; the last ends after it
    assert counted["used"] == 2
    uncovered = [row for row in rows(periods(zone.root)) if row["status"] == "not_covered"]
    assert all(row["kwh_nem_aest"] is None and row["fit_nem_aest"] is None for row in uncovered)
    assert all(row["reported_kwh"] is not None for row in uncovered)
    assert result(zone.root).periods.model_dump() == {"used": 2, "not_covered": 2, "total_mismatch": 0}


def test_a_total_that_disagrees_with_the_meter_is_a_total_mismatch_and_is_not_fitted(zone: RawZone) -> None:
    data = make_retailer_usage_from_nem12(zone.truth, basis="nem_aest", windows=WINDOWS, reads=WINTER, labels=LABELS)
    lines = data.decode().split("\r\n")
    target = next(i for i, line in enumerate(lines) if ",2025-07-01T00:00:00," in line)
    fields = lines[target].split(",")
    fields[-1] = f"{float(fields[-1]) + 1.0:.4f}"
    lines[target] = ",".join(fields)
    land_blob(zone.root, SourceType.RETAILER_USAGE, "synthetic_usage.csv", "\r\n".join(lines).encode())
    validate(zone.root)
    assert statuses(zone.root) == {"used": 4, "total_mismatch": 1}
    mismatched = [row for row in rows(periods(zone.root)) if row["status"] == "total_mismatch"]
    assert {row["period_end"] for row in mismatched} == {date(2025, 7, 1)}
    assert all(row["fit_nem_aest"] is None for row in mismatched)
    assert result(zone.root).fits.nem_aest == 4


def test_a_total_just_inside_the_tolerance_is_used(zone: RawZone) -> None:
    data = make_retailer_usage_from_nem12(zone.truth, basis="nem_aest", windows=WINDOWS, reads=WINTER, labels=LABELS)
    lines = data.decode().split("\r\n")
    target = next(i for i, line in enumerate(lines) if ",2025-07-01T00:00:00," in line)
    fields = lines[target].split(",")
    fields[-1] = f"{float(fields[-1]) + 0.04:.4f}"
    lines[target] = ",".join(fields)
    land_blob(zone.root, SourceType.RETAILER_USAGE, "synthetic_usage.csv", "\r\n".join(lines).encode())
    validate(zone.root)
    assert statuses(zone.root) == {"used": 5}


def test_a_read_not_at_midnight_is_dropped_and_counted(zone: RawZone) -> None:
    land_usage(zone, [day(5), day(6, 1, 12), day(7)], "nem_aest")
    validate(zone.root)
    outcome = result(zone.root)
    assert outcome.not_midnight == 1
    assert outcome.incomplete == 0
    assert statuses(zone.root) == {"total_mismatch": 1}  # May 1 to Jul 1, with a row that covers only June 2 onward


def test_a_row_incomplete_in_the_newest_export_is_taken_from_the_older_one(zone: RawZone) -> None:
    complete = land_usage(zone, WINTER, "nem_aest", mtime=1, name="older")
    land_usage(zone, WINTER, "nem_aest", blank_last=True, mtime=2, name="newer", account="synthetic-account-two")
    validate(zone.root)
    outcome = result(zone.root)
    assert outcome.incomplete == 0
    assert statuses(zone.root) == {"used": 5}
    last = [row for row in rows(periods(zone.root)) if row["period_end"] == date(2025, 10, 1)]
    assert {row["dataset_id"] for row in last} == {complete.dataset_id}


def test_a_row_incomplete_in_every_export_is_dropped_and_counted(zone: RawZone) -> None:
    land_usage(zone, WINTER, "nem_aest", blank_last=True, mtime=1, name="one")
    land_usage(zone, WINTER, "nem_aest", blank_last=True, mtime=2, name="two", account="synthetic-account-two")
    validate(zone.root)
    outcome = result(zone.root)
    assert outcome.incomplete == 1
    assert statuses(zone.root) == {"used": 4}


def test_the_highest_precedence_export_supplies_each_period(zone: RawZone) -> None:
    land_usage(zone, WINTER, "nem_aest", mtime=1, name="older")
    newer = land_usage(zone, WINTER, "nem_aest", mtime=2, name="newer", account="synthetic-account-two")
    validate(zone.root)
    assert {row["dataset_id"] for row in rows(periods(zone.root))} == {newer.dataset_id}


def test_a_superseded_export_is_not_read(zone: RawZone) -> None:
    old = land_usage(zone, WINTER[:3], "nem_aest", name="old")
    data = make_retailer_usage_from_nem12(
        zone.truth, basis="nem_aest", windows=WINDOWS, reads=WINTER[2:], labels=LABELS
    )
    new = land_blob(zone.root, SourceType.RETAILER_USAGE, "synthetic_new.csv", data, supersedes=old.dataset_id)
    validate(zone.root)
    assert {row["dataset_id"] for row in rows(periods(zone.root))} == {new.dataset_id}


# --- the checks --------------------------------------------------------------------------------------------------


def test_labels_match_fails_for_an_export_whose_labels_are_not_the_windows(zone: RawZone) -> None:
    renamed = {"Peak": WINDOWS["Peak"], "Off Peak": WINDOWS["Off Peak"], "Other": WINDOWS["Shoulder"]}
    land_usage(zone, WINTER, "nem_aest", windows=renamed, labels=("Off Peak", "Other", "Peak"))
    failed = failing(zone.root)
    assert failed == {"labels_match": {"missing": ["Shoulder"], "extra": ["Other"]}}
    assert result(zone.root).verdict in {"unconfirmed", "contradicted"}  # the outputs are still written


def test_windows_align_fails_for_a_window_edge_inside_an_interval(zone: RawZone) -> None:
    land_usage(zone, WINTER, "nem_aest")
    unaligned = {"Peak": ("06:15-10:00", "15:00-01:00"), "Off Peak": ("01:00-06:15",), "Shoulder": ("10:00-15:00",)}
    set_params(zone.root, windows=unaligned)
    failed = failing(zone.root)
    assert failed == {"windows_align": {"interval_minutes": [30]}}


def test_windows_align_passes_when_every_edge_is_a_multiple_of_the_interval_length(zone: RawZone) -> None:
    land_usage(zone, WINTER, "nem_aest")
    validate(zone.root)
    assert checks(zone.root)["windows_align"] == ("pass", {"interval_minutes": [30]})


def test_blobs_match_catalog_fails_for_a_tampered_export_and_it_is_not_read(zone: RawZone) -> None:
    dataset = land_usage(zone, WINTER, "nem_aest")
    blob = zone.root / dataset.stored_path
    blob.write_bytes(blob.read_bytes() + b"\r\n")
    failed = failing(zone.root)
    assert failed == {"blobs_match_catalog": {"mismatched": [dataset.dataset_id]}}
    assert periods(zone.root).num_rows == 0


def test_blobs_match_catalog_fails_for_a_missing_blob(zone: RawZone) -> None:
    dataset = land_usage(zone, WINTER, "nem_aest")
    (zone.root / dataset.stored_path).unlink()
    assert failing(zone.root) == {"blobs_match_catalog": {"mismatched": [dataset.dataset_id]}}


def test_detector_known_fails_for_a_detector_version_the_stage_doesnt_know(zone: RawZone) -> None:
    dataset = land_usage(zone, WINTER, "nem_aest")
    edit_record(zone.root, dataset, lambda doc: doc["detector"].update({"version": 2}))
    assert failing(zone.root) == {"detector_known": {"unknown": [dataset.dataset_id]}}
    assert periods(zone.root).num_rows == 0


def test_blobs_match_catalog_checks_a_superseded_export_too(zone: RawZone) -> None:
    old = land_usage(zone, WINTER[:3], "nem_aest", name="old")
    data = make_retailer_usage_from_nem12(
        zone.truth, basis="nem_aest", windows=WINDOWS, reads=WINTER[2:], labels=LABELS
    )
    land_blob(zone.root, SourceType.RETAILER_USAGE, "synthetic_new.csv", data, supersedes=old.dataset_id)
    (zone.root / old.stored_path).write_bytes(b"tampered")
    assert failing(zone.root) == {"blobs_match_catalog": {"mismatched": [old.dataset_id]}}


# --- determinism, privacy and inputs -----------------------------------------------------------------------------


def test_running_twice_writes_the_same_bytes(zone: RawZone) -> None:
    land_usage(zone, MIXED, "nem_aest")
    validate(zone.root)
    first = {path.name: path.read_bytes() for path in report_dir(zone.root).iterdir()}
    validate(zone.root)
    assert {path.name: path.read_bytes() for path in report_dir(zone.root).iterdir()} == first


def test_provenance_names_the_interval_directory_the_usage_datasets_and_the_params(zone: RawZone) -> None:
    dataset = land_usage(zone, WINTER, "nem_aest")
    validate(zone.root)
    provenance = StageProvenance.from_document(json.loads((report_dir(zone.root) / "_provenance.json").read_text()))
    assert [(item.name, item.type, item.path) for item in provenance.inputs] == [
        ("interval", "dir", "data/interim/elec_interval_nem"),
        ("usage", "dir", "data/raw/retailer_usage"),
    ]
    assert provenance.inputs[1].dataset_ids == [dataset.dataset_id]
    assert sorted(provenance.params.values) == ["meter.local_tz", "meter.timebase", "validation.timebase"]


def test_the_inputs_can_be_overridden(zone: RawZone, tmp_path: Path) -> None:
    land_usage(zone, WINTER, "nem_aest")
    moved = tmp_path / "elsewhere"
    shutil.copytree(zone.root / "data" / "raw" / "retailer_usage", moved)
    shutil.rmtree(zone.root / "data" / "raw" / "retailer_usage")
    (zone.root / "data" / "raw" / "retailer_usage").mkdir()
    (zone.root / "data" / "raw" / "retailer_usage" / "_catalog.jsonl").write_bytes(b"")
    validate(zone.root, usage=moved)
    assert result(zone.root).periods.used == 5


def test_no_nmi_serial_or_account_reaches_any_output(zone: RawZone) -> None:
    land_usage(zone, MIXED, "nem_aest")
    validate(zone.root)
    secrets = [SYNTHETIC_NMI, SYNTHETIC_NMI11, SYNTHETIC_SERIAL, SYNTHETIC_ACCOUNT]
    for path in report_dir(zone.root).iterdir():
        data = path.read_bytes()
        assert not any(secret.encode() in data for secret in secrets), path.name


def test_a_null_zone_or_window_set_is_invalid_input_naming_the_field(zone: RawZone) -> None:
    (zone.root / "params.yaml").write_text(params_v2_text())
    with pytest.raises(InvalidInputError) as caught:
        validate(zone.root)
    assert caught.value.details["field"] in {"meter.local_tz", "validation.timebase.windows"}


def test_an_unknown_zone_is_invalid_input(zone: RawZone) -> None:
    text = params_v2_text(region=True).replace("Australia/Adelaide", "Nowhere/Land")
    (zone.root / "params.yaml").write_text(text)
    with pytest.raises(InvalidInputError) as caught:
        validate(zone.root)
    assert caught.value.details["field"] == "meter.local_tz"


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


def test_a_missing_usage_catalog_is_invalid_input(zone: RawZone) -> None:
    (zone.root / "data" / "raw" / "retailer_usage" / "_catalog.jsonl").unlink()
    with pytest.raises(InvalidInputError):
        validate(zone.root)
