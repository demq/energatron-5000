"""The meter pipeline's table kinds (P2 spec §7, §12).

Parquet kinds are metadata only (D1 §5): a file carries `energatron.kind` and `energatron.schema_version`, and the
stage that writes it owns the column types, because core holds no pyarrow. This module names the kinds, their
versions and their columns, so the stages that share a table agree on them. Each P2 stage sub-task adds its kinds.
The JSON kinds of the reports are `Persisted` models and join this module with their stages.
"""

from typing import Final

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
