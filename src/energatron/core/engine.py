"""The cost engine (P4 spec §7): one register's metered intervals priced by a `Tariff`, line by line, in `Decimal`.

`prepare_usage(frame)` turns a register's rows (`utc_start`, `local_start`, `local_date`, `meter_date`, `kwh`) into
arrays once, for both tariff clocks; `price(tariff, usage, periods, ...)` then prices any number of tariffs over the
billing periods (inclusive date pairs, chosen on `local_date` or `meter_date`).

- **Clock:** `LOCAL` is `local_start`; `AEST` is `utc_start` + 10 h. Weekday and minute come from the season's clock;
  an interval is labelled by its start minute. The season of a row is chosen by the `MM-DD` of its date on the first
  season's clock (CDR tariffs use one clock throughout).
- **Energy:** per period, season and charge; blocks fill per tariff-clock day (`P1D`) or per period (`P1M`). Only a
  group's total matters for its blocks, so a group of total `T` puts `clip(T, lo, hi) - lo` in each block `[lo, hi)`.
  Quantities become `Decimal` once per line, to 6 places; each line's amount is rounded to cents, `ROUND_HALF_UP`.
- **Supply:** the period's dates (on the period's own date column) by season, x the daily charge.
- **Demand:** aligned blocks of `demand_minutes` on the season's clock, complete and wholly inside the window on a
  listed day; kW = kWh x 60 / minutes; charged per day (every date of the period) or once per period (§7.5).
- **Discounts:** percentages of usage + supply + demand (`percent_of_bill`) or of usage (`percent_of_use`), added, not
  compounded; guaranteed ones always, conditional ones only in `conditions_met` and only for an assumed category.
  Fixed guaranteed amounts are a one-off credit outside every period.
- **GST:** one rounded line per period on the GST-exclusive lines. **Fees** (GST-inclusive) are prorated by days and
  added after GST.

Pure and deterministic: no I/O, no clock, no randomness; floats are summed in row order.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Final, Literal

import numpy as np
import numpy.typing as npt
import pandas as pd

from energatron.core.tariff import (
    DAYS,
    MINUTES_PER_DAY,
    Clock,
    DemandCharge,
    EnergyCharge,
    Fee,
    Tariff,
    month_days,
)

type Scenario = Literal["conditions_met", "conditions_not_met", "backtest"]
type Line = Literal["supply", "usage", "demand", "discount", "gst", "fee"]
type PeriodBy = Literal["local_date", "meter_date"]

CENT: Final = Decimal("0.01")
QUANTITY_PLACES: Final = 6
LINE_ORDER: Final[dict[str, int]] = {"supply": 0, "usage": 1, "demand": 2, "discount": 3, "gst": 4, "fee": 5}
WEEK_MINUTES: Final = len(DAYS) * MINUTES_PER_DAY
_AEST_NS: Final = 10 * 3600 * 10**9
_MINUTE_NS: Final = 60 * 10**9
_DAY_NS: Final = 1440 * _MINUTE_NS
_EPOCH_ORDINAL: Final = date(1970, 1, 1).toordinal()
_FEE_DAYS: Final[dict[str, Decimal]] = {
    "ANNUAL": Decimal(365),
    "BIANNUAL": Decimal("182.5"),
    "MONTHLY": Decimal(365) / Decimal(12),
    "WEEKLY": Decimal(7),
    "DAILY": Decimal(1),
}
_MD_INDEX: Final[dict[str, int]] = {text: index for index, text in enumerate(month_days())}


def money(value: Decimal) -> Decimal:
    """`value` rounded to cents, half up."""
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def quantity(value: float) -> Decimal:
    """A float quantity (kWh, kW, kW-day) as a `Decimal` with six places."""
    return Decimal(f"{value:.{QUANTITY_PLACES}f}")


@dataclass(frozen=True)
class Pricing:
    """What `params.pricing` gives the engine (P4 spec §4.1)."""

    gst_rate: Decimal
    assume_conditions_met: frozenset[str]
    demand_minutes: int


@dataclass(frozen=True)
class LineItem:
    """One line of one billing period. `season` and `block` are indexes into the tariff; `rate` is per unit."""

    period_start: date
    period_end: date
    line: Line
    label: str
    season: int | None
    block: int | None
    quantity: Decimal
    unit: str
    rate: Decimal
    amount: Decimal


@dataclass(frozen=True)
class Bill:
    """A tariff's lines over the periods, and its one-off credit (GST-inclusive, outside every period)."""

    lines: tuple[LineItem, ...]
    one_off_credit: Decimal

    def period_totals(self) -> dict[tuple[date, date], Decimal]:
        totals: dict[tuple[date, date], Decimal] = {}
        for item in self.lines:
            key = (item.period_start, item.period_end)
            totals[key] = totals.get(key, Decimal(0)) + item.amount
        return totals

    def total(self) -> Decimal:
        return sum((item.amount for item in self.lines), Decimal(0))


