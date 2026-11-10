"""The pyarrow schemas of the interval tables that more than one stage reads or writes (P2 spec §7), and the reader
that checks a table against one.

Core holds no pyarrow (D1 §3), and a stage may not import another stage (D1 §11.1), so the schemas that
`parse_nem12` writes and `merge_interval` reads and writes again live here, in the module set of every stage that
shares them. So do `localise`'s table, which `usage_profile` reads, and the nine tables of `usage_profile`, which
`usage_report` reads, and the P4 offer and cost tables. Column names, kinds and versions are in `core.models.meter`,
`core.models.usage` and `core.models.costs`.
"""

from dataclasses import dataclass
from datetime import UTC
from pathlib import Path
from typing import Final

import pyarrow as pa
import pyarrow.parquet as pq

from energatron.core.errors import InvalidInputError
from energatron.core.models import usage

STAGED_SCHEMA: Final = pa.schema(
    [
        ("suffix", pa.string()),
        ("meter_date", pa.date32()),
        ("meter_start", pa.timestamp("us")),
        ("interval_minutes", pa.int16()),
        ("kwh", pa.float64()),
        ("quality", pa.string()),
        ("quality_flag", pa.string()),
        ("reason_code", pa.int16()),
        ("update_dt", pa.timestamp("us")),
        ("dataset_id", pa.string()),
    ]
)
"""`<dataset_id>.parquet` of `parse_nem12`, and `intervals.parquet` of `merge_interval`."""
INDEX_SCHEMA: Final = pa.schema(
    [
        ("dataset_id", pa.string()),
        ("precedence", pa.int32()),
        ("is_current", pa.bool_()),
        ("read_ok", pa.bool_()),
        ("interval_minutes", pa.int16()),
        ("suffixes", pa.list_(pa.string())),
        ("days", pa.int32()),
        ("coverage_start", pa.date32()),
        ("coverage_end", pa.date32()),
    ]
)
"""`index.parquet` of `parse_nem12`."""


LOCAL_SCHEMA: Final = pa.schema(
    [
        ("suffix", pa.string()),
        ("utc_start", pa.timestamp("us", tz=UTC)),
        ("local_start", pa.timestamp("us")),
        ("local_date", pa.date32()),
        ("utc_offset_minutes", pa.int16()),
        ("meter_start", pa.timestamp("us")),
        ("meter_date", pa.date32()),
        ("interval_minutes", pa.int16()),
        ("kwh", pa.float64()),
        ("quality", pa.string()),
        ("quality_flag", pa.string()),
        ("reason_code", pa.int16()),
        ("dataset_id", pa.string()),
    ]
)
"""`intervals.parquet` of `localise` (kind `elec_interval_local`)."""

