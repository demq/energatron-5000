"""The `usage_profile` stage (P3 spec §8): the local interval table turned into the usage marts.

It reads `elec_interval_local` and nothing else, keeps the consumption registers (suffix starting with `E`), and writes
nine tables and a `summary.json` (kind `usage_summary`) into `data/marts/usage_profile/`. The stage only wires: every
number comes from the pure functions in `core.usage`, and the zone is used for one thing, the length of each local date
(`local_day_minutes`).

Seven checks follow the metrics. A register with more than one interval length stops the stage before any table, as the
`localise` gate does, because every metric assumes one. `reference_window` fails the stage when a register has fewer
than `analysis.reference_months` covered months or too few complete days in them (P3 spec §17 Q4). Check details hold
counts, never a value. Two warnings do not fail it: months in which two TOU window sets applied, and dates inside a
covered month that are not complete.
"""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from itertools import pairwise
from pathlib import Path
from typing import Final, cast

import pandas as pd
import pyarrow as pa

from energatron.core.canonical import JSONValue
from energatron.core.errors import InternalError, InvalidInputError, WarningCode
from energatron.core.models import usage as kinds
from energatron.core.models.meter import LOCAL_KIND, LOCAL_VERSION
from energatron.core.models.params import AnalysisParams
from energatron.core.models.usage import DstDays, SuffixSummary, UsageSummary, WindowSetUsed
from energatron.core.timebase import local_day_minutes, parse_window, parse_windows
from energatron.core.usage import (
    SUM_TOLERANCE_KWH,
    ReferenceWindow,
    Scope,
    WindowSets,
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
    split_registers,
    tou_monthly,
    tou_split,
    window_avg_daily_kwh,
    window_tou_shares,
    yoy,
)
from energatron.services.files import write_json, write_parquet
from energatron.services.stages.runner import StageRun, StageSpec
from energatron.services.stages.tables import (
    LOCAL_SCHEMA,
    USAGE_DAILY_SCHEMA,
    USAGE_HEATMAP_SCHEMA,
    USAGE_HOURLY_PROFILE_SCHEMA,
    USAGE_MONTHLY_SCHEMA,
    USAGE_QUALITY_MONTHLY_SCHEMA,
    USAGE_SEASONAL_SCHEMA,
    USAGE_TOU_DAILY_SCHEMA,
    USAGE_TOU_MONTHLY_SCHEMA,
    USAGE_YOY_SCHEMA,
    load_table,
)

INTERVALS_FILENAME: Final = "intervals.parquet"
SUMMARY_FILENAME: Final = "summary.json"
_HOURS: Final = frozenset(range(24))


@dataclass(frozen=True)
class _Kind:
    """How one table is written: its file stem, kind, version, pyarrow schema and sort key."""

    stem: str
    kind: str
    version: int
    schema: pa.Schema
    sort: tuple[str, ...]


_KINDS: Final = (
    _Kind("daily", kinds.DAILY_KIND, kinds.DAILY_VERSION, USAGE_DAILY_SCHEMA, kinds.DAILY_SORT),
    _Kind("monthly", kinds.MONTHLY_KIND, kinds.MONTHLY_VERSION, USAGE_MONTHLY_SCHEMA, kinds.MONTHLY_SORT),
    _Kind("seasonal", kinds.SEASONAL_KIND, kinds.SEASONAL_VERSION, USAGE_SEASONAL_SCHEMA, kinds.SEASONAL_SORT),
    _Kind("yoy", kinds.YOY_KIND, kinds.YOY_VERSION, USAGE_YOY_SCHEMA, kinds.YOY_SORT),
    _Kind(
        "hourly_profile",
        kinds.HOURLY_PROFILE_KIND,
        kinds.HOURLY_PROFILE_VERSION,
        USAGE_HOURLY_PROFILE_SCHEMA,
        kinds.HOURLY_PROFILE_SORT,
    ),
    _Kind("heatmap", kinds.HEATMAP_KIND, kinds.HEATMAP_VERSION, USAGE_HEATMAP_SCHEMA, kinds.HEATMAP_SORT),
    _Kind("tou_daily", kinds.TOU_DAILY_KIND, kinds.TOU_DAILY_VERSION, USAGE_TOU_DAILY_SCHEMA, kinds.TOU_DAILY_SORT),
    _Kind(
        "tou_monthly",
        kinds.TOU_MONTHLY_KIND,
        kinds.TOU_MONTHLY_VERSION,
        USAGE_TOU_MONTHLY_SCHEMA,
        kinds.TOU_MONTHLY_SORT,
    ),
    _Kind(
        "quality_monthly",
        kinds.QUALITY_MONTHLY_KIND,
        kinds.QUALITY_MONTHLY_VERSION,
        USAGE_QUALITY_MONTHLY_SCHEMA,
        kinds.QUALITY_MONTHLY_SORT,
    ),
)


