"""Map a CDR plan's `data` object to a `Tariff`, or to the reasons it can't be priced (P4 spec §5.2).

Every feature of the contract lands in exactly one of: supported, an approximation (the tariff is still ranked, with
`approximations` naming why) or an exclusion reason. A note never changes a price. Reasons, approximations and notes
are fixed strings, sorted and unique. `offer_manual` documents use the same mapper on their `electricityContract`,
with `prices_include_gst` dividing every GST-exclusive amount by `1 + gst_rate`. Pure: no I/O.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Final, cast

from pydantic import ValidationError

from energatron.core.canonical import JSONValue
from energatron.core.errors import InvalidInputError
from energatron.core.tariff import (
    ALL_WEEK,
    DAYS,
    Block,
    BlockPeriod,
    Clock,
    Day,
    DemandCharge,
    DemandPeriod,
    Discount,
    EnergyCharge,
    Fee,
    FeeTerm,
    Season,
    Tariff,
    TariffSource,
    Window,
    inclusive_end_to_half_open,
    seasons_partition_the_year,
    windows_partition_the_week,
)

TOU_LABELS: Final = frozenset({"PEAK", "OFF_PEAK", "SHOULDER", "SHOULDER1", "SHOULDER2"})
_PRICING_MODELS: Final = frozenset(
    {
        "SINGLE_RATE",
        "SINGLE_RATE_CONT_LOAD",
        "TIME_OF_USE",
        "TIME_OF_USE_CONT_LOAD",
        "FLEXIBLE",
        "FLEXIBLE_CONT_LOAD",
        "QUOTA",
    }
)
_UNSUPPORTED_MODELS: Final = frozenset({"FLEXIBLE", "FLEXIBLE_CONT_LOAD", "QUOTA"})
_PERIODIC_FEES: Final[frozenset[str]] = frozenset({"ANNUAL", "BIANNUAL", "MONTHLY", "WEEKLY", "DAILY"})
_CONDITIONS: Final = frozenset({"PAY_ON_TIME", "DIRECT_DEBIT"})
_TIME: Final = re.compile(r"([01][0-9]|2[0-3]):([0-5][0-9])(?::[0-5][0-9])?")
_TEN_PLACES: Final = Decimal("1e-10")
_UNPRICED_FEATURES: Final = (
    ("incentives", "incentives_unpriced"),
    ("greenPowerCharges", "green_power_unpriced"),
    ("solarFeedInTariff", "feed_in_unpriced"),
)


@dataclass(frozen=True)
class Excluded:
    """An offer that can't be priced: the sorted, unique `reasons`, and the notes gathered on the way."""

    reasons: tuple[str, ...]
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class Household:
    """What the mapping needs from `params.offers` (P4 spec §4.1)."""

    unmet_eligibility: frozenset[str]
    controlled_load: bool


@dataclass(frozen=True)
class Plan:
    """Where the plan came from: its snapshot ID, the CDR base it was listed under, and how it was published."""

    offer_snapshot_id: str
    retailer: str
    source: TariffSource = "cdr"
    prices_include_gst: bool = False
    gst_rate: Decimal = Decimal("0.10")


class _Malformed(Exception):
    def __init__(self, where: str) -> None:
        super().__init__(where)
        self.where = where


@dataclass
class _Found:
    reasons: set[str] = field(default_factory=lambda: set[str]())
    approximations: set[str] = field(default_factory=lambda: set[str]())
    notes: set[str] = field(default_factory=lambda: set[str]())


def map_cdr(data: Mapping[str, JSONValue], plan: Plan, household: Household, *, as_of: date) -> Tariff | Excluded:
    """The tariff of a CDR plan's `data` object, or why it is excluded (P4 spec §5.2). Never raises on bad data."""
    found = _Found()
    try:
        return _map(data, plan, household, as_of, found)
    except _Malformed as error:
        return Excluded(reasons=tuple(sorted({*found.reasons, f"malformed:{error.where}"})), notes=_sorted(found.notes))