# The nine tables of `usage_profile` (P3 spec §8), in the column order of `core.models.usage`. A null is a Parquet null.
USAGE_DAILY_SCHEMA: Final = pa.schema(
    [
        ("suffix", pa.string()),
        ("local_date", pa.date32()),
        ("month", pa.string()),
        ("season", pa.string()),
        ("daytype", pa.string()),
        ("kwh", pa.float64()),
        ("intervals", pa.int32()),
        ("expected", pa.int32()),
        ("coverage", pa.float64()),
        ("complete", pa.bool_()),
    ]
)
USAGE_MONTHLY_SCHEMA: Final = pa.schema(
    [
        ("suffix", pa.string()),
        ("month", pa.string()),
        ("days_present", pa.int32()),
        ("days_in_month", pa.int32()),
        ("complete_days", pa.int32()),
        ("covered", pa.bool_()),
        ("coverage", pa.float64()),
        ("kwh", pa.float64()),
        ("avg_daily_kwh", pa.float64()),
        ("base_load_kw", pa.float64()),
        ("peak_demand_kw", pa.float64()),
        ("peak_demand_at", pa.timestamp("us")),
        ("load_factor", pa.float64()),
    ]
)
USAGE_SEASONAL_SCHEMA: Final = pa.schema(
    [
        ("suffix", pa.string()),
        ("season_year", pa.int32()),
        ("season", pa.string()),
        ("days", pa.int32()),
        ("kwh", pa.float64()),
        ("avg_daily_kwh", pa.float64()),
    ]
)
USAGE_YOY_SCHEMA: Final = pa.schema(
    [
        ("suffix", pa.string()),
        ("month", pa.string()),
        ("prior_month", pa.string()),
        ("avg_daily_kwh", pa.float64()),
        ("prior_avg_daily_kwh", pa.float64()),
        ("change_pct", pa.float64()),
    ]
)
USAGE_HOURLY_PROFILE_SCHEMA: Final = pa.schema(
    [
        ("suffix", pa.string()),
        ("season", pa.string()),
        ("daytype", pa.string()),
        ("hour", pa.int16()),
        ("cells", pa.int32()),
        ("mean_kwh", pa.float64()),
        ("p_low_kwh", pa.float64()),
        ("p_mid_kwh", pa.float64()),
        ("p_high_kwh", pa.float64()),
    ]
)
USAGE_HEATMAP_SCHEMA: Final = pa.schema(
    [("suffix", pa.string()), ("local_date", pa.date32()), ("hour", pa.int16()), ("kwh", pa.float64())]
)
USAGE_TOU_DAILY_SCHEMA: Final = pa.schema(
    [
        ("suffix", pa.string()),
        ("local_date", pa.date32()),
        ("label", pa.string()),
        ("kwh", pa.float64()),
        ("intervals", pa.int32()),
        ("window_set", pa.int16()),
    ]
)
USAGE_TOU_MONTHLY_SCHEMA: Final = pa.schema(
    [
        ("suffix", pa.string()),
        ("month", pa.string()),
        ("label", pa.string()),
        ("kwh", pa.float64()),
        ("share", pa.float64()),
        ("avg_daily_kwh", pa.float64()),
        ("mixed_windows", pa.bool_()),
    ]
)
USAGE_QUALITY_MONTHLY_SCHEMA: Final = pa.schema(
    [
        ("suffix", pa.string()),
        ("month", pa.string()),
        ("quality_class", pa.string()),
        ("intervals", pa.int32()),
        ("kwh", pa.float64()),
        ("share_intervals", pa.float64()),
        ("share_kwh", pa.float64()),
    ]
)


@dataclass(frozen=True)
class UsageTable:
    """One mart table: its file stem, kind, version, pyarrow schema and sort key."""

    stem: str
    kind: str
    version: int
    schema: pa.Schema
    sort: tuple[str, ...]


OFFER_STATUS_SCHEMA: Final = pa.schema(
    [
        ("offer_snapshot_id", pa.string()),
        ("plan_id", pa.string()),
        ("retailer", pa.string()),
        ("brand", pa.string()),
        ("display_name", pa.string()),
        ("source", pa.string()),
        ("pricing_model", pa.string()),
        ("status", pa.string()),
        ("reasons", pa.list_(pa.string())),
        ("approximations", pa.list_(pa.string())),
        ("notes", pa.list_(pa.string())),
        ("rules", pa.list_(pa.string())),
    ]
)
"""`offers.parquet` of `normalise_offers` (kind `offer_status`, P4 spec §8)."""


