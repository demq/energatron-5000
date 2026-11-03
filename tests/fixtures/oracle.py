"""An independent oracle for the P2 tests (P2 spec §13.2). Every value is made up.

It imports nothing from `energatron`, so a test that compares code against it isn't checking the code against
itself. It reads a NEM12 text with plain `split`, converts meter times to local times from fixed offsets and the
daylight-saving dates of `sa_dst_dates`, and sums energy by time-of-use window. The zone is South Australia's: UTC+9:30
in standard time and UTC+10:30 in daylight time.

The P3 half (P3 spec §13.2) works on a *local table spec*: a list of `LocalInterval`, each already on the local wall
clock. It sums by local day, time-of-use label and clock hour, finds the aligned-block peak, takes a percentile by the
linear rule written out, and repeats the TOU sums on the meter clock (`meter_tou_by_day`) for the §12 reconciliation.
"""

import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta

BASES = ("nem_aest", "local_wall", "local_standard")
STANDARD_OFFSET = timedelta(hours=9, minutes=30)
DAYLIGHT_OFFSET = timedelta(hours=10, minutes=30)
NEM_OFFSET = timedelta(hours=10)


def _first_sunday(year: int, month: int) -> date:
    first = date(year, month, 1)
    return first + timedelta(days=(6 - first.weekday()) % 7)


def sa_dst_dates(year: int) -> tuple[date, date]:
    """South Australia's daylight-saving changes: the first Sunday in April (ends) and in October (starts)."""
    return _first_sunday(year, 4), _first_sunday(year, 10)


@dataclass(frozen=True)
class OracleInterval:
    suffix: str
    meter_start: datetime
    minutes: int
    kwh: float


def intervals(nem12: bytes) -> list[OracleInterval]:
    """Every interval of a NEM12 text, in file order."""
    suffix = ""
    minutes = 0
    found: list[OracleInterval] = []
    for line in nem12.decode("utf-8").removeprefix("﻿").splitlines():
        fields = line.split(",")
        if fields[0] == "200":
            suffix, minutes = fields[4], int(fields[8])
        elif fields[0] == "300":
            midnight = datetime.strptime(fields[1], "%Y%m%d")
            count = 1440 // minutes
            for index, value in enumerate(fields[2 : 2 + count]):
                found.append(
                    OracleInterval(suffix, midnight + timedelta(minutes=index * minutes), minutes, float(value))
                )
    return found


def day_sums(nem12: bytes) -> dict[tuple[str, date], float]:
    """The sum of each day's values, by `(suffix, meter date)`."""
    parts: dict[tuple[str, date], list[float]] = defaultdict(list)
    for item in intervals(nem12):
        parts[item.suffix, item.meter_start.date()].append(item.kwh)
    return {key: math.fsum(values) for key, values in parts.items()}


def period_sum(nem12: bytes, first: date, last: date, *, suffixes: Sequence[str] | None = None) -> float:
    """The sum over the meter dates `first…last` (both inclusive), for the given suffixes or every suffix."""
    return math.fsum(
        item.kwh
        for item in intervals(nem12)
        if first <= item.meter_start.date() <= last and (suffixes is None or item.suffix in suffixes)
    )


def _utc_instant(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, 2, 0) - STANDARD_OFFSET  # both changes happen at 02:00 standard time


def _local_offset(utc: datetime) -> timedelta:
    april, october = sa_dst_dates(utc.year)
    daylight = utc < _utc_instant(april) or utc >= _utc_instant(october)
    return DAYLIGHT_OFFSET if daylight else STANDARD_OFFSET


def local_time(meter_start: datetime, basis: str) -> datetime:
    """The local wall clock of a meter time under a basis, from fixed offsets and the daylight-saving dates.

    `nem_aest`: the meter is UTC+10 all year. `local_wall`: the meter time is the local wall clock.
    `local_standard`: the meter is UTC+9:30 all year.
    """
    match basis:
        case "nem_aest":
            utc = meter_start - NEM_OFFSET
        case "local_wall":
            return meter_start
        case "local_standard":
            utc = meter_start - STANDARD_OFFSET
        case _:
            raise ValueError(f"unknown basis {basis!r}")
    return utc + _local_offset(utc)