def _map(
    data: Mapping[str, JSONValue], plan: Plan, household: Household, as_of: date, found: _Found
) -> Tariff | Excluded:
    fuel = _text(data, "fuelType", "fuelType")
    if fuel != "ELECTRICITY":
        found.reasons.add(f"fuel:{fuel}")
    contract = _mapping(data.get("electricityContract"), "electricityContract")
    effective_from = _instant_date(data.get("effectiveFrom"), "effectiveFrom")
    effective_to = _instant_date(data.get("effectiveTo"), "effectiveTo")
    if (effective_to is not None and effective_to < as_of) or (effective_from is not None and effective_from > as_of):
        found.reasons.add("not_effective")
    pricing_model = _text(contract, "pricingModel", "pricingModel")
    if pricing_model not in _PRICING_MODELS:
        raise _Malformed("pricingModel")
    if pricing_model in _UNSUPPORTED_MODELS:
        found.reasons.add(f"pricing_model:{pricing_model}")
    elif pricing_model.endswith("_CONT_LOAD") and not household.controlled_load:
        found.reasons.add("controlled_load_plan")
    _eligibility(contract, household, found)
    clock = _clock(contract.get("timeZone"), "timeZone", default="AEST")
    divisor = Decimal(1) + plan.gst_rate if plan.prices_include_gst else Decimal(1)
    periods = _list(contract.get("tariffPeriod"), "tariffPeriod")
    if not periods:
        raise _Malformed("tariffPeriod")
    seasons = [_season(_mapping(period, "tariffPeriod"), clock, divisor, found) for period in periods]
    if not seasons_partition_the_year((start, end) for start, end, _ in seasons):
        found.reasons.add("seasons_not_partition")
    discounts = _discounts(contract, as_of, found)
    fees = _fees(contract, found)
    for key, note in _UNPRICED_FEATURES:
        if _list(contract.get(key), key):
            found.notes.add(note)
    if data.get("meteringCharges"):
        found.notes.add("metering_charges_unpriced")
    if _list(contract.get("controlledLoad"), "controlledLoad") and "controlled_load_plan" not in found.reasons:
        found.notes.add("controlled_load_unpriced")
    if not found.reasons and _zero_priced([season for _, _, season in seasons if season is not None]):
        found.reasons.add("zero_priced")  # a companion plan (export only): it prices nothing an import plan does
    if found.reasons:
        return Excluded(reasons=_sorted(found.reasons), notes=_sorted(found.notes))
    geography = _mapping(data.get("geography", {}), "geography")
    try:
        return Tariff(
            offer_snapshot_id=plan.offer_snapshot_id,
            plan_id=_text(data, "planId", "planId"),
            brand=brand_of(data),
            retailer=plan.retailer,
            display_name=_text(data, "displayName", "displayName"),
            source=plan.source,
            customer_type=_text(data, "customerType", "customerType"),
            distributors=tuple(_texts(geography.get("distributors", []), "geography.distributors")),
            pricing_model=pricing_model,
            effective_from=effective_from,
            effective_to=effective_to,
            seasons=tuple(season for _, _, season in seasons if season is not None),
            discounts=tuple(discounts),
            fees=tuple(fees),
            status="approximate" if found.approximations else "normalised",
            approximations=_sorted(found.approximations),
            notes=_sorted(found.notes),
        )
    except (ValidationError, InvalidInputError) as error:  # a rule the checks above don't cover; never expected
        where = _first_location(error)
        return Excluded(reasons=(f"malformed:{where}",), notes=_sorted(found.notes))


def brand_of(data: Mapping[str, JSONValue]) -> str:
    """The plan's brand, trimmed: some retailers publish it with surrounding whitespace. Missing → `malformed:brand`."""
    value = data.get("brand")
    if not isinstance(value, str) or not value.strip():
        raise _Malformed("brand")
    return value.strip()


def _zero_priced(seasons: Sequence[Season]) -> bool:
    """Whether every season charges nothing for supply, usage or demand."""
    return all(
        season.daily_supply == 0
        and all(block.unit_price == 0 for charge in season.energy for block in charge.blocks)
        and all(demand.amount_per_kw == 0 for demand in season.demand)
        for season in seasons
    )


# Eligibility, seasons and charges


def _eligibility(contract: Mapping[str, JSONValue], household: Household, found: _Found) -> None:
    for item in _list(contract.get("eligibility"), "eligibility"):
        kind = _text(_mapping(item, "eligibility"), "type", "eligibility.type")
        if kind in household.unmet_eligibility:
            found.reasons.add(f"eligibility:{kind}")
        elif kind == "OTHER":
            found.approximations.add("eligibility_unverified")


