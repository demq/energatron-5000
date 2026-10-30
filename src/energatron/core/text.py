"""Text helpers the format readers and the source parsers share (P2 spec §4; D3 §5).

Pure. Errors carry a fixed snake_case `reason`, never file content, a file name or an identifier. Moved from
`adapters/sources/_common.py` in P2.2.2 with no change of behaviour.
"""

import csv
import re
from datetime import date, datetime
from typing import Final

from energatron.core.errors import InvalidInputError
from energatron.core.models.catalog import SourceType

BOM: Final = "﻿"
_DECIMAL: Final = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)")


def fail(source: SourceType, message: str, reason: str) -> InvalidInputError:
    """`E_INVALID_INPUT` with `details.source` and `details.reason`."""
    return InvalidInputError(message, source=source.value, reason=reason)


def lines(text: str) -> list[str]:
    """The text's lines, a leading BOM dropped, LF or CRLF."""
    return [line.removesuffix("\r") for line in text.removeprefix(BOM).split("\n")]


def decode(source: SourceType, data: bytes) -> str:
    """The whole file as UTF-8 text; anything else → `not_utf8`."""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        raise fail(source, "The file isn't UTF-8 text.", "not_utf8") from None


def csv_rows(text: str) -> list[list[str]]:
    """The non-blank rows of a CSV text, with quoted fields handled; a leading BOM dropped."""
    return [row for row in csv.reader(lines(text)) if any(field.strip() for field in row)]


def is_decimal(text: str) -> bool:
    return _DECIMAL.fullmatch(text.strip()) is not None


def parse_datetime(text: str, pattern: str) -> datetime | None:
    try:
        return datetime.strptime(text.strip(), pattern)
    except ValueError:
        return None


def compact_datetime(text: str, pattern: str, digits: int) -> datetime | None:
    """A compact all-digit timestamp of exactly `digits` characters, or `None` (SAPN's `YYYYMMDD…` fields)."""
    if len(text) != digits or not text.isdigit():
        return None
    try:
        return datetime.strptime(text, pattern)
    except ValueError:
        return None


def compact_date(text: str) -> date | None:
    """`YYYYMMDD` as a date, or `None`."""
    parsed = compact_datetime(text, "%Y%m%d", 8)
    return None if parsed is None else parsed.date()


def add_unique(values: list[str], value: str) -> None:
    """Append a stripped, non-empty value once, keeping first-seen order."""
    value = value.strip()
    if value and value not in values:
        values.append(value)