def utc_time(meter_start: datetime, basis: str) -> datetime:
    """The UTC instant of a meter time under a fixed-offset basis (`local_wall` has none and raises)."""
    match basis:
        case "nem_aest":
            return meter_start - NEM_OFFSET
        case "local_standard":
            return meter_start - STANDARD_OFFSET
        case _:
            raise ValueError(f"no fixed offset for basis {basis!r}")


def _minute_of(text: str) -> int:
    hours, minutes = text.split(":")
    return int(hours) * 60 + int(minutes)


def window_label(minute: int, windows: Mapping[str, Sequence[str]]) -> str:
    """The label whose `HH:MM-HH:MM` window holds a minute of the day (a window wraps when its end <= its start)."""
    for label, spans in windows.items():
        for span in spans:
            first, last = span.split("-")
            start, end = _minute_of(first), _minute_of(last)
            inside = start <= minute < end if start < end else minute >= start or minute < end
            if inside:
                return label
    raise ValueError(f"no window holds minute {minute}")


def tou_sums(items: Sequence[OracleInterval], windows: Mapping[str, Sequence[str]], basis: str) -> dict[str, float]:
    """The sum of the intervals by window label, each interval placed by its local start under the basis."""
    parts: dict[str, list[float]] = {label: [] for label in windows}
    for item in items:
        local = local_time(item.meter_start, basis)
        parts[window_label(local.hour * 60 + local.minute, windows)].append(item.kwh)
    return {label: math.fsum(values) for label, values in parts.items()}


# P3: sums over a local table spec


@dataclass(frozen=True)
class LocalInterval:
    """One interval of a local table spec. `utc_offset` tells the two passes through a repeated hour apart."""

    suffix: str
    local_start: datetime
    minutes: int
    kwh: float
    utc_offset: int = 570  # minutes; 9:30 is South Australia's standard time
    flag: str = "A"


WindowSets = Sequence[tuple[date | None, Mapping[str, Sequence[str]]]]
"""`(effective_from, windows)` pairs: the first has no start date, the others increase."""


