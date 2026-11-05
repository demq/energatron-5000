"""The normalised gas tariff (P5 spec §5.1): what the gas engine prices, mapped from a CDR `gasContract` or a gas
`offer_manual`.

Like `Tariff` (P4 spec §5.1), every money amount is a `Decimal` and excludes GST, except a fee's `amount_inc_gst` and a
fixed discount's `amount`, taken as published; the GST constants of `core.tariff` apply unchanged. Gas usage is known
per bill, not per interval, so a season has one block ladder and no windows: blocks hold MJ allowances per day (`P1D`)
or per month (`P1M`). Construction checks that the seasons cover each `MM-DD` of a leap year exactly once. Pure.
"""

from datetime import date
from decimal import Decimal
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from energatron.core.errors import InvalidInputError
from energatron.core.models._common import FrozenModel
from energatron.core.tariff import (
    Discount,
    Fee,
    MonthDay,
    NonNegative,
    Positive,
    TariffSource,
    TariffStatus,
    month_days,
    seasons_partition_the_year,
)

type GasBlockPeriod = Literal["P1D", "P1M"]


class GasBlock(FrozenModel):
    """`volume_mj` MJ per block period at `unit_price` $/MJ; the last block has no volume."""

    volume_mj: Positive | None
    unit_price: NonNegative


class GasSeason(FrozenModel):
    """The prices in force from `start` to `end` (`MM-DD`, both inclusive; wraps the year end when `end < start`)."""

    start: MonthDay
    end: MonthDay
    daily_supply: NonNegative
    block_period: GasBlockPeriod
    blocks: Annotated[tuple[GasBlock, ...], Field(min_length=1)]

    def month_days(self) -> list[str]:
        """The `MM-DD` values the season covers."""
        ordered = month_days()
        first, last = ordered.index(self.start), ordered.index(self.end)
        return ordered[first : last + 1] if first <= last else ordered[first:] + ordered[: last + 1]

    @model_validator(mode="after")
    def _only_the_last_block_is_open(self) -> Self:
        *bounded, last = self.blocks
        if any(block.volume_mj is None for block in bounded) or last.volume_mj is not None:
            raise InvalidInputError("Every block but the last needs a volume, and the last has none.", field="blocks")
        return self


class GasTariff(FrozenModel):
    """A gas offer the gas engine can price. `approximations` say where it is priced only approximately."""

    offer_snapshot_id: str
    plan_id: str
    brand: str
    retailer: str  # the CDR base URI, or `manual`
    display_name: str
    source: TariffSource
    customer_type: str
    distributors: tuple[str, ...]
    pricing_model: str
    effective_from: date | None
    effective_to: date | None
    seasons: Annotated[tuple[GasSeason, ...], Field(min_length=1)]
    discounts: tuple[Discount, ...] = ()
    fees: tuple[Fee, ...] = ()
    status: TariffStatus
    approximations: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _seasons_partition_the_year(self) -> Self:
        if not seasons_partition_the_year((season.start, season.end) for season in self.seasons):
            raise InvalidInputError("The seasons don't cover each day of the year exactly once.", field="seasons")
        if (self.status == "approximate") != bool(self.approximations):
            raise InvalidInputError("A tariff is approximate exactly when it has approximations.", field="status")
        return self


def zero_priced(seasons: tuple[GasSeason, ...] | list[GasSeason]) -> bool:
    """Whether every season charges nothing for supply or usage."""
    return all(
        season.daily_supply == 0 and all(block.unit_price == Decimal(0) for block in season.blocks)
        for season in seasons
    )
