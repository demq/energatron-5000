"""The NEM12 interval-data reader (P2 spec §4.1; D3 §5.2).

Pure: bytes in, frozen models or a DataFrame out, no I/O. `scan_nem12` is what `inspect` calls: the facts plus the
identifiers for the premises check. `read_nem12` is what stages call: the same facts and every interval, and **no
identifier**, because its result types have no identifier field. Both run one private walk over the records, so they
accept exactly the same files; `read_nem12` then checks the values, which `scan_nem12` leaves alone. Errors are
`E_INVALID_INPUT` with a fixed `reason` word and never carry content, a file name or an identifier.
"""

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Final

import numpy as np
import pandas as pd

from energatron.core.errors import InvalidInputError
from energatron.core.models.catalog import Coverage, SourceType
from energatron.core.text import add_unique, compact_date, compact_datetime, decode, fail, is_decimal, lines

_SOURCE: Final = SourceType.SAPN_INTERVAL
_INTERVALS: Final = {"5": 5, "05": 5, "15": 15, "30": 30}
_RECORD_TYPES: Final = frozenset({"100", "200", "300", "400", "500", "900"})
_INTEGER: Final = re.compile(r"[+-]?\d+")
_INT16_MAX: Final = 32767


@dataclass(frozen=True)
class Nem12Facts:
    """What `inspect` and the stages both use, in D3 §5.2's terms. No identifiers."""

    interval_minutes: int
    suffixes: tuple[str, ...]  # sorted
    days: int  # distinct 300 dates
    days_v: int
    header_records: int
    header_dt: datetime | None  # the 100 record's DateTime
    latest_update: datetime
    coverage: Coverage


@dataclass(frozen=True)
class Nem12Scan:
    """Returned to `inspect` only: the identifiers are for the premises check and nothing else."""

    facts: Nem12Facts
    nmis: tuple[str, ...]
    serials: tuple[str, ...]


@dataclass(frozen=True, eq=False)
class Nem12Read:
    """Returned to stages: no identifier field."""

    facts: Nem12Facts
    table: pd.DataFrame  # the staged columns of P2 spec §7.1, except `dataset_id`
    v_coverage_faults: int  # V-day intervals covered by no 400 range, or by more than one


def scan_nem12(data: bytes) -> Nem12Scan:
    """Validate a NEM12 file and return its facts and identifiers (D3 §5.2's record checks, in its order)."""
    walk = _walk(decode(_SOURCE, data))
    return Nem12Scan(facts=_facts(walk), nmis=tuple(walk.nmis), serials=tuple(walk.serials))


def read_nem12(data: bytes) -> Nem12Read:
    """The same checks as `scan_nem12`, then every interval; a value that isn't a decimal → `bad_value`."""
    walk = _walk(decode(_SOURCE, data))
    facts = _facts(walk)
    table, faults = _table(walk)
    return Nem12Read(facts=facts, table=table, v_coverage_faults=faults)


# --- the walk: one pass for both entry points ------------------------------------------------------------------------


@dataclass
class _Day:
    """One 300 record with the 400 records that follow it."""

    suffix: str
    day: date
    fields: list[str]
    ranges: list[list[str]] = field(default_factory=list)


@dataclass
class _Walk:
    """What the walk collects from the records."""

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
    days: list[_Day] = field(default_factory=list)


def _fail(message: str, reason: str) -> InvalidInputError:
    return fail(_SOURCE, message, reason)


