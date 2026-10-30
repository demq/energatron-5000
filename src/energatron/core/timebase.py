"""The time-basis hypotheses and the time-of-use windows (P2 spec §9.1).

The meter files state naive times, and which real clock they run on is unknown until `validate_timebase` has compared
them with the retailer's time-of-use export. Each hypothesis is one way to turn a meter time into an instant and a
local wall clock:

- `nem_aest` (H1): the meter runs on NEM time, UTC+10 all year.
- `local_wall` (H2): the meter time already is the local wall clock.
- `local_standard` (H3): the meter runs on the zone's standard time all year.

Pure: no clock, no I/O, and no zone is built here. The zone is always an object the caller got from
`StageRun.zone()` (P2 spec §6). `parse_windows` applies the rules of `validation.timebase.windows` (§5.1); `bucket`
gives each local time the label of the window that holds it.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from enum import StrEnum
from typing import Final

import numpy as np
import pandas as pd

from energatron.core.models._common import window_minutes, windows_partition_the_day

NEM_UTC_OFFSET: Final = timedelta(hours=10)
"""NEM time is UTC+10 with no daylight saving, in every NEM region."""
_WINDOWS_FIELD: Final = "validation.timebase.windows"
_MINUTES_PER_DAY: Final = 1440
_NOON: Final = time(12)


class Timebase(StrEnum):
    NEM_AEST = "nem_aest"  # H1: meter time is UTC+10 all year
    LOCAL_WALL = "local_wall"  # H2: meter time is already the local wall clock
    LOCAL_STANDARD = "local_standard"  # H3: meter time is local standard time all year


@dataclass(frozen=True)
class Window:
    """One parsed `HH:MM-HH:MM` entry. The window wraps past midnight when `end_minute <= start_minute`."""

    start_minute: int  # minutes since local midnight, 0…1435
    end_minute: int  # exclusive


@dataclass(frozen=True, eq=False)
class LocalTimes:
    """One entry per input row, with the input's index. A row that can't be placed has a null in every column."""

    utc_start: pd.Series  # datetime64[us, UTC]; NaT where unplaceable
    local_start: pd.Series  # naive wall clock in the zone; NaT where unplaceable
    utc_offset_minutes: pd.Series  # Int16; the zone's offset at that instant; <NA> where unplaceable
    unplaceable: pd.Series  # bool


def parse_windows(windows: Mapping[str, Sequence[str]]) -> dict[str, tuple[Window, ...]]:
    """The windows by label, parsed; label order kept.

    The rules of P2 spec §5.1: every window is `HH:MM-HH:MM` on a 5-minute boundary, every label has one, and together
    they cover every minute of the day exactly once. Otherwise `E_INVALID_INPUT`, `details.field` naming
    `validation.timebase.windows`; the message never repeats a window.
    """
    windows_partition_the_day(windows, field=_WINDOWS_FIELD)
    parsed: dict[str, tuple[Window, ...]] = {}
    for label, texts in windows.items():
        parsed[label] = tuple(_window(text) for text in texts)
    return parsed


def _window(text: str) -> Window:
    minutes = window_minutes(text)
    start = minutes[0]
    end = (minutes[-1] + 1) % _MINUTES_PER_DAY
    return Window(start_minute=start, end_minute=end)


def to_local(meter_start: pd.Series, basis: Timebase, zone: tzinfo) -> LocalTimes:
    """Place naive meter times on the real clock under `basis` in `zone`.

    `nem_aest`: the meter time at UTC+10 is the instant. `local_standard`: the instant comes from the zone's standard
    offset on the meter date (its offset at 12:00 minus its daylight saving, taken once per distinct date).
    `local_wall`: the meter time is read as wall time in the zone; a time that doesn't exist (spring forward) or occurs
    twice (fall back) is `unplaceable`. The result has the input's index.
    """
    match Timebase(basis):
        case Timebase.NEM_AEST:
            return _from_instants((meter_start - NEM_UTC_OFFSET).dt.tz_localize(UTC), zone)
        case Timebase.LOCAL_STANDARD:
            return _from_instants((meter_start - _standard_offsets(meter_start, zone)).dt.tz_localize(UTC), zone)
        case Timebase.LOCAL_WALL:
            return _from_wall(meter_start, zone)


def local_clock(meter_start: pd.Series, basis: Timebase, zone: tzinfo) -> pd.Series:
    """The naive local wall clock of each meter time under `basis`.

    For `local_wall` that is the meter time itself, so a time inside a daylight-saving hour is still returned;
    `to_local` would mark it unplaceable. This is all that matching time-of-use windows needs (P2 spec §9.1).
    """
    if Timebase(basis) is Timebase.LOCAL_WALL:
        return meter_start
    return to_local(meter_start, basis, zone).local_start


def bucket(local_start: pd.Series, windows: Mapping[str, Sequence[Window]]) -> pd.Series:
    """The label of the window that holds each local start; null for a null time or a minute no window holds.

    Only the minute of the day counts. With 5-minute data and window edges on 5-minute boundaries, no interval
    straddles an edge (P2 spec §9.1).
    """
    labels = np.full(_MINUTES_PER_DAY, None, dtype=object)
    for label, label_windows in windows.items():
        for window in label_windows:
            if window.end_minute > window.start_minute:
                labels[window.start_minute : window.end_minute] = label
            else:
                labels[window.start_minute :] = label
                labels[: window.end_minute] = label
    present = local_start.notna()
    minute = np.zeros(len(local_start), dtype=np.int64)
    minute[present.to_numpy()] = (local_start[present].dt.hour * 60 + local_start[present].dt.minute).to_numpy()
    found = labels[minute]
    found[~present.to_numpy()] = None
    return pd.Series(found, index=local_start.index, dtype=object)


# the conversions


def _from_instants(utc: pd.Series, zone: tzinfo) -> LocalTimes:
    """Every row is placeable: `utc` holds instants, and a zone converts any instant."""
    local = utc.dt.tz_convert(zone).dt.tz_localize(None)
    offset = (local - utc.dt.tz_localize(None)) // timedelta(minutes=1)
    return LocalTimes(
        utc_start=utc.astype("datetime64[us, UTC]"),
        local_start=local.astype("datetime64[us]"),
        utc_offset_minutes=offset.astype("Int16"),
        unplaceable=pd.Series(False, index=utc.index, dtype=bool),
    )


def _from_wall(meter_start: pd.Series, zone: tzinfo) -> LocalTimes:
    placed = meter_start.dt.tz_localize(zone, ambiguous="NaT", nonexistent="NaT")
    unplaceable = placed.isna() & meter_start.notna()
    utc = placed.dt.tz_convert(UTC)
    offset = (placed.dt.tz_localize(None) - utc.dt.tz_localize(None)) // timedelta(minutes=1)
    return LocalTimes(
        utc_start=utc.astype("datetime64[us, UTC]"),
        local_start=meter_start.where(~unplaceable).astype("datetime64[us]"),
        utc_offset_minutes=offset.astype("Int16"),
        unplaceable=unplaceable.astype(bool),
    )


def _standard_offsets(meter_start: pd.Series, zone: tzinfo) -> pd.Series:
    """The zone's standard offset on each row's meter date, as a timedelta series (one lookup per distinct date)."""
    dates = meter_start.dt.normalize()
    offsets = {day: _standard_offset(day.date(), zone) for day in dates.dropna().unique()}
    return dates.map(offsets).astype("timedelta64[us]")


def _standard_offset(day: date, zone: tzinfo) -> timedelta:
    """`utcoffset` minus `dst` of the zone at 12:00 wall time on `day`, first occurrence (`fold=0`)."""
    noon = datetime.combine(day, _NOON, tzinfo=zone)
    offset, saving = noon.utcoffset(), noon.dst()
    if offset is None or saving is None:
        raise ValueError("the zone has no offset")
    return offset - saving
