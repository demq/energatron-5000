"""`core/usage.py` (P3 spec §5, §7, §13.1): each metric on a small hand table, then against the oracle.

The hand-built tables have their expected values written out by hand. The oracle comparison runs on a generated 14-month
local table that spans both daylight-saving changes of South Australia, with a gap, a partial day and flagged days.
Everything is made up.
"""

import math
from datetime import date, datetime, timedelta
from typing import Any, Final

import numpy as np
import pandas as pd
import pytest

from energatron.core.errors import InvalidInputError
from energatron.core.models import usage as kinds
from energatron.core.timebase import Window, parse_windows
from energatron.core.usage import (
    SUM_TOLERANCE_KWH,
    ReferenceWindow,
    base_load,
    daily,
    dst_day_counts,
    heatmap,
    hourly_profile,
    monthly,
    peak_demand,
    quality_class,
    quality_monthly,
    reference_window,
    seasonal,
    split_registers,
    tou_monthly,
    tou_split,
    window_avg_daily_kwh,
    window_tou_shares,
    yoy,
)
from tests.fixtures import oracle
from tests.fixtures.oracle import LocalInterval
from tests.fixtures.usage_marts import SUFFIX, build_marts, day_minutes_for, local_frame, local_rows

SEASONS: Final = {"DJF": (12, 1, 2), "MAM": (3, 4, 5), "JJA": (6, 7, 8), "SON": (9, 10, 11)}
WEEKEND: Final = ("saturday", "sunday")
HALVES: Final = {"A": ["00:00-12:00"], "B": ["12:00-00:00"]}
FALL_BACK: Final = date(2025, 4, 6)  # the first Sunday in April: a 25-hour day
SPRING_FORWARD: Final = date(2025, 10, 5)  # the first Sunday in October: a 23-hour day
MONDAY: Final = date(2025, 3, 3)


def at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute)


def lengths(*days: date) -> dict[date, int]:
    """Day lengths in minutes for a hand-built table; the two daylight-saving days have their real length."""
    return {day: {FALL_BACK: 1500, SPRING_FORWARD: 1380}.get(day, 1440) for day in days}


def run(day: date, kwh: float = 0.5, *, count: int = 48, start: int = 0, minutes: int = 30, suffix: str = SUFFIX,
        offset: int = 570, flag: str = "A") -> list[LocalInterval]:  # fmt: skip
    """`count` consecutive intervals from `start` minutes past local midnight, each `kwh`."""
    first = datetime(day.year, day.month, day.day) + timedelta(minutes=start)
    return [
        LocalInterval(suffix, first + timedelta(minutes=minutes * index), minutes, kwh, offset, flag)
        for index in range(count)
    ]


