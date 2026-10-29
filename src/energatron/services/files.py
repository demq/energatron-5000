"""Local file helpers: atomic writes, JSON documents and JSONL lines (D1 §12).

- Every write is atomic: a temporary file in the same directory, `fsync`, then `os.replace`, then an `fsync` of
  the directory.
- Text is UTF-8 with LF line endings, written as given.
- JSON documents: sorted keys, `indent=2`, a trailing newline. JSONL lines: compact canonical JSON (D1 §6.1).

Parquet helpers arrive with the first stage that writes a table (P1). Errors are left to the caller, who knows
which file it was; nothing here puts a path into a message.
"""

import json
import os
import tempfile
from collections.abc import Iterable
from pathlib import Path

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
