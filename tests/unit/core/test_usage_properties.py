"""Properties of the metric functions (P3 spec §13.1, plan §13) on generated hourly tables. Every value is made up."""

import math
from datetime import date

import pandas as pd
from hypothesis import given, settings
from hypothesis import strategies as st

from energatron.core.timebase import Window, bucket, parse_windows
from energatron.core.usage import (
    SUM_TOLERANCE_KWH,
    base_load,
    daily,
    peak_demand,
    tou_monthly,
    tou_split,
)
from tests.fixtures.usage_marts import day_minutes_for, local_frame, local_rows

SEASONS = {"DJF": (12, 1, 2), "MAM": (3, 4, 5), "JJA": (6, 7, 8), "SON": (9, 10, 11)}
WEEKEND = ("saturday", "sunday")
STARTS = [date(2025, 1, 20), date(2025, 3, 25), date(2025, 9, 28)]  # the later two reach a daylight-saving change
PEAK_WINDOW = Window(60, 300)


@st.composite
def tables(draw: st.DrawFn) -> pd.DataFrame:
    """Up to 20 days of hourly rows, with values from a small drawn pool, from a start near a month or DST edge."""
    first = draw(st.sampled_from(STARTS))
    days = draw(st.integers(min_value=1, max_value=20))
    pool = draw(st.lists(st.floats(min_value=0.0, max_value=5.0), min_size=8, max_size=8))
    rows = local_rows(
        first, first.fromordinal(first.toordinal() + days - 1), minutes=60,
        value=lambda start: pool[(start.hour + start.day) % len(pool)],
    )  # fmt: skip
    return local_frame(rows)


@st.composite
def window_specs(draw: st.DrawFn) -> dict[str, list[str]]:
    """A random valid window set: 2-4 cut points on the 5-minute grid, labels cycling, the last window wrapping."""
    cuts = sorted(draw(st.lists(st.integers(min_value=0, max_value=287), min_size=2, max_size=4, unique=True)))
    text = [f"{cut * 5 // 60:02d}:{cut * 5 % 60:02d}" for cut in cuts]
    spec: dict[str, list[str]] = {}
    for index, start in enumerate(text):
        spec.setdefault(f"L{index % 2}", []).append(f"{start}-{text[(index + 1) % len(text)]}")
    return spec


def spans_of(table: pd.DataFrame) -> dict[date, int]:
    dates = sorted(set(table["local_date"]))
    from tests.fixtures.oracle import LocalInterval

    stub = [LocalInterval("E1", pd.Timestamp(day).to_pydatetime(), 60, 0.0) for day in (dates[0], dates[-1])]
    return day_minutes_for(stub)


@settings(max_examples=25, deadline=None)
@given(table=tables())
def test_the_daily_totals_add_up_to_the_interval_total(table: pd.DataFrame) -> None:
    found = daily(table, spans_of(table), weekend=WEEKEND, seasons=SEASONS)
    assert abs(math.fsum(found["kwh"]) - math.fsum(table["kwh"])) <= SUM_TOLERANCE_KWH


@settings(max_examples=25, deadline=None)
@given(table=tables(), spec=window_specs())
def test_every_interval_gets_one_label_and_the_labels_add_up_to_the_day(
    table: pd.DataFrame, spec: dict[str, list[str]]
) -> None:
    windows = parse_windows(spec)
    split = tou_split(table, [(None, windows)])
    assert split.unlabelled == 0
    assert int(split.daily["intervals"].sum()) == len(table)
    per_day = daily(table, spans_of(table), weekend=WEEKEND, seasons=SEASONS)
    by_day = split.daily.groupby("local_date")["kwh"].agg(math.fsum)
    for day, total in zip(per_day["local_date"], per_day["kwh"], strict=True):
        assert abs(by_day[day] - total) <= SUM_TOLERANCE_KWH
    labels = bucket(table["local_start"], windows)
    assert labels.notna().all()
    expected = table.groupby(labels.to_numpy())["kwh"].agg(math.fsum).to_dict()
    found = split.daily.groupby("label")["kwh"].agg(math.fsum).to_dict()
    assert found.keys() == expected.keys()
    for label, total in expected.items():
        assert abs(found[label] - total) <= SUM_TOLERANCE_KWH


@settings(max_examples=25, deadline=None)
@given(table=tables(), spec=window_specs())
def test_the_label_shares_of_a_month_add_up_to_one(table: pd.DataFrame, spec: dict[str, list[str]]) -> None:
    per_day = daily(table, spans_of(table), weekend=WEEKEND, seasons=SEASONS)
    found = tou_monthly(tou_split(table, [(None, parse_windows(spec))]).daily, per_day)
    for month, share in found.groupby("month")["share"].agg(math.fsum).items():
        if table.loc[[str(day)[:7] == month for day in table["local_date"]], "kwh"].sum() > 0:
            assert abs(share - 1.0) <= 1e-9, month


@settings(max_examples=25, deadline=None)
@given(table=tables(), months=st.lists(st.integers(min_value=0, max_value=3), min_size=12, max_size=12))
def test_any_season_partition_gives_every_day_one_season(table: pd.DataFrame, months: list[int]) -> None:
    seasons: dict[str, list[int]] = {}
    for month, label in enumerate(months, start=1):
        seasons.setdefault(f"S{label}", []).append(month)
    found = daily(
        table, spans_of(table), weekend=WEEKEND, seasons={label: tuple(found) for label, found in seasons.items()}
    )
    assert found["season"].notna().all()
    for row in found.itertuples():
        assert row.season == f"S{months[int(str(row.local_date)[5:7]) - 1]}"


@settings(max_examples=25, deadline=None)
@given(table=tables())
def test_the_load_factor_lies_in_zero_to_one_when_the_peak_is_positive(table: pd.DataFrame) -> None:
    found = peak_demand(table, demand_minutes=60)
    for peak, factor in zip(found["peak_demand_kw"], found["load_factor"], strict=True):
        if float(peak) > 0:
            assert 0 < float(factor) <= 1 + 1e-12
        else:
            assert pd.isna(factor)


@settings(max_examples=25, deadline=None)
@given(table=tables(), percentile=st.integers(min_value=0, max_value=100))
def test_base_load_lies_between_the_least_and_greatest_power_in_the_window(
    table: pd.DataFrame, percentile: int
) -> None:
    per_day = daily(table, spans_of(table), weekend=WEEKEND, seasons=SEASONS)
    found = base_load(table, per_day, window=PEAK_WINDOW, percentile=percentile)
    power = table["kwh"]  # hourly rows: kWh per hour is kW
    for value in found["base_load_kw"]:
        assert power.min() - 1e-12 <= value <= power.max() + 1e-12