def dst_run(day: date, kwh: float = 0.5, *, count: int | None = None, minutes: int = 30) -> list[LocalInterval]:
    """The intervals of a daylight-saving day on its real clock, the first `count` of them.

    The 25-hour day repeats the wall-clock hour 02:00 (summer time, then standard time); the 23-hour day skips it.
    """
    step = timedelta(minutes=minutes)
    midnight = datetime(day.year, day.month, day.day)
    if day == FALL_BACK:
        plan = [(midnight + index * step, 630) for index in range(60 // minutes * 3)]  # 00:00 to 02:59, summer time
        plan += [(midnight + (index + 60 // minutes * 2) * step, 570) for index in range(60 // minutes)]  # 02:00 again
        plan += [(midnight + index * step, 570) for index in range(60 // minutes * 3, 1440 // minutes)]
    else:
        plan = [(midnight + index * step, 570) for index in range(60 // minutes * 2)]  # 00:00 to 01:59, standard time
        plan += [(midnight + index * step, 630) for index in range(60 // minutes * 3, 1440 // minutes)]
    return [LocalInterval(SUFFIX, start, minutes, kwh, offset) for start, offset in plan[:count]]


def windows(spec: dict[str, list[str]]) -> dict[str, tuple[Window, ...]]:
    return parse_windows(spec)


def rows_of(frame: pd.DataFrame) -> list[dict[str, Any]]:
    return [{str(key): value for key, value in row.items()} for row in frame.to_dict("records")]


def approx_rows(found: pd.DataFrame, expected: list[dict[str, Any]]) -> None:
    got = rows_of(found)
    assert len(got) == len(expected)
    for row, want in zip(got, expected, strict=True):
        assert set(row) >= set(want), set(want) - set(row)
        for key, value in want.items():
            if isinstance(value, float):
                assert row[key] == pytest.approx(value, abs=1e-9, nan_ok=True), key
            else:
                assert row[key] == value, key


# the registers


def test_split_registers_keeps_consumption_registers_and_names_the_rest() -> None:
    frame = local_frame(run(MONDAY, count=2) + run(MONDAY, count=2, suffix="B1") + run(MONDAY, count=2, suffix="E2"))
    kept, excluded = split_registers(frame)
    assert sorted(kept["suffix"].unique()) == ["E1", "E2"]
    assert excluded == ["B1"]
    assert len(kept) == 4


# §5.1 daily


def test_daily_counts_complete_partial_and_daylight_saving_days() -> None:
    saturday = date(2025, 3, 8)
    december = date(2025, 12, 1)
    table = local_frame(
        run(MONDAY)  # complete
        + run(saturday, count=40)  # partial, weekend
        + dst_run(FALL_BACK, 0.1)  # 25 hours, complete
        + dst_run(SPRING_FORWARD, 0.1)  # 23 hours, complete
        + run(december, count=1)
    )
    found = daily(
        table, lengths(MONDAY, saturday, FALL_BACK, SPRING_FORWARD, december), weekend=WEEKEND, seasons=SEASONS
    )
    assert list(found.columns) == list(kinds.DAILY_COLUMNS)
    approx_rows(
        found,
        [
            {"local_date": MONDAY, "month": "2025-03", "season": "MAM", "daytype": "weekday", "kwh": 24.0,
             "intervals": 48, "expected": 48, "coverage": 1.0, "complete": True},
            {"local_date": saturday, "daytype": "weekend", "kwh": 20.0, "intervals": 40, "expected": 48,
             "coverage": 40 / 48, "complete": False},
            {"local_date": FALL_BACK, "month": "2025-04", "daytype": "weekend", "kwh": 5.0, "intervals": 50,
             "expected": 50, "coverage": 1.0, "complete": True},
            {"local_date": SPRING_FORWARD, "month": "2025-10", "season": "SON", "kwh": 4.6, "intervals": 46,
             "expected": 46, "complete": True},
            {"local_date": december, "month": "2025-12", "season": "DJF", "kwh": 0.5, "intervals": 1,
             "expected": 48, "complete": False},
        ],
    )  # fmt: skip
    assert found["suffix"].tolist() == [SUFFIX] * 5


def test_daily_uses_the_weekend_names_it_is_given() -> None:
    friday = date(2025, 3, 7)
    found = daily(local_frame(run(friday, count=2)), lengths(friday), weekend=("friday",), seasons=SEASONS)
    assert found["daytype"].tolist() == ["weekend"]


def test_daily_keeps_registers_apart() -> None:
    table = local_frame(run(MONDAY, count=2) + run(MONDAY, 1.0, count=3, suffix="E2"))
    found = daily(table, lengths(MONDAY), weekend=WEEKEND, seasons=SEASONS)
    assert found[["suffix", "intervals", "kwh"]].values.tolist() == [["E1", 2, 1.0], ["E2", 3, 3.0]]


def test_daily_coverage_never_exceeds_one() -> None:
    found = daily(local_frame(run(MONDAY) + run(MONDAY, count=2)), lengths(MONDAY), weekend=WEEKEND, seasons=SEASONS)
    assert found["coverage"].tolist() == [1.0]
    assert found["complete"].tolist() == [False]


# §5.1 monthly and the reference window


def monthly_of(rows: list[LocalInterval], *, demand: int = 30) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    table = local_frame(rows)
    spans = day_minutes_for(rows)
    per_day = daily(table, spans, weekend=WEEKEND, seasons=SEASONS)
    base = base_load(table, per_day, window=Window(60, 300), percentile=10)
    peak = peak_demand(table, demand_minutes=demand)
    return table, per_day, monthly(table, per_day, spans, base_load=base, peak=peak)


def test_monthly_marks_covered_months_and_averages_complete_days_only() -> None:
    rows = local_rows(date(2025, 1, 30), date(2025, 3, 3), value=lambda _: 1.0, skip={date(2025, 2, 10)},
                      partial={date(2025, 2, 11): 10})  # fmt: skip
    _, _, found = monthly_of(rows)
    assert list(found.columns) == list(kinds.MONTHLY_COLUMNS)
    assert found["month"].tolist() == ["2025-01", "2025-02", "2025-03"]
    assert found["covered"].tolist() == [False, True, False]
    february = rows_of(found)[1]
    assert february["days_present"] == 27
    assert february["days_in_month"] == 28
    assert february["complete_days"] == 26
    assert february["kwh"] == pytest.approx(26 * 48 + 10)  # partial days count in the month's energy
    assert february["avg_daily_kwh"] == 48.0  # but not in the day average
    assert february["coverage"] == pytest.approx((26 * 48 + 10) / (28 * 48))
    january = rows_of(found)[0]
    assert (january["days_present"], january["complete_days"], january["kwh"]) == (2, 2, 96.0)


def test_monthly_has_a_row_for_a_month_without_data_between_the_first_and_last() -> None:
    gap = {date(2025, 2, day) for day in range(1, 29)}
    rows = local_rows(date(2025, 1, 30), date(2025, 3, 3), value=lambda _: 1.0, skip=gap)
    _, _, found = monthly_of(rows)
    february = rows_of(found)[1]
    assert (february["month"], february["days_present"], february["covered"], february["kwh"]) == (
        "2025-02",
        0,
        True,
        0.0,
    )
    assert math.isnan(february["avg_daily_kwh"])
    assert february["coverage"] == 0.0


def test_the_reference_window_is_the_last_covered_months() -> None:
    rows = local_rows(date(2024, 12, 15), date(2026, 1, 20), partial={date(2025, 6, 10): 5})
    _, _, found = monthly_of(rows)
    twelve = reference_window(found, 12)[SUFFIX]
    assert twelve.months[0] == "2025-01" and twelve.months[-1] == "2025-12" and len(twelve.months) == 12
    assert (twelve.days, twelve.complete_days) == (365, 364)  # the two daylight-saving days are complete
    assert twelve.coverage == pytest.approx(364 / 365)
    assert reference_window(found, 3)[SUFFIX].months == ("2025-10", "2025-11", "2025-12")


def test_the_reference_window_is_short_when_the_data_is() -> None:
    rows = local_rows(date(2024, 12, 15), date(2025, 4, 20))
    _, _, found = monthly_of(rows)
    window = reference_window(found, 12)[SUFFIX]
    assert window.months == ("2025-01", "2025-02", "2025-03")
    assert window.coverage == 1.0


def test_a_register_with_no_covered_month_has_an_empty_window() -> None:
    _, _, found = monthly_of(local_rows(date(2025, 3, 3), date(2025, 3, 20)))
    assert reference_window(found, 12)[SUFFIX] == ReferenceWindow(months=(), days=0, complete_days=0, coverage=None)


# §5.2 seasonal


def test_a_december_to_february_season_has_one_season_year() -> None:
    rows = local_rows(date(2024, 12, 15), date(2026, 1, 20))
    table = local_frame(rows)
    per_day = daily(table, day_minutes_for(rows), weekend=WEEKEND, seasons=SEASONS)
    found = seasonal(per_day, SEASONS)
    assert list(found.columns) == list(kinds.SEASONAL_COLUMNS)
    sums = oracle.local_day_sums(rows)
    for year, days_wanted in ((2024, 17 + 31 + 28), (2025, 31 + 20)):
        row = found[(found["season"] == "DJF") & (found["season_year"] == year)].iloc[0]
        wanted = [
            total for (_, day), total in sums.items()
            if (day.year == year and day.month == 12) or (day.year == year + 1 and day.month in (1, 2))
        ]  # fmt: skip
        assert row["days"] == days_wanted == len(wanted)
        assert row["kwh"] == pytest.approx(math.fsum(wanted), abs=SUM_TOLERANCE_KWH)
        assert row["avg_daily_kwh"] == pytest.approx(math.fsum(wanted) / len(wanted), abs=SUM_TOLERANCE_KWH)


def test_a_season_with_no_complete_day_has_no_row() -> None:
    september = date(2025, 9, 3)
    table = local_frame(run(MONDAY) + run(september, count=10))
    per_day = daily(table, lengths(MONDAY, september), weekend=WEEKEND, seasons=SEASONS)
    found = seasonal(per_day, SEASONS)
    assert found["season"].tolist() == ["MAM"]


def test_a_season_that_wraps_the_year_is_kept_together_whatever_its_months() -> None:
    seasons = {"WARM": (11, 12, 1, 2, 3), "COOL": (4, 5, 6, 7, 8, 9, 10)}
    rows = local_rows(date(2024, 11, 28), date(2025, 1, 3))
    per_day = daily(local_frame(rows), day_minutes_for(rows), weekend=WEEKEND, seasons=seasons)
    found = seasonal(per_day, seasons)
    assert found["season_year"].tolist() == [2024]  # November, December and January are one WARM season


# §5.3 year on year


def test_yoy_compares_covered_months_a_year_apart() -> None:
    rows = local_rows(date(2023, 12, 15), date(2025, 2, 20))
    _, per_day, table = monthly_of(rows)
    found = yoy(table)
    assert list(found.columns) == list(kinds.YOY_COLUMNS)
    assert found["month"].tolist() == ["2025-01"]
    row = rows_of(found)[0]
    sums = oracle.local_day_sums(rows)
    this = [total for (_, day), total in sums.items() if (day.year, day.month) == (2025, 1)]
    prior = [total for (_, day), total in sums.items() if (day.year, day.month) == (2024, 1)]
    assert row["prior_month"] == "2024-01"
    assert row["avg_daily_kwh"] == pytest.approx(math.fsum(this) / len(this))
    assert row["prior_avg_daily_kwh"] == pytest.approx(math.fsum(prior) / len(prior))
    assert row["change_pct"] == pytest.approx(
        (math.fsum(this) / 31 - math.fsum(prior) / 31) / (math.fsum(prior) / 31) * 100
    )
    assert per_day is not None


def test_yoy_change_is_null_when_the_prior_month_is_zero() -> None:
    rows = local_rows(
        date(2023, 12, 15),
        date(2025, 2, 20),
        value=lambda start: 0.0 if start.year == 2024 and start.month == 1 else 1.0,
    )
    _, _, table = monthly_of(rows)
    row = rows_of(yoy(table))[0]
    assert row["prior_avg_daily_kwh"] == 0.0
    assert math.isnan(row["change_pct"])


# §5.4 hourly profile and §5.5 heatmap


def test_the_hourly_profile_takes_percentiles_of_the_hour_cells() -> None:
    rows: list[LocalInterval] = []
    for index in range(5):  # five weekdays; the 10:00 cell of day n holds n kWh
        day = MONDAY + timedelta(days=index)
        rows += run(day, 0.0)
        rows[-48 + 20] = LocalInterval(SUFFIX, at(day, 10), 30, (index + 1) / 2)
        rows[-48 + 21] = LocalInterval(SUFFIX, at(day, 10, 30), 30, (index + 1) / 2)
    table = local_frame(rows)
    spans = lengths(*{row.local_start.date() for row in rows})
    per_day = daily(table, spans, weekend=WEEKEND, seasons=SEASONS)
    found = hourly_profile(table, per_day, {SUFFIX: {"2025-03"}}, band_percentiles=(10, 50, 90))
    assert list(found.columns) == list(kinds.HOURLY_PROFILE_COLUMNS)
    ten = found[found["hour"] == 10].iloc[0]
    assert (ten["season"], ten["daytype"], ten["cells"]) == ("MAM", "weekday", 5)
    assert ten["mean_kwh"] == pytest.approx(3.0)
    assert (ten["p_low_kwh"], ten["p_mid_kwh"], ten["p_high_kwh"]) == pytest.approx((1.4, 3.0, 4.6))
    assert found["season"].unique().tolist() == ["MAM"]  # a combination without cells has no row
    assert found["daytype"].unique().tolist() == ["weekday"]
    assert len(found) == 24


def test_the_hourly_profile_skips_partial_days_and_months_outside_the_window() -> None:
    rows = run(MONDAY) + run(MONDAY + timedelta(days=1), 9.0, count=30) + run(date(2025, 4, 7), 9.0)
    table = local_frame(rows)
    per_day = daily(table, lengths(*{row.local_start.date() for row in rows}), weekend=WEEKEND, seasons=SEASONS)
    found = hourly_profile(table, per_day, {SUFFIX: {"2025-03"}}, band_percentiles=(10, 50, 90))
    assert found["cells"].unique().tolist() == [1]
    assert found["mean_kwh"].unique().tolist() == [1.0]  # only the complete day in the window


def test_a_daylight_saving_day_adds_no_cell_to_the_hour_it_distorts() -> None:
    other_sunday = date(2025, 4, 13)
    rows = dst_run(FALL_BACK, 1.0) + run(other_sunday)
    table = local_frame(rows)
    spans = lengths(FALL_BACK, other_sunday)
    per_day = daily(table, spans, weekend=WEEKEND, seasons=SEASONS)
    found = hourly_profile(table, per_day, {SUFFIX: {"2025-04"}}, band_percentiles=(10, 50, 90))
    cells = dict(zip(found["hour"], found["cells"], strict=True))
    assert cells[2] == 1  # the repeated hour of the 25-hour day is not a full cell
    assert cells[1] == cells[3] == 2


def test_the_heatmap_is_blank_for_the_cells_daylight_saving_and_partial_days_distort() -> None:
    partial = date(2025, 4, 7)
    rows = dst_run(FALL_BACK, 1.0) + dst_run(SPRING_FORWARD, 1.0) + run(partial, 1.0, count=5)
    found = heatmap(local_frame(rows))
    assert list(found.columns) == list(kinds.HEATMAP_COLUMNS)
    cell = {(row["local_date"], row["hour"]): row["kwh"] for row in rows_of(found)}
    assert math.isnan(cell[FALL_BACK, 2])  # doubled hour
    assert cell[FALL_BACK, 3] == 2.0
    assert (SPRING_FORWARD, 2) not in cell  # the missing hour has no rows, so no cell
    assert cell[SPRING_FORWARD, 3] == 2.0
    assert cell[partial, 1] == 2.0  # 00:00-02:30 is five intervals: hours 0 and 1 are full
    assert math.isnan(cell[partial, 2])  # and hour 2 holds one
    assert (partial, 3) not in cell


# §5.6 TOU split


def test_tou_labels_follow_the_window_edges_to_the_minute() -> None:
    spec = windows({"Night": ["22:00-06:00"], "Day": ["06:00-22:00"]})
    starts = [(5, 30, "Night"), (6, 0, "Day"), (21, 30, "Day"), (22, 0, "Night"), (0, 0, "Night"), (12, 0, "Day")]
    rows = [
        LocalInterval(SUFFIX, at(MONDAY, hour, minute), 30, 1.0 + index)
        for index, (hour, minute, _) in enumerate(starts)
    ]
    split = tou_split(local_frame(rows), [(None, spec)])
    assert split.unlabelled == 0
    labels = {(row["label"]): row["kwh"] for row in rows_of(split.daily)}
    assert labels == {
        "Day": sum(1.0 + index for index, (_, _, label) in enumerate(starts) if label == "Day"),
        "Night": sum(1.0 + index for index, (_, _, label) in enumerate(starts) if label == "Night"),
    }
    assert list(split.daily.columns) == list(kinds.TOU_DAILY_COLUMNS)


def test_a_window_set_applies_from_its_start_date() -> None:
    before, after = date(2025, 3, 4), date(2025, 3, 5)
    first = windows({"A": ["00:00-12:00"], "B": ["12:00-00:00"]})
    second = windows({"A": ["00:00-13:00"], "B": ["13:00-00:00"]})
    rows = run(before, 1.0, count=2, start=12 * 60) + run(after, 1.0, count=2, start=12 * 60)
    split = tou_split(local_frame(rows), [(None, first), (after, second)])
    found = {(row["local_date"], row["label"]): (row["kwh"], row["window_set"]) for row in rows_of(split.daily)}
    assert found == {(before, "B"): (2.0, 0), (after, "A"): (2.0, 1)}


def test_an_interval_no_window_holds_is_counted_unlabelled() -> None:
    split = tou_split(local_frame(run(MONDAY)), [(None, {"A": (Window(0, 720),)})])
    assert split.unlabelled == 24
    assert split.daily["kwh"].tolist() == [12.0]


def test_the_monthly_split_shares_energy_and_averages_complete_days() -> None:
    next_day = MONDAY + timedelta(days=1)
    rows = run(MONDAY, 1.0) + run(next_day, 1.0, count=12)  # one complete day, one with only the first six hours
    table = local_frame(rows)
    per_day = daily(table, lengths(MONDAY, next_day), weekend=WEEKEND, seasons=SEASONS)
    split = tou_split(table, [(None, windows(HALVES))])
    found = tou_monthly(split.daily, per_day)
    assert list(found.columns) == list(kinds.TOU_MONTHLY_COLUMNS)
    approx_rows(
        found,
        [
            {
                "month": "2025-03",
                "label": "A",
                "kwh": 36.0,
                "share": 0.6,
                "avg_daily_kwh": 24.0,
                "mixed_windows": False,
            },
            {
                "month": "2025-03",
                "label": "B",
                "kwh": 24.0,
                "share": 0.4,
                "avg_daily_kwh": 24.0,
                "mixed_windows": False,
            },
        ],
    )


def test_a_month_that_spans_a_window_change_is_mixed() -> None:
    first, second = (
        windows({"A": ["00:00-12:00"], "B": ["12:00-00:00"]}),
        windows({"A": ["00:00-13:00"], "B": ["13:00-00:00"]}),
    )
    rows = run(date(2025, 2, 27), 1.0) + run(date(2025, 3, 3), 1.0) + run(date(2025, 3, 4), 1.0)
    table = local_frame(rows)
    per_day = daily(table, lengths(*{row.local_start.date() for row in rows}), weekend=WEEKEND, seasons=SEASONS)
    found = tou_monthly(tou_split(table, [(None, first), (date(2025, 3, 1), second)]).daily, per_day)
    assert found.groupby("month")["mixed_windows"].first().to_dict() == {"2025-02": False, "2025-03": False}
    mixed = tou_monthly(tou_split(table, [(None, first), (date(2025, 3, 4), second)]).daily, per_day)
    assert mixed.groupby("month")["mixed_windows"].first().to_dict() == {"2025-02": False, "2025-03": True}


# §5.7 base load, peak demand and load factor


def test_base_load_is_a_percentile_of_the_window_powers_on_complete_days() -> None:
    saturday = date(2025, 3, 8)
    rows = run(MONDAY, 5.0)
    for index in range(8):  # 01:00 to 04:30: kWh 0.1…0.8, power 0.2…1.6 kW
        rows[2 + index] = LocalInterval(SUFFIX, at(MONDAY, 1, 0) + timedelta(minutes=30 * index), 30, (index + 1) / 10)
    rows += run(saturday, 100.0, count=10)  # a partial day in the window: ignored
    table = local_frame(rows)
    per_day = daily(table, lengths(MONDAY, saturday), weekend=WEEKEND, seasons=SEASONS)
    found = base_load(table, per_day, window=Window(60, 300), percentile=10)
    assert list(found.columns) == ["suffix", "month", "base_load_kw"]
    assert found["base_load_kw"].tolist() == pytest.approx([0.34])  # 0.2 + (0.4 - 0.2) * 0.7
    scoped = base_load(table, per_day, window=Window(60, 300), percentile=10, scope={SUFFIX: {"2025-03"}})
    assert list(scoped.columns) == ["suffix", "base_load_kw"]
    assert scoped["base_load_kw"].tolist() == pytest.approx([0.34])
    assert base_load(table, per_day, window=Window(60, 300), percentile=10, scope={SUFFIX: {"2025-04"}}).empty


def test_a_base_load_window_may_wrap_past_midnight() -> None:
    rows = run(MONDAY, 5.0)
    rows[46] = LocalInterval(SUFFIX, at(MONDAY, 23, 0), 30, 0.01)  # inside 22:00-06:00
    rows[11] = LocalInterval(SUFFIX, at(MONDAY, 5, 30), 30, 0.02)  # inside
    rows[24] = LocalInterval(SUFFIX, at(MONDAY, 12, 0), 30, 0.0001)  # outside
    table = local_frame(rows)
    per_day = daily(table, lengths(MONDAY), weekend=WEEKEND, seasons=SEASONS)
    lowest = base_load(table, per_day, window=Window(22 * 60, 6 * 60), percentile=0)
    assert lowest["base_load_kw"].tolist() == pytest.approx([0.02])  # 0.01 kWh in 30 minutes is 0.02 kW


def test_peak_demand_is_the_largest_aligned_full_block() -> None:
    other = MONDAY + timedelta(days=1)
    rows = run(MONDAY, 0.1)
    rows[34] = LocalInterval(SUFFIX, at(MONDAY, 17, 0), 30, 1.0)
    rows[35] = LocalInterval(SUFFIX, at(MONDAY, 17, 30), 30, 0.6)
    rows += run(other, 100.0, count=1, start=17 * 60 + 30)  # a lone interval in the 17:00 block: the block is not full
    found = peak_demand(local_frame(rows), demand_minutes=60)
    assert list(found.columns) == ["suffix", "month", "peak_demand_kw", "peak_demand_at", "load_factor"]
    row = rows_of(found)[0]
    assert row["peak_demand_kw"] == pytest.approx(1.6)  # 1.6 kWh over one hour
    assert row["peak_demand_at"] == pd.Timestamp(at(MONDAY, 17))
    mean = (23 * 0.2 + 1.6) / 24
    assert row["load_factor"] == pytest.approx(mean / 1.6)


def test_a_block_is_not_rolled_across_the_clock() -> None:
    rows = run(MONDAY, 0.0)
    rows[1] = LocalInterval(SUFFIX, at(MONDAY, 0, 30), 30, 1.0)
    rows[2] = LocalInterval(SUFFIX, at(MONDAY, 1, 0), 30, 1.0)  # the two busiest intervals straddle the 01:00 edge
    found = peak_demand(local_frame(rows), demand_minutes=60)
    assert found["peak_demand_kw"].tolist() == pytest.approx([1.0])  # not 2.0: aligned blocks hold one each


def test_each_pass_through_a_repeated_hour_is_its_own_block() -> None:
    rows = dst_run(FALL_BACK, 0.1)
    for index in (4, 5):  # 02:00 and 02:30, first pass: still summer time
        rows[index] = LocalInterval(SUFFIX, rows[index].local_start, 30, 1.0, 630)
    for index in (6, 7):  # 02:00 and 02:30 again, now in standard time
        rows[index] = LocalInterval(SUFFIX, rows[index].local_start, 30, 2.0, 570)
    found = peak_demand(local_frame(rows), demand_minutes=60)
    row = rows_of(found)[0]
    assert row["peak_demand_kw"] == pytest.approx(4.0)  # the second pass: 4 kWh in an hour
    assert row["peak_demand_at"] == pd.Timestamp(at(FALL_BACK, 2))


def test_a_demand_block_the_interval_length_does_not_divide_is_refused() -> None:
    table = local_frame(run(MONDAY, minutes=60, count=24))
    with pytest.raises(InvalidInputError) as caught:
        peak_demand(table, demand_minutes=30)
    assert caught.value.details["field"] == "analysis.demand_minutes"


def test_the_load_factor_is_null_when_the_peak_is_zero() -> None:
    row = rows_of(peak_demand(local_frame(run(MONDAY, 0.0)), demand_minutes=30))[0]
    assert row["peak_demand_kw"] == 0.0
    assert math.isnan(row["load_factor"])


def test_peak_demand_over_a_window_takes_the_largest_block_of_its_months() -> None:
    february = date(2025, 2, 3)
    rows = run(february, 1.0, count=2) + run(MONDAY, 0.5, count=2)
    table = local_frame(rows)
    by_month = peak_demand(table, demand_minutes=30)
    assert by_month["peak_demand_kw"].tolist() == pytest.approx([2.0, 1.0])
    window = peak_demand(table, demand_minutes=30, scope={SUFFIX: {"2025-02", "2025-03"}})
    assert list(window.columns) == ["suffix", "peak_demand_kw", "peak_demand_at", "load_factor"]
    assert window["peak_demand_kw"].tolist() == pytest.approx([2.0])
    assert window["load_factor"].tolist() == pytest.approx([0.75])  # mean block power 1.5 kW over a 2.0 kW peak


# §5.8 data quality


@pytest.mark.parametrize(
    ("flag", "name"),
    [
        ("A", "actual"),
        ("E", "estimated"),
        ("F", "substituted"),
        ("S", "substituted"),
        ("N", "other"),
        ("V", "other"),
        ("Z", "other"),
        ("", "other"),
    ],
)
def test_every_flag_the_meter_files_can_hold_has_a_quality_class(flag: str, name: str) -> None:
    assert quality_class(flag) == name
    assert quality_class(flag) in kinds.QUALITY_CLASSES


def test_the_monthly_quality_split_shares_intervals_and_energy() -> None:
    next_day = MONDAY + timedelta(days=1)
    rows = (
        run(MONDAY, 1.0, flag="A")
        + run(next_day, 3.0, count=24, flag="E")
        + run(next_day, 1.0, count=24, start=720, flag="V")
    )
    found = quality_monthly(local_frame(rows))
    assert list(found.columns) == list(kinds.QUALITY_MONTHLY_COLUMNS)
    approx_rows(
        found,
        [
            {"quality_class": "actual", "intervals": 48, "kwh": 48.0, "share_intervals": 0.5, "share_kwh": 48 / 144},
            {"quality_class": "estimated", "intervals": 24, "kwh": 72.0, "share_intervals": 0.25, "share_kwh": 0.5},
            {"quality_class": "other", "intervals": 24, "kwh": 24.0, "share_intervals": 0.25, "share_kwh": 24 / 144},
        ],
    )


# the window helpers


def test_window_helpers_describe_the_reference_window_only() -> None:
    rows = local_rows(date(2025, 2, 25), date(2025, 4, 3), value=lambda _: 1.0)
    table = local_frame(rows)
    spans = day_minutes_for(rows)
    per_day = daily(table, spans, weekend=WEEKEND, seasons=SEASONS)
    split = tou_split(table, [(None, windows(HALVES))])
    assert window_avg_daily_kwh(per_day, {SUFFIX: {"2025-03"}}) == {SUFFIX: 48.0}
    assert window_avg_daily_kwh(per_day, {SUFFIX: {"2024-03"}}) == {SUFFIX: None}
    shares = window_tou_shares(split.daily, {SUFFIX: {"2025-03"}})
    assert shares == {SUFFIX: pytest.approx({"A": 0.5, "B": 0.5})}


def test_dst_day_counts_find_the_short_and_long_days() -> None:
    rows = local_rows(date(2025, 3, 30), date(2025, 4, 10)) + local_rows(date(2025, 10, 1), date(2025, 10, 8))
    spans = day_minutes_for(rows)
    per_day = daily(local_frame(rows), spans, weekend=WEEKEND, seasons=SEASONS)
    assert dst_day_counts(per_day, spans) == (1, 1)


# every function against the independent oracle

DAY_FIRST: Final = date(2024, 12, 20)
DAY_LAST: Final = date(2026, 2, 10)
GAP: Final = date(2025, 5, 14)
PARTIAL: Final = date(2025, 8, 20)
TOU_SETS: Final[oracle.WindowSets] = [
    (None, {"Off Peak": ["00:00-07:00", "22:00-00:00"], "Peak": ["17:00-22:00"], "Shoulder": ["07:00-17:00"]}),
    (
        date(2025, 7, 1),
        {"Off Peak": ["00:00-06:00", "23:00-00:00"], "Peak": ["18:00-23:00"], "Shoulder": ["06:00-18:00"]},
    ),
]


@pytest.fixture(scope="module")
def fixture() -> tuple[list[LocalInterval], pd.DataFrame, dict[date, int], pd.DataFrame]:
    rows = local_rows(
        DAY_FIRST, DAY_LAST, skip={GAP}, partial={PARTIAL: 20}, flags={date(2025, 2, 2): "E", date(2025, 9, 9): "S"}
    )
    table = local_frame(rows)
    spans = day_minutes_for(rows)
    per_day = daily(table, spans, weekend=WEEKEND, seasons=SEASONS)
    return rows, table, spans, per_day


def test_daily_and_monthly_match_the_oracle(
    fixture: tuple[list[LocalInterval], pd.DataFrame, dict[date, int], pd.DataFrame],
) -> None:
    rows, table, spans, per_day = fixture
    sums = oracle.local_day_sums(rows)
    assert {(row["suffix"], row["local_date"]): row["kwh"] for row in rows_of(per_day)} == pytest.approx(
        sums, abs=SUM_TOLERANCE_KWH
    )
    complete = oracle.complete_dates(rows, spans)
    assert {(row["suffix"], row["local_date"]) for row in rows_of(per_day) if row["complete"]} == complete
    assert (GAP not in per_day["local_date"].tolist()) and (SUFFIX, PARTIAL) not in complete
    base = base_load(table, per_day, window=Window(60, 300), percentile=10)
    peak = peak_demand(table, demand_minutes=30)
    found = monthly(table, per_day, spans, base_load=base, peak=peak)
    month_sums: dict[str, list[float]] = {}
    for (_, day), total in sums.items():
        month_sums.setdefault(f"{day.year:04d}-{day.month:02d}", []).append(total)
    for row in rows_of(found):
        assert row["kwh"] == pytest.approx(math.fsum(month_sums[str(row["month"])]), abs=SUM_TOLERANCE_KWH)


def test_the_tou_split_matches_the_oracle(
    fixture: tuple[list[LocalInterval], pd.DataFrame, dict[date, int], pd.DataFrame],
) -> None:
    rows, table, _, per_day = fixture
    parsed = [(start, parse_windows(spec)) for start, spec in TOU_SETS]
    split = tou_split(table, parsed)
    assert split.unlabelled == 0
    found = {(row["suffix"], row["local_date"], row["label"]): row for row in rows_of(split.daily)}
    wanted = oracle.local_tou_sums(rows, TOU_SETS)
    assert {key: row["kwh"] for key, row in found.items()} == pytest.approx(wanted, abs=SUM_TOLERANCE_KWH)
    assert all(row["window_set"] == oracle.window_set_index(key[1], TOU_SETS) for key, row in found.items())
    assert {1, 0} == {row["window_set"] for row in found.values()}
    labelled = split.daily.groupby("local_date")["kwh"].sum()
    assert labelled.to_dict() == pytest.approx(
        {day: total for (_, day), total in oracle.local_day_sums(rows).items()}, abs=SUM_TOLERANCE_KWH
    )
    monthly_split = tou_monthly(split.daily, per_day)
    assert monthly_split.groupby("month")["share"].sum().tolist() == pytest.approx(
        [1.0] * monthly_split["month"].nunique()
    )
    mixed = monthly_split[monthly_split["mixed_windows"]]["month"].unique().tolist()
    assert mixed == []  # the change is on the first of a month: no month straddles it


def test_the_heatmap_and_the_hourly_profile_match_the_oracle(
    fixture: tuple[list[LocalInterval], pd.DataFrame, dict[date, int], pd.DataFrame],
) -> None:
    rows, table, spans, per_day = fixture
    cells = oracle.hour_cells(rows)
    full = oracle.full_hour_cells(rows)
    shown = {(row["suffix"], row["local_date"], row["hour"]): row["kwh"] for row in rows_of(heatmap(table))}
    assert set(shown) == set(cells)
    for key, value in shown.items():
        if key in full:
            assert value == pytest.approx(full[key], abs=SUM_TOLERANCE_KWH)
        else:
            assert math.isnan(value)
    window_months = {f"2025-{month:02d}" for month in range(1, 13)}
    profile = hourly_profile(table, per_day, {SUFFIX: window_months}, band_percentiles=(10, 50, 90))
    complete = oracle.complete_dates(rows, spans)
    seasons = {month: label for label, months in SEASONS.items() for month in months}
    groups: dict[tuple[str, str, int], list[float]] = {}
    for (suffix, day, hour), total in full.items():
        if f"{day.year:04d}-{day.month:02d}" in window_months and (suffix, day) in complete:
            daytype = "weekend" if day.strftime("%A").lower() in WEEKEND else "weekday"
            groups.setdefault((seasons[day.month], daytype, hour), []).append(total)
    assert len(profile) == len(groups)
    for row in rows_of(profile):
        values = groups[str(row["season"]), str(row["daytype"]), int(row["hour"])]
        assert row["cells"] == len(values)
        assert row["mean_kwh"] == pytest.approx(math.fsum(values) / len(values), abs=1e-9)
        assert [row["p_low_kwh"], row["p_mid_kwh"], row["p_high_kwh"]] == pytest.approx(
            [oracle.percentile(values, q) for q in (10, 50, 90)], abs=1e-9
        )


@pytest.mark.parametrize("demand", [30, 60])
def test_peak_demand_and_load_factor_match_the_oracle(
    fixture: tuple[list[LocalInterval], pd.DataFrame, dict[date, int], pd.DataFrame], demand: int
) -> None:
    rows, table, _, _ = fixture
    wanted = oracle.monthly_peak(rows, demand)
    found = rows_of(peak_demand(table, demand_minutes=demand))
    assert {(row["suffix"], row["month"]) for row in found} == set(wanted)
    for row in found:
        peak, at_time, factor = wanted[str(row["suffix"]), str(row["month"])]
        assert row["peak_demand_kw"] == pytest.approx(peak, abs=1e-9)
        assert row["peak_demand_at"] == pd.Timestamp(at_time)
        assert row["load_factor"] == pytest.approx(factor, abs=1e-9)
        assert 0 < float(row["load_factor"]) <= 1


def test_base_load_matches_the_oracle(
    fixture: tuple[list[LocalInterval], pd.DataFrame, dict[date, int], pd.DataFrame],
) -> None:
    rows, table, spans, per_day = fixture
    complete = oracle.complete_dates(rows, spans)
    groups: dict[str, list[float]] = {}
    for row in rows:
        day = row.local_start.date()
        if (row.suffix, day) in complete and oracle.minute_in(
            row.local_start.hour * 60 + row.local_start.minute, 60, 300
        ):
            groups.setdefault(f"{day.year:04d}-{day.month:02d}", []).append(row.kwh * 60 / row.minutes)
    found = {
        row["month"]: row["base_load_kw"]
        for row in rows_of(base_load(table, per_day, window=Window(60, 300), percentile=10))
    }
    assert found == pytest.approx({month: oracle.percentile(values, 10) for month, values in groups.items()}, abs=1e-9)
    assert np.isfinite(list(found.values())).all()


def test_the_quality_split_matches_the_oracle(
    fixture: tuple[list[LocalInterval], pd.DataFrame, dict[date, int], pd.DataFrame],
) -> None:
    rows, table, _, _ = fixture
    counts: dict[tuple[str, str], int] = {}
    for row in rows:
        key = (
            f"{row.local_start.year:04d}-{row.local_start.month:02d}",
            {"A": "actual", "E": "estimated", "S": "substituted"}[row.flag],
        )
        counts[key] = counts.get(key, 0) + 1
    found = {(row["month"], row["quality_class"]): row["intervals"] for row in rows_of(quality_monthly(table))}
    assert found == counts


# the mart builder the page tests use

MART_COLUMNS: Final = {
    "daily": kinds.DAILY_COLUMNS,
    "monthly": kinds.MONTHLY_COLUMNS,
    "seasonal": kinds.SEASONAL_COLUMNS,
    "yoy": kinds.YOY_COLUMNS,
    "hourly_profile": kinds.HOURLY_PROFILE_COLUMNS,
    "heatmap": kinds.HEATMAP_COLUMNS,
    "tou_daily": kinds.TOU_DAILY_COLUMNS,
    "tou_monthly": kinds.TOU_MONTHLY_COLUMNS,
    "quality_monthly": kinds.QUALITY_MONTHLY_COLUMNS,
}


def test_the_mart_builder_gives_nine_tables_with_their_columns_and_a_valid_summary() -> None:
    marts = build_marts(
        second_set=(
            date(2025, 7, 1),
            {"Off Peak": ("00:00-06:00", "22:00-00:00"), "Peak": ("17:00-22:00",), "Shoulder": ("06:00-17:00",)},
        )
    )
    assert {name: tuple(table.columns) for name, table in marts.tables.items()} == MART_COLUMNS
    summary = marts.summary.suffixes[SUFFIX]
    assert (summary.window_start, summary.window_end, summary.window_days) == ("2025-01", "2025-12", 365)
    assert sum(summary.tou_shares.values()) == pytest.approx(1.0)
    assert [found.effective_from for found in marts.summary.window_sets_used] == [None, date(2025, 7, 1)]
    assert marts.summary.dst_days.model_dump() == {"short": 1, "long": 1}
    assert kinds.UsageSummary.from_document(marts.summary.model_dump(mode="json")) == marts.summary
