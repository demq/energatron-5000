"""The meter pipeline's table kinds (P2 spec §7, §12).

Parquet kinds are metadata only (D1 §5): a file carries `energatron.kind` and `energatron.schema_version`, and the
stage that writes it owns the column types, because core holds no pyarrow. This module names the kinds, their
versions and their columns, so the stages that share a table agree on them. Each P2 stage sub-task adds its kinds.
The JSON kinds of the reports are `Persisted` models and join this module with their stages (`MergeSummary` and
`MeterReport` so far).
"""

from datetime import date
from typing import ClassVar, Final, Literal

from pydantic import NonNegativeInt

from energatron.core.models._common import DatasetId, FrozenModel
from energatron.core.persisted import Persisted

STAGED_KIND: Final = "elec_interval_staged"
STAGED_VERSION: Final = 1
STAGED_COLUMNS: Final = (
    "suffix",
    "meter_date",
    "meter_start",
    "interval_minutes",
    "kwh",
    "quality",
    "quality_flag",
    "reason_code",
    "update_dt",
    "dataset_id",
)
"""`<dataset_id>.parquet` of `parse_nem12`, sorted by `STAGED_SORT` (P2 spec §7.1)."""
STAGED_SORT: Final = ("suffix", "meter_start")

INDEX_KIND: Final = "elec_interval_index"
INDEX_VERSION: Final = 1
INDEX_COLUMNS: Final = (
    "dataset_id",
    "precedence",
    "is_current",
    "read_ok",
    "interval_minutes",
    "suffixes",
    "days",
    "coverage_start",
    "coverage_end",
)
"""`index.parquet` of `parse_nem12`: one row per catalogued dataset, sorted by precedence (P2 spec §7.1)."""
INDEX_SORT: Final = ("precedence",)

INTERVALS_KIND: Final = "elec_interval_nem"
INTERVALS_VERSION: Final = 1
INTERVALS_COLUMNS: Final = STAGED_COLUMNS
"""`intervals.parquet` of `merge_interval`: the kept days, with the staged columns (P2 spec §7.2)."""
INTERVALS_SORT: Final = STAGED_SORT

CONFLICTS_KIND: Final = "merge_conflicts"
CONFLICTS_VERSION: Final = 1
CONFLICTS_COLUMNS: Final = (
    "suffix",
    "meter_date",
    "kept_dataset_id",
    "dropped_dataset_id",
    "kept_update_dt",
    "dropped_update_dt",
    "intervals_differing",
    "quality_differing",
    "kept_kwh",
    "dropped_kwh",
    "kept_flags",
    "dropped_flags",
)
"""`conflicts.parquet` of `merge_interval`: one row per dropped candidate day that differs from the kept day."""
CONFLICTS_SORT: Final = ("suffix", "meter_date", "dropped_dataset_id")

COMPARE_KIND: Final = "meter_summary_compare"
COMPARE_VERSION: Final = 1
COMPARE_COLUMNS: Final = (
    "dataset_id",
    "period_start",
    "period_end",
    "summary_quality",
    "summary_kwh",
    "meter_kwh",
    "diff_kwh",
    "status",
    "summary_disagreement",
)
"""`summary_compare.parquet` of `validate_meter`: one row per SUMMARY period (P2 spec §8)."""
COMPARE_SORT: Final = ("period_start", "period_end")

GAPS_KIND: Final = "meter_gaps"
GAPS_VERSION: Final = 1
GAPS_COLUMNS: Final = ("suffix", "gap_start", "gap_end", "days")
"""`gaps.parquet` of `validate_meter`: each run of missing meter dates inside a register's span."""
GAPS_SORT: Final = ("suffix", "gap_start")

QUALITY_KIND: Final = "meter_quality_monthly"
QUALITY_VERSION: Final = 1
QUALITY_COLUMNS: Final = ("suffix", "month", "quality_flag", "intervals", "kwh", "share")
"""`quality_monthly.parquet` of `validate_meter`: intervals, energy and share per register, meter month and flag."""
QUALITY_SORT: Final = ("suffix", "month", "quality_flag")


class SkippedDatasets(FrozenModel):
    """The catalogued datasets `merge_interval` did not use, by reason, each list highest precedence first."""

    superseded: list[DatasetId]  # some record's `supersedes` names them
    unread: list[DatasetId]  # current, but `parse_nem12` could not read them


class MergeSummary(Persisted):
    """`merge_summary` v1: `summary.json` of `merge_interval` (P2 spec §7.2). Counts and dataset IDs only.

    A candidate is a current dataset that was read. `datasets_used` lists the candidates, highest precedence first.
    `days` counts the distinct meter dates in the merged table, per register suffix. `overlap_days` counts the
    `(suffix, meter date)` pairs more than one candidate holds. A conflict is a dropped candidate's day whose values or
    qualities differ from the kept day: `conflict_days` counts the pairs that have one, `conflict_intervals` the
    intervals that differ, summed over the dropped candidates. `same_update_conflicts` counts the conflict days where a
    dropped day has the kept day's `UpdateDateTime`, so only precedence chose between them.
    """

    KIND: ClassVar[str] = "merge_summary"
    SCHEMA_VERSION: ClassVar[int] = 1
    kind: Literal["merge_summary"] = "merge_summary"
    schema_version: Literal[1] = 1

    datasets_used: list[DatasetId]
    datasets_skipped: SkippedDatasets
    days: dict[str, NonNegativeInt]
    overlap_days: NonNegativeInt
    conflict_days: NonNegativeInt
    conflict_intervals: NonNegativeInt
    same_update_conflicts: NonNegativeInt


class StatusCounts(FrozenModel):
    """How many SUMMARY periods ended in each status of `summary_compare.parquet`."""

    match: NonNegativeInt
    mismatch: NonNegativeInt
    not_covered: NonNegativeInt


class MeterReport(Persisted):
    """`meter_report` v1: `report.json` of `validate_meter` (P2 spec §8). Counts and dates only, no energy figure.

    `summary_rows` counts the SUMMARY periods by status. `gap_days` is the number of missing meter dates inside the
    registers' spans, summed over registers. `intervals`, `first_date` and `last_date` are per register suffix.
    `a_share` is the share of all merged intervals whose quality flag is `A`; `null` when there are none.
    """

    KIND: ClassVar[str] = "meter_report"
    SCHEMA_VERSION: ClassVar[int] = 1
    kind: Literal["meter_report"] = "meter_report"
    schema_version: Literal[1] = 1

    summary_rows: StatusCounts
    gap_days: NonNegativeInt
    intervals: dict[str, NonNegativeInt]
    first_date: dict[str, date]
    last_date: dict[str, date]
    a_share: float | None