MONEY: Final = pa.decimal128(18, 2)
"""Money in the cost tables: dollars to the cent (D1 §12)."""
FINE: Final = pa.decimal128(18, 6)
"""Quantities in the cost tables (D1 §12)."""
RATE: Final = pa.decimal128(24, 10)
"""Rates in the cost tables: a manual offer's GST-exclusive price is kept to ten places (D1 §12)."""
COST_LINE_SCHEMA: Final = pa.schema(
    [
        ("offer_snapshot_id", pa.string()),
        ("scenario", pa.string()),
        ("period_start", pa.date32()),
        ("period_end", pa.date32()),
        ("line", pa.string()),
        ("label", pa.string()),
        ("season", pa.int16()),
        ("block", pa.int16()),
        ("quantity", FINE),
        ("unit", pa.string()),
        ("rate", RATE),
        ("amount", MONEY),
    ]
)
"""`line_items.parquet` of `cost_elec` and `backtest` (kind `elec_cost_line`, P4 spec §9, §10)."""
COST_TOTAL_SCHEMA: Final = pa.schema(
    [
        ("offer_snapshot_id", pa.string()),
        ("scenario", pa.string()),
        ("status", pa.string()),
        ("annual_ongoing", MONEY),
        ("annual_year1", MONEY),
        ("rank", pa.int32()),
        ("approximations", pa.list_(pa.string())),
    ]
)
"""`totals.parquet` of `cost_elec` (kind `elec_cost_total`, P4 spec §9)."""
GAS_COST_TOTAL_SCHEMA: Final = pa.schema(
    [
        ("offer_snapshot_id", pa.string()),
        ("scenario", pa.string()),
        ("status", pa.string()),
        ("reference_cost", MONEY),
        ("annual_ongoing", MONEY),
        ("annual_year1", MONEY),
        ("rank", pa.int32()),
        ("approximations", pa.list_(pa.string())),
    ]
)
"""`totals.parquet` of `cost_gas` (kind `gas_cost_total`, P5 spec §9.3)."""
SENSITIVITY_TOTAL_SCHEMA: Final = pa.schema(
    [
        ("fuel", pa.string()),
        ("variation", pa.string()),
        ("offer_snapshot_id", pa.string()),
        ("annual_ongoing", MONEY),
        ("annual_year1", MONEY),
        ("rank", pa.int32()),
    ]
)
"""`totals.parquet` of `sensitivity` (kind `sensitivity_total`, P6 spec §4)."""
BUNDLE_OPTION_SCHEMA: Final = pa.schema(
    [
        ("variation", pa.string()),
        ("rank", pa.int32()),
        ("option_key", pa.string()),
        ("kind", pa.string()),
        ("brand", pa.string()),
        ("elec_snapshot_id", pa.string()),
        ("gas_snapshot_id", pa.string()),
        ("elec_annual_ongoing", MONEY),
        ("gas_annual_ongoing", MONEY),
        ("adjustment", MONEY),
        ("annual_ongoing", MONEY),
        ("annual_year1", MONEY),
        ("rules", pa.list_(pa.string())),
    ]
)
"""`options.parquet` of `bundles` (kind `bundle_option`, P6 spec §5.3)."""
GAS_BACKTEST_BILL_SCHEMA: Final = pa.schema(
    [
        ("period_start", pa.date32()),
        ("period_end", pa.date32()),
        ("offer_snapshot_id", pa.string()),
        ("bill_total", MONEY),
        ("engine_total", MONEY),
        ("diff", MONEY),
        ("diff_pct", pa.float64()),
        ("status", pa.string()),
    ]
)
"""`bills.parquet` of `backtest_gas` (kind `gas_backtest_bill`, P5 spec §10)."""
BILL_SCHEMA: Final = pa.schema(
    [
        ("period_start", pa.date32()),
        ("period_end", pa.date32()),
        ("offer_snapshot_id", pa.string()),
        ("invoice_date", pa.date32()),
        ("invoice_amount", MONEY),
        ("engine_amount", MONEY),
        ("diff", MONEY),
        ("diff_pct", pa.float64()),
        ("status", pa.string()),
    ]
)
"""`bills.parquet` of `backtest` (kind `backtest_bill`, P4 spec §10)."""


GAS_BILL_SCHEMA: Final = pa.schema(
    [
        ("record_dataset_id", pa.string()),
        ("pdf_dataset_id", pa.string()),
        ("issue_date", pa.date32()),
        ("period_start", pa.date32()),
        ("period_end", pa.date32()),
        ("days", pa.int32()),
        ("mj", FINE),
        ("end_read_type", pa.string()),
        ("estimated_reads", pa.int32()),
        ("subtotal", MONEY),
        ("gst", MONEY),
        ("total", MONEY),
        ("recomputed_total", MONEY),
    ]
)
"""`bills.parquet` of `gas_bills` (kind `gas_bill`, P5 spec §6)."""
GAS_READ_SCHEMA: Final = pa.schema(
    [
        ("record_dataset_id", pa.string()),
        ("position", pa.int32()),
        ("read_date", pa.date32()),
        ("read_type", pa.string()),
        ("start", FINE),
        ("end", FINE),
        ("heating_value", FINE),
        ("pressure_factor", FINE),
        ("mj", FINE),
        ("recomputed_mj", FINE),
    ]
)
"""`reads.parquet` of `gas_bills` (kind `gas_bill_read`)."""
GAS_LINE_SCHEMA: Final = pa.schema(
    [
        ("record_dataset_id", pa.string()),
        ("position", pa.int32()),
        ("line", pa.string()),
        ("label", pa.string()),
        ("quantity", FINE),
        ("unit", pa.string()),
        ("rate", RATE),
        ("amount", MONEY),
        ("gst_free", pa.bool_()),
    ]
)
"""`lines.parquet` of `gas_bills` (kind `gas_bill_line`)."""
GAS_PERIOD_SCHEMA: Final = pa.schema(
    [
        ("period_start", pa.date32()),
        ("period_end", pa.date32()),
        ("days", pa.int32()),
        ("mj", FINE),
        ("mj_per_day", FINE),
        ("bills", pa.int32()),
        ("estimated", pa.bool_()),
        ("in_reference", pa.bool_()),
    ]
)
"""`periods.parquet` of `gas_profile` (kind `gas_period`, P5 spec §7)."""
GAS_MONTH_SCHEMA: Final = pa.schema(
    [
        ("month", pa.string()),
        ("days_covered", pa.int32()),
        ("mj", FINE),
        ("mj_per_day", FINE),
    ]
)
"""`monthly.parquet` of `gas_profile` (kind `gas_month`)."""


