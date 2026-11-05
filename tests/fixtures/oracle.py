"""An independent oracle for the P2 tests (P2 spec §13.2). Every value is made up.

It imports nothing from `energatron`, so a test that compares code against it isn't checking the code against
itself. It reads a NEM12 text with plain `split`, converts meter times to local times from fixed offsets and the
daylight-saving dates of `sa_dst_dates`, and sums energy by time-of-use window. The zone is South Australia's: UTC+9:30
in standard time and UTC+10:30 in daylight time.

The P3 half (P3 spec §13.2) works on a *local table spec*: a list of `LocalInterval`, each already on the local wall
clock. It sums by local day, time-of-use label and clock hour, finds the aligned-block peak, takes a percentile by the
linear rule written out, and repeats the TOU sums on the meter clock (`meter_tou_by_day`) for the §12 reconciliation.

The P4 half (P4 spec §14) prices a plain-dict tariff interval by interval (`oracle_bill`).
"""

import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from fractions import Fraction
from typing import Any

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


# The P4 half (P4 spec §14): a bill computed interval by interval, from a plain-dict tariff.
#
# `OracleTariff` is a dict: {"seasons": [{"start": "MM-DD", "end": "MM-DD", "clock": "LOCAL" | "AEST",
# "supply": Decimal, "charges": [{"label", "windows": [(days, start, end)], "period": "P1D" | "P1M",
# "blocks": [(volume | None, price)]}], "demand": [{"days", "start", "end", "amount", "measurement", "charge",
# "min_kw", "max_kw"}]}], "discounts": [{"kind", "category", "method", "rate"}], "fees": [{"term", "amount"}]}.
# Windows are half-open minute ranges of the clock's day; an end not after the start wraps past midnight.

WEEKDAYS = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")


@dataclass(frozen=True)
class UsageRow:
    utc_start: datetime  # naive UTC
    local_start: datetime  # naive local wall clock
    local_date: date
    meter_date: date
    minutes: int
    kwh: float


def _clock_time(row: UsageRow, clock: str) -> datetime:
    return row.local_start if clock == "LOCAL" else row.utc_start + NEM_OFFSET


def _in_range(text: str, start: str, end: str) -> bool:
    return start <= text <= end if start <= end else text >= start or text <= end


def _in_window(minute: int, start: int, end: int) -> bool:
    return start <= minute < end if end > start else minute >= start or minute < end


def _season_of(tariff: dict, md: str) -> int:
    matches = [index for index, season in enumerate(tariff["seasons"]) if _in_range(md, season["start"], season["end"])]
    assert len(matches) == 1, "the seasons partition the year"
    return matches[0]


