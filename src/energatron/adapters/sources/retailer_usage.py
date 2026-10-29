"""`retailer_usage`: the retailer portal's usage export (D3 §5.4), version 1.

v1 recognises the one portal format in the workspace: `Account Code,Read Datetime,<register columns>,Total`, each
register column named `<suffix> (<meter serial> - <TOU label>)`. Pure.
"""

import re
from datetime import datetime
from typing import Final

from energatron.adapters.sources._common import (
    add_unique,
    csv_rows,
    decode,
    fail,
    head_lines,
    is_decimal,
    lines,
    parse_datetime,
)
from energatron.core.canonical import JSONValue
from energatron.core.models.catalog import Coverage, Detection, Evidence, SourceInfo, SourceType

_SOURCE: Final = SourceType.RETAILER_USAGE
_FILENAME: Final = re.compile(r"(?i)usage_all\.csv$")
_HEADER: Final = re.compile(r"^Account Code,Read Datetime(,[A-Z][0-9A-Z] \([^,()]+ - [^,()]+\))+,Total$")
_REGISTER: Final = re.compile(r"(?P<suffix>[A-Z][0-9A-Z]) \((?P<serial>[^,()]+?) - (?P<label>[^,()]+)\)")
_READ_FORMAT: Final = "%Y-%m-%dT%H:%M:%S"


class RetailerUsageParser:
    """The `SourceParser` for `retailer_usage` (D1 §4.3, D3 §5.4)."""

    source: SourceType = _SOURCE
    version: int = 1

    def detect(self, filename: str, head: bytes) -> Detection | None:
        evidence: list[Evidence] = []
        if _FILENAME.search(filename):
            evidence.append("filename")
        if _HEADER.match(head_lines(head)[0]):
            evidence.append("content")
        return Detection(source=self.source, evidence=tuple(evidence)) if evidence else None

    def inspect(self, filename: str, data: bytes) -> SourceInfo:
        text = decode(_SOURCE, data)
        header_line = lines(text)[0]
        if _HEADER.match(header_line) is None:
            raise fail(_SOURCE, "The first line isn't the usage export's header.", "bad_header")
        header, *rows = csv_rows(text)
        registers = [_REGISTER.fullmatch(column) for column in header[2:-1]]
        labels = sorted({match["label"] for match in registers if match is not None})
        suffixes = sorted({match["suffix"] for match in registers if match is not None})
        serials: list[str] = []
        for match in registers:
            if match is not None:
                add_unique(serials, match["serial"])
        accounts: list[str] = []
        reads: list[datetime] = []
        for fields in rows:
            if len(fields) != len(header):
                raise fail(_SOURCE, "A row's field count differs from the header's.", "bad_field_count")
            read = parse_datetime(fields[1], _READ_FORMAT)
            if read is None:
                raise fail(_SOURCE, "A row's Read Datetime is invalid.", "bad_datetime")
            if not all(value.strip() == "" or is_decimal(value) for value in fields[2:]):
                raise fail(_SOURCE, "A row has a value that is neither empty nor a decimal.", "bad_number")
            reads.append(read)
            add_unique(accounts, fields[0])
        if not reads:
            raise fail(_SOURCE, "The export has no rows.", "no_data")
        extra: dict[str, JSONValue] = {
            "rows": len(reads),
            "tou_labels": labels,
            "nmi_suffixes": suffixes,
            "source_ts_from": "content",
        }
        return SourceInfo(
            source_ts=max(reads),
            coverage=Coverage(start=min(reads).date(), end=max(reads).date()),
            extra=extra,
            identifiers={"account": accounts, "meter_serial": serials},
            extension=".csv",
        )


RETAILER_USAGE: Final = RetailerUsageParser()
"""The registered instance (`registry.PARSERS`)."""