# Usage, prepared once per register


@dataclass(frozen=True)
class _ClockArrays:
    day: npt.NDArray[np.int64]  # days since 1970-01-01 on this clock
    week_minute: npt.NDArray[np.int32]  # weekday x 1440 + minute of day, Monday first
    md: npt.NDArray[np.int16]  # index of the date's MM-DD in a leap year


@dataclass(frozen=True)
class Usage:
    """One register's intervals as arrays, in `utc_start` order (see `prepare_usage`)."""

    kwh: npt.NDArray[np.float64]
    minutes: npt.NDArray[np.int32]
    local_date: npt.NDArray[np.int64]  # day numbers since 1970-01-01
    meter_date: npt.NDArray[np.int64]
    clocks: dict[Clock, _ClockArrays]


def prepare_usage(frame: pd.DataFrame) -> Usage:
    """Arrays of one register's placed rows (`local_start` not null), sorted by `utc_start`."""
    placed = frame[frame["local_start"].notna()].sort_values("utc_start", kind="stable")
    utc = placed["utc_start"]  # aware UTC in `elec_interval_local`, or naive UTC
    utc_ns = utc.dt.tz_localize(None) if utc.dt.tz is not None else utc
    local_ns = placed["local_start"].astype("datetime64[ns]").to_numpy().astype(np.int64)
    aest_ns = utc_ns.astype("datetime64[ns]").to_numpy().astype(np.int64) + _AEST_NS
    return Usage(
        kwh=placed["kwh"].to_numpy(dtype=np.float64),
        minutes=placed["interval_minutes"].to_numpy(dtype=np.int32),
        local_date=_day_numbers(placed["local_date"]),
        meter_date=_day_numbers(placed["meter_date"]),
        clocks={"LOCAL": _clock_arrays(local_ns), "AEST": _clock_arrays(aest_ns)},
    )


def _day_numbers(dates: pd.Series) -> npt.NDArray[np.int64]:
    return pd.to_datetime(dates).astype("datetime64[ns]").to_numpy().astype(np.int64) // _DAY_NS


