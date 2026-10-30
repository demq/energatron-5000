"""`sapn_interval`: SAPN's NEM12 interval-data CSV (D3 §5.2), version 1.

Pure: bytes in, models out. `detect` reads the file name and the first non-blank line; `inspect` validates every
record through `core.nem12.scan_nem12` (the reader the P2 stages share) and counts what P1 needs. Errors carry a
fixed `reason` word, never file content, a file name or an identifier.
"""

import re
from datetime import date, datetime
from typing import Final

from energatron.core.canonical import JSONValue
from energatron.core.errors import InvalidInputError
from energatron.core.models.catalog import Detection, Evidence, SourceInfo, SourceType
from energatron.core.nem12 import scan_nem12
from energatron.core.text import compact_date, compact_datetime, fail, lines

_FILENAME: Final = re.compile(
    r"^(?P<nmi>[A-Z0-9]{10}[0-9])_(?P<from>\d{8})_(?P<to>\d{8})_(?P<gen>\d{14})_SAPN_DETAILED\.(?i:csv)$"
)
_INTERVALS: Final = {"5": 5, "05": 5, "15": 15, "30": 30}


def _content_matches(first: str) -> bool:
    fields = first.split(",")
    if fields[0] == "100":
        return len(fields) > 1 and fields[1] == "NEM12"
    return fields[0] == "200" and len(fields) >= 10 and fields[7].upper() == "KWH" and fields[8] in _INTERVALS


def _fail(message: str, reason: str) -> InvalidInputError:
    return fail(SourceType.SAPN_INTERVAL, message, reason)


class SapnIntervalParser:
    """The `SourceParser` for `sapn_interval` (D1 §4.3, D3 §5.2)."""

    source: SourceType = SourceType.SAPN_INTERVAL
    version: int = 1

    def detect(self, filename: str, head: bytes) -> Detection | None:
        evidence: list[Evidence] = []
        if _FILENAME.match(filename):
            evidence.append("filename")
        text = head.decode("utf-8", errors="replace")
        first = next((line for line in lines(text) if line.strip()), None)
        if first is not None and _content_matches(first):
            evidence.append("content")
        return Detection(source=self.source, evidence=tuple(evidence)) if evidence else None

    def inspect(self, filename: str, data: bytes) -> SourceInfo:
        try:
            scan = scan_nem12(data)  # the record checks first, D3 §5.2's order: then the 100 DateTime, the filename
        except InvalidInputError as error:
            if error.details.get("reason") == "no_data":
                _filename_facts(filename)  # a bad file name outranks a file without 300 records
            raise
        facts = scan.facts
        window, generated = _filename_facts(filename)
        if generated is not None:
            source_ts, source_ts_from = generated, "filename"
        elif facts.header_dt is not None:
            source_ts, source_ts_from = facts.header_dt, "nem12_header"
        else:
            source_ts, source_ts_from = facts.latest_update, "content"
        extra: dict[str, JSONValue] = {
            "interval_minutes": facts.interval_minutes,
            "nmi_suffixes": list(facts.suffixes),
            "uom": "KWH",
            "days": facts.days,
            "days_v": facts.days_v,
            "header_records": facts.header_records,
            "requested_window": (
                None if window is None else {"start": window[0].isoformat(), "end": window[1].isoformat()}
            ),
            "source_ts_from": source_ts_from,
        }
        nmis = list(scan.nmis)
        match = _FILENAME.match(filename)
        if match is not None and match["nmi"] not in nmis:
            nmis.append(match["nmi"])
        return SourceInfo(
            source_ts=source_ts,
            coverage=facts.coverage,
            extra=extra,
            identifiers={"nmi": nmis, "meter_serial": list(scan.serials)},
            extension=".csv",
        )


def _filename_facts(filename: str) -> tuple[tuple[date, date] | None, datetime | None]:
    """The requested window and generation time from a SAPN name; an invalid date or time → `bad_filename`."""
    match = _FILENAME.match(filename)
    if match is None:
        return None, None
    start, end = compact_date(match["from"]), compact_date(match["to"])
    generated = compact_datetime(match["gen"], "%Y%m%d%H%M%S", 14)
    if start is None or end is None or generated is None:
        raise _fail("The SAPN file name holds an invalid date or time.", "bad_filename")
    return (start, end), generated


SAPN_INTERVAL: Final = SapnIntervalParser()
"""The registered instance (`registry.PARSERS`)."""
