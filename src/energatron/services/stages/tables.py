"""The pyarrow schemas of the interval tables that more than one stage reads or writes (P2 spec §7).

Core holds no pyarrow (D1 §3), and a stage may not import another stage (D1 §11.1), so the schemas that
`parse_nem12` writes and `merge_interval` reads and writes again live here, in the module set of both. Column names,
kinds and versions are in `core.models.meter`.
"""

from typing import Final

import pyarrow as pa

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
