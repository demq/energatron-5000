"""The normalised tariff (P4 spec §5.1): what the engine prices, mapped from a CDR contract or an `offer_manual`.

Every money amount is a `Decimal` and **excludes GST**, except a fee's `amount_inc_gst` and a fixed discount's
`amount`, which are taken as published (GST-inclusive). The two `CDR_*_INCLUDE_GST` constants are that convention,
verified against a Basic Plan Information Document at P4 acceptance (P4 spec §7.8, §13 check 4).

Windows are half-open minute ranges `[start_minute, end_minute)` of the tariff clock's day; one whose end is not after
its start wraps past midnight and covers both ends of the same day (the CDR gives windows per day, and the minutes after
midnight belong to the day they fall on). Construction checks the partition rules: the seasons cover each `MM-DD` of a
leap year exactly once, and in every season the energy charges' windows cover each `(weekday, minute)` exactly once.
Pure: no I/O.
"""

from collections import Counter
from collections.abc import Iterable, Sequence
from datetime import date, timedelta
from decimal import Decimal
from typing import Annotated, Final, Literal, Self

from pydantic import AfterValidator, Field, model_validator

from energatron.core.errors import InvalidInputError
from energatron.core.models._common import FrozenModel

CDR_RATES_INCLUDE_GST: Final = False
"""The CDR energy standard (v1.36): supply charges, usage unit prices and demand amounts exclude GST."""
CDR_FEES_INCLUDE_GST: Final = True
"""Fees and fixed discount amounts are taken as published; the standard is silent, retailers write them with GST."""