def _season(
    period: Mapping[str, JSONValue], contract_clock: Clock, divisor: Decimal, found: _Found
) -> tuple[str, str, Season | None]:
    start = _text(period, "startDate", "tariffPeriod.startDate")
    end = _text(period, "endDate", "tariffPeriod.endDate")
    clock = _clock(period.get("timeZone"), "tariffPeriod.timeZone", default=contract_clock)
    supply_type = period.get("dailySupplyChargeType", "SINGLE")
    if supply_type == "BAND":
        found.reasons.add("banded_supply")
        supply = Decimal(0)
    elif supply_type == "SINGLE":
        supply = _price(period.get("dailySupplyCharge"), "tariffPeriod.dailySupplyCharge", divisor)
    else:
        raise _Malformed("tariffPeriod.dailySupplyChargeType")
    before = len(found.reasons)
    energy = _energy(period, divisor, found)
    demand = _demand(period, divisor, found)
    if len(found.reasons) > before:  # a charge was dropped for its own reason: its windows are missing, not wrong
        return start, end, None
    if not energy or not windows_partition_the_week(charge.windows for charge in energy):
        found.reasons.add("windows_not_partition")
        return start, end, None
    if found.reasons:
        return start, end, None
    try:
        return start, end, Season(start=start, end=end, clock=clock, daily_supply=supply, energy=energy, demand=demand)
    except ValidationError, InvalidInputError:
        raise _Malformed("tariffPeriod") from None


def _energy(period: Mapping[str, JSONValue], divisor: Decimal, found: _Found) -> tuple[EnergyCharge, ...]:
    kind = period.get("rateBlockUType")
    if kind == "singleRate":
        single = _mapping(period.get("singleRate"), "tariffPeriod.singleRate")
        charge = _charge("ALL", (ALL_WEEK,), single, "tariffPeriod.singleRate", divisor, found)
        return () if charge is None else (charge,)
    if kind == "timeOfUseRates":
        charges: list[EnergyCharge] = []
        for item in _list(period.get("timeOfUseRates"), "tariffPeriod.timeOfUseRates"):
            rate = _mapping(item, "tariffPeriod.timeOfUseRates")
            label = _text(rate, "type", "tariffPeriod.timeOfUseRates.type")
            if label not in TOU_LABELS:
                raise _Malformed("tariffPeriod.timeOfUseRates.type")
            windows = _windows(rate.get("timeOfUse"), "tariffPeriod.timeOfUseRates.timeOfUse", found)
            if not windows:
                continue
            charge = _charge(label, windows, rate, "tariffPeriod.timeOfUseRates", divisor, found)
            if charge is not None:
                charges.append(charge)
        return tuple(charges)
    if kind == "demandCharges":
        return ()  # demand only: no usage price, so the windows can't partition the week
    raise _Malformed("tariffPeriod.rateBlockUType")


def _charge(
    label: str,
    windows: tuple[Window, ...],
    rate: Mapping[str, JSONValue],
    where: str,
    divisor: Decimal,
    found: _Found,
) -> EnergyCharge | None:
    rates = _list(rate.get("rates"), f"{where}.rates")
    if not rates:
        raise _Malformed(f"{where}.rates")
    blocks: list[Block] = []
    for index, item in enumerate(rates):
        entry = _mapping(item, f"{where}.rates")
        unit = entry.get("measureUnit", "KWH")
        if unit != "KWH":
            found.reasons.add(f"measure_unit:{unit}")
            return None
        volume = entry.get("volume") if index < len(rates) - 1 else None
        if index < len(rates) - 1 and volume is None:
            raise _Malformed(f"{where}.rates.volume")
        blocks.append(
            Block(
                volume_kwh=None if volume is None else _decimal(volume, f"{where}.rates.volume"),
                unit_price=_price(entry.get("unitPrice"), f"{where}.rates.unitPrice", divisor),
            )
        )
    period = rate.get("period", "P1Y")
    block_period: BlockPeriod = "P1D"
    if len(blocks) > 1:
        if period not in ("P1D", "P1M"):
            found.reasons.add("block_period_unclear")
            return None
        block_period = cast(BlockPeriod, period)
    try:
        return EnergyCharge(label=label, windows=windows, block_period=block_period, blocks=tuple(blocks))
    except ValidationError, InvalidInputError:
        raise _Malformed(f"{where}.rates") from None


