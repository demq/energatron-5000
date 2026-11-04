"""The `usage_profile` stage (P3 spec §8, §13.3): the local interval table turned into nine usage tables and a summary.

Every value is synthetic. Most tests write a small `elec_interval_local` table straight from generated rows
(`write_local_table`), so a span, a gap or a second interval length costs one line; one test runs the real upstream
stages over the fixture raw zone. Expected values come from the independent oracle (`tests/fixtures/oracle.py`), not
from `core.usage`.
"""

import json
import math
import shutil
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import Any, Final

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from energatron.core.canonical import content_hash, sha256_hex
from energatron.core.errors import CheckFailedError, InvalidInputError, WarningCode
from energatron.core.models import usage as kinds
from energatron.core.models.provenance import StageCode, StageProvenance
from energatron.core.models.usage import UsageSummary
from energatron.core.usage import TouSplit
from energatron.services.stages import usage_profile
from energatron.services.stages.registry import STAGES, get_stage
from energatron.services.stages.runner import StageResult, prepare_stage, run_stage
from tests.fixtures import oracle
from tests.fixtures.make_nem12 import make_nem12
from tests.fixtures.oracle import LocalInterval
from tests.fixtures.params_text import params_v3_text
from tests.fixtures.pipeline_workspace import WINDOWS, interval_specs
from tests.fixtures.synthetic import SYNTHETIC_ACCOUNT, SYNTHETIC_NMI, SYNTHETIC_NMI11, SYNTHETIC_SERIAL
from tests.fixtures.usage_marts import LOCAL_DIR, SECOND_SUFFIX, SUFFIX, local_rows, write_local_table
from tests.integration._stage_inputs import REFERENCE_MONTHS, build_inputs

CHECKS: Final = [
    "one_interval_length",
    "daily_conserved",
    "keys_unique",
    "tou_conserved",
    "windows_resolved",
    "reference_window",
    "profile_cells",
]
TABLES: Final = {
    "daily": (kinds.DAILY_KIND, kinds.DAILY_VERSION, kinds.DAILY_COLUMNS),
    "monthly": (kinds.MONTHLY_KIND, kinds.MONTHLY_VERSION, kinds.MONTHLY_COLUMNS),
    "seasonal": (kinds.SEASONAL_KIND, kinds.SEASONAL_VERSION, kinds.SEASONAL_COLUMNS),
    "yoy": (kinds.YOY_KIND, kinds.YOY_VERSION, kinds.YOY_COLUMNS),
    "hourly_profile": (kinds.HOURLY_PROFILE_KIND, kinds.HOURLY_PROFILE_VERSION, kinds.HOURLY_PROFILE_COLUMNS),
    "heatmap": (kinds.HEATMAP_KIND, kinds.HEATMAP_VERSION, kinds.HEATMAP_COLUMNS),
    "tou_daily": (kinds.TOU_DAILY_KIND, kinds.TOU_DAILY_VERSION, kinds.TOU_DAILY_COLUMNS),
    "tou_monthly": (kinds.TOU_MONTHLY_KIND, kinds.TOU_MONTHLY_VERSION, kinds.TOU_MONTHLY_COLUMNS),
    "quality_monthly": (kinds.QUALITY_MONTHLY_KIND, kinds.QUALITY_MONTHLY_VERSION, kinds.QUALITY_MONTHLY_COLUMNS),
}
FILES: Final = sorted([*(f"{stem}.parquet" for stem in TABLES), "summary.json", "_provenance.json"])
SECOND_WINDOWS: Final = {
    "Peak": ("07:00-11:00", "16:00-02:00"),
    "Off Peak": ("02:00-07:00",),
    "Shoulder": ("11:00-16:00",),
}
SWITCH: Final = date(2025, 3, 15)
FIRST, LAST = date(2025, 1, 20), date(2025, 5, 10)
"""The default span: February to April are the three covered months, and 6 April is a 25-hour day."""
FALL_BACK: Final = date(2025, 4, 6)
MONTHS: Final = ("2025-02", "2025-03", "2025-04")
TOLERANCE: Final = 1e-9
IDENTIFIERS: Final = [SYNTHETIC_NMI, SYNTHETIC_NMI11, SYNTHETIC_SERIAL, SYNTHETIC_ACCOUNT]


