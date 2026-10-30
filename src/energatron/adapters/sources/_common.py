"""Helpers only detection needs: head decoding, window JSON and SAPN file names (D3 §5).

Pure. The text helpers the parsers and the format readers share live in `core.text` (P2 spec §4). Errors carry a
fixed snake_case `reason`, never file content, a file name or an identifier.
"""

import re
from datetime import date, datetime

from energatron.core.models.catalog import SourceType
from energatron.core.text import fail, lines


def head_lines(head: bytes) -> list[str]:
    """`detect`'s view of a head: decoded leniently, so a multi-byte character cut at 64 KiB does no harm."""
    return lines(head.decode("utf-8", errors="replace"))


def first_line(head: bytes) -> str | None:
    """The first non-blank line of a head, or `None`."""
    return next((line for line in head_lines(head) if line.strip()), None)


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