def _windows(value: JSONValue, where: str, found: _Found) -> tuple[Window, ...]:
    windows: list[Window] = []
    for item in _list(value, where):
        entry = _mapping(item, where)
        days, dropped = _days(entry.get("days"), f"{where}.days")
        if dropped:
            found.approximations.add("public_holidays_ignored")
        if not days:
            continue
        start = _minute(entry.get("startTime"), f"{where}.startTime")
        end = inclusive_end_to_half_open(_minute(entry.get("endTime"), f"{where}.endTime"))
        windows.append(Window(days=days, start_minute=start, end_minute=end))
    return tuple(windows)


def _demand(period: Mapping[str, JSONValue], divisor: Decimal, found: _Found) -> tuple[DemandCharge, ...]:
    items = _list(period.get("demandCharges"), "tariffPeriod.demandCharges")
    if not items:
        return ()
    charges: list[DemandCharge] = []
    seen: set[tuple[tuple[Day, ...], int, int]] = set()
    for item in items:
        entry = _mapping(item, "tariffPeriod.demandCharges")
        where = "tariffPeriod.demandCharges"
        measurement = _text(entry, "measurementPeriod", f"{where}.measurementPeriod")
        charge = _text(entry, "chargePeriod", f"{where}.chargePeriod")
        if "TARIFF_PERIOD" in (measurement, charge):
            found.reasons.add("demand_period:TARIFF_PERIOD")
            continue
        if measurement not in ("DAY", "MONTH") or charge not in ("DAY", "MONTH"):
            raise _Malformed(f"{where}.measurementPeriod")
        days, _ = _days(entry.get("days", list(DAYS)), f"{where}.days")
        if not days:
            continue
        start = _minute(entry.get("startTime"), f"{where}.startTime")
        end = inclusive_end_to_half_open(_minute(entry.get("endTime"), f"{where}.endTime"))
        key = (days, start, end)
        if key in seen:
            found.reasons.add("demand_season_unstated")
        seen.add(key)
        maximum = entry.get("maxDemand")
        charges.append(
            DemandCharge(
                days=days,
                start_minute=start,
                end_minute=end,
                amount_per_kw=_price(entry.get("amount"), f"{where}.amount", divisor),
                measurement=cast(DemandPeriod, measurement),
                charge=cast(DemandPeriod, charge),
                min_kw=_decimal(entry.get("minDemand", "0"), f"{where}.minDemand"),
                max_kw=None if maximum is None else _decimal(maximum, f"{where}.maxDemand"),
            )
        )
    found.approximations.add("demand_day_count")
    return tuple(charges)


# Discounts and fees


def _discounts(contract: Mapping[str, JSONValue], as_of: date, found: _Found) -> list[Discount]:
    discounts: list[Discount] = []
    for item in _list(contract.get("discounts"), "discounts"):
        entry = _mapping(item, "discounts")
        ends = _plain_date(entry.get("endDate"), "discounts.endDate")
        if ends is not None and ends < as_of:
            found.notes.add("discount_ended")
            continue
        kind = _text(entry, "type", "discounts.type")
        category = entry.get("category")
        method = _text(entry, "methodUType", "discounts.methodUType")
        name = str(entry.get("displayName", ""))
        if method == "percentOverThreshold":
            found.approximations.add("discount_unpriced:percentOverThreshold")
            continue
        if method not in ("percentOfBill", "percentOfUse", "fixedAmount"):
            raise _Malformed("discounts.methodUType")
        if kind == "CONDITIONAL" and category in _CONDITIONS and method != "fixedAmount":
            discount_kind, discount_category = "conditional", cast(str, category)
        elif kind == "GUARANTEED":
            discount_kind, discount_category = "guaranteed", None
        else:
            found.notes.add("conditional_discount_unpriced" if kind == "CONDITIONAL" else "discount_unpriced:other")
            continue
        detail = _mapping(entry.get(method), f"discounts.{method}")
        if method == "fixedAmount":
            amount = _decimal(detail.get("amount"), "discounts.fixedAmount.amount")
            discounts.append(Discount(name=name, kind="guaranteed", category=None, method="fixed", amount=amount))
            continue
        rate = _decimal(detail.get("rate"), f"discounts.{method}.rate")
        if not Decimal(0) <= rate <= Decimal(1):
            raise _Malformed(f"discounts.{method}.rate")
        discounts.append(
            Discount(
                name=name,
                kind=discount_kind,
                category=discount_category,
                method="percent_of_bill" if method == "percentOfBill" else "percent_of_use",
                rate=rate,
            )
        )
    return discounts


