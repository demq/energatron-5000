"""The retailer portal's usage-export reader (P2 spec §4.3; D3 §5.4).

v1 recognises the one portal format in the workspace: `Account Code,Read Datetime,<register columns>,Total`, each
register column named `<suffix> (<meter serial> - <TOU label>)`. Pure. `scan_usage_export` is what `inspect` calls:
the labels, the rows and the identifiers for the premises check. `read_usage_export` is what stages call: the labels,
the suffixes and the rows, and **no identifier**. Both run one private walk, so they accept exactly the same files,
except that `read_usage_export` also refuses two register columns that share a label (`duplicate_label`), because a
row maps a label to one value. Errors are `E_INVALID_INPUT` with a fixed `reason` word, never content or an
identifier.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from types import MappingProxyType
from typing import Final

from energatron.core.models.catalog import SourceType
from energatron.core.text import add_unique, csv_rows, decode, fail, is_decimal, lines, parse_datetime

_SOURCE: Final = SourceType.RETAILER_USAGE
HEADER: Final = re.compile(r"^Account Code,Read Datetime(,[A-Z][0-9A-Z] \([^,()]+ - [^,()]+\))+,Total$")
"""The usage export's first line; `detect` matches it too."""
_REGISTER: Final = re.compile(r"(?P<suffix>[A-Z][0-9A-Z]) \((?P<serial>[^,()]+?) - (?P<label>[^,()]+)\)")
_READ_FORMAT: Final = "%Y-%m-%dT%H:%M:%S"


@dataclass(frozen=True)
class UsageRow:
    read_dt: datetime  # naive
    values: Mapping[str, Decimal | None]  # TOU label as written -> kWh; None for an empty cell
    total: Decimal | None


@dataclass(frozen=True)
class UsageScan:
    """Returned to `inspect` only: the accounts and serials are for the premises check and nothing else."""

    labels: tuple[str, ...]  # sorted
    suffixes: tuple[str, ...]  # sorted
    rows: tuple[UsageRow, ...]
    accounts: tuple[str, ...]
    serials: tuple[str, ...]


@dataclass(frozen=True)
class UsageRead:
    """Returned to stages: no identifier field."""

    labels: tuple[str, ...]  # sorted
    suffixes: tuple[str, ...]  # sorted
    rows: tuple[UsageRow, ...]  # in file order


def scan_usage_export(data: bytes) -> UsageScan:
    """Validate a usage export and return its labels, rows and identifiers (D3 §5.4's checks)."""
    return _walk(data)[0]


def read_usage_export(data: bytes) -> UsageRead:
    """The same checks as `scan_usage_export`, plus unique labels; the rows in file order."""
    scan, labels = _walk(data)
    if len(set(labels)) != len(labels):
        raise fail(_SOURCE, "Two register columns share a TOU label.", "duplicate_label")
    return UsageRead(labels=scan.labels, suffixes=scan.suffixes, rows=scan.rows)


def _decimal(text: str) -> Decimal | None:
    return Decimal(text.strip()) if text.strip() else None


def _walk(data: bytes) -> tuple[UsageScan, list[str]]:
    """The scan, and the register labels in column order."""
    text = decode(_SOURCE, data)
    if HEADER.match(lines(text)[0]) is None:
        raise fail(_SOURCE, "The first line isn't the usage export's header.", "bad_header")
    header, *rows = csv_rows(text)
    registers = [_REGISTER.fullmatch(column) for column in header[2:-1]]
    labels = [match["label"] for match in registers if match is not None]
    suffixes = sorted({match["suffix"] for match in registers if match is not None})
    serials: list[str] = []
    for match in registers:
        if match is not None:
            add_unique(serials, match["serial"])
    accounts: list[str] = []
    read: list[UsageRow] = []
    for fields in rows:
        if len(fields) != len(header):
            raise fail(_SOURCE, "A row's field count differs from the header's.", "bad_field_count")
        read_dt = parse_datetime(fields[1], _READ_FORMAT)
        if read_dt is None:
            raise fail(_SOURCE, "A row's Read Datetime is invalid.", "bad_datetime")
        if not all(value.strip() == "" or is_decimal(value) for value in fields[2:]):
            raise fail(_SOURCE, "A row has a value that is neither empty nor a decimal.", "bad_number")
        values = {label: _decimal(value) for label, value in zip(labels, fields[2:-1], strict=True)}
        read.append(UsageRow(read_dt=read_dt, values=MappingProxyType(values), total=_decimal(fields[-1])))
        add_unique(accounts, fields[0])
    if not read:
        raise fail(_SOURCE, "The export has no rows.", "no_data")
    scan = UsageScan(
        labels=tuple(sorted(set(labels))),
        suffixes=tuple(suffixes),
        rows=tuple(read),
        accounts=tuple(accounts),
        serials=tuple(serials),
    )
    return scan, labels