def _clock_arrays(wall_ns: npt.NDArray[np.int64]) -> _ClockArrays:
    day = wall_ns // _DAY_NS
    minute = ((wall_ns % _DAY_NS) // _MINUTE_NS).astype(np.int32)
    weekday = ((day + 3) % 7).astype(np.int32)  # 1970-01-01 was a Thursday
    stamps = pd.to_datetime(day, unit="D")
    md_keys = stamps.month.to_numpy() * 100 + stamps.day.to_numpy()
    md_lookup = np.full(1232, -1, dtype=np.int16)
    for text, index in _MD_INDEX.items():
        md_lookup[int(text[:2]) * 100 + int(text[3:])] = index
    return _ClockArrays(day=day, week_minute=weekday * MINUTES_PER_DAY + minute, md=md_lookup[md_keys])


# Pricing


def price(
    tariff: Tariff,
    usage: Usage,
    periods: Sequence[tuple[date, date]],
    *,
    scenario: Scenario,
    pricing: Pricing,
    by: PeriodBy = "local_date",
) -> Bill:
    """Price `tariff` over `periods` (P4 spec §7). Rows are chosen per period on `by`."""
    compiled = _Compiled.of(tariff)
    dates = usage.local_date if by == "local_date" else usage.meter_date
    lines: list[LineItem] = []
    for first, last in periods:
        lines.extend(_period(compiled, usage, dates, first, last, scenario, pricing))
    credit = sum(
        (
            discount.amount
            for discount in tariff.discounts
            if discount.method == "fixed" and discount.amount is not None
        ),
        Decimal(0),
    )
    return Bill(lines=tuple(lines), one_off_credit=credit)


@dataclass(frozen=True)
class _Compiled:
    """Lookups built once per tariff: the season of each `MM-DD`, and per season the charge of each week minute."""

    tariff: Tariff
    season_of_md: npt.NDArray[np.int16]
    charge_of: tuple[npt.NDArray[np.int16], ...]

    @classmethod
    def of(cls, tariff: Tariff) -> _Compiled:
        season_of_md = np.full(len(_MD_INDEX), -1, dtype=np.int16)
        for index, season in enumerate(tariff.seasons):
            for text in season.month_days():
                season_of_md[_MD_INDEX[text]] = index
        return cls(
            tariff=tariff, season_of_md=season_of_md, charge_of=tuple(_charge_lookup(s.energy) for s in tariff.seasons)
        )


def _charge_lookup(energy: Sequence[EnergyCharge]) -> npt.NDArray[np.int16]:
    lookup = np.full(WEEK_MINUTES, -1, dtype=np.int16)
    for index, charge in enumerate(energy):
        for window in charge.windows:
            ranges = window.minutes()
            for found in ranges if isinstance(ranges, tuple) else (ranges,):
                for day in window.days:
                    start = DAYS.index(day) * MINUTES_PER_DAY
                    lookup[start + found.start : start + found.stop] = index
    return lookup


def _period(
    compiled: _Compiled,
    usage: Usage,
    dates: npt.NDArray[np.int64],
    first: date,
    last: date,
    scenario: Scenario,
    pricing: Pricing,
) -> list[LineItem]:
    tariff = compiled.tariff
    lo, hi = first.toordinal() - _EPOCH_ORDINAL, last.toordinal() - _EPOCH_ORDINAL
    rows = np.flatnonzero((dates >= lo) & (dates <= hi))
    selector = usage.clocks[tariff.seasons[0].clock]
    seasons_of_rows = compiled.season_of_md[selector.md[rows]]
    period_dates = [first + timedelta(days=offset) for offset in range((last - first).days + 1)]
    days_in_season = [0] * len(tariff.seasons)
    for day in period_dates:
        days_in_season[int(compiled.season_of_md[_MD_INDEX[day.strftime("%m-%d")]])] += 1

    def item(
        line: Line,
        label: str,
        season: int | None,
        block: int | None,
        qty: Decimal,
        unit: str,
        rate: Decimal,
        amount: Decimal,
    ) -> LineItem:
        return LineItem(first, last, line, label, season, block, qty, unit, rate, amount)

    supply: list[LineItem] = []
    energy: list[LineItem] = []
    demand: list[LineItem] = []
    for index, season in enumerate(tariff.seasons):
        if days_in_season[index]:
            days = Decimal(days_in_season[index])
            supply.append(
                item(
                    "supply", "supply", index, None, days, "day", season.daily_supply, money(days * season.daily_supply)
                )
            )
        in_season = rows[seasons_of_rows == index]
        clock = usage.clocks[season.clock]
        charges = compiled.charge_of[index][clock.week_minute[in_season]]
        for charge_index, charge in enumerate(season.energy):
            members = in_season[charges == charge_index]
            for block_index, total in enumerate(_block_totals(charge, usage.kwh[members], clock.day[members])):
                qty = quantity(total)
                rate = charge.blocks[block_index].unit_price
                energy.append(item("usage", charge.label, index, block_index, qty, "kWh", rate, money(qty * rate)))
        for charge in season.demand:
            demand.extend(
                item(
                    "demand",
                    f"demand:{charge.measurement}:{charge.charge}",
                    index,
                    None,
                    qty,
                    unit,
                    charge.amount_per_kw,
                    money(qty * charge.amount_per_kw),
                )
                for qty, unit in [
                    _demand_quantity(charge, usage, in_season, clock, days_in_season[index], pricing.demand_minutes)
                ]
            )
    usage_amount = sum((line.amount for line in energy), Decimal(0))
    base_bill = usage_amount + sum((line.amount for line in (*supply, *demand)), Decimal(0))
    discounts: list[LineItem] = []
    for discount in tariff.discounts:
        if discount.method == "fixed" or discount.rate is None:
            continue
        applies = discount.kind == "guaranteed" or (
            scenario != "conditions_not_met" and discount.category in pricing.assume_conditions_met
        )
        if not applies:
            continue
        base = base_bill if discount.method == "percent_of_bill" else usage_amount
        discounts.append(
            item(
                "discount",
                discount.name or discount.method,
                None,
                None,
                base,
                "$",
                discount.rate,
                -money(base * discount.rate),
            )
        )
    net = base_bill + sum((line.amount for line in discounts), Decimal(0))
    gst = item("gst", "gst", None, None, net, "$", pricing.gst_rate, money(net * pricing.gst_rate))
    fees = [_fee_line(fee, first, last) for fee in tariff.fees]
    return [*supply, *energy, *demand, *discounts, gst, *fees]


def _block_totals(charge: EnergyCharge, kwh: npt.NDArray[np.float64], day: npt.NDArray[np.int64]) -> list[float]:
    """The energy in each block of `charge`, filled per clock day (`P1D`) or over the whole period (`P1M`)."""
    if len(charge.blocks) == 1:
        return [math.fsum(kwh.tolist())]
    if charge.block_period == "P1D":
        _, codes = np.unique(day, return_inverse=True)
        groups = np.bincount(codes, weights=kwh) if len(kwh) else np.zeros(0)
    else:
        groups = np.array([math.fsum(kwh.tolist())])
    totals: list[float] = []
    lower = 0.0
    for block in charge.blocks:
        upper = math.inf if block.volume_kwh is None else lower + float(block.volume_kwh)
        totals.append(math.fsum((np.clip(groups, lower, upper) - lower).tolist()))
        lower = upper
    return totals


def _demand_quantity(
    charge: DemandCharge,
    usage: Usage,
    rows: npt.NDArray[np.intp],
    clock: _ClockArrays,
    period_days: int,
    block_minutes: int,
) -> tuple[Decimal, str]:
    """The chargeable kW (charge `MONTH`) or kW-days (charge `DAY`) of one demand charge over one period's season."""
    weekday = clock.week_minute[rows] // MINUTES_PER_DAY
    listed = np.isin(weekday, [DAYS.index(day) for day in charge.days])
    keep = rows[listed]
    if not len(keep):
        return Decimal(0), "kW" if charge.charge == "MONTH" else "kW-day"
    day = clock.day[keep]
    block = (clock.week_minute[keep] % MINUTES_PER_DAY) // block_minutes
    keys = day * (MINUTES_PER_DAY // block_minutes) + block
    unique, codes = np.unique(keys, return_inverse=True)
    energy = np.bincount(codes, weights=usage.kwh[keep])
    covered = np.bincount(codes, weights=usage.minutes[keep])
    block_start = (unique % (MINUTES_PER_DAY // block_minutes)) * block_minutes
    inside = np.array([_inside(charge, int(start), block_minutes) for start in block_start], dtype=bool)
    valid = inside & (covered == block_minutes)
    kw = energy[valid] * 60.0 / block_minutes
    block_days = unique[valid] // (MINUTES_PER_DAY // block_minutes)

    def chargeable(peak: float) -> float:
        capped = peak if charge.max_kw is None else min(peak, float(charge.max_kw))
        return max(0.0, capped - float(charge.min_kw))

    if charge.measurement == "DAY":
        daily = [chargeable(float(kw[block_days == found].max())) for found in np.unique(block_days)]
        measured = math.fsum(daily) if charge.charge == "DAY" else max(daily, default=0.0)
        return quantity(measured), "kW-day" if charge.charge == "DAY" else "kW"
    peak = chargeable(float(kw.max())) if len(kw) else 0.0
    if charge.charge == "DAY":
        return quantity(peak * period_days), "kW-day"
    return quantity(peak), "kW"


def _inside(charge: DemandCharge, start: int, length: int) -> bool:
    """Whether the block `[start, start + length)` lies wholly inside the charge's window."""
    last = start + length - 1
    if charge.end_minute > charge.start_minute:
        return charge.start_minute <= start and last < charge.end_minute
    return (start >= charge.start_minute and last < MINUTES_PER_DAY) or last < charge.end_minute


def _fee_line(fee: Fee, first: date, last: date) -> LineItem:
    days = Decimal((last - first).days + 1)
    amount = money(fee.amount_inc_gst * days / _FEE_DAYS[fee.term])
    return LineItem(first, last, "fee", fee.type, None, None, days, "day", fee.amount_inc_gst, amount)