def _fees(contract: Mapping[str, JSONValue], found: _Found) -> list[Fee]:
    fees: list[Fee] = []
    for item in _list(contract.get("fees"), "fees"):
        entry = _mapping(item, "fees")
        kind = _text(entry, "type", "fees.type")
        term = entry.get("term")
        if term in _PERIODIC_FEES and kind != "PAPER_BILL" and entry.get("amount") is not None:
            amount = _decimal(entry.get("amount"), "fees.amount")
            fees.append(Fee(type=kind, term=cast(FeeTerm, term), amount_inc_gst=amount))
        else:
            found.notes.add(f"fee_not_periodic:{kind}")
    return fees


# Values


def _mapping(value: object, where: str) -> Mapping[str, JSONValue]:
    if not isinstance(value, Mapping):
        raise _Malformed(where)
    return cast(Mapping[str, JSONValue], value)


def _list(value: object, where: str) -> Sequence[JSONValue]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise _Malformed(where)
    return cast(list[JSONValue], value)


def _text(mapping: Mapping[str, JSONValue], key: str, where: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value:
        raise _Malformed(where)
    return value


def _texts(value: JSONValue, where: str) -> list[str]:
    items = _list(value, where)
    if any(not isinstance(item, str) for item in items):
        raise _Malformed(where)
    return [cast(str, item) for item in items]


def _decimal(value: object, where: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, str | int | float):
        raise _Malformed(where)
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        raise _Malformed(where) from None
    if not number.is_finite() or number < 0:
        raise _Malformed(where)
    return number


def _price(value: object, where: str, divisor: Decimal) -> Decimal:
    """A GST-exclusive price; one written with GST (`divisor` > 1) is divided back, to ten decimal places."""
    number = _decimal(value, where)
    if divisor == 1:
        return number
    return (number / divisor).quantize(_TEN_PLACES).normalize()


def _minute(value: object, where: str) -> int:
    match = _TIME.fullmatch(value) if isinstance(value, str) else None
    if match is None:
        raise _Malformed(where)
    return int(match[1]) * 60 + int(match[2])


def _days(value: object, where: str) -> tuple[tuple[Day, ...], bool]:
    """The weekdays listed, and whether another token (`PUBLIC_HOLIDAYS`) was dropped."""
    tokens = _texts(cast(JSONValue, value), where)
    days: tuple[Day, ...] = tuple(day for day in DAYS if day in tokens)
    return days, len(days) < len(set(tokens))


def _clock(value: object, where: str, *, default: Clock) -> Clock:
    if value is None:
        return default
    if value not in ("LOCAL", "AEST"):
        raise _Malformed(where)
    return cast(Clock, value)


def _instant_date(value: object, where: str) -> date | None:
    """The UTC date of an RFC 3339 instant, or None when absent."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise _Malformed(where)
    try:
        instant = datetime.fromisoformat(value.upper())  # RFC 3339 allows `t` and `z`
    except ValueError:
        raise _Malformed(where) from None
    offset = instant.utcoffset()
    if offset is None:
        raise _Malformed(where)
    return (instant - offset).date()


def _plain_date(value: object, where: str) -> date | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise _Malformed(where)
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        raise _Malformed(where) from None


def _sorted(values: set[str]) -> tuple[str, ...]:
    return tuple(sorted(values))


def _first_location(error: ValidationError | InvalidInputError) -> str:
    if isinstance(error, ValidationError):
        problems = error.errors(include_url=False, include_context=False, include_input=False)
        return ".".join(str(part) for part in problems[0]["loc"]) if problems else "tariff"
    field_name = error.details.get("field")
    return str(field_name) if field_name else "tariff"