def _run(run: StageRun) -> None:
    zone = run.zone(_text(run.params, "meter.local_tz"))
    analysis = AnalysisParams.model_validate(run.params.get("analysis"))
    sets = _window_sets(analysis)

    local = _load(run.input("interval") / INTERVALS_FILENAME)
    consumption, excluded = split_registers(local)
    if consumption.empty:
        raise InvalidInputError("The table holds no consumption register.", input="interval")

    lengths = consumption.groupby("suffix")["interval_minutes"].nunique()
    mixed_lengths = int((lengths > 1).sum())
    run.check("one_interval_length", mixed_lengths == 0, registers=mixed_lengths)
    if mixed_lengths:
        return

    first, last = consumption["local_date"].min(), consumption["local_date"].max()
    day_minutes = {
        first + timedelta(days=offset): local_day_minutes(first + timedelta(days=offset), zone)
        for offset in range((last - first).days + 1)
    }

    per_day = daily(consumption, day_minutes, weekend=analysis.weekend, seasons=analysis.seasons)
    window = parse_window(f"{analysis.base_load.start}-{analysis.base_load.end}")
    percentile = analysis.base_load.percentile
    per_month = monthly(
        consumption,
        per_day,
        day_minutes,
        base_load=base_load(consumption, per_day, window=window, percentile=percentile),
        peak=peak_demand(consumption, demand_minutes=analysis.demand_minutes),
    )
    windows = reference_window(per_month, analysis.reference_months)
    scope: Scope = {suffix: set(found.months) for suffix, found in windows.items()}
    split = tou_split(consumption, sets)
    tou_by_month = tou_monthly(split.daily, per_day)
    profile = hourly_profile(consumption, per_day, scope, band_percentiles=analysis.band_percentiles)
    tables = {
        "daily": per_day,
        "monthly": per_month,
        "seasonal": seasonal(per_day, analysis.seasons),
        "yoy": yoy(per_month),
        "hourly_profile": profile,
        "heatmap": heatmap(consumption),
        "tou_daily": split.daily,
        "tou_monthly": tou_by_month,
        "quality_monthly": quality_monthly(consumption),
    }

    outcomes = _checks(run, analysis, consumption, tables, split.unlabelled, split.daily, sets, windows, scope)

    out = run.output("profile")
    for kind in _KINDS:
        write_parquet(
            tables[kind.stem],
            out / f"{kind.stem}.parquet",
            kind=kind.kind,
            schema_version=kind.version,
            sort_by=kind.sort,
            schema=kind.schema,
        )
    averages = window_avg_daily_kwh(per_day, scope)
    shares = window_tou_shares(split.daily, scope)
    base = base_load(consumption, per_day, window=window, percentile=percentile, scope=scope)
    peak = peak_demand(consumption, demand_minutes=analysis.demand_minutes, scope=scope)
    short, long = dst_day_counts(per_day, day_minutes)
    summary = UsageSummary(
        suffixes={
            suffix: _suffix_summary(
                consumption, per_month, found, averages[suffix], shares[suffix], base, peak, suffix=suffix
            )
            for suffix, found in windows.items()
        },
        excluded_suffixes=excluded,
        window_sets_used=_sets_used(analysis, first, last),
        band_percentiles=analysis.band_percentiles,
        dst_days=DstDays(short=short, long=long),
        checks=outcomes,
    )
    write_json(out / SUMMARY_FILENAME, summary.model_dump(mode="json"))

    _warn(run, per_month, tou_by_month)


