"""Synthetic local tables and mart tables for the P3 tests (P3 spec §13.2). Every value is made up.

`local_rows` builds the rows of an `elec_interval_local` table on South Australia's clock, from the independent oracle's
offsets, so the daylight-saving days have 23 and 25 hours. `local_frame` turns them into the table the metric functions
take. `build_marts` runs the metric functions on such a table and returns the
tables and a `UsageSummary` for the page tests, so the renderer tests need no pipeline.
"""

from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Final

import pandas as pd

from energatron.core.models.params import AnalysisParams
from energatron.core.models.usage import DstDays, SuffixSummary, UsageSummary, WindowSetUsed
from energatron.core.timebase import Window, parse_windows
from energatron.core.usage import (
    base_load,
    daily,
    dst_day_counts,
    heatmap,
    hourly_profile,
    monthly,
    peak_demand,
    quality_monthly,
    reference_window,
    seasonal,
    tou_monthly,
    tou_split,
    window_avg_daily_kwh,
    window_tou_shares,
    yoy,
)
from tests.fixtures import oracle
from tests.fixtures.oracle import LocalInterval
from tests.fixtures.pipeline_workspace import WINDOWS

SUFFIX: Final = "E1"
SECOND_SUFFIX: Final = "E2"


def default_value(start: datetime) -> float:
    """A positive, varied energy per 30 minutes that depends on the local time only."""
    return round(0.05 + 0.01 * (start.hour % 5) + 0.002 * (start.day % 7) + (0.03 if start.minute == 30 else 0.0), 4)


def local_rows(
    first: date,
    last: date,
    *,
    suffix: str = SUFFIX,
    minutes: int = 30,
    value: Callable[[datetime], float] = default_value,
    skip: Collection[date] = (),
    partial: Mapping[date, int] | None = None,
    flags: Mapping[date, str] | None = None,
) -> list[LocalInterval]:
    """Every interval of the local dates `first…last` (both inclusive), the daylight-saving days at their real length.

    `skip` removes whole local dates, `partial` keeps only the first N rows of a date, and `flags` sets a date's quality
    flag (default `A`). The rows come from the `nem_aest` clock: meter times at UTC+10 become local times.
    """
    kept = partial or {}
    marks = flags or {}
    found: list[LocalInterval] = []
    meter = datetime(first.year, first.month, first.day) - timedelta(days=1)
    stop = datetime(last.year, last.month, last.day) + timedelta(days=3)
    counts: dict[date, int] = {}
    while meter < stop:
        local = oracle.local_time(meter, "nem_aest")
        offset = oracle.local_offset_minutes(meter)
        day = local.date()
        meter += timedelta(minutes=minutes)
        if not first <= day <= last or day in skip:
            continue
        counts[day] = counts.get(day, 0) + 1
        if day in kept and counts[day] > kept[day]:
            continue
        found.append(LocalInterval(suffix, local, minutes, value(local), offset, marks.get(day, "A")))
    return found


def local_frame(rows: list[LocalInterval]) -> pd.DataFrame:
    """The rows as the `elec_interval_local` columns the metric functions read."""
    return pd.DataFrame(
        {
            "suffix": pd.Series([row.suffix for row in rows], dtype="str"),
            "local_start": pd.Series([row.local_start for row in rows], dtype="datetime64[us]"),
            "local_date": pd.Series([row.local_start.date() for row in rows], dtype="object"),
            "utc_offset_minutes": pd.Series([row.utc_offset for row in rows], dtype="int16"),
            "interval_minutes": pd.Series([row.minutes for row in rows], dtype="int16"),
            "kwh": pd.Series([row.kwh for row in rows], dtype="float64"),
            "quality": pd.Series([row.flag for row in rows], dtype="str"),
            "quality_flag": pd.Series([row.flag for row in rows], dtype="str"),
        }
    )