def _code(name: str) -> StageCode:
    source = sha256_hex(name.encode())
    digest = content_hash({"source_sha256": source, "packages": {}, "python": "3.14.0"})
    return StageCode(stage_sha256=digest, source_sha256=source, python="3.14.0", packages={}, files=["__init__.py"])


def run_named(root: Path, name: str = "usage_profile") -> StageResult:
    spec = get_stage(name)
    prepared = prepare_stage(spec, root=root, params_file=root / "params.yaml")
    code = _code(name)
    return run_stage(
        spec, prepared.inputs, prepared.params, prepared.outputs, code=code, recorded_code=code.stage_sha256
    )


def set_params(root: Path, **changes: Any) -> None:
    changes.setdefault("reference_months", 3)
    (root / "params.yaml").write_text(params_v3_text(region=True, **changes))


def make_root(
    tmp_path: Path, rows: list[LocalInterval] | None = None, *, second_set: bool = False, **changes: Any
) -> Path:
    """A workspace root holding `params.yaml` (three-month window) and a local table of `rows` (default span)."""
    root = tmp_path / "workspace"
    root.mkdir()
    sets = (SWITCH.isoformat(), SECOND_WINDOWS) if second_set else None
    set_params(root, second_set=sets, **changes)
    write_local_table(root, rows if rows is not None else local_rows(FIRST, LAST))
    return root


@pytest.fixture(scope="module")
def default_rows() -> list[LocalInterval]:
    return local_rows(FIRST, LAST)


def out_dir(root: Path) -> Path:
    return root / "data" / "marts" / "usage_profile"


def read(root: Path, stem: str) -> list[dict[str, Any]]:
    return pq.read_table(out_dir(root) / f"{stem}.parquet").to_pylist()


def summary(root: Path) -> UsageSummary:
    return UsageSummary.from_document(json.loads((out_dir(root) / "summary.json").read_text()))


def checks(root: Path) -> dict[str, tuple[str, dict[str, object]]]:
    provenance = StageProvenance.from_document(json.loads((out_dir(root) / "_provenance.json").read_text()))
    return {check.name: (check.status, dict(check.details)) for check in provenance.checks}


def failing(root: Path) -> dict[str, dict[str, object]]:
    with pytest.raises(CheckFailedError):
        run_named(root)
    return {name: details for name, (status, details) in checks(root).items() if status == "fail"}


def close(found: float, expected: float) -> bool:
    return math.isclose(found, expected, rel_tol=TOLERANCE, abs_tol=TOLERANCE)


# --- the spec ----------------------------------------------------------------------------------------------------


def test_spec_reads_the_local_table_and_the_analysis_settings() -> None:
    spec = usage_profile.SPEC
    assert spec.name == "usage_profile"
    assert dict(spec.inputs) == {"interval": "data/interim/elec_interval_local"}
    assert spec.params == ("meter.local_tz", "analysis")
    assert dict(spec.outputs) == {"profile": "data/marts/usage_profile"}


def test_it_is_registered_right_after_localise() -> None:
    names = list(STAGES)
    assert names[names.index("localise") + 1] == "usage_profile"


# --- a passing run -----------------------------------------------------------------------------------------------