# input


def _text(params: Mapping[str, object], key: str) -> str:
    value = params.get(key)
    if not isinstance(value, str):
        raise InternalError(f"usage_profile got no {key}.")
    return value


def _load(path: Path) -> pd.DataFrame:
    """The local table; one that is missing, of another kind or with a row `localise` could not place →
    `E_INVALID_INPUT`, naming the input and never a value."""
    table = load_table(path, LOCAL_SCHEMA, LOCAL_KIND, LOCAL_VERSION, input="interval").to_pandas()
    if bool(table["local_start"].isna().any() or table["local_date"].isna().any()):
        raise InvalidInputError("The table holds rows that were never placed on the local clock.", input="interval")
    return table


def _window_sets(analysis: AnalysisParams) -> WindowSets:
    sets: list[tuple[date | None, Mapping[str, Sequence[object]]]] = []
    for found in analysis.tou.window_sets:
        if found.windows is None:
            raise InternalError("usage_profile got no analysis.tou.window_sets[0].windows.")
        sets.append((found.effective_from, parse_windows(found.windows)))
    return cast("WindowSets", sets)


def _sets_used(analysis: AnalysisParams, first: date, last: date) -> list[WindowSetUsed]:
    """The window sets in force on some date of the data: a set runs from its start until the next set's."""
    found = analysis.tou.window_sets
    used: list[WindowSetUsed] = []
    for position, item in enumerate(found):
        following = found[position + 1].effective_from if position + 1 < len(found) else None
        starts_before_end = item.effective_from is None or item.effective_from <= last
        ends_after_start = following is None or following > first
        if starts_before_end and ends_after_start and item.windows is not None:
            used.append(
                WindowSetUsed(
                    effective_from=item.effective_from,
                    windows={label: list(texts) for label, texts in item.windows.items()},
                )
            )
    return used


# checks


def resolves_every_date(starts: Sequence[date | None]) -> bool:
    """Whether every local date falls in some window set: the first has no start and the others' starts increase."""
    if not starts or starts[0] is not None:
        return False
    later = starts[1:]
    if any(start is None for start in later):
        return False
    dates = cast("list[date]", later)
    return all(before < after for before, after in pairwise(dates))


def _checks(
    run: StageRun,
    analysis: AnalysisParams,
    consumption: pd.DataFrame,
    tables: Mapping[str, pd.DataFrame],
    unlabelled: int,
    tou_daily: pd.DataFrame,
    sets: WindowSets,
    windows: Mapping[str, ReferenceWindow],
    scope: Scope,
) -> dict[str, bool]:
    """Record the checks after `one_interval_length`, and return every check's outcome by name."""
    outcomes: dict[str, bool] = {"one_interval_length": True}

    def record(name: str, passed: bool, **details: JSONValue) -> None:
        outcomes[name] = passed
        run.check(name, passed, **details)

    per_day = tables["daily"]
    record(
        "daily_conserved",
        abs(_fsum(per_day["kwh"]) - _fsum(consumption["kwh"])) <= SUM_TOLERANCE_KWH,
        days=len(per_day),
    )
    duplicated = [kind.stem for kind in _KINDS if bool(tables[kind.stem].duplicated(list(kind.sort)).any())]
    record("keys_unique", not duplicated, tables=duplicated)
    mismatched = sum(
        abs(_fsum(tou_daily.loc[tou_daily["suffix"] == suffix, "kwh"]) - _fsum(part["kwh"])) > SUM_TOLERANCE_KWH
        for suffix, part in per_day.groupby("suffix", sort=True)
    )
    record("tou_conserved", unlabelled == 0 and mismatched == 0, unlabelled=unlabelled, mismatched=int(mismatched))
    record("windows_resolved", resolves_every_date([start for start, _ in sets]), window_sets=len(sets))
    months_found = {suffix: len(found.months) for suffix, found in windows.items()}
    coverage = {suffix: found.coverage for suffix, found in windows.items()}
    enough = all(
        months_found[suffix] == analysis.reference_months and (coverage[suffix] or 0.0) >= analysis.coverage_threshold
        for suffix in windows
    )
    record("reference_window", enough, months_found=months_found, coverage=coverage)
    incomplete = _incomplete_profiles(per_day, tables["hourly_profile"], scope)
    record("profile_cells", incomplete == 0, incomplete=incomplete)
    return outcomes