def _walk(text: str) -> _Walk:
    """One pass for the record types and the file's shape, then one over the records, in D3 §5.2's order.

    Then the `100` record's DateTime, and a file without `300` records is `no_data`.
    """
    records = [line.split(",") for line in lines(text) if line.strip()]
    for position, fields in enumerate(records):
        record = fields[0].strip()
        if record not in _RECORD_TYPES:
            raise _fail("The file holds an unknown record type.", "unknown_record")
        if record == "100" and (position != 0 or len(fields) < 3 or fields[1].strip() != "NEM12"):
            raise _fail("A 100 header record isn't first, or isn't a NEM12 header.", "bad_header")
    if not records or records[-1][0].strip() != "900":
        raise _fail("The file doesn't end with a 900 record: it may be truncated.", "truncated")
    walk = _Walk()
    current: tuple[str, str] | None = None  # (NMI, suffix) of the last 200
    day: _Day | None = None  # the last 300 of quality V, which a 400 may follow
    after: str | None = None  # what a 400 may follow: "300v" or "400"
    for position, fields in enumerate(records):
        match fields[0].strip():
            case "100":
                walk.header = fields
            case "200":
                current = _header_200(fields, walk)
                after = None
            case "300":
                if current is None:
                    raise _fail("A 300 record comes before any 200 record.", "orphan_300")
                day = _interval_300(fields, current, walk)
                after = "300v" if _is_v(fields, walk) else "300a"
            case "400":
                if after not in {"300v", "400"} or day is None:
                    raise _fail("A 400 record doesn't follow a 300 record of quality V.", "unexpected_400")
                _quality_400(fields, walk)
                day.ranges.append(fields)
                after = "400"
            case "500":
                after = None
            case _:  # 900
                if position != len(records) - 1:
                    raise _fail("A 900 record isn't the last record.", "misplaced_900")
    if walk.header is not None:
        _header_100(walk.header)
    if not walk.dates:
        raise _fail("The file has no 300 (interval data) records.", "no_data")
    return walk


def _facts(walk: _Walk) -> Nem12Facts:
    assert walk.interval is not None and walk.latest_update is not None
    return Nem12Facts(
        interval_minutes=walk.interval,
        suffixes=tuple(sorted(walk.suffixes)),
        days=len(walk.dates),
        days_v=len(walk.v_dates),
        header_records=walk.headers,
        header_dt=None if walk.header is None else _header_100(walk.header),
        latest_update=walk.latest_update,
        coverage=Coverage(start=min(walk.dates), end=max(walk.dates)),
    )


def _header_100(fields: list[str]) -> datetime:
    header_dt = compact_datetime(fields[2].strip(), "%Y%m%d%H%M", 12)
    if header_dt is None:
        raise _fail("The 100 header record's DateTime is invalid.", "bad_header")
    return header_dt


def _header_200(fields: list[str], walk: _Walk) -> tuple[str, str]:
    if len(fields) < 10:
        raise _fail("A 200 record has fewer than 10 fields.", "bad_200")
    interval = _INTERVALS.get(fields[8].strip())
    if interval is None:
        raise _fail("A 200 record has an unsupported interval length.", "bad_interval")
    if walk.interval is not None and interval != walk.interval:
        raise _fail("The 200 records give different interval lengths.", "mixed_interval")
    if fields[7].strip().upper() != "KWH":
        raise _fail("A 200 record's unit isn't KWH.", "bad_unit")
    walk.interval = interval
    walk.headers += 1
    nmi, suffix, serial = fields[1].strip(), fields[4].strip(), fields[6].strip()
    walk.suffixes.add(suffix)
    add_unique(walk.nmis, nmi)
    add_unique(walk.serials, serial)
    return nmi, suffix


def _interval_300(fields: list[str], current: tuple[str, str], walk: _Walk) -> _Day:
    assert walk.interval is not None
    values = 1440 // walk.interval
    if len(fields) != 2 + values + 5:
        raise _fail("A 300 record has the wrong number of fields for its interval length.", "bad_field_count")
    day = compact_date(fields[1].strip())
    if day is None:
        raise _fail("A 300 record has an invalid date.", "bad_date")
    update = compact_datetime(fields[2 + values + 3].strip(), "%Y%m%d%H%M%S", 14)
    if update is None:
        raise _fail("A 300 record has a missing or invalid UpdateDateTime.", "bad_update_time")
    key = (*current, day)
    if key in walk.seen:
        raise _fail("A day appears twice for one NMI and suffix.", "duplicate_day")
    walk.seen.add(key)
    walk.dates.add(day)
    if walk.latest_update is None or update > walk.latest_update:
        walk.latest_update = update
    if _is_v(fields, walk):
        walk.v_dates.add(day)
    entry = _Day(suffix=current[1], day=day, fields=fields)
    walk.days.append(entry)
    return entry