def test_a_run_writes_nine_tables_and_a_summary_and_passes_every_check(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    found = run_named(root)
    assert sorted(path.name for path in out_dir(root).iterdir()) == FILES
    assert [(check.name, check.status) for check in found.checks] == [(name, "pass") for name in CHECKS]
    assert found.warnings == []


def test_every_table_carries_its_kind_version_and_columns_and_is_sorted_by_its_key(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    run_named(root)
    for stem, (kind, version, columns) in TABLES.items():
        table = pq.read_table(out_dir(root) / f"{stem}.parquet")
        assert table.column_names == list(columns), stem
        assert table.schema.metadata[b"energatron.kind"] == kind.encode(), stem
        assert table.schema.metadata[b"energatron.schema_version"] == str(version).encode(), stem
        assert table.num_rows > 0 or stem == "yoy", stem  # a year-on-year row needs a month a year earlier


def test_the_daily_table_equals_the_oracle_and_counts_the_25_hour_day_as_complete(
    tmp_path: Path, default_rows: list[LocalInterval]
) -> None:
    root = make_root(tmp_path, default_rows)
    run_named(root)
    expected = oracle.local_day_sums(default_rows)
    found = {(row["suffix"], row["local_date"]): row for row in read(root, "daily")}
    assert set(found) == set(expected)
    assert all(close(found[key]["kwh"], total) for key, total in expected.items())
    long_day = found[(SUFFIX, FALL_BACK)]
    assert (long_day["intervals"], long_day["expected"], long_day["complete"]) == (50, 50, True)


def test_the_time_of_use_tables_equal_the_oracle_under_two_window_sets(
    tmp_path: Path, default_rows: list[LocalInterval]
) -> None:
    root = make_root(tmp_path, default_rows, second_set=True)
    run_named(root)
    sets = [(None, WINDOWS), (SWITCH, SECOND_WINDOWS)]
    expected = oracle.local_tou_sums(default_rows, sets)
    found = {(row["suffix"], row["local_date"], row["label"]): row for row in read(root, "tou_daily")}
    assert set(found) == set(expected)
    assert all(close(found[key]["kwh"], total) for key, total in expected.items())
    for (_, day, _), row in found.items():
        assert row["window_set"] == oracle.window_set_index(day, sets)
    months = {row["month"]: row["mixed_windows"] for row in read(root, "tou_monthly")}
    assert months["2025-03"] is True
    assert [month for month, mixed in months.items() if mixed] == ["2025-03"]


def test_the_heatmap_and_the_peak_equal_the_oracle(tmp_path: Path, default_rows: list[LocalInterval]) -> None:
    root = make_root(tmp_path, default_rows)
    run_named(root)
    cells = oracle.hour_cells(default_rows)
    full = oracle.full_hour_cells(default_rows)
    heat = {(row["suffix"], row["local_date"], row["hour"]): row["kwh"] for row in read(root, "heatmap")}
    assert set(heat) == set(cells)
    assert all(close(value, full[key]) for key, value in heat.items() if value is not None)
    assert {key for key, value in heat.items() if value is None} == set(cells) - set(full)
    peaks = oracle.monthly_peak(default_rows, 30)
    for row in read(root, "monthly"):
        expected = peaks.get((row["suffix"], row["month"]))
        assert expected is not None
        assert close(row["peak_demand_kw"], expected[0])
        assert row["peak_demand_at"] == expected[1]
        assert expected[2] is not None
        assert close(row["load_factor"], expected[2])


def test_the_summary_describes_the_window_and_mirrors_the_checks(tmp_path: Path) -> None:
    root = make_root(tmp_path, second_set=True)
    run_named(root)
    found = summary(root)
    assert list(found.suffixes) == [SUFFIX]
    window = found.suffixes[SUFFIX]
    assert (window.window_start, window.window_end) == (MONTHS[0], MONTHS[-1])
    assert (window.first_date, window.last_date) == (FIRST, LAST)
    assert window.covered_months == 3
    assert window.window_days == 28 + 31 + 30
    assert window.window_complete_days == window.window_days
    assert window.window_coverage == 1.0
    assert set(window.tou_shares) == set(WINDOWS)
    assert close(math.fsum(window.tou_shares.values()), 1.0)
    assert found.excluded_suffixes == []
    assert found.band_percentiles == (10, 50, 90)
    assert (found.dst_days.short, found.dst_days.long) == (0, 1)
    assert found.checks == {name: True for name in CHECKS}
    assert [(item.effective_from, item.windows) for item in found.window_sets_used] == [
        (None, {label: list(found) for label, found in WINDOWS.items()}),
        (SWITCH, {label: list(found) for label, found in SECOND_WINDOWS.items()}),
    ]


def test_an_export_register_is_left_out_and_counted(tmp_path: Path, default_rows: list[LocalInterval]) -> None:
    export = local_rows(FIRST, LAST, suffix="B1")
    root = make_root(tmp_path, [*default_rows, *export])
    run_named(root)
    assert summary(root).excluded_suffixes == ["B1"]
    for stem in TABLES:
        assert {row["suffix"] for row in read(root, stem)} <= {SUFFIX}, stem
    assert {row["suffix"] for row in read(root, "daily")} == {SUFFIX}


def test_a_second_consumption_register_is_profiled_on_its_own(
    tmp_path: Path, default_rows: list[LocalInterval]
) -> None:
    second = local_rows(FIRST, LAST, suffix=SECOND_SUFFIX, value=lambda start: 0.01)
    root = make_root(tmp_path, [*default_rows, *second])
    run_named(root)
    found = summary(root)
    assert list(found.suffixes) == [SUFFIX, SECOND_SUFFIX]
    totals = {row["suffix"]: row["kwh"] for row in read(root, "monthly") if row["month"] == "2025-03"}
    assert set(totals) == {SUFFIX, SECOND_SUFFIX}
    assert totals[SECOND_SUFFIX] == pytest.approx(0.01 * 48 * 31)
    assert not close(totals[SUFFIX], totals[SECOND_SUFFIX])


def test_the_fixture_raw_zone_through_the_real_upstream_stages_equals_the_oracle(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    build_inputs("usage_profile", root)  # the fixture raw zone, parsed, merged, judged and localised
    found = run_named(root)
    assert [(check.name, check.status) for check in found.checks] == [(name, "pass") for name in CHECKS]
    truth = [
        LocalInterval(
            item.suffix,
            oracle.local_time(item.meter_start, "nem_aest"),
            item.minutes,
            item.kwh,
            oracle.local_offset_minutes(item.meter_start),
        )
        for item in oracle.intervals(make_nem12(interval_specs().truth))
        if item.suffix.startswith("E")
    ]
    expected = oracle.local_day_sums(truth)
    daily = {(row["suffix"], row["local_date"]): row["kwh"] for row in read(root, "daily")}
    assert set(daily) == set(expected)
    assert all(close(daily[key], total) for key, total in expected.items())
    tou = {(row["suffix"], row["local_date"], row["label"]): row["kwh"] for row in read(root, "tou_daily")}
    expected_tou = oracle.local_tou_sums(truth, [(None, WINDOWS)])
    assert set(tou) == set(expected_tou)
    assert all(close(tou[key], total) for key, total in expected_tou.items())
    window = summary(root).suffixes[SUFFIX]
    assert window.covered_months == REFERENCE_MONTHS
    assert (window.window_start, window.window_end) == ("2025-05", "2025-09")
    assert (summary(root).dst_days.short, summary(root).dst_days.long) == (1, 1)


# --- warnings ----------------------------------------------------------------------------------------------------


def test_a_month_with_two_window_sets_raises_a_warning_naming_it(tmp_path: Path) -> None:
    root = make_root(tmp_path, second_set=True)
    found = run_named(root)
    mixed = [item for item in found.warnings if item.code is WarningCode.W_USAGE_MIXED_WINDOWS]
    assert len(mixed) == 1
    assert mixed[0].details == {"months": ["2025-03"]}


def test_a_partial_day_and_a_missing_day_inside_a_covered_month_raise_one_warning(tmp_path: Path) -> None:
    rows = local_rows(FIRST, LAST, partial={date(2025, 3, 3): 10}, skip=[date(2025, 3, 20)])
    root = make_root(tmp_path, rows)
    found = run_named(root)  # 87 of 89 days are complete: above the 0.95 threshold
    partial = [item for item in found.warnings if item.code is WarningCode.W_USAGE_PARTIAL_DAYS]
    assert len(partial) == 1
    assert partial[0].details == {"days": 2}
    assert summary(root).suffixes[SUFFIX].window_complete_days == 87


def test_a_clean_single_set_run_raises_no_warning(tmp_path: Path) -> None:
    assert run_named(make_root(tmp_path)).warnings == []


# --- the checks that fail the stage -----------------------------------------------------------------------------


def test_fewer_covered_months_than_the_window_asks_for_fails_reference_window(tmp_path: Path) -> None:
    root = make_root(tmp_path, reference_months=4)
    failed = failing(root)
    assert set(failed) == {"reference_window"}
    assert failed["reference_window"]["months_found"] == {SUFFIX: 3}
    window = summary(root).suffixes[SUFFIX]  # the outputs are still written
    assert window.covered_months == 3
    assert summary(root).checks["reference_window"] is False


def test_low_coverage_in_the_window_fails_reference_window(tmp_path: Path) -> None:
    skipped = [date(2025, 3, day) for day in range(2, 8)]  # 83 of 89 complete days: 0.93 < 0.95
    root = make_root(tmp_path, local_rows(FIRST, LAST, skip=skipped))
    failed = failing(root)
    assert set(failed) == {"reference_window"}
    assert failed["reference_window"]["months_found"] == {SUFFIX: 3}
    coverage = failed["reference_window"]["coverage"]
    assert isinstance(coverage, dict)
    assert coverage[SUFFIX] == pytest.approx(83 / 89)


def test_a_lower_threshold_lets_the_same_gaps_through(tmp_path: Path) -> None:
    skipped = [date(2025, 3, day) for day in range(2, 8)]
    root = make_root(tmp_path, local_rows(FIRST, LAST, skip=skipped), coverage_threshold=0.9)
    assert [check.status for check in run_named(root).checks] == ["pass"] * len(CHECKS)


def test_a_register_with_two_interval_lengths_fails_and_writes_no_table(tmp_path: Path) -> None:
    hourly = local_rows(date(2025, 5, 11), date(2025, 5, 12), minutes=60)
    root = make_root(tmp_path, [*local_rows(FIRST, LAST), *hourly])
    failed = failing(root)
    assert failed == {"one_interval_length": {"registers": 1}}
    assert sorted(path.name for path in out_dir(root).iterdir()) == ["_provenance.json"]
    assert list(checks(root)) == ["one_interval_length"]


def test_a_lost_daily_row_fails_daily_conserved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real = usage_profile.daily
    monkeypatch.setattr(usage_profile, "daily", lambda *a, **k: real(*a, **k).iloc[1:].reset_index(drop=True))
    failed = failing(make_root(tmp_path))
    assert "daily_conserved" in failed


def test_a_duplicated_key_fails_keys_unique_and_names_the_table(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = usage_profile.heatmap

    def doubled(local: Any) -> Any:
        table = real(local)
        return pd.concat([table, table.iloc[[0]]], ignore_index=True)

    monkeypatch.setattr(usage_profile, "heatmap", doubled)
    failed = failing(make_root(tmp_path))
    assert failed["keys_unique"] == {"tables": ["heatmap"]}


def test_a_tampered_label_total_fails_tou_conserved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real = usage_profile.tou_split

    def tampered(local: Any, sets: Any) -> TouSplit:
        split = real(local, sets)
        daily = split.daily.copy()
        daily.loc[daily.index[0], "kwh"] += 1.0
        return TouSplit(daily, split.unlabelled)

    monkeypatch.setattr(usage_profile, "tou_split", tampered)
    failed = failing(make_root(tmp_path))
    assert failed["tou_conserved"]["unlabelled"] == 0
    assert failed["tou_conserved"]["mismatched"] == 1


def test_an_unlabelled_interval_fails_tou_conserved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real = usage_profile.tou_split
    monkeypatch.setattr(usage_profile, "tou_split", lambda local, sets: TouSplit(real(local, sets).daily, 3))
    assert failing(make_root(tmp_path))["tou_conserved"]["unlabelled"] == 3


def test_an_hour_missing_from_the_profile_fails_profile_cells(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real = usage_profile.hourly_profile

    def without_three(*a: Any, **k: Any) -> Any:
        table = real(*a, **k)
        return table[table["hour"] != 3].reset_index(drop=True)

    monkeypatch.setattr(usage_profile, "hourly_profile", without_three)
    failed = failing(make_root(tmp_path))
    assert failed["profile_cells"]["incomplete"] > 0  # type: ignore[operator]


def test_window_sets_resolve_every_date_only_when_the_first_has_no_start_and_the_rest_increase() -> None:
    assert usage_profile.resolves_every_date([None])
    assert usage_profile.resolves_every_date([None, date(2025, 3, 1), date(2025, 4, 1)])
    assert not usage_profile.resolves_every_date([date(2025, 1, 1)])
    assert not usage_profile.resolves_every_date([None, date(2025, 4, 1), date(2025, 3, 1)])
    assert not usage_profile.resolves_every_date([None, None])
    assert not usage_profile.resolves_every_date([])


# --- bad inputs --------------------------------------------------------------------------------------------------


def test_a_null_zone_is_invalid_input(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    (root / "params.yaml").write_text(params_v3_text())
    with pytest.raises(InvalidInputError) as caught:
        run_named(root)
    assert caught.value.details["field"] == "meter.local_tz"


def test_a_null_first_window_set_is_invalid_input(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    text = (root / "params.yaml").read_text()
    sets = text.index("    window_sets:")  # the last section of the file
    null_set = "    window_sets:\n      - {effective_from: null, windows: null}\n"
    (root / "params.yaml").write_text(text[:sets] + null_set)
    with pytest.raises(InvalidInputError) as caught:
        run_named(root)
    assert caught.value.details["field"] == "analysis.tou.window_sets[0].windows"


def test_a_demand_block_the_interval_does_not_divide_is_invalid_input(tmp_path: Path) -> None:
    root = make_root(tmp_path, local_rows(FIRST, LAST, minutes=60))
    with pytest.raises(InvalidInputError) as caught:
        run_named(root)
    assert caught.value.details["field"] == "analysis.demand_minutes"


def test_a_table_of_another_kind_is_invalid_input(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    run_named(root)
    shutil.copy(out_dir(root) / "daily.parquet", root / LOCAL_DIR / "intervals.parquet")
    with pytest.raises(InvalidInputError) as caught:
        run_named(root)
    assert caught.value.details["input"] == "interval"


def test_a_missing_table_is_invalid_input(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    (root / LOCAL_DIR / "intervals.parquet").unlink()
    with pytest.raises(InvalidInputError) as caught:
        run_named(root)
    assert caught.value.details["input"] == "interval"


def test_a_table_with_no_consumption_register_is_invalid_input(tmp_path: Path) -> None:
    root = make_root(tmp_path, local_rows(FIRST, LAST, suffix="B1"))
    with pytest.raises(InvalidInputError) as caught:
        run_named(root)
    assert caught.value.details["input"] == "interval"


def test_a_row_that_was_never_placed_is_invalid_input(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    path = root / LOCAL_DIR / "intervals.parquet"
    table = pq.read_table(path)
    column = table.schema.get_field_index("local_start")
    blank = pa.array([None] * table.num_rows, type=table.schema.field("local_start").type)
    pq.write_table(table.set_column(column, table.schema.field("local_start"), blank), path)
    with pytest.raises(InvalidInputError) as caught:
        run_named(root)
    assert caught.value.details["input"] == "interval"
    assert "placed" in str(caught.value)


# --- privacy -----------------------------------------------------------------------------------------------------


def test_no_table_has_an_identifier_column_and_no_file_holds_an_identifier(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    run_named(root)
    banned = ("nmi", "serial", "account", "mirn", "address", "dataset", "customer")
    for stem in TABLES:
        for column in pq.read_table(out_dir(root) / f"{stem}.parquet").column_names:
            assert not any(word in column for word in banned), (stem, column)
    for path in out_dir(root).iterdir():
        assert not any(item.encode() in path.read_bytes() for item in IDENTIFIERS)


def test_a_second_run_writes_the_same_bytes(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    run_named(root)
    first: Mapping[str, bytes] = {path.name: path.read_bytes() for path in out_dir(root).iterdir()}
    run_named(root)
    assert {path.name: path.read_bytes() for path in out_dir(root).iterdir()} == first
