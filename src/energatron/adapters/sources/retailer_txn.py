"""`retailer_txn`: the retailer portal's transactions export (D3 §5.5), version 1.

Descriptions hold invoice numbers, so their text never reaches `extra` or an error. The file carries no NMI, serial
or account, so the premises check warns (D3 §6). Pure.
"""

import re
from datetime import datetime
from typing import Final

from energatron.adapters.sources._common import head_lines
from energatron.core.canonical import JSONValue
from energatron.core.models.catalog import Coverage, Detection, Evidence, SourceInfo, SourceType
from energatron.core.text import csv_rows, decode, fail, is_decimal, lines, parse_datetime

_SOURCE: Final = SourceType.RETAILER_TXN
_FILENAME: Final = re.compile(r"(?i)transactions_all\.csv$")
_HEADER: Final = "Description,Date,Amount"
_DATE_FORMAT: Final = "%Y-%m-%dT%H:%M:%S"


class RetailerTxnParser:
    """The `SourceParser` for `retailer_txn` (D1 §4.3, D3 §5.5)."""

    source: SourceType = _SOURCE
    version: int = 1

    def detect(self, filename: str, head: bytes) -> Detection | None:
        evidence: list[Evidence] = []
        if _FILENAME.search(filename):
            evidence.append("filename")
        if head_lines(head)[0] == _HEADER:
            evidence.append("content")
        return Detection(source=self.source, evidence=tuple(evidence)) if evidence else None

    def inspect(self, filename: str, data: bytes) -> SourceInfo:
        text = decode(_SOURCE, data)
        if lines(text)[0] != _HEADER:
            raise fail(_SOURCE, "The first line isn't the transactions export's header.", "bad_header")
        _, *rows = csv_rows(text)
        dates: list[datetime] = []
        for fields in rows:
            if len(fields) != 3:
                raise fail(_SOURCE, "A row doesn't have 3 fields.", "bad_field_count")
            when = parse_datetime(fields[1], _DATE_FORMAT)
            if when is None:
                raise fail(_SOURCE, "A row's Date is invalid.", "bad_datetime")
            if not is_decimal(fields[2]):
                raise fail(_SOURCE, "A row's Amount isn't a decimal.", "bad_number")
            dates.append(when)
        if not dates:
            raise fail(_SOURCE, "The export has no rows.", "no_data")
        extra: dict[str, JSONValue] = {"rows": len(dates), "source_ts_from": "content"}
        return SourceInfo(
            source_ts=max(dates),
            coverage=Coverage(start=min(dates).date(), end=max(dates).date()),
            extra=extra,
            identifiers={},
            extension=".csv",
        )


RETAILER_TXN: Final = RetailerTxnParser()
"""The registered instance (`registry.PARSERS`)."""