def _is_v(fields: list[str], walk: _Walk) -> bool:
    assert walk.interval is not None
    return fields[2 + 1440 // walk.interval].strip().upper().startswith("V")


def _quality_400(fields: list[str], walk: _Walk) -> None:
    assert walk.interval is not None
    last = 1440 // walk.interval
    try:
        start, end = int(fields[1]), int(fields[2])
    except IndexError, ValueError:
        raise _fail("A 400 record's interval range is invalid.", "bad_quality_range") from None
    if not 1 <= start <= end <= last:
        raise _fail("A 400 record's interval range lies outside the day.", "bad_quality_range")


# --- the table: only `read_nem12` ------------------------------------------------------------------------------------


def _reason_code(text: str) -> int | None:
    """An integer ReasonCode, or `None` for an empty field; anything else → `bad_value`."""
    text = text.strip()
    if not text:
        return None
    if _INTEGER.fullmatch(text) is None or abs(int(text)) > _INT16_MAX:
        raise _fail("A ReasonCode isn't an integer.", "bad_value")
    return int(text)


def _quality(text: str) -> str:
    text = text.strip()
    if not text:
        raise _fail("A QualityMethod is empty.", "bad_value")
    return text


@dataclass
class _Columns:
    """One day's columns, all `n` long."""

    kwh: np.ndarray
    quality: np.ndarray  # object
    reason: np.ndarray  # int16
    reason_null: np.ndarray  # bool
    faults: int


def _day_columns(entry: _Day, minutes: int) -> _Columns:
    n = 1440 // minutes
    fields = entry.fields
    raw = [value.strip() for value in fields[2 : 2 + n]]
    if not all(is_decimal(value) for value in raw):
        raise _fail("A value isn't a decimal.", "bad_value")
    kwh = np.array([float(value) for value in raw], dtype=np.float64)
    day_quality = _quality(fields[2 + n])
    day_reason = _reason_code(fields[2 + n + 1])
    quality = np.full(n, day_quality, dtype=object)
    reason = np.full(n, 0 if day_reason is None else day_reason, dtype=np.int16)
    null = np.full(n, day_reason is None, dtype=bool)
    faults = 0
    if day_quality.upper().startswith("V"):
        covered = np.zeros(n, dtype=np.int16)
        for fields_400 in entry.ranges:
            first, last = int(fields_400[1]), int(fields_400[2])
            method = _quality(fields_400[3] if len(fields_400) > 3 else "")
            code = _reason_code(fields_400[4] if len(fields_400) > 4 else "")
            quality[first - 1 : last] = method
            reason[first - 1 : last] = 0 if code is None else code
            null[first - 1 : last] = code is None
            covered[first - 1 : last] += 1
        faults = int(np.count_nonzero(covered != 1))
    return _Columns(kwh=kwh, quality=quality, reason=reason, reason_null=null, faults=faults)


def _table(walk: _Walk) -> tuple[pd.DataFrame, int]:
    assert walk.interval is not None
    minutes = walk.interval
    n = 1440 // minutes
    steps = (np.arange(n) * minutes).astype("timedelta64[m]")
    parts: list[_Columns] = []
    suffixes: list[np.ndarray] = []
    dates: list[np.ndarray] = []
    starts: list[np.ndarray] = []
    updates: list[np.ndarray] = []
    for entry in walk.days:
        parts.append(_day_columns(entry, minutes))
        suffixes.append(np.full(n, entry.suffix, dtype=object))
        dates.append(np.full(n, entry.day, dtype=object))
        starts.append(np.datetime64(entry.day.isoformat() + "T00:00:00", "us") + steps)
        update = compact_datetime(entry.fields[2 + n + 3].strip(), "%Y%m%d%H%M%S", 14)
        assert update is not None
        updates.append(np.full(n, np.datetime64(update.isoformat(timespec="seconds"), "us")))
    quality = pd.Series(np.concatenate([part.quality for part in parts]), dtype="str")
    table = pd.DataFrame(
        {
            "suffix": pd.Series(np.concatenate(suffixes), dtype="str"),
            "meter_date": pd.Series(np.concatenate(dates), dtype="object"),
            "meter_start": pd.Series(np.concatenate(starts), dtype="datetime64[us]"),
            "interval_minutes": pd.Series(np.full(len(walk.days) * n, minutes, dtype=np.int16)),
            "kwh": pd.Series(np.concatenate([part.kwh for part in parts]), dtype="float64"),
            "quality": quality,
            "quality_flag": quality.str.slice(0, 1),
            "reason_code": pd.Series(
                pd.arrays.IntegerArray(
                    np.concatenate([part.reason for part in parts]),
                    np.concatenate([part.reason_null for part in parts]),
                ),
                dtype="Int16",
            ),
            "update_dt": pd.Series(np.concatenate(updates), dtype="datetime64[us]"),
        }
    )
    table = table.sort_values(["suffix", "meter_start"], kind="stable").reset_index(drop=True)
    return table, sum(part.faults for part in parts)
