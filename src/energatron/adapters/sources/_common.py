"""Helpers the P1.5 parsers share: text decoding, fixed-reason errors, SAPN file names, decimals (D3 §5).

Pure. Errors carry a fixed snake_case `reason`, never file content, a file name or an identifier.
"""

import csv
import re
from datetime import date, datetime
from typing import Final

from energatron.core.errors import InvalidInputError
from energatron.core.models.catalog import SourceType

BOM: Final = "\ufeff"
_DECIMAL: Final = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)")


def fail(source: SourceType, message: str, reason: str) -> InvalidInputError:
    """`E_INVALID_INPUT` with `details.source` and `details.reason`."""
    return InvalidInputError(message, source=source.value, reason=reason)


def lines(text: str) -> list[str]:
    """The text's lines, a leading BOM dropped, LF or CRLF."""
    return [line.removesuffix("\r") for line in text.removeprefix(BOM).split("\n")]


def head_lines(head: bytes) -> list[str]:
    """`detect`'s view of a head: decoded leniently, so a multi-byte character cut at 64 KiB does no harm."""
    return lines(head.decode("utf-8", errors="replace"))


def first_line(head: bytes) -> str | None:
    """The first non-blank line of a head, or `None`."""
    return next((line for line in head_lines(head) if line.strip()), None)


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


def window_json(window: tuple[date, date] | None) -> dict[str, str] | None:
    return None if window is None else {"start": window[0].isoformat(), "end": window[1].isoformat()}


def sapn_filename_facts(
    source: SourceType, pattern: re.Pattern[str], filename: str
) -> tuple[str, tuple[date, date], datetime] | None:
    """The NMI, requested window and generation time from a SAPN file name, or `None` when it isn't one.

    An invalid date or time → `bad_filename`.
    """
    match = pattern.match(filename)
    if match is None:
        return None
    try:
        start = datetime.strptime(match["from"], "%Y%m%d").date()
        end = datetime.strptime(match["to"], "%Y%m%d").date()
        generated = datetime.strptime(match["gen"], "%Y%m%d%H%M%S")
    except ValueError:
        raise fail(source, "The SAPN file name holds an invalid date or time.", "bad_filename") from None
    return match["nmi"], (start, end), generated


def add_unique(values: list[str], value: str) -> None:
    """Append a stripped, non-empty value once, keeping first-seen order."""
    value = value.strip()
    if value and value not in values:
        values.append(value)