SUMMARY_FILENAME: Final = "summary.json"
"""The `usage_summary` document beside the nine tables in `usage_profile`'s output."""
USAGE_TABLES: Final = (
    UsageTable("daily", usage.DAILY_KIND, usage.DAILY_VERSION, USAGE_DAILY_SCHEMA, usage.DAILY_SORT),
    UsageTable("monthly", usage.MONTHLY_KIND, usage.MONTHLY_VERSION, USAGE_MONTHLY_SCHEMA, usage.MONTHLY_SORT),
    UsageTable("seasonal", usage.SEASONAL_KIND, usage.SEASONAL_VERSION, USAGE_SEASONAL_SCHEMA, usage.SEASONAL_SORT),
    UsageTable("yoy", usage.YOY_KIND, usage.YOY_VERSION, USAGE_YOY_SCHEMA, usage.YOY_SORT),
    UsageTable(
        "hourly_profile",
        usage.HOURLY_PROFILE_KIND,
        usage.HOURLY_PROFILE_VERSION,
        USAGE_HOURLY_PROFILE_SCHEMA,
        usage.HOURLY_PROFILE_SORT,
    ),
    UsageTable("heatmap", usage.HEATMAP_KIND, usage.HEATMAP_VERSION, USAGE_HEATMAP_SCHEMA, usage.HEATMAP_SORT),
    UsageTable(
        "tou_daily", usage.TOU_DAILY_KIND, usage.TOU_DAILY_VERSION, USAGE_TOU_DAILY_SCHEMA, usage.TOU_DAILY_SORT
    ),
    UsageTable(
        "tou_monthly",
        usage.TOU_MONTHLY_KIND,
        usage.TOU_MONTHLY_VERSION,
        USAGE_TOU_MONTHLY_SCHEMA,
        usage.TOU_MONTHLY_SORT,
    ),
    UsageTable(
        "quality_monthly",
        usage.QUALITY_MONTHLY_KIND,
        usage.QUALITY_MONTHLY_VERSION,
        USAGE_QUALITY_MONTHLY_SCHEMA,
        usage.QUALITY_MONTHLY_SORT,
    ),
)
"""The nine tables of `usage_profile` in file order: the stage writes them and `usage_report` reads them."""


def load_table(path: Path, schema: pa.Schema, kind: str, version: int, *, input: str) -> pa.Table:
    """A table a stage reads from the input `input`, checked against its kind, version and columns.

    A file that is missing or isn't Parquet, or a table of another kind or version or with other columns →
    `E_INVALID_INPUT`, naming the input and the file, never a value.
    """
    try:
        table = pq.read_table(path)
    except OSError, pa.ArrowException:
        raise InvalidInputError("A table is missing or isn't Parquet.", input=input, file=path.name) from None
    metadata = table.schema.metadata or {}
    found = (metadata.get(b"energatron.kind"), metadata.get(b"energatron.schema_version"))
    if found != (kind.encode(), str(version).encode()) or not table.schema.remove_metadata().equals(schema):
        raise InvalidInputError(f"A table isn't a {kind} v{version} table.", input=input, file=path.name)
    return table