type Day = Literal["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]
DAYS: Final[tuple[Day, ...]] = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")
"""Monday first, as `date.weekday()` counts."""
MINUTES_PER_DAY: Final = 1440

type Clock = Literal["LOCAL", "AEST"]
type BlockPeriod = Literal["P1D", "P1M"]
type DemandPeriod = Literal["DAY", "MONTH"]
type FeeTerm = Literal["ANNUAL", "BIANNUAL", "MONTHLY", "WEEKLY", "DAILY"]
type DiscountKind = Literal["guaranteed", "conditional"]
type DiscountMethod = Literal["percent_of_bill", "percent_of_use", "fixed"]
type TariffStatus = Literal["normalised", "approximate"]
type TariffSource = Literal["cdr", "manual"]

_MONTH_DAY_LEAP_YEAR: Final = 2024


def month_days() -> list[str]:
    """Every `MM-DD` of a leap year, in calendar order (366 values)."""
    first = date(_MONTH_DAY_LEAP_YEAR, 1, 1)
    return [(first + timedelta(days=offset)).strftime("%m-%d") for offset in range(366)]


_MONTH_DAYS: Final = frozenset(month_days())


def _month_day(value: str) -> str:
    if value not in _MONTH_DAYS:
        raise ValueError("a calendar day as MM-DD")
    return value


MonthDay = Annotated[str, AfterValidator(_month_day)]
NonNegative = Annotated[Decimal, Field(ge=0, allow_inf_nan=False)]
Positive = Annotated[Decimal, Field(gt=0, allow_inf_nan=False)]
Fraction = Annotated[Decimal, Field(ge=0, le=1, allow_inf_nan=False)]


def _days(days: tuple[Day, ...]) -> tuple[Day, ...]:
    if not days or len(set(days)) != len(days):
        raise ValueError("at least one weekday, none twice")
    return tuple(sorted(days, key=DAYS.index))


Days = Annotated[tuple[Day, ...], AfterValidator(_days)]


class Window(FrozenModel):
    """Minutes `[start_minute, end_minute)` of each listed day; `end_minute <= start_minute` wraps past midnight."""

    days: Days
    start_minute: Annotated[int, Field(strict=True, ge=0, lt=MINUTES_PER_DAY)]
    end_minute: Annotated[int, Field(strict=True, gt=0, le=MINUTES_PER_DAY)]

    def minutes(self) -> range | tuple[range, range]:
        """The minute ranges of one day the window covers."""
        if self.end_minute > self.start_minute:
            return range(self.start_minute, self.end_minute)
        return range(self.start_minute, MINUTES_PER_DAY), range(0, self.end_minute)

    def covers(self, day: Day, minute: int) -> bool:
        if day not in self.days:
            return False
        if self.end_minute > self.start_minute:
            return self.start_minute <= minute < self.end_minute
        return minute >= self.start_minute or minute < self.end_minute


ALL_WEEK: Final = Window(days=DAYS, start_minute=0, end_minute=MINUTES_PER_DAY)
"""The one window of a single-rate charge."""


class Block(FrozenModel):
    """A usage block: `volume_kwh` kWh per block period at `unit_price` $/kWh; the last block has no volume."""

    volume_kwh: Positive | None
    unit_price: NonNegative


class EnergyCharge(FrozenModel):
    """Usage in `windows` priced by `blocks`, filled per tariff-clock day (`P1D`) or billing period (`P1M`)."""

    label: str  # PEAK, OFF_PEAK, SHOULDER, SHOULDER1, SHOULDER2, or ALL for a single rate
    windows: Annotated[tuple[Window, ...], Field(min_length=1)]
    block_period: BlockPeriod
    blocks: Annotated[tuple[Block, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def _only_the_last_block_is_open(self) -> Self:
        *bounded, last = self.blocks
        if any(block.volume_kwh is None for block in bounded) or last.volume_kwh is not None:
            raise InvalidInputError("Every block but the last needs a volume, and the last has none.", field="blocks")
        return self


class DemandCharge(FrozenModel):
    """$/kW for the demand in `[start_minute, end_minute)` on `days` (P4 spec §7.5)."""

    days: Days
    start_minute: Annotated[int, Field(strict=True, ge=0, lt=MINUTES_PER_DAY)]
    end_minute: Annotated[int, Field(strict=True, gt=0, le=MINUTES_PER_DAY)]
    amount_per_kw: NonNegative
    measurement: DemandPeriod
    charge: DemandPeriod
    min_kw: NonNegative
    max_kw: NonNegative | None


class Season(FrozenModel):
    """The prices in force from `start` to `end` (`MM-DD`, both inclusive; wraps the year end when `end < start`)."""

    start: MonthDay
    end: MonthDay
    clock: Clock
    daily_supply: NonNegative
    energy: Annotated[tuple[EnergyCharge, ...], Field(min_length=1)]
    demand: tuple[DemandCharge, ...] = ()

    def month_days(self) -> list[str]:
        """The `MM-DD` values the season covers."""
        ordered = month_days()
        first, last = ordered.index(self.start), ordered.index(self.end)
        return ordered[first : last + 1] if first <= last else ordered[first:] + ordered[: last + 1]

    @model_validator(mode="after")
    def _windows_partition_the_week(self) -> Self:
        if not windows_partition_the_week(charge.windows for charge in self.energy):
            raise InvalidInputError(
                "The energy windows don't cover each minute of the week exactly once.", field="energy"
            )
        return self


class Discount(FrozenModel):
    """A discount the engine can price (P4 spec §7.7). `rate` for a percentage, `amount` (GST-inclusive) for `fixed`."""

    name: str
    kind: DiscountKind
    category: str | None  # PAY_ON_TIME, DIRECT_DEBIT for a conditional one
    method: DiscountMethod
    rate: Fraction | None = None
    amount: NonNegative | None = None

    @model_validator(mode="after")
    def _method_has_its_value(self) -> Self:
        if (self.method == "fixed") != (self.amount is not None) or (self.method != "fixed") != (self.rate is not None):
            raise InvalidInputError("A percentage discount has a rate, a fixed one an amount.", field="discounts")
        return self


class Fee(FrozenModel):
    """A periodic fee, GST-inclusive as published (P4 spec §7.6)."""

    type: str
    term: FeeTerm
    amount_inc_gst: NonNegative


class Tariff(FrozenModel):
    """An offer the engine can price. `approximations` say where it is priced only approximately (still ranked)."""

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
    seasons: Annotated[tuple[Season, ...], Field(min_length=1)]
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


# Partition rules


def windows_partition_the_week(charges: Iterable[Sequence[Window]]) -> bool:
    """Whether the windows of all `charges` together cover each `(weekday, minute)` exactly once."""
    counts: Counter[tuple[Day, int]] = Counter()
    for windows in charges:
        for window in windows:
            ranges = window.minutes()
            for found in ranges if isinstance(ranges, tuple) else (ranges,):
                for day in window.days:
                    counts.update((day, minute) for minute in found)
    return len(counts) == len(DAYS) * MINUTES_PER_DAY and all(count == 1 for count in counts.values())


def seasons_partition_the_year(ranges: Iterable[tuple[str, str]]) -> bool:
    """Whether the inclusive `MM-DD` ranges (wrapping the year end when `end < start`) cover each day exactly once."""
    ordered = month_days()
    counts: Counter[str] = Counter()
    for start, end in ranges:
        if start not in _MONTH_DAYS or end not in _MONTH_DAYS:
            return False
        first, last = ordered.index(start), ordered.index(end)
        counts.update(ordered[first : last + 1] if first <= last else ordered[first:] + ordered[: last + 1])
    return len(counts) == len(ordered) and all(count == 1 for count in counts.values())


def inclusive_end_to_half_open(end_minute_inclusive: int) -> int:
    """A CDR end time names the last minute in the window (`22:59`); the half-open end is the minute after it."""
    if not 0 <= end_minute_inclusive < MINUTES_PER_DAY:
        raise ValueError("an end minute of the day")
    return end_minute_inclusive + 1
