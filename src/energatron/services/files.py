"""Local file helpers: atomic writes, JSON documents and JSONL lines (D1 §12).

- Every write is atomic: a temporary file in the same directory, `fsync`, then `os.replace`, then an `fsync` of
  the directory.
- Text is UTF-8 with LF line endings, written as given.
- JSON documents: sorted keys, `indent=2`, a trailing newline. JSONL lines: compact canonical JSON (D1 §6.1).

- Parquet tables: only through `write_parquet` (D1 §9.4): pyarrow, zstd, rows sorted, and exactly the metadata keys
  `energatron.kind` and `energatron.schema_version` (D1 §5), so a rewrite of the same table gives the same bytes.

Errors are left to the caller, who knows which file it was; nothing here puts a path into a message.
"""

import io
import json
import os
import tempfile
from collections.abc import Iterable, Sequence
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from energatron.core.canonical import JSONValue, canonical_json


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write `data` to `path` atomically. On any failure the old file, if any, is unchanged, and no temp is left."""
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    fsync_directory(path.parent)


def atomic_write_text(path: Path, text: str) -> None:
    """Write `text` as UTF-8, atomically. Line endings are written as given: callers use `\\n`."""
    atomic_write_bytes(path, text.encode("utf-8"))


def fsync_directory(directory: Path) -> None:
    """Flush a directory entry change (a rename or a new file) to disk."""
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def make_dirs(directory: Path) -> None:
    """Create `directory` and any missing parents, `fsync`ing the parent of each one created, so the new entries
    survive a crash. An existing directory is left alone; a file in the way raises `OSError`."""
    missing: list[Path] = []
    current = directory
    while not current.is_dir():
        missing.append(current)
        current = current.parent
    for path in reversed(missing):
        try:
            path.mkdir()
        except FileExistsError:
            if not path.is_dir():
                raise
            continue
        fsync_directory(path.parent)


def dump_json(value: JSONValue) -> str:
    """`value` as a JSON document: sorted keys, `indent=2`, non-ASCII kept, a trailing newline; NaN refused."""
    return json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n"


def write_json(path: Path, value: JSONValue) -> None:
    """Write `value` as a JSON document, atomically. A value `json` can't write raises before anything is written."""
    atomic_write_text(path, dump_json(value))


def read_json(path: Path) -> JSONValue:
    """Read a JSON document. Bad UTF-8 or bad JSON raises `ValueError`; a missing file raises `OSError`."""
    return json.loads(path.read_bytes().decode("utf-8"))


def write_jsonl(path: Path, rows: Iterable[JSONValue]) -> None:
    """Write one compact canonical JSON line per row, atomically."""
    atomic_write_bytes(path, b"".join(canonical_json(row) + b"\n" for row in rows))


def read_jsonl(path: Path) -> list[JSONValue]:
    """Read every line as JSON. A bad line raises `ValueError` (its line number is in the message, not its text)."""
    rows: list[JSONValue] = []
    for number, line in enumerate(path.read_bytes().decode("utf-8").splitlines(), start=1):
        try:
            rows.append(json.loads(line))
        except ValueError:
            raise ValueError(f"line {number} is not valid JSON") from None
    return rows


def write_parquet(
    df: pd.DataFrame,
    path: Path,
    *,
    kind: str,
    schema_version: int,
    sort_by: Sequence[str],
    schema: pa.Schema,
) -> None:
    """Write `df` as a Parquet table, atomically (D1 §5, §9.4).

    Rows are sorted by `sort_by` (stable, so ties keep their order) and the index is dropped. `schema` fixes every
    column's type, so an empty table keeps its types. Its metadata is replaced by `energatron.kind` and
    `energatron.schema_version` (string values), with no pandas metadata. Compression is zstd.
    """
    ordered = df.sort_values(list(sort_by), kind="stable").reset_index(drop=True)
    table = pa.Table.from_pandas(ordered, schema=schema, preserve_index=False)
    table = table.replace_schema_metadata({"energatron.kind": kind, "energatron.schema_version": str(schema_version)})
    buffer = io.BytesIO()
    pq.write_table(table, buffer, compression="zstd")
    atomic_write_bytes(path, buffer.getvalue())


def parquet_rows(path: Path) -> int:
    """The row count in a Parquet file's metadata."""
    return pq.ParquetFile(path).metadata.num_rows
