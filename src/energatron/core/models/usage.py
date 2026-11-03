"""The usage profile's table kinds and summary (P3 spec §5, §8, §11).

Parquet kinds are metadata only (D1 §5): a file carries `energatron.kind` and `energatron.schema_version`, and the stage
that writes it owns the column types, because core holds no pyarrow. This module names the kinds, their versions, their
columns and their sort keys, so `usage_profile`, `usage_report` and the renderer agree on them. A value that is "null"
in the spec is a float `NaN` in memory (a `NaT` for `peak_demand_at`) and a Parquet null on disk.

No table has an identifier column: the keys are the register `suffix`, local dates and months, and labels.
"""

from datetime import date
from typing import ClassVar, Final, Literal

from pydantic import NonNegativeInt

from energatron.core.models._common import FrozenModel
from energatron.core.persisted import Persisted

DAILY_KIND: Final = "usage_daily"
DAILY_VERSION: Final = 1
DAILY_COLUMNS: Final = (
    "suffix",
    "local_date",
    "month",
    "season",
    "daytype",
    "kwh",
    "intervals",
    "expected",
    "coverage",
    "complete",
)
"""`daily.parquet`: one row per register and local date with at least one interval (P3 spec §5.1)."""
DAILY_SORT: Final = ("suffix", "local_date")

MONTHLY_KIND: Final = "usage_monthly"
MONTHLY_VERSION: Final = 1
MONTHLY_COLUMNS: Final = (
    "suffix",
    "month",
    "days_present",
    "days_in_month",
    "complete_days",
    "covered",
    "coverage",
    "kwh",
    "avg_daily_kwh",
    "base_load_kw",
    "peak_demand_kw",
    "peak_demand_at",
    "load_factor",
)
"""`monthly.parquet`: one row per register and calendar month, first to last month of its data (§5.1, §5.7)."""
MONTHLY_SORT: Final = ("suffix", "month")

SEASONAL_KIND: Final = "usage_seasonal"
SEASONAL_VERSION: Final = 1
SEASONAL_COLUMNS: Final = ("suffix", "season_year", "season", "days", "kwh", "avg_daily_kwh")
"""`seasonal.parquet`: complete days by register and season (§5.2)."""
SEASONAL_SORT: Final = ("suffix", "season_year", "season")

YOY_KIND: Final = "usage_yoy"
YOY_VERSION: Final = 1
YOY_COLUMNS: Final = ("suffix", "month", "prior_month", "avg_daily_kwh", "prior_avg_daily_kwh", "change_pct")
"""`yoy.parquet`: a month against the same calendar month a year earlier (§5.3)."""
YOY_SORT: Final = ("suffix", "month")

HOURLY_PROFILE_KIND: Final = "usage_hourly_profile"
HOURLY_PROFILE_VERSION: Final = 1
HOURLY_PROFILE_COLUMNS: Final = (
    "suffix",
    "season",
    "daytype",
    "hour",
    "cells",
    "mean_kwh",
    "p_low_kwh",
    "p_mid_kwh",
    "p_high_kwh",
)
"""`hourly_profile.parquet`: the reference window's hour cells by season, day type and hour (§5.4)."""
HOURLY_PROFILE_SORT: Final = ("suffix", "season", "daytype", "hour")

HEATMAP_KIND: Final = "usage_heatmap"
HEATMAP_VERSION: Final = 1
HEATMAP_COLUMNS: Final = ("suffix", "local_date", "hour", "kwh")
"""`heatmap.parquet`: energy per local date and clock hour; blank for a cell that is not full (§5.5)."""
HEATMAP_SORT: Final = ("suffix", "local_date", "hour")

TOU_DAILY_KIND: Final = "usage_tou_daily"
TOU_DAILY_VERSION: Final = 1
TOU_DAILY_COLUMNS: Final = ("suffix", "local_date", "label", "kwh", "intervals", "window_set")
"""`tou_daily.parquet`: energy per local date and time-of-use label (§5.6)."""
TOU_DAILY_SORT: Final = ("suffix", "local_date", "label")

TOU_MONTHLY_KIND: Final = "usage_tou_monthly"
TOU_MONTHLY_VERSION: Final = 1
TOU_MONTHLY_COLUMNS: Final = ("suffix", "month", "label", "kwh", "share", "avg_daily_kwh", "mixed_windows")
"""`tou_monthly.parquet`: energy per month and time-of-use label (§5.6)."""
TOU_MONTHLY_SORT: Final = ("suffix", "month", "label")

QUALITY_MONTHLY_KIND: Final = "usage_quality_monthly"
QUALITY_MONTHLY_VERSION: Final = 1
QUALITY_MONTHLY_COLUMNS: Final = (
    "suffix",
    "month",
    "quality_class",
    "intervals",
    "kwh",
    "share_intervals",
    "share_kwh",
)
"""`quality_monthly.parquet`: intervals and energy per local month and quality class (§5.8)."""
QUALITY_MONTHLY_SORT: Final = ("suffix", "month", "quality_class")

QUALITY_CLASSES: Final = ("actual", "estimated", "substituted", "other")
"""The values of `quality_class`, in the order a report lists them."""


class SuffixSummary(FrozenModel):
    """One register's span and reference-window headline values (P3 spec §8).

    `window_*` are `None` and `window_days` is 0 when the register has no covered month; the headline values are `None`
    when the window holds nothing they can be computed from. `tou_shares` is the window's share of energy by label.
    """

    first_date: date
    last_date: date
    covered_months: NonNegativeInt
    window_start: str | None  # YYYY-MM
    window_end: str | None  # YYYY-MM
    window_days: NonNegativeInt
    window_complete_days: NonNegativeInt
    window_coverage: float | None
    avg_daily_kwh: float | None
    base_load_kw: float | None
    peak_demand_kw: float | None
    load_factor: float | None
    tou_shares: dict[str, float]


class DstDays(FrozenModel):
    """How many short (23-hour) and long (25-hour) local days the data holds."""

    short: NonNegativeInt
    long: NonNegativeInt


class WindowSetUsed(FrozenModel):
    """A TOU window set in force somewhere in the data, as copied from `params` (public tariff windows only)."""

    effective_from: date | None
    windows: dict[str, list[str]]


class UsageSummary(Persisted):
    """`usage_summary` v1: `summary.json` of `usage_profile` (P3 spec §8). Values only, no free text, no identifier.

    `suffixes` is keyed by register suffix. `excluded_suffixes` are the registers left out because they are not
    consumption registers. `window_sets_used` lists the sets in force. `checks` mirrors the stage's checks by name.
    """

    KIND: ClassVar[str] = "usage_summary"
    SCHEMA_VERSION: ClassVar[int] = 1
    kind: Literal["usage_summary"] = "usage_summary"
    schema_version: Literal[1] = 1

    suffixes: dict[str, SuffixSummary]
    excluded_suffixes: list[str]
    window_sets_used: list[WindowSetUsed]
    band_percentiles: tuple[int, int, int]
    dst_days: DstDays
    checks: dict[str, bool]
