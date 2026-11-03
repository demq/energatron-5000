"""The usage profile's metrics (P3 spec §5, §7).

Every function is pure: DataFrames and plain values in, a DataFrame out, no I/O and no zone lookup. A stage that needs
the length of each local date computes it with `local_day_minutes` and passes it in (`day_minutes`).

The input is the `elec_interval_local` table of one or more consumption registers (`split_registers`), with the columns
`suffix`, `local_start` (naive), `local_date`, `utc_offset_minutes`, `interval_minutes`, `kwh`, `quality_flag`, and no
row that `localise` could not place. A register has a single interval length. Determinism (D1 §9.4): every table is
sorted by its key, totals use `math.fsum`, percentiles use `numpy.percentile(..., method="linear")`, and nothing reads
the clock.
A "null" is `NaN` (`NaT` for a time) in memory.
"""

import math
from collections.abc import Collection, Hashable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from types import MappingProxyType
from typing import Final

import numpy as np
import pandas as pd

from energatron.core.errors import InvalidInputError
from energatron.core.models import usage as kinds
from energatron.core.timebase import Window, bucket

SUM_TOLERANCE_KWH: Final = 1e-6
"""Two sums that should agree are compared within this (P3 spec §3): a sum of group sums is not bit-equal to one sum."""

QUALITY_BY_FLAG: Final = MappingProxyType({"A": "actual", "E": "estimated", "F": "substituted", "S": "substituted"})
"""The quality class of each NEM12 quality flag (the first character of `quality`); any other flag is `other`."""
OTHER_QUALITY: Final = "other"

type WindowSets = Sequence[tuple[date | None, Mapping[str, Sequence[Window]]]]
"""`(effective_from, parsed windows)` pairs: the first has no start date, the others strictly increasing ones."""
type Scope = Mapping[str, Collection[str]]
"""Months (`YYYY-MM`) by register: restricts a metric to them and gives one value per register instead of per month."""

_BOTH = ["suffix", "month"]


@dataclass(frozen=True)
class ReferenceWindow:
    """A register's reference window (P3 spec §5.1): `months` oldest first, fewer than asked when the data has fewer."""

    months: tuple[str, ...]
    days: int  # local dates in those months
    complete_days: int
    coverage: float | None  # complete_days / days; None with no month


@dataclass(frozen=True)
class TouSplit:
    """`daily` is the `tou_daily` table; `unlabelled` counts the intervals no window held (not in `daily`)."""

    daily: pd.DataFrame
    unlabelled: int


# registers and small helpers


def quality_class(flag: str) -> str:
    """The class of a quality flag: `actual`, `estimated`, `substituted` or `other`."""
    return QUALITY_BY_FLAG.get(flag, OTHER_QUALITY)


