"""The retailer transactions export, read for the backtest (P4 spec §10).

Each row is `Description,Date,Amount`. Descriptions hold invoice numbers, so they never leave this module: a row is
reduced to its kind (`invoice` for a description starting `Invoice`, `payment` for one starting `Payment`, else
`other`), its datetime and its amount. The same checks as the `retailer_txn` parser apply (D3 §5.5). Pure.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Final, Literal

from energatron.core.models.catalog import SourceType
from energatron.core.text import csv_rows, decode, fail, is_decimal, lines, parse_datetime

_SOURCE: Final = SourceType.RETAILER_TXN
_HEADER: Final = "Description,Date,Amount"
_DATE_FORMAT: Final = "%Y-%m-%dT%H:%M:%S"

type TxnKind = Literal["invoice", "payment", "other"]


@dataclass(frozen=True)
class TxnRow:
    kind: TxnKind
    at: datetime  # naive, as written
    amount: Decimal


def read_txn(data: bytes) -> tuple[TxnRow, ...]:
    """The export's rows in file order; a malformed export → `E_INVALID_INPUT` with a fixed `reason`."""
    text = decode(_SOURCE, data)
    if lines(text)[0] != _HEADER:
        raise fail(_SOURCE, "The first line isn't the transactions export's header.", "bad_header")
    _, *rows = csv_rows(text)
    found: list[TxnRow] = []
    for fields in rows:
        if len(fields) != 3:
            raise fail(_SOURCE, "A row doesn't have 3 fields.", "bad_field_count")
        description, when_text, amount_text = fields
        when = parse_datetime(when_text, _DATE_FORMAT)
        if when is None:
            raise fail(_SOURCE, "A row's Date is invalid.", "bad_datetime")
        if not is_decimal(amount_text):
            raise fail(_SOURCE, "A row's Amount isn't a decimal.", "bad_number")
        lowered = description.strip().lower()
        kind: TxnKind = (
            "invoice" if lowered.startswith("invoice") else "payment" if lowered.startswith("payment") else "other"
        )
        found.append(TxnRow(kind=kind, at=when, amount=Decimal(amount_text)))
    return tuple(found)