def _fsum(values: pd.Series) -> float:
    return math.fsum(values.to_numpy(dtype="float64"))


def _incomplete_profiles(per_day: pd.DataFrame, profile: pd.DataFrame, scope: Scope) -> int:
    """How many `(register, season, day type)` combinations with a complete day in the window lack an hour."""
    complete = per_day[per_day["complete"]]
    wanted = {
        (str(suffix), str(season), str(daytype))
        for suffix, month, season, daytype in zip(
            complete["suffix"], complete["month"], complete["season"], complete["daytype"], strict=True
        )
        if month in scope.get(str(suffix), ())
    }
    hours: dict[tuple[str, str, str], set[int]] = {}
    for suffix, season, daytype, hour in zip(
        profile["suffix"], profile["season"], profile["daytype"], profile["hour"], strict=True
    ):
        hours.setdefault((str(suffix), str(season), str(daytype)), set()).add(int(hour))
    return sum(1 for key in wanted if hours.get(key, set()) != _HOURS)


# warnings


def _warn(run: StageRun, per_month: pd.DataFrame, tou_by_month: pd.DataFrame) -> None:
    mixed = sorted({str(month) for month in tou_by_month.loc[tou_by_month["mixed_windows"], "month"]})
    if mixed:
        run.warn(
            WarningCode.W_USAGE_MIXED_WINDOWS,
            "More than one TOU window set applied inside some months.",
            months=mixed,
        )
    covered = per_month[per_month["covered"]]
    incomplete = int((covered["days_in_month"] - covered["complete_days"]).sum())
    if incomplete:
        run.warn(
            WarningCode.W_USAGE_PARTIAL_DAYS,
            "Some local dates inside a covered month are missing or not complete.",
            days=incomplete,
        )


# the summary


def _number(value: object) -> float | None:
    """A float, or `None` for a missing value (`NaN`)."""
    if value is None:
        return None
    number = float(cast("float", value))
    return number if math.isfinite(number) else None


def _scoped(table: pd.DataFrame, suffix: str, column: str) -> float | None:
    rows = table.loc[table["suffix"] == suffix, column]
    return _number(rows.iloc[0]) if len(rows) else None


def _suffix_summary(
    consumption: pd.DataFrame,
    per_month: pd.DataFrame,
    window: ReferenceWindow,
    average: float | None,
    shares: Mapping[str, float],
    base: pd.DataFrame,
    peak: pd.DataFrame,
    *,
    suffix: str,
) -> SuffixSummary:
    dates = consumption.loc[consumption["suffix"] == suffix, "local_date"]
    months = per_month[(per_month["suffix"] == suffix) & per_month["covered"]]
    return SuffixSummary(
        first_date=dates.min(),
        last_date=dates.max(),
        covered_months=len(months),
        window_start=window.months[0] if window.months else None,
        window_end=window.months[-1] if window.months else None,
        window_days=window.days,
        window_complete_days=window.complete_days,
        window_coverage=window.coverage,
        avg_daily_kwh=_number(average),
        base_load_kw=_scoped(base, suffix, "base_load_kw"),
        peak_demand_kw=_scoped(peak, suffix, "peak_demand_kw"),
        load_factor=_scoped(peak, suffix, "load_factor"),
        tou_shares=dict(shares) if all(math.isfinite(share) for share in shares.values()) else {},
    )


SPEC: Final = StageSpec(
    name="usage_profile",
    inputs={"interval": "data/interim/elec_interval_local"},
    params=("meter.local_tz", "analysis"),
    outputs={"profile": "data/marts/usage_profile"},
    impl=_run,
)
