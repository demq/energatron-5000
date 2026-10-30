"""An independent oracle for the P2 tests (P2 spec §13.2). Every value is made up.

It imports nothing from `energatron`, so a test that compares code against it isn't checking the code against
itself. It reads a NEM12 text with plain `split`, converts meter times to local times from fixed offsets and the
daylight-saving dates of `sa_dst_dates`, and sums energy by time-of-use window. The zone is South Australia's: UTC+9:30
in standard time and UTC+10:30 in daylight time.
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