def _round(value: Any) -> Any:
    from decimal import ROUND_HALF_UP, Decimal

    return Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def oracle_bill(
    tariff: dict,
    rows: Sequence[UsageRow],
    periods: Sequence[tuple[date, date]],
    *,
    gst_rate: Any,
    conditions: frozenset[str],
    scenario: str,
    demand_minutes: int,
    by: str = "local_date",
) -> dict[str, Any]:
    """Each period's total and its usage quantities by `(season, label, block)`: plain loops, no shared code."""
    from decimal import Decimal

    totals: dict[tuple[date, date], Any] = {}
    usage_kwh: dict[tuple[date, date, int, str, int], float] = {}
    for first, last in periods:
        chosen = [row for row in rows if first <= getattr(row, by) <= last]
        groups: dict[tuple[int, int, object], float] = defaultdict(float)
        demand_blocks: dict[tuple[int, int, date, int], list[float]] = defaultdict(lambda: [0.0, 0.0])
        for row in chosen:
            first_clock = _clock_time(row, tariff["seasons"][0]["clock"])
            s_index = _season_of(tariff, first_clock.strftime("%m-%d"))
            season = tariff["seasons"][s_index]
            when = _clock_time(row, season["clock"])
            minute = when.hour * 60 + when.minute
            weekday = WEEKDAYS[when.weekday()]
            found = [
                index
                for index, charge in enumerate(season["charges"])
                for days, start, end in charge["windows"]
                if weekday in days and _in_window(minute, start, end)
            ]
            assert len(found) == 1, "the windows partition the week"
            charge = season["charges"][found[0]]
            group = when.date() if charge["period"] == "P1D" else "period"
            groups[(s_index, found[0], group)] += row.kwh
            for d_index, demand in enumerate(season["demand"]):
                if weekday not in demand["days"]:
                    continue
                block_start = (minute // demand_minutes) * demand_minutes
                cell = demand_blocks[(s_index, d_index, when.date(), block_start)]
                cell[0] += row.kwh
                cell[1] += row.minutes
        quantities: dict[tuple[int, int, int], float] = defaultdict(float)
        for (s_index, c_index, _), total in sorted(groups.items(), key=lambda item: str(item[0])):
            remaining = total
            for b_index, (volume, _price) in enumerate(tariff["seasons"][s_index]["charges"][c_index]["blocks"]):
                take = remaining if volume is None else min(remaining, float(volume))
                quantities[(s_index, c_index, b_index)] += take
                remaining -= take
        usage_amount = Decimal(0)
        for (s_index, c_index, b_index), qty in quantities.items():
            charge = tariff["seasons"][s_index]["charges"][c_index]
            exact = Decimal(f"{qty:.6f}")
            usage_amount += _round(exact * charge["blocks"][b_index][1])
            usage_kwh[(first, last, s_index, charge["label"], b_index)] = qty
        supply = Decimal(0)
        day = first
        season_days: dict[int, int] = defaultdict(int)
        while day <= last:
            season_days[_season_of(tariff, day.strftime("%m-%d"))] += 1
            day += timedelta(days=1)
        for s_index, count in season_days.items():
            supply += _round(Decimal(count) * tariff["seasons"][s_index]["supply"])
        demand_amount = Decimal(0)
        for s_index, season in enumerate(tariff["seasons"]):
            for d_index, demand in enumerate(season["demand"]):
                per_day: dict[date, float] = {}
                for (bs, bd, when_day, block_start), (energy, minutes) in demand_blocks.items():
                    if (bs, bd) != (s_index, d_index) or minutes != demand_minutes:
                        continue
                    if not all(
                        _in_window(m, demand["start"], demand["end"])
                        for m in (block_start, block_start + demand_minutes - 1)
                    ):
                        continue
                    kw = energy * 60.0 / demand_minutes
                    per_day[when_day] = max(per_day.get(when_day, 0.0), kw)

                def chargeable(peak: float, demand: dict = demand) -> float:
                    capped = peak if demand["max_kw"] is None else min(peak, float(demand["max_kw"]))
                    return max(0.0, capped - float(demand["min_kw"]))

                if demand["measurement"] == "DAY":
                    daily = [chargeable(value) for value in per_day.values()]
                    measured = math.fsum(daily) if demand["charge"] == "DAY" else max(daily, default=0.0)
                else:
                    peak = chargeable(max(per_day.values(), default=0.0)) if per_day else 0.0
                    measured = peak * season_days.get(s_index, 0) if demand["charge"] == "DAY" else peak
                demand_amount += _round(Decimal(f"{measured:.6f}") * demand["amount"])
        base = usage_amount + supply + demand_amount
        discounts = Decimal(0)
        for discount in tariff["discounts"]:
            applies = discount["kind"] == "guaranteed" or (
                scenario != "conditions_not_met" and discount["category"] in conditions
            )
            if applies:
                discount_base = base if discount["method"] == "percent_of_bill" else usage_amount
                discounts -= _round(discount_base * discount["rate"])
        net = base + discounts
        gst = _round(net * gst_rate)
        fee_days = {
            "ANNUAL": Decimal(365),
            "BIANNUAL": Decimal("182.5"),
            "MONTHLY": Decimal(365) / 12,
            "WEEKLY": Decimal(7),
            "DAILY": Decimal(1),
        }
        days = Decimal((last - first).days + 1)
        fees = sum((_round(fee["amount"] * days / fee_days[fee["term"]]) for fee in tariff["fees"]), Decimal(0))
        totals[(first, last)] = net + gst + fees
    return {"totals": totals, "usage_kwh": usage_kwh}


# The gas engine (P5 spec §9.2)


def oracle_gas_totals(
    tariff: Mapping[str, Any],
    periods: Sequence[tuple[date, date, Decimal]],
    *,
    scenario: str,
    gst_rate: Decimal,
    conditions: frozenset[str],
) -> dict[tuple[date, date], Decimal]:
    """Each period's total, day by day in exact fractions: each day gets `mj / D`; the day's season adds a day of
    supply and of each block's allowance; usage then fills the season's blocks over the whole period. `tariff` is plain
    data: `seasons` (each `days`, a set of `MM-DD`, `supply`, `period`, `blocks` of `(volume | None, price)`),
    `discounts` and `fees` as in `oracle_bill`."""
    totals: dict[tuple[date, date], Decimal] = {}
    for first, last, mj in periods:
        days = (last - first).days + 1
        per_day = Fraction(mj) / days
        used: dict[int, Fraction] = {}
        counted: dict[int, int] = {}
        for offset in range(days):
            text = (first + timedelta(days=offset)).strftime("%m-%d")
            index = next(i for i, season in enumerate(tariff["seasons"]) if text in season["days"])
            used[index] = used.get(index, Fraction(0)) + per_day
            counted[index] = counted.get(index, 0) + 1
        usage = Decimal(0)
        supply = Decimal(0)
        for index, energy in used.items():
            season = tariff["seasons"][index]
            supply += _round(Decimal(counted[index]) * season["supply"])
            left = energy
            for volume, price in season["blocks"]:
                if left <= 0:
                    break
                if volume is None:
                    taken = left
                else:
                    scale = Fraction(12, 365) if season["period"] == "P1M" else Fraction(1)
                    taken = min(left, Fraction(volume) * counted[index] * scale)
                left -= taken
                usage += _round(Decimal(taken.numerator) / Decimal(taken.denominator) * price)
        base = usage + supply
        discounts = Decimal(0)
        for discount in tariff["discounts"]:
            applies = discount["kind"] == "guaranteed" or (
                scenario != "conditions_not_met" and discount["category"] in conditions
            )
            if applies:
                discounts -= _round((base if discount["method"] == "percent_of_bill" else usage) * discount["rate"])
        net = base + discounts
        fee_days = {"ANNUAL": Decimal(365), "MONTHLY": Decimal(365) / 12, "DAILY": Decimal(1)}
        fees = sum((_round(fee["amount"] * days / fee_days[fee["term"]]) for fee in tariff["fees"]), Decimal(0))
        totals[(first, last)] = net + _round(net * gst_rate) + fees
    return totals
