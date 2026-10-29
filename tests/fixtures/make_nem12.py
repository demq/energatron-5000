"""Synthetic NEM12 interval files and SAPN file names (D3 §12.1). Every value is made up.

Deterministic: the same `Nem12Spec` gives the same bytes. Tests read the facts they expect (coverage, days, `V`
days) from the spec, never from the values. Malformed variants are made by the tests, by editing the output.
"""

import random
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta
from typing import Any, Literal

from energatron.core.privacy import nmi_checksum
from tests.fixtures.synthetic import SYNTHETIC_NMI, SYNTHETIC_SERIAL


@dataclass(frozen=True)
class QualityRange:  # one 400 record
    start: int  # 1-based interval index
    end: int
    method: str  # e.g. "A", "S14", "E52", "F17"
    reason: str = ""


@dataclass(frozen=True)
class Nem12Spec:
    start: date
    days: int
    nmi: str = SYNTHETIC_NMI  # 10 characters, as in the 200 record
    meter_serial: str = SYNTHETIC_SERIAL
    suffixes: tuple[str, ...] = ("E1",)
    interval_minutes: int = 5  # 5, 15 or 30
    header_100: bool = False
    repeat_200_every: int | None = None  # repeat the 200 header every n days, SAPN-style
    blank_line_before_repeat: bool = True  # SAPN puts a blank line before each repeated 200
    v_days: Mapping[date, tuple[QualityRange, ...]] = field(default_factory=dict)
    update_dt: Callable[[date], datetime] | None = None  # default: the next day at 05:00:00
    seed: int = 0
    trailer_900: bool = True
    line_ending: str = "\n"


def _update_dt(spec: Nem12Spec, day: date) -> datetime:
    if spec.update_dt is not None:
        return spec.update_dt(day)
    return datetime.combine(day + timedelta(days=1), time(5, 0, 0))


def _dates(spec: Nem12Spec) -> list[date]:
    return [spec.start + timedelta(days=offset) for offset in range(spec.days)]


def nem12_header_dt(spec: Nem12Spec) -> datetime:
    """The `100` record's DateTime: one hour after the latest `UpdateDateTime`, to the minute."""
    latest = max((_update_dt(spec, day) for day in _dates(spec)), default=datetime.combine(spec.start, time()))
    return (latest + timedelta(hours=1)).replace(second=0, microsecond=0)


def _values(rng: random.Random, intervals: int, minutes: int) -> list[str]:
    hours = minutes / 60
    values: list[str] = []
    for index in range(intervals):
        hour = index * 24 / intervals
        shape = 0.6 if 7 <= hour < 9 else 1.4 if 17 <= hour < 21 else 0.3 if hour < 6 else 0.5
        kw = 0.2 + shape * rng.random()
        values.append(f"{max(kw * hours, 0.0):.3f}")
    return values


def make_nem12(spec: Nem12Spec) -> bytes:
    """A NEM12 file as SAPN writes it: per suffix, a `200` then one `300` per day (and its `400`s), then `900`."""
    if 1440 % spec.interval_minutes:
        raise ValueError("the interval must divide a day")
    intervals = 1440 // spec.interval_minutes
    rng = random.Random(spec.seed)
    rows: list[str] = []
    if spec.header_100:
        rows.append(f"100,NEM12,{nem12_header_dt(spec):%Y%m%d%H%M},MDPSYN,RETSYN")
    for register, suffix in enumerate(spec.suffixes, start=1):
        header = (
            f"200,{spec.nmi},{''.join(spec.suffixes)},{register},{suffix},{suffix},{spec.meter_serial},KWH,"
            f"{spec.interval_minutes},"
        )
        for offset, day in enumerate(_dates(spec)):
            if offset == 0:
                rows.append(header)
            elif spec.repeat_200_every and offset % spec.repeat_200_every == 0:
                if spec.blank_line_before_repeat:
                    rows.append("")
                rows.append(header)
            ranges = spec.v_days.get(day)
            quality = "V" if ranges else "A"
            stamp = f"{_update_dt(spec, day):%Y%m%d%H%M%S}"
            values = ",".join(_values(rng, intervals, spec.interval_minutes))
            rows.append(f"300,{day:%Y%m%d},{values},{quality},,,{stamp},{stamp}")
            rows.extend(f"400,{item.start},{item.end},{item.method},{item.reason}," for item in ranges or ())
    if spec.trailer_900:
        rows.append("900")
    return "".join(row + spec.line_ending for row in rows).encode("utf-8")


def sapn_filename(
    kind: Literal["DETAILED", "SUMMARY"],
    nmi10: str,
    window: tuple[date, date],
    generated: datetime,
    ext: str = "csv",
) -> str:
    """`<NMI with checksum digit>_<from>_<to>_<generated>_SAPN_<kind>.<ext>`; the helper adds the checksum digit."""
    start, end = window
    return f"{nmi10}{nmi_checksum(nmi10)}_{start:%Y%m%d}_{end:%Y%m%d}_{generated:%Y%m%d%H%M%S}_SAPN_{kind}.{ext}"


def _first_sunday(year: int, month: int) -> date:
    first = date(year, month, 1)
    return first + timedelta(days=(6 - first.weekday()) % 7)


def sa_dst_dates(year: int) -> tuple[date, date]:
    """South Australia's daylight-saving changes: the first Sunday in April (ends) and in October (starts)."""
    return _first_sunday(year, 4), _first_sunday(year, 10)


def dst_spec(year: int, **kw: Any) -> Nem12Spec:
    """A spec covering both of `year`'s changes, from three days before the first to three days after the second."""
    april, october = sa_dst_dates(year)
    start = april - timedelta(days=3)
    spec = Nem12Spec(start=start, days=(october - start).days + 4)
    return replace(spec, **kw)