def local_offset_minutes(meter_start: datetime) -> int:
    """The zone's UTC offset, in minutes, at a meter time read as UTC+10 (the `nem_aest` basis)."""
    return int((local_time(meter_start, "nem_aest") - utc_time(meter_start, "nem_aest")).total_seconds() // 60)


def local_day_sums(rows: Sequence[LocalInterval]) -> dict[tuple[str, date], float]:
    """The sum of each local day, by `(suffix, local date)`."""
    parts: dict[tuple[str, date], list[float]] = defaultdict(list)
    for row in rows:
        parts[row.suffix, row.local_start.date()].append(row.kwh)
    return {key: math.fsum(values) for key, values in parts.items()}


def window_set_index(day: date, sets: WindowSets) -> int:
    """The index of the window set in force on a local date: the latest one whose start is on or before it."""
    found = 0
    for index, (start, _) in enumerate(sets):
        if start is None or start <= day:
            found = index
    return found


def local_tou_sums(rows: Sequence[LocalInterval], sets: WindowSets) -> dict[tuple[str, date, str], float]:
    """The sum by `(suffix, local date, label)`, each interval labelled by the window set in force on its date."""
    parts: dict[tuple[str, date, str], list[float]] = defaultdict(list)
    for row in rows:
        day = row.local_start.date()
        windows = sets[window_set_index(day, sets)][1]
        label = window_label(row.local_start.hour * 60 + row.local_start.minute, windows)
        parts[row.suffix, day, label].append(row.kwh)
    return {key: math.fsum(values) for key, values in parts.items()}


def complete_dates(rows: Sequence[LocalInterval], day_minutes: Mapping[date, int]) -> set[tuple[str, date]]:
    """The `(suffix, local date)` pairs whose row count equals the day's length over the interval length."""
    counts: dict[tuple[str, date], int] = defaultdict(int)
    length: dict[str, int] = {}
    for row in rows:
        counts[row.suffix, row.local_start.date()] += 1
        length[row.suffix] = row.minutes
    return {(suffix, day) for (suffix, day), count in counts.items() if count == day_minutes[day] // length[suffix]}


def hour_cells(rows: Sequence[LocalInterval]) -> dict[tuple[str, date, int], tuple[float, int]]:
    """`(sum, row count)` by `(suffix, local date, wall-clock hour)`."""
    parts: dict[tuple[str, date, int], list[float]] = defaultdict(list)
    for row in rows:
        parts[row.suffix, row.local_start.date(), row.local_start.hour].append(row.kwh)
    return {key: (math.fsum(values), len(values)) for key, values in parts.items()}


def full_hour_cells(rows: Sequence[LocalInterval]) -> dict[tuple[str, date, int], float]:
    """The sums of the hour cells that hold exactly `60 / minutes` rows."""
    length = {row.suffix: row.minutes for row in rows}
    return {key: total for key, (total, count) in hour_cells(rows).items() if count == 60 // length[key[0]]}


def percentile(values: Sequence[float], q: float) -> float:
    """The q-th percentile (0-100) by linear interpolation between the closest ranks, written out."""
    ordered = sorted(values)
    position = (len(ordered) - 1) * q / 100
    low = math.floor(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def minute_in(minute: int, start: int, end: int) -> bool:
    """Whether a minute of the day is in `[start, end)`, the window wrapping past midnight when `end <= start`."""
    return start <= minute < end if start < end else minute >= start or minute < end


def aligned_blocks(rows: Sequence[LocalInterval], block_minutes: int) -> dict[tuple[str, datetime, int], float]:
    """The kW of each full block, by `(suffix, block start, utc offset)`.

    A block is `block_minutes` aligned to the local clock; the offset keeps the passes through a repeated hour apart.
    It is full when it holds `block_minutes / minutes` rows.
    """
    parts: dict[tuple[str, datetime, int], list[float]] = defaultdict(list)
    length: dict[str, int] = {}
    for row in rows:
        start = row.local_start.replace(minute=row.local_start.minute // block_minutes * block_minutes)
        if block_minutes >= 60:
            start = start.replace(minute=0)
        parts[row.suffix, start, row.utc_offset].append(row.kwh)
        length[row.suffix] = row.minutes
    return {
        key: math.fsum(values) * 60 / block_minutes
        for key, values in parts.items()
        if len(values) == block_minutes // length[key[0]]
    }


def monthly_peak(
    rows: Sequence[LocalInterval], block_minutes: int
) -> dict[tuple[str, str], tuple[float, datetime, float | None]]:
    """`(peak kW, the block's start, load factor)` by `(suffix, "YYYY-MM")`; the earliest block wins a tie."""
    found: dict[tuple[str, str], list[tuple[float, datetime]]] = defaultdict(list)
    for (suffix, start, _), kw in aligned_blocks(rows, block_minutes).items():
        found[suffix, f"{start.year:04d}-{start.month:02d}"].append((kw, start))
    result: dict[tuple[str, str], tuple[float, datetime, float | None]] = {}
    for key, blocks in found.items():
        peak = max(kw for kw, _ in blocks)
        at = min(start for kw, start in blocks if kw == peak)
        mean = math.fsum(kw for kw, _ in blocks) / len(blocks)
        result[key] = (peak, at, mean / peak if peak > 0 else None)
    return result


def meter_tou_by_day(
    items: Sequence[OracleInterval], windows: Mapping[str, Sequence[str]]
) -> dict[tuple[str, date, str], float]:
    """The sum by `(suffix, meter date, label)`, labelled by the meter clock itself (no conversion): the §12 view."""
    parts: dict[tuple[str, date, str], list[float]] = defaultdict(list)
    for item in items:
        label = window_label(item.meter_start.hour * 60 + item.meter_start.minute, windows)
        parts[item.suffix, item.meter_start.date(), label].append(item.kwh)
    return {key: math.fsum(values) for key, values in parts.items()}
