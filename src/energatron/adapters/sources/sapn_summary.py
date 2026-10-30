"""`sapn_summary`: SAPN's SUMMARY download, as CSV or PDF (D3 §5.3), version 1.

Pure. The CSV is checked row by row; the PDF only for completeness, as P1 doesn't read PDF text. Stages that read
summaries (P2) select `extra.format == "csv"`.
"""

import re
from datetime import datetime, time
from typing import Final

from energatron.adapters.sources._common import first_line, sapn_filename_facts, window_json
from energatron.adapters.sources.bill_pdf import PDF_MAGIC, is_complete_pdf
from energatron.adapters.sources.pdf_meta import creation_date
from energatron.core.canonical import JSONValue
from energatron.core.models.catalog import Coverage, Detection, Evidence, SourceInfo, SourceType
from energatron.core.sapn_summary import DMY, FIELDS, scan_summary_csv
from energatron.core.text import fail

_SOURCE: Final = SourceType.SAPN_SUMMARY
_FILENAME: Final = re.compile(
    r"^(?P<nmi>[A-Z0-9]{10}[0-9])_(?P<from>\d{8})_(?P<to>\d{8})_(?P<gen>\d{14})_SAPN_SUMMARY\.(?i:csv|pdf)$"
)


def _csv_matches(fields: list[str]) -> bool:
    return (
        len(fields) == FIELDS
        and fields[2].strip() == "kWh"
        and DMY.fullmatch(fields[4].strip()) is not None
        and DMY.fullmatch(fields[5].strip()) is not None
        and fields[10].strip() == "kW"
    )


class SapnSummaryParser:
    """The `SourceParser` for `sapn_summary` (D1 §4.3, D3 §5.3)."""

    source: SourceType = _SOURCE
    version: int = 1

    def detect(self, filename: str, head: bytes) -> Detection | None:
        evidence: list[Evidence] = []
        if _FILENAME.match(filename):
            evidence.append("filename")
        if head.startswith(PDF_MAGIC):
            evidence.append("content")
        else:
            first = first_line(head)
            if first is not None and _csv_matches(first.split(",")):
                evidence.append("content")
        return Detection(source=self.source, evidence=tuple(evidence)) if evidence else None

    def inspect(self, filename: str, data: bytes) -> SourceInfo:
        if data.startswith(PDF_MAGIC):
            return _inspect_pdf(filename, data)
        return _inspect_csv(filename, data)


def _inspect_csv(filename: str, data: bytes) -> SourceInfo:
    scan = scan_summary_csv(data)
    nmis = list(scan.nmis)
    facts = sapn_filename_facts(_SOURCE, _FILENAME, filename)
    if facts is not None:
        if facts[0] not in nmis:
            nmis.append(facts[0])
        source_ts, source_ts_from = facts[2], "filename"
    else:
        source_ts, source_ts_from = datetime.combine(max(row.end for row in scan.rows), time()), "content"
    extra: dict[str, JSONValue] = {
        "format": "csv",
        "rows": len(scan.rows),
        "requested_window": window_json(None if facts is None else facts[1]),
        "source_ts_from": source_ts_from,
    }
    return SourceInfo(
        source_ts=source_ts,
        coverage=Coverage(start=min(row.start for row in scan.rows), end=max(row.end for row in scan.rows)),
        extra=extra,
        identifiers={"nmi": nmis, "meter_serial": list(scan.serials)},
        extension=".csv",
    )


def _inspect_pdf(filename: str, data: bytes) -> SourceInfo:
    if not is_complete_pdf(data):
        raise fail(_SOURCE, "The PDF doesn't end with %%EOF: it may be truncated.", "truncated")
    facts = sapn_filename_facts(_SOURCE, _FILENAME, filename)
    if facts is not None:
        source_ts, source_ts_from = facts[2], "filename"
    else:
        created = creation_date(data)
        if created is None:
            raise fail(_SOURCE, "The PDF carries no creation date.", "no_creation_date")
        source_ts, source_ts_from = created, "pdf_metadata"
    extra: dict[str, JSONValue] = {
        "format": "pdf",
        "requested_window": window_json(None if facts is None else facts[1]),
        "source_ts_from": source_ts_from,
    }
    return SourceInfo(
        source_ts=source_ts,
        coverage=None,
        extra=extra,
        identifiers={} if facts is None else {"nmi": [facts[0]]},
        extension=".pdf",
    )


SAPN_SUMMARY: Final = SapnSummaryParser()
"""The registered instance (`registry.PARSERS`)."""
