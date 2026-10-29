"""`sapn_interval`: SAPN's NEM12 interval-data CSV (D3 §5.2), version 1.

Pure: bytes in, models out. `detect` reads the file name and the first non-blank line; `inspect` validates every
record and counts what P1 needs. Values and quality detail are P2's (`parse`). Errors carry a fixed `reason` word,
never file content, a file name or an identifier.
"""

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Final

from energatron.core.canonical import JSONValue
from energatron.core.errors import InvalidInputError
from energatron.core.models.catalog import Coverage, Detection, Evidence, SourceInfo, SourceType

_FILENAME: Final = re.compile(
    r"^(?P<nmi>[A-Z0-9]{10}[0-9])_(?P<from>\d{8})_(?P<to>\d{8})_(?P<gen>\d{14})_SAPN_DETAILED\.(?i:csv)$"
)
_INTERVALS: Final = {"5": 5, "05": 5, "15": 15, "30": 30}
_RECORD_TYPES: Final = frozenset({"100", "200", "300", "400", "500", "900"})
_BOM: Final = "\ufeff"


def _lines(text: str) -> list[str]:
    """The text's lines, a leading BOM dropped, LF or CRLF."""
    return [line.removesuffix("\r") for line in text.removeprefix(_BOM).split("\n")]


def _content_matches(first: str) -> bool:
    fields = first.split(",")
    if fields[0] == "100":
        return len(fields) > 1 and fields[1] == "NEM12"
    return fields[0] == "200" and len(fields) >= 10 and fields[7].upper() == "KWH" and fields[8] in _INTERVALS


def _fail(message: str, reason: str) -> InvalidInputError:
    return InvalidInputError(message, source=SourceType.SAPN_INTERVAL.value, reason=reason)


def _date(text: str) -> date | None:
    if len(text) != 8 or not text.isdigit():
        return None
    try:
        return datetime.strptime(text, "%Y%m%d").date()
    except ValueError:
        return None


def _datetime(text: str, pattern: str, digits: int) -> datetime | None:
    if len(text) != digits or not text.isdigit():
        return None
    try:
        return datetime.strptime(text, pattern)
    except ValueError:
        return None


@dataclass
class _Scan:
    """What `inspect` collects while it walks the records."""

    header: list[str] | None = None  # the 100 record's fields; its DateTime is checked after the records
    interval: int | None = None
    headers: int = 0
    suffixes: set[str] = field(default_factory=set)
    nmis: list[str] = field(default_factory=list)
    serials: list[str] = field(default_factory=list)
    dates: set[date] = field(default_factory=set)
    v_dates: set[date] = field(default_factory=set)
    seen: set[tuple[str, str, date]] = field(default_factory=set)
    latest_update: datetime | None = None


class SapnIntervalParser:
    """The `SourceParser` for `sapn_interval` (D1 §4.3, D3 §5.2)."""

    source: SourceType = SourceType.SAPN_INTERVAL
    version: int = 1

    def detect(self, filename: str, head: bytes) -> Detection | None:
        evidence: list[Evidence] = []
        if _FILENAME.match(filename):
            evidence.append("filename")
        text = head.decode("utf-8", errors="replace")
        first = next((line for line in _lines(text) if line.strip()), None)
        if first is not None and _content_matches(first):
            evidence.append("content")
        return Detection(source=self.source, evidence=tuple(evidence)) if evidence else None

    def inspect(self, filename: str, data: bytes) -> SourceInfo:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            raise _fail("The file isn't UTF-8 text.", "not_utf8") from None
        scan = _scan(_lines(text))  # the record checks first, D3 §5.2's order: then the 100 DateTime, the filename
        header_dt = None if scan.header is None else _header_100(scan.header)
        window, generated = _filename_facts(filename)
        if not scan.dates:
            raise _fail("The file has no 300 (interval data) records.", "no_data")
        if generated is not None:
            source_ts, source_ts_from = generated, "filename"
        elif header_dt is not None:
            source_ts, source_ts_from = header_dt, "nem12_header"
        else:
            assert scan.latest_update is not None
            source_ts, source_ts_from = scan.latest_update, "content"
        extra: dict[str, JSONValue] = {
            "interval_minutes": scan.interval,
            "nmi_suffixes": sorted(scan.suffixes),
            "uom": "KWH",
            "days": len(scan.dates),
            "days_v": len(scan.v_dates),
            "header_records": scan.headers,
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
            coverage=Coverage(start=min(scan.dates), end=max(scan.dates)),
            extra=extra,
            identifiers={"nmi": nmis, "meter_serial": scan.serials},
            extension=".csv",
        )


