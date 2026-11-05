"""The gas engine (P5 spec §9.2): a `GasTariff` priced over billing periods of known MJ, line by line, in `Decimal`.

A gas bill gives one MJ figure per period, so usage is spread evenly over the period's days:
- **Seasons:** each date belongs to the season of its `MM-DD`. A season with `d` of the period's `D` days gets
  `mj x d / D`, to six places; the last season in date order takes the remainder, so the seasons add up to `mj`.
- **Blocks:** within a season, block `i`'s allowance is `volume x d` (`P1D`) or `volume x d x 12 / 365` (`P1M`), to six
  places; usage fills the blocks in order and the last takes the rest. One `usage` line per block used.
- **Supply:** `d x daily_supply` per season with days in the period.
- **Discounts, GST, fees, one-off credits:** `core.engine.settle`, exactly as for electricity (P4 spec §7.6-§7.8).

Pure and deterministic: no I/O, no float.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Final

from energatron.core.engine import Bill, LineItem, Pricing, Scenario, money, one_off_credit, settle
from energatron.core.gas_tariff import GasSeason, GasTariff

SIX_PLACES: Final = Decimal("0.000001")
MONTHS_PER_YEAR: Final = Decimal(12)
DAYS_PER_YEAR: Final = Decimal(365)


@dataclass(frozen=True)
class GasPeriod:
    """A billing period: inclusive dates and the MJ used in it."""

    first: date
    last: date
    mj: Decimal

    @property
    def days(self) -> int:
        return (self.last - self.first).days + 1


def six(value: Decimal) -> Decimal:
    return value.quantize(SIX_PLACES, rounding=ROUND_HALF_UP)


def price_gas(tariff: GasTariff, periods: Sequence[GasPeriod], *, scenario: Scenario, pricing: Pricing) -> Bill:
    """Price `tariff` over `periods` (P5 spec §9.2)."""
    season_of = {text: index for index, season in enumerate(tariff.seasons) for text in season.month_days()}
    lines: list[LineItem] = []
    for period in periods:
        lines.extend(_period(tariff, season_of, period, scenario, pricing))
    return Bill(lines=tuple(lines), one_off_credit=one_off_credit(tariff.discounts))


def season_days(season_of: dict[str, int], period: GasPeriod) -> dict[int, int]:
    """The period's days per season index, in the order the seasons first appear in the period."""
    counts: dict[int, int] = {}
    for offset in range(period.days):
        index = season_of[(period.first + timedelta(days=offset)).strftime("%m-%d")]
        counts[index] = counts.get(index, 0) + 1
    return counts


def _period(
    tariff: GasTariff, season_of: dict[str, int], period: GasPeriod, scenario: Scenario, pricing: Pricing
) -> list[LineItem]:
    first, last = period.first, period.last
    counts = season_days(season_of, period)
    closing = season_of[period.last.strftime("%m-%d")]  # takes the remainder (the last season in date order)
    shares = {index: six(period.mj * days / period.days) for index, days in counts.items() if index != closing}
    shares[closing] = period.mj - sum(shares.values(), Decimal(0))
    supply: list[LineItem] = []
    usage: list[LineItem] = []
    for index, days in counts.items():
        season = tariff.seasons[index]
        share = shares[index]
        quantity = Decimal(days)
        supply.append(
            LineItem(
                first, last, "supply", "supply", index, None, quantity, "day", season.daily_supply,
                money(quantity * season.daily_supply),
            )
        )  # fmt: skip
        usage.extend(_blocks(season, index, share, days, first, last))
    return [
        *supply,
        *usage,
        *settle(tariff.discounts, tariff.fees, first, last, usage, supply, scenario=scenario, pricing=pricing),
    ]


def allowance(season: GasSeason, volume: Decimal, days: int) -> Decimal:
    """A block's MJ allowance over `days` days of the season."""
    if season.block_period == "P1D":
        return six(volume * days)
    return six(volume * days * MONTHS_PER_YEAR / DAYS_PER_YEAR)


def _blocks(season: GasSeason, index: int, mj: Decimal, days: int, first: date, last: date) -> list[LineItem]:
    lines: list[LineItem] = []
    remaining = mj
    for position, block in enumerate(season.blocks):
        if remaining <= 0:
            break
        taken = remaining if block.volume_mj is None else min(remaining, allowance(season, block.volume_mj, days))
        remaining -= taken
        lines.append(
            LineItem(
                first,
                last,
                "usage",
                "ALL",
                index,
                position,
                taken,
                "MJ",
                block.unit_price,
                money(taken * block.unit_price),
            )
        )
    return lines
