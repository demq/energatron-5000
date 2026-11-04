"""The pyarrow schemas of the interval tables that more than one stage reads or writes (P2 spec §7), and the reader
that checks a table against one.

Core holds no pyarrow (D1 §3), and a stage may not import another stage (D1 §11.1), so the schemas that
`parse_nem12` writes and `merge_interval` reads and writes again live here, in the module set of every stage that
shares them. So do `localise`'s table, which `usage_profile` reads, and the nine tables of `usage_profile`, which
`usage_report` reads. Column names, kinds and versions are in `core.models.meter` and `core.models.usage`.
"""

from datetime import UTC
from pathlib import Path
from typing import Final

import pyarrow as pa
import pyarrow.parquet as pq

from energatron.core.errors import InvalidInputError

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