def _filename_facts(filename: str) -> tuple[tuple[date, date] | None, datetime | None]:
    """The requested window and generation time from a SAPN name; an invalid date or time → `bad_filename`."""
    match = _FILENAME.match(filename)
    if match is None:
        return None, None
    start, end = _date(match["from"]), _date(match["to"])
    generated = _datetime(match["gen"], "%Y%m%d%H%M%S", 14)
    if start is None or end is None or generated is None:
        raise _fail("The SAPN file name holds an invalid date or time.", "bad_filename")
    return (start, end), generated


def _scan(lines: list[str]) -> _Scan:
    """One pass for the record types and the file's shape, then one over the records, in D3 §5.2's order."""
    records = [line.split(",") for line in lines if line.strip()]
    for position, fields in enumerate(records):
        record = fields[0].strip()
        if record not in _RECORD_TYPES:
            raise _fail("The file holds an unknown record type.", "unknown_record")
        if record == "100" and (position != 0 or len(fields) < 3 or fields[1].strip() != "NEM12"):
            raise _fail("A 100 header record isn't first, or isn't a NEM12 header.", "bad_header")
    if not records or records[-1][0].strip() != "900":
        raise _fail("The file doesn't end with a 900 record: it may be truncated.", "truncated")
    scan = _Scan()
    current: tuple[str, str] | None = None  # (NMI, suffix) of the last 200
    after: str | None = None  # what a 400 may follow: "300v" or "400"
    for position, fields in enumerate(records):
        match fields[0].strip():
            case "100":
                scan.header = fields
            case "200":
                current = _header_200(fields, scan)
                after = None
            case "300":
                if current is None:
                    raise _fail("A 300 record comes before any 200 record.", "orphan_300")
                after = _interval_300(fields, current, scan)
            case "400":
                if after not in {"300v", "400"}:
                    raise _fail("A 400 record doesn't follow a 300 record of quality V.", "unexpected_400")
                _quality_400(fields, scan)
                after = "400"
            case "500":
                after = None
            case _:  # 900
                if position != len(records) - 1:
                    raise _fail("A 900 record isn't the last record.", "misplaced_900")
    return scan


def _header_100(fields: list[str]) -> datetime:
    header_dt = _datetime(fields[2].strip(), "%Y%m%d%H%M", 12)
    if header_dt is None:
        raise _fail("The 100 header record's DateTime is invalid.", "bad_header")
    return header_dt


def _header_200(fields: list[str], scan: _Scan) -> tuple[str, str]:
    if len(fields) < 10:
        raise _fail("A 200 record has fewer than 10 fields.", "bad_200")
    interval = _INTERVALS.get(fields[8].strip())
    if interval is None:
        raise _fail("A 200 record has an unsupported interval length.", "bad_interval")
    if scan.interval is not None and interval != scan.interval:
        raise _fail("The 200 records give different interval lengths.", "mixed_interval")
    if fields[7].strip().upper() != "KWH":
        raise _fail("A 200 record's unit isn't KWH.", "bad_unit")
    scan.interval = interval
    scan.headers += 1
    nmi, suffix, serial = fields[1].strip(), fields[4].strip(), fields[6].strip()
    scan.suffixes.add(suffix)
    if nmi and nmi not in scan.nmis:
        scan.nmis.append(nmi)
    if serial and serial not in scan.serials:
        scan.serials.append(serial)
    return nmi, suffix


def _interval_300(fields: list[str], current: tuple[str, str], scan: _Scan) -> str:
    assert scan.interval is not None
    values = 1440 // scan.interval
    if len(fields) != 2 + values + 5:
        raise _fail("A 300 record has the wrong number of fields for its interval length.", "bad_field_count")
    day = _date(fields[1].strip())
    if day is None:
        raise _fail("A 300 record has an invalid date.", "bad_date")
    update = _datetime(fields[2 + values + 3].strip(), "%Y%m%d%H%M%S", 14)
    if update is None:
        raise _fail("A 300 record has a missing or invalid UpdateDateTime.", "bad_update_time")
    key = (*current, day)
    if key in scan.seen:
        raise _fail("A day appears twice for one NMI and suffix.", "duplicate_day")
    scan.seen.add(key)
    scan.dates.add(day)
    if scan.latest_update is None or update > scan.latest_update:
        scan.latest_update = update
    if fields[2 + values].strip().upper().startswith("V"):
        scan.v_dates.add(day)
        return "300v"
    return "300a"


def _quality_400(fields: list[str], scan: _Scan) -> None:
    assert scan.interval is not None
    last = 1440 // scan.interval
    try:
        start, end = int(fields[1]), int(fields[2])
    except IndexError, ValueError:
        raise _fail("A 400 record's interval range is invalid.", "bad_quality_range") from None
    if not 1 <= start <= end <= last:
        raise _fail("A 400 record's interval range lies outside the day.", "bad_quality_range")


SAPN_INTERVAL: Final = SapnIntervalParser()
"""The registered instance (`registry.PARSERS`)."""
