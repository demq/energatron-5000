"""The pyarrow schemas of the interval tables that more than one stage reads or writes (P2 spec §7), and the reader
that checks a table against one.

Core holds no pyarrow (D1 §3), and a stage may not import another stage (D1 §11.1), so the schemas that
`parse_nem12` writes and `merge_interval` reads and writes again live here, in the module set of every stage that
shares them. Column names, kinds and versions are in `core.models.meter`.
"""

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