def day_minutes_for(rows: list[LocalInterval]) -> dict[date, int]:
    """The length in minutes of every local date from the first row's to the last row's (the oracle's clock)."""
    first, last = rows[0].local_start.date(), rows[-1].local_start.date()
    lengths: dict[date, int] = {}
    day = first
    while day <= last:
        lengths[day] = 1440
        day += timedelta(days=1)
    for year in range(first.year, last.year + 1):
        april, october = oracle.sa_dst_dates(year)
        if april in lengths:
            lengths[april] = 1500
        if october in lengths:
            lengths[october] = 1380
    return lengths


@dataclass(frozen=True)
class Marts:
    """The nine tables of `usage_profile` by file stem, and its summary: what `usage_report` reads."""

    tables: dict[str, pd.DataFrame]
    summary: UsageSummary


def build_marts(
    first: date = date(2024, 12, 15),
    last: date = date(2026, 1, 20),
    *,
    second_set: tuple[date, Mapping[str, tuple[str, ...]]] | None = None,
    reference_months: int = 12,
    minutes: int = 60,
) -> Marts:
    """Run the metric functions on a generated local table with the default analysis settings.

    `second_set` adds a later TOU window set. The default span holds a full 2025 and so a 12-month window. Hourly rows
    (`minutes=60`) keep the table small. This is a stand-in for the stage, for tests that need marts without a pipeline.
    """
    rows = local_rows(first, last, minutes=minutes)
    table = local_frame(rows)
    spans = day_minutes_for(rows)
    sets = [(None, dict(WINDOWS))] + ([(second_set[0], dict(second_set[1]))] if second_set else [])
    analysis = AnalysisParams.defaults(dict(WINDOWS))
    demand = max(analysis.demand_minutes, minutes)  # the default block must be a whole number of intervals
    parsed = [(start, parse_windows(windows)) for start, windows in sets]
    base_window = Window(60, 300)
    per_day = daily(table, spans, weekend=analysis.weekend, seasons=analysis.seasons)
    per_month = monthly(
        table,
        per_day,
        spans,
        base_load=base_load(table, per_day, window=base_window, percentile=analysis.base_load.percentile),
        peak=peak_demand(table, demand_minutes=demand),
    )
    window = reference_window(per_month, reference_months)[SUFFIX]
    scope = {SUFFIX: set(window.months)}
    split = tou_split(table, parsed)
    averages = window_avg_daily_kwh(per_day, scope)
    shares = window_tou_shares(split.daily, scope)
    base = base_load(table, per_day, window=base_window, percentile=analysis.base_load.percentile, scope=scope)
    peak = peak_demand(table, demand_minutes=demand, scope=scope)
    short, long = dst_day_counts(per_day, spans)
    tables = {
        "daily": per_day,
        "monthly": per_month,
        "seasonal": seasonal(per_day, analysis.seasons),
        "yoy": yoy(per_month),
        "hourly_profile": hourly_profile(table, per_day, scope, band_percentiles=analysis.band_percentiles),
        "heatmap": heatmap(table),
        "tou_daily": split.daily,
        "tou_monthly": tou_monthly(split.daily, per_day),
        "quality_monthly": quality_monthly(table),
    }
    summary = UsageSummary(
        suffixes={
            SUFFIX: SuffixSummary(
                first_date=rows[0].local_start.date(),
                last_date=rows[-1].local_start.date(),
                covered_months=int(per_month["covered"].sum()),
                window_start=window.months[0] if window.months else None,
                window_end=window.months[-1] if window.months else None,
                window_days=window.days,
                window_complete_days=window.complete_days,
                window_coverage=window.coverage,
                avg_daily_kwh=averages[SUFFIX],
                base_load_kw=float(base["base_load_kw"].iloc[0]),
                peak_demand_kw=float(peak["peak_demand_kw"].iloc[0]),
                load_factor=float(peak["load_factor"].iloc[0]),
                tou_shares=shares[SUFFIX],
            )
        },
        excluded_suffixes=[],
        window_sets_used=[
            WindowSetUsed(effective_from=start, windows={k: list(v) for k, v in w.items()}) for start, w in sets
        ],
        band_percentiles=analysis.band_percentiles,
        dst_days=DstDays(short=short, long=long),
        checks={"one_interval_length": True, "daily_conserved": True, "tou_conserved": True, "reference_window": True},
    )
    return Marts(tables, summary)
