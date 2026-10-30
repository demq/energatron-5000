"""The SAPN SUMMARY CSV reader (P2 spec §4.2; D3 §5.3).

Pure. `scan_summary_csv` is what `inspect` calls: the rows plus the identifiers for the premises check.
`read_summary_csv` is what stages call: the rows, and **no identifier**. Both run one private walk, so they accept
exactly the same files. Errors are `E_INVALID_INPUT` with a fixed `reason` word, never content or an identifier.
"""

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Final

from energatron.core.models.catalog import SourceType
from energatron.core.text import add_unique, csv_rows, decode, fail, is_decimal, parse_datetime

_SOURCE: Final = SourceType.SAPN_SUMMARY
DMY: Final = re.compile(r"\d{2}/\d{2}/\d{4}")
FIELDS: Final = 11
"""The shape of a SUMMARY CSV row: its field count and its `DD/MM/YYYY` dates; `detect` matches them too."""


@dataclass(frozen=True)
class SummaryRow:
    """One period of the SUMMARY CSV. Fields 8-10 are validated as decimals and not returned."""

    start: date  # inclusive meter date
    end: date  # inclusive meter date
    quality: str  # field 4, e.g. "A", "AF"
    kwh: Decimal  # field 7, the period's total; a stage compares float(kwh)


@dataclass(frozen=True)
class SummaryScan:
    """Returned to `inspect` only: the identifiers are for the premises check and nothing else."""

    rows: tuple[SummaryRow, ...]
    nmis: tuple[str, ...]
    serials: tuple[str, ...]


@dataclass(frozen=True)
class SummaryRead:
    """Returned to stages: the rows in file order, no identifier field."""

    rows: tuple[SummaryRow, ...]


def scan_summary_csv(data: bytes) -> SummaryScan:
    """Validate a SUMMARY CSV and return its rows and identifiers (D3 §5.3's checks)."""
    rows, nmis, serials = _walk(data)
    return SummaryScan(rows=rows, nmis=tuple(nmis), serials=tuple(serials))


def read_summary_csv(data: bytes) -> SummaryRead:
    """The same checks as `scan_summary_csv`; the rows in file order."""
    return SummaryRead(rows=_walk(data)[0])


def _walk(data: bytes) -> tuple[tuple[SummaryRow, ...], list[str], list[str]]:
    rows: list[SummaryRow] = []
    nmis: list[str] = []
    serials: list[str] = []
    for fields in csv_rows(decode(_SOURCE, data)):
        if len(fields) != FIELDS:
            raise fail(_SOURCE, "A summary row doesn't have 11 fields.", "bad_field_count")
        if fields[2].strip() != "kWh" or fields[10].strip() != "kW":
            raise fail(_SOURCE, "A summary row's units aren't kWh and kW.", "bad_row")
        start, end = _dmy(fields[4]), _dmy(fields[5])
        if start is None or end is None:
            raise fail(_SOURCE, "A summary row has an invalid date.", "bad_date")
        if start > end:
            raise fail(_SOURCE, "A summary row starts after it ends.", "bad_date_range")
        if not all(is_decimal(value) for value in fields[6:10]):
            raise fail(_SOURCE, "A summary row has a value that isn't a decimal.", "bad_number")
        rows.append(SummaryRow(start=start, end=end, quality=fields[3].strip(), kwh=Decimal(fields[6].strip())))
        add_unique(nmis, fields[0])
        add_unique(serials, fields[1])
    if not rows:
        raise fail(_SOURCE, "The file has no summary rows.", "no_data")
    return tuple(rows), nmis, serials


def _dmy(text: str) -> date | None:
    text = text.strip()
    if DMY.fullmatch(text) is None:
        return None
    parsed = parse_datetime(text, "%d/%m/%Y")
    return None if parsed is None else parsed.date()
