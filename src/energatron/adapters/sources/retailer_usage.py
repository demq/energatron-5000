"""`retailer_usage`: the retailer portal's usage export (D3 §5.4), version 1.

v1 recognises the one portal format in the workspace: `Account Code,Read Datetime,<register columns>,Total`, each
register column named `<suffix> (<meter serial> - <TOU label>)`. Pure.
"""

import re
from typing import Final

from energatron.adapters.sources._common import head_lines
from energatron.core.canonical import JSONValue
from energatron.core.models.catalog import Coverage, Detection, Evidence, SourceInfo, SourceType
from energatron.core.retailer_usage import HEADER, scan_usage_export

_SOURCE: Final = SourceType.RETAILER_USAGE
_FILENAME: Final = re.compile(r"(?i)usage_all\.csv$")


class RetailerUsageParser:
    """The `SourceParser` for `retailer_usage` (D1 §4.3, D3 §5.4)."""

    source: SourceType = _SOURCE
    version: int = 1

    def detect(self, filename: str, head: bytes) -> Detection | None:
        evidence: list[Evidence] = []
        if _FILENAME.search(filename):
            evidence.append("filename")
        if HEADER.match(head_lines(head)[0]):
            evidence.append("content")
        return Detection(source=self.source, evidence=tuple(evidence)) if evidence else None

    def inspect(self, filename: str, data: bytes) -> SourceInfo:
        scan = scan_usage_export(data)
        reads = [row.read_dt for row in scan.rows]
        extra: dict[str, JSONValue] = {
            "rows": len(reads),
            "tou_labels": list(scan.labels),
            "nmi_suffixes": list(scan.suffixes),
            "source_ts_from": "content",
        }
        return SourceInfo(
            source_ts=max(reads),
            coverage=Coverage(start=min(reads).date(), end=max(reads).date()),
            extra=extra,
            identifiers={"account": list(scan.accounts), "meter_serial": list(scan.serials)},
            extension=".csv",
        )


RETAILER_USAGE: Final = RetailerUsageParser()
"""The registered instance (`registry.PARSERS`)."""