def split_registers(local: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """The consumption registers' rows (suffix starting with `E`), and the other suffixes, sorted (P3 spec §3)."""
    consumption = local["suffix"].str.startswith("E")
    excluded = sorted(local.loc[~consumption, "suffix"].unique())
    return local.loc[consumption].reset_index(drop=True), [str(suffix) for suffix in excluded]


def _fsum(values: pd.Series) -> float:
    return math.fsum(values.to_numpy(dtype="float64"))


def _month(dates: pd.Series) -> pd.Series:
    return pd.to_datetime(dates).dt.strftime("%Y-%m")


def _month_of(day: date) -> str:
    return f"{day.year:04d}-{day.month:02d}"


def _with_month(local: pd.DataFrame) -> pd.DataFrame:
    return local.assign(month=_month(local["local_date"]))


def _in_scope(table: pd.DataFrame, scope: Scope) -> pd.DataFrame:
    wanted = pd.Series(
        [month in scope.get(suffix, ()) for suffix, month in zip(table["suffix"], table["month"], strict=True)],
        index=table.index,
        dtype=bool,
    )
    return table.loc[wanted]


def _keys(scope: Scope | None) -> list[str]:
    return ["suffix"] if scope is not None else list(_BOTH)


def _minute_of_day(local_start: pd.Series) -> np.ndarray:
    return (local_start.dt.hour * 60 + local_start.dt.minute).to_numpy()


def _in_window(minutes: np.ndarray, window: Window) -> np.ndarray:
    """Whether each minute of the day is in `[start, end)`; the window wraps past midnight when `end <= start`."""
    if window.end_minute > window.start_minute:
        return (minutes >= window.start_minute) & (minutes < window.end_minute)
    return (minutes >= window.start_minute) | (minutes < window.end_minute)


def _interval_by_suffix(local: pd.DataFrame) -> dict[str, int]:
    return {str(suffix): int(found) for suffix, found in local.groupby("suffix")["interval_minutes"].first().items()}


# §5.1 days, months and the reference window


def daily(
    local: pd.DataFrame,
    day_minutes: Mapping[date, int],
    *,
    weekend: Collection[str],
    seasons: Mapping[str, Collection[int]],
) -> pd.DataFrame:
    """One row per register and local date with an interval: energy, row count, completeness, day type and season.

    A day is complete when its row count equals `day_minutes[date] / interval_minutes`, so a 23- or 25-hour day is
    complete with 46 or 50 half-hour rows.
    """
    grouped = local.groupby(["suffix", "local_date"], sort=True)
    found = grouped.agg(
        kwh=("kwh", _fsum), intervals=("kwh", "size"), interval_minutes=("interval_minutes", "first")
    ).reset_index()
    expected = [
        day_minutes[day] // int(length)
        for day, length in zip(found["local_date"], found["interval_minutes"], strict=True)
    ]
    found["expected"] = pd.Series(expected, index=found.index, dtype="int64")
    found["coverage"] = np.minimum(found["intervals"] / found["expected"], 1.0)
    found["complete"] = found["intervals"] == found["expected"]
    stamps = pd.to_datetime(found["local_date"])
    season_of = {month: label for label, months in seasons.items() for month in months}
    found["month"] = stamps.dt.strftime("%Y-%m")
    found["season"] = stamps.dt.month.map(season_of)
    weekend_days = frozenset(weekend)
    found["daytype"] = np.where(stamps.dt.day_name().str.lower().isin(weekend_days), "weekend", "weekday")
    return found[list(kinds.DAILY_COLUMNS)].reset_index(drop=True)


def monthly(
    local: pd.DataFrame,
    daily_table: pd.DataFrame,
    day_minutes: Mapping[date, int],
    *,
    base_load: pd.DataFrame,
    peak: pd.DataFrame,
) -> pd.DataFrame:
    """One row per register and calendar month from its first to its last month of data (P3 spec §5.1).

    `base_load` and `peak` are the per-month results of `base_load` and `peak_demand`. `day_minutes` must cover every
    local date from the register's first to its last. A month in the middle of the range with no data has a row too.
    """
    intervals = _interval_by_suffix(local)
    energy = _with_month(local).groupby(_BOTH, sort=True)["kwh"].agg(_fsum)
    counts = _with_month(local).groupby(_BOTH, sort=True)["kwh"].size()
    per_day = daily_table.groupby(_BOTH, sort=True)
    present = per_day.size()
    complete = daily_table[daily_table["complete"]].groupby(_BOTH, sort=True)
    complete_days = complete.size()
    complete_kwh = complete["kwh"].agg(_fsum)
    rows: list[dict[str, object]] = []
    for suffix, dates in local.groupby("suffix", sort=True)["local_date"]:
        first, last = min(dates), max(dates)
        for period in pd.period_range(_month_of(first), _month_of(last), freq="M"):
            month = str(period)
            start, end = period.start_time.date(), period.end_time.date()
            span = [start + pd.Timedelta(days=offset).to_pytimedelta() for offset in range((end - start).days + 1)]
            inside = [day for day in span if first <= day <= last]
            expected = sum(day_minutes[day] // intervals[str(suffix)] for day in inside)
            key = (suffix, month)
            n_complete = int(complete_days.get(key, 0))
            rows.append(
                {
                    "suffix": suffix,
                    "month": month,
                    "days_present": int(present.get(key, 0)),
                    "days_in_month": len(span),
                    "complete_days": n_complete,
                    "covered": start > first and end < last,
                    "coverage": min(int(counts.get(key, 0)) / expected, 1.0) if expected else math.nan,
                    "kwh": float(energy.get(key, 0.0)),
                    "avg_daily_kwh": float(complete_kwh[key]) / n_complete if n_complete else math.nan,
                }
            )
    table = pd.DataFrame(rows)
    table = table.merge(base_load[[*_BOTH, "base_load_kw"]], on=_BOTH, how="left")
    table = table.merge(peak[[*_BOTH, "peak_demand_kw", "peak_demand_at", "load_factor"]], on=_BOTH, how="left")
    table["peak_demand_at"] = table["peak_demand_at"].astype("datetime64[us]")
    return table[list(kinds.MONTHLY_COLUMNS)].sort_values(_BOTH).reset_index(drop=True)


def reference_window(monthly_table: pd.DataFrame, reference_months: int) -> dict[str, ReferenceWindow]:
    """Each register's last `reference_months` covered months, with their local days, complete days and coverage."""
    found: dict[str, ReferenceWindow] = {}
    for suffix, rows in monthly_table[monthly_table["covered"]].groupby("suffix", sort=True):
        chosen = rows.sort_values("month").tail(reference_months)
        days = int(chosen["days_in_month"].sum())
        complete = int(chosen["complete_days"].sum())
        found[str(suffix)] = ReferenceWindow(tuple(chosen["month"]), days, complete, complete / days if days else None)
    for suffix in monthly_table["suffix"].unique():
        found.setdefault(str(suffix), ReferenceWindow((), 0, 0, None))
    return found


# §5.2 and §5.3 seasons and year on year


def seasonal(daily_table: pd.DataFrame, seasons: Mapping[str, Collection[int]]) -> pd.DataFrame:
    """Complete days by register, season and season year (P3 spec §5.2); a season that wraps the year is one season.

    A season wraps when it holds both December and January. Its January-onward run belongs to the year before, so a
    December-to-February season is labelled with the year of its December.
    """
    head = {label: _january_run(months) for label, months in seasons.items()}
    days = daily_table[daily_table["complete"]]
    stamps = pd.to_datetime(days["local_date"])
    earlier = [month in head[season] for month, season in zip(stamps.dt.month, days["season"], strict=True)]
    table = days.assign(season_year=stamps.dt.year - pd.Series(earlier, index=days.index, dtype="int64"))
    found = (
        table.groupby(["suffix", "season_year", "season"], sort=True)
        .agg(days=("kwh", "size"), kwh=("kwh", _fsum))
        .reset_index()
    )
    found["avg_daily_kwh"] = found["kwh"] / found["days"]
    return found[list(kinds.SEASONAL_COLUMNS)].reset_index(drop=True)


def _january_run(months: Collection[int]) -> frozenset[int]:
    """The months of a year-wrapping season that fall in the new calendar year (1, 2, … up to the first gap)."""
    held = frozenset(months)
    if 12 not in held or 1 not in held or len(held) == 12:
        return frozenset()
    run = 0
    while run + 1 in held:
        run += 1
    return frozenset(range(1, run + 1))


def yoy(monthly_table: pd.DataFrame) -> pd.DataFrame:
    """A month against the same calendar month a year earlier, where both are covered and have a complete day."""
    usable = monthly_table[monthly_table["covered"] & monthly_table["avg_daily_kwh"].notna()]
    entries = [
        (str(suffix), str(month), float(average))
        for suffix, month, average in zip(usable["suffix"], usable["month"], usable["avg_daily_kwh"], strict=True)
    ]
    by_key = {(suffix, month): average for suffix, month, average in entries}
    rows: list[dict[str, object]] = []
    for suffix, month, average in entries:
        prior = f"{int(month[:4]) - 1:04d}{month[4:]}"
        before = by_key.get((suffix, prior))
        if before is None:
            continue
        rows.append(
            {
                "suffix": suffix,
                "month": month,
                "prior_month": prior,
                "avg_daily_kwh": average,
                "prior_avg_daily_kwh": before,
                "change_pct": (average - before) / before * 100 if before else math.nan,
            }
        )
    return _frame(rows, kinds.YOY_COLUMNS, kinds.YOY_SORT)


def _frame(rows: list[dict[str, object]], columns: Sequence[str], sort: Sequence[str]) -> pd.DataFrame:
    table = pd.DataFrame(rows, columns=pd.Index(columns))
    return table.sort_values(list(sort)).reset_index(drop=True)


# §5.4 and §5.5 hour cells


def _hour_cells(local: pd.DataFrame) -> pd.DataFrame:
    """One row per register, local date and wall-clock hour: the cell's energy, row count and whether it is full."""
    table = local.assign(hour=local["local_start"].dt.hour.astype("int64"))
    cells = (
        table.groupby(["suffix", "local_date", "hour"], sort=True)
        .agg(kwh=("kwh", _fsum), rows=("kwh", "size"), interval_minutes=("interval_minutes", "first"))
        .reset_index()
    )
    cells["full"] = cells["rows"] == 60 // cells["interval_minutes"]
    return cells


def heatmap(local: pd.DataFrame) -> pd.DataFrame:
    """Energy per local date and clock hour; blank (`NaN`) for a cell that is not full (P3 spec §5.5)."""
    cells = _hour_cells(local)
    cells["kwh"] = cells["kwh"].where(cells["full"])
    return cells[list(kinds.HEATMAP_COLUMNS)].reset_index(drop=True)


def hourly_profile(
    local: pd.DataFrame,
    daily_table: pd.DataFrame,
    months: Scope,
    *,
    band_percentiles: tuple[int, int, int],
) -> pd.DataFrame:
    """Mean and percentile band of the full hour cells of complete days in `months`, by season, day type and hour."""
    cells = _hour_cells(local)
    cells = cells[cells["full"]]
    days = daily_table[daily_table["complete"]][["suffix", "local_date", "month", "season", "daytype"]]
    cells = cells.merge(days, on=["suffix", "local_date"], how="inner")
    cells = _in_scope(cells, months)
    low, middle, high = band_percentiles
    rows: list[dict[str, object]] = []
    for (suffix, season, daytype, hour), group in cells.groupby(["suffix", "season", "daytype", "hour"], sort=True):
        values = group["kwh"].to_numpy(dtype="float64")
        p_low, p_mid, p_high = np.percentile(values, [low, middle, high], method="linear")
        rows.append(
            {
                "suffix": suffix,
                "season": season,
                "daytype": daytype,
                "hour": int(hour),  # type: ignore[call-overload]
                "cells": len(values),
                "mean_kwh": math.fsum(values) / len(values),
                "p_low_kwh": float(p_low),
                "p_mid_kwh": float(p_mid),
                "p_high_kwh": float(p_high),
            }
        )
    return _frame(rows, kinds.HOURLY_PROFILE_COLUMNS, kinds.HOURLY_PROFILE_SORT)


# §5.6 time-of-use split


def tou_split(local: pd.DataFrame, window_sets: WindowSets) -> TouSplit:
    """Energy per register, local date and label under the window set in force on each date (P3 spec §5.6).

    The set in force on a date is the latest whose `effective_from` is on or before it. `bucket` labels each interval by
    its local start. An interval no window holds is counted in `unlabelled` and left out of `daily`.
    """
    stamps = pd.to_datetime(local["local_date"])
    index = np.zeros(len(local), dtype="int64")
    for position, (start, _) in enumerate(window_sets):
        if start is not None:
            index[(stamps >= pd.Timestamp(start)).to_numpy()] = position
    labels = pd.Series(np.full(len(local), None, dtype=object), index=local.index)
    for position, (_, windows) in enumerate(window_sets):
        chosen = index == position
        if chosen.any():
            labels[chosen] = bucket(local.loc[chosen, "local_start"], windows).to_numpy()
    labelled = labels.notna().to_numpy()
    table = local.loc[labelled, ["suffix", "local_date", "kwh"]].assign(
        label=labels[labelled].astype(str).to_numpy(), window_set=index[labelled]
    )
    found = (
        table.groupby(["suffix", "local_date", "label", "window_set"], sort=True)
        .agg(kwh=("kwh", _fsum), intervals=("kwh", "size"))
        .reset_index()
    )
    return TouSplit(found[list(kinds.TOU_DAILY_COLUMNS)].reset_index(drop=True), int((~labelled).sum()))


def tou_monthly(tou_daily: pd.DataFrame, daily_table: pd.DataFrame) -> pd.DataFrame:
    """Energy, share and complete-day average per register, month and label, and whether two window sets applied."""
    table = _with_month(tou_daily)
    complete = daily_table[daily_table["complete"]][["suffix", "local_date"]]
    on_complete = table.merge(complete, on=["suffix", "local_date"], how="inner")
    days_by_month: dict[Hashable, int] = daily_table[daily_table["complete"]].groupby(_BOTH, sort=True).size().to_dict()
    totals: dict[Hashable, float] = table.groupby(_BOTH, sort=True)["kwh"].agg(_fsum).to_dict()
    sets: dict[Hashable, int] = table.groupby(_BOTH, sort=True)["window_set"].nunique().to_dict()
    on_complete_kwh: dict[Hashable, float] = (
        on_complete.groupby([*_BOTH, "label"], sort=True)["kwh"].agg(_fsum).to_dict()
    )
    rows: list[dict[str, object]] = []
    for (suffix, month, label), group in table.groupby([*_BOTH, "label"], sort=True):
        energy = _fsum(group["kwh"])
        total = totals[(suffix, month)]
        complete_days = days_by_month.get((suffix, month), 0)
        rows.append(
            {
                "suffix": suffix,
                "month": month,
                "label": label,
                "kwh": energy,
                "share": energy / total if total else math.nan,
                "avg_daily_kwh": on_complete_kwh.get((suffix, month, label), 0.0) / complete_days
                if complete_days
                else math.nan,
                "mixed_windows": sets[(suffix, month)] > 1,
            }
        )
    return _frame(rows, kinds.TOU_MONTHLY_COLUMNS, kinds.TOU_MONTHLY_SORT)


# §5.7 base load, peak demand, load factor


def base_load(
    local: pd.DataFrame,
    daily_table: pd.DataFrame,
    *,
    window: Window,
    percentile: int,
    scope: Scope | None = None,
) -> pd.DataFrame:
    """The percentile of interval power (kW) inside the clock `window`, over complete local days.

    One value per register and month, or per register over the months of `scope`.
    """
    complete = daily_table[daily_table["complete"]][["suffix", "local_date"]]
    table = _with_month(local).merge(complete, on=["suffix", "local_date"], how="inner")
    table = table[_in_window(_minute_of_day(table["local_start"]), window)]
    if scope is not None:
        table = _in_scope(table, scope)
    table = table.assign(power=table["kwh"] * 60 / table["interval_minutes"])
    rows = [
        {
            **_key_fields(_keys(scope), key),
            "base_load_kw": float(np.percentile(group["power"].to_numpy(dtype="float64"), percentile, method="linear")),
        }
        for key, group in table.groupby(_keys(scope), sort=True)
    ]
    return _frame(rows, [*_keys(scope), "base_load_kw"], _keys(scope))


def peak_demand(local: pd.DataFrame, *, demand_minutes: int, scope: Scope | None = None) -> pd.DataFrame:
    """The largest full block's power (kW), where it starts, and the load factor, per register and month or scope.

    Blocks are `demand_minutes` aligned to the local clock. The UTC offset is part of a block's identity, so a repeated
    daylight-saving hour gives one block per pass, and a block is full when it holds `demand_minutes / interval_minutes`
    rows. The load factor is the mean full-block power over the peak, `NaN` when the peak is not positive. An interval
    length that does not divide `demand_minutes` is `E_INVALID_INPUT` (`details.field: "analysis.demand_minutes"`).
    """
    if any(demand_minutes % length for length in _interval_by_suffix(local).values()):
        raise InvalidInputError(
            "The demand block must be a whole number of meter intervals.", field="analysis.demand_minutes"
        )
    table = local.assign(block=local["local_start"].dt.floor(f"{demand_minutes}min"))
    blocks = (
        table.groupby(["suffix", "block", "utc_offset_minutes"], sort=True)
        .agg(kwh=("kwh", _fsum), rows=("kwh", "size"), interval_minutes=("interval_minutes", "first"))
        .reset_index()
    )
    blocks = blocks[blocks["rows"] == demand_minutes // blocks["interval_minutes"]]
    blocks = blocks.assign(month=blocks["block"].dt.strftime("%Y-%m"), kw=blocks["kwh"] * 60 / demand_minutes)
    if scope is not None:
        blocks = _in_scope(blocks, scope)
    rows: list[dict[str, object]] = []
    for key, group in blocks.groupby(_keys(scope), sort=True):
        peak = float(group["kw"].max())
        first = group[group["kw"] == peak].sort_values("block").iloc[0]
        mean = _fsum(group["kw"]) / len(group)
        rows.append(
            {
                **_key_fields(_keys(scope), key),
                "peak_demand_kw": peak,
                "peak_demand_at": first["block"],
                "load_factor": mean / peak if peak > 0 else math.nan,
            }
        )
    columns = [*_keys(scope), "peak_demand_kw", "peak_demand_at", "load_factor"]
    found = _frame(rows, columns, _keys(scope))
    found["peak_demand_at"] = found["peak_demand_at"].astype("datetime64[us]")
    return found


def _key_fields(names: Sequence[str], key: object) -> dict[str, object]:
    values = key if isinstance(key, tuple) else (key,)
    return dict(zip(names, values, strict=True))


# §5.8 data quality


def quality_monthly(local: pd.DataFrame) -> pd.DataFrame:
    """Intervals and energy per register, local month and quality class, with their shares of the month."""
    table = _with_month(local).assign(quality_class=local["quality_flag"].map(quality_class))
    found = (
        table.groupby([*_BOTH, "quality_class"], sort=True)
        .agg(intervals=("kwh", "size"), kwh=("kwh", _fsum))
        .reset_index()
    )
    rows = []
    month_intervals: dict[Hashable, int] = found.groupby(_BOTH, sort=True)["intervals"].sum().to_dict()
    month_kwh: dict[Hashable, float] = found.groupby(_BOTH, sort=True)["kwh"].agg(_fsum).to_dict()
    for suffix, month, name, count, energy in zip(
        found["suffix"], found["month"], found["quality_class"], found["intervals"], found["kwh"], strict=True
    ):
        rows.append(
            {
                "suffix": suffix,
                "month": month,
                "quality_class": name,
                "intervals": int(count),
                "kwh": float(energy),
                "share_intervals": count / month_intervals[(suffix, month)],
                "share_kwh": energy / month_kwh[(suffix, month)] if month_kwh[(suffix, month)] else math.nan,
            }
        )
    return _frame(rows, kinds.QUALITY_MONTHLY_COLUMNS, kinds.QUALITY_MONTHLY_SORT)


# the reference window's headline values


def window_avg_daily_kwh(daily_table: pd.DataFrame, months: Scope) -> dict[str, float | None]:
    """Each register's mean energy per complete day over `months`; `None` when it has none."""
    days = _in_scope(daily_table[daily_table["complete"]], months)
    found: dict[str, float | None] = {str(suffix): None for suffix in months}
    for suffix, group in days.groupby("suffix", sort=True):
        found[str(suffix)] = _fsum(group["kwh"]) / len(group)
    return found


def window_tou_shares(tou_daily: pd.DataFrame, months: Scope) -> dict[str, dict[str, float]]:
    """Each register's share of energy by label over `months` (empty when there is none)."""
    table = _in_scope(_with_month(tou_daily), months)
    found: dict[str, dict[str, float]] = {str(suffix): {} for suffix in months}
    for suffix, group in table.groupby("suffix", sort=True):
        total = _fsum(group["kwh"])
        found[str(suffix)] = {
            str(label): (_fsum(part["kwh"]) / total if total else math.nan)
            for label, part in group.groupby("label", sort=True)
        }
    return found


def dst_day_counts(daily_table: pd.DataFrame, day_minutes: Mapping[date, int]) -> tuple[int, int]:
    """How many distinct short (23-hour) and long (25-hour) local dates the data holds, over all registers."""
    lengths = [day_minutes[day] for day in sorted(set(daily_table["local_date"]))]
    return sum(length < 1440 for length in lengths), sum(length > 1440 for length in lengths)
