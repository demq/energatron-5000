"""Map a CDR gas plan's `data` object to a `GasTariff`, or to the reasons it can't be priced (P5 spec §5.2).

The same discipline as `core.cdr_tariff` (P4 spec §5.2), whose value, eligibility, discount and fee helpers it shares:
every feature of the contract lands in exactly one of supported, an approximation or an exclusion reason; reasons,
approximations and notes are fixed strings, sorted and unique. Gas rates carry no `measureUnit` in practice; absent or
`MJ` is MJ. The contract's `timeZone` is ignored: gas data is daily. Pure: no I/O.
"""

from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from typing import Final, cast

from pydantic import ValidationError

from energatron.core.canonical import JSONValue
from energatron.core.cdr_tariff import (
    UNPRICED_FEATURES,
    Excluded,
    Found,
    Household,
    Malformed,
    Plan,
    brand_of,
    decimal_of,
    discounts_of,
    eligibility_of,
    fees_of,
    first_location,
    instant_date,
    list_of,
    mapping_of,
    price_of,
    sorted_unique,
    text_of,
    texts_of,
)
from energatron.core.errors import InvalidInputError
from energatron.core.gas_tariff import GasBlock, GasBlockPeriod, GasSeason, GasTariff, zero_priced
from energatron.core.tariff import seasons_partition_the_year

SUPPORTED_MODEL: Final = "SINGLE_RATE"
_UNITS: Final = frozenset({"MJ"})


def map_cdr_gas(
    data: Mapping[str, JSONValue], plan: Plan, household: Household, *, as_of: date
) -> GasTariff | Excluded:
    """The gas tariff of a CDR plan's `data` object, or why it is excluded (P5 spec §5.2). Never raises on bad data."""
    found = Found()
    try:
        return _map(data, plan, household, as_of, found)
    except Malformed as error:
        return Excluded(
            reasons=sorted_unique({*found.reasons, f"malformed:{error.where}"}), notes=sorted_unique(found.notes)
        )


def _map(
    data: Mapping[str, JSONValue], plan: Plan, household: Household, as_of: date, found: Found
) -> GasTariff | Excluded:
    fuel = text_of(data, "fuelType", "fuelType")
    if fuel != "GAS":
        found.reasons.add(f"fuel:{fuel}")
    contract = mapping_of(data.get("gasContract"), "gasContract")
    effective_from = instant_date(data.get("effectiveFrom"), "effectiveFrom")
    effective_to = instant_date(data.get("effectiveTo"), "effectiveTo")
    if (effective_to is not None and effective_to < as_of) or (effective_from is not None and effective_from > as_of):
        found.reasons.add("not_effective")
    pricing_model = text_of(contract, "pricingModel", "pricingModel")
    if pricing_model != SUPPORTED_MODEL:
        found.reasons.add(f"pricing_model:{pricing_model}")
    eligibility_of(contract, household, found)
    divisor = Decimal(1) + plan.gst_rate if plan.prices_include_gst else Decimal(1)
    periods = list_of(contract.get("tariffPeriod"), "tariffPeriod")
    if not periods:
        raise Malformed("tariffPeriod")
    seasons = [_season(mapping_of(period, "tariffPeriod"), divisor, found) for period in periods]
    if not seasons_partition_the_year((start, end) for start, end, _ in seasons):
        found.reasons.add("seasons_not_partition")
    discounts = discounts_of(contract, as_of, found)
    fees = fees_of(contract, found)
    for key, note in UNPRICED_FEATURES:
        if list_of(contract.get(key), key):
            found.notes.add(note)
    if data.get("meteringCharges"):
        found.notes.add("metering_charges_unpriced")
    priced = [season for _, _, season in seasons if season is not None]
    if not found.reasons and zero_priced(priced):
        found.reasons.add("zero_priced")
    if found.reasons:
        return Excluded(reasons=sorted_unique(found.reasons), notes=sorted_unique(found.notes))
    geography = mapping_of(data.get("geography", {}), "geography")
    try:
        return GasTariff(
            offer_snapshot_id=plan.offer_snapshot_id,
            plan_id=text_of(data, "planId", "planId"),
            brand=brand_of(data),
            retailer=plan.retailer,
            display_name=text_of(data, "displayName", "displayName"),
            source=plan.source,
            customer_type=text_of(data, "customerType", "customerType"),
            distributors=tuple(texts_of(geography.get("distributors", []), "geography.distributors")),
            pricing_model=pricing_model,
            effective_from=effective_from,
            effective_to=effective_to,
            seasons=tuple(priced),
            discounts=tuple(discounts),
            fees=tuple(fees),
            status="approximate" if found.approximations else "normalised",
            approximations=sorted_unique(found.approximations),
            notes=sorted_unique(found.notes),
        )
    except (ValidationError, InvalidInputError) as error:  # a rule the checks above don't cover; never expected
        return Excluded(reasons=(f"malformed:{first_location(error)}",), notes=sorted_unique(found.notes))


def _season(period: Mapping[str, JSONValue], divisor: Decimal, found: Found) -> tuple[str, str, GasSeason | None]:
    start = text_of(period, "startDate", "tariffPeriod.startDate")
    end = text_of(period, "endDate", "tariffPeriod.endDate")
    supply_type = period.get("dailySupplyChargeType", "SINGLE")
    if supply_type == "BAND":
        found.reasons.add("banded_supply")
        return start, end, None
    if supply_type != "SINGLE":
        raise Malformed("tariffPeriod.dailySupplyChargeType")
    supply = price_of(period.get("dailySupplyCharge"), "tariffPeriod.dailySupplyCharge", divisor)
    kind = text_of(period, "rateBlockUType", "tariffPeriod.rateBlockUType")
    if kind != "singleRate":
        found.reasons.add(f"rate_type:{kind}")
        return start, end, None
    single = mapping_of(period.get("singleRate"), "tariffPeriod.singleRate")
    rates = list_of(single.get("rates"), "tariffPeriod.singleRate.rates")
    if not rates:
        raise Malformed("tariffPeriod.singleRate.rates")
    blocks: list[GasBlock] = []
    for index, item in enumerate(rates):
        entry = mapping_of(item, "tariffPeriod.singleRate.rates")
        unit = entry.get("measureUnit", "MJ")
        if unit not in _UNITS:
            found.reasons.add(f"measure_unit:{unit}")
            return start, end, None
        last = index == len(rates) - 1
        volume = None if last else entry.get("volume")
        if not last and volume is None:
            raise Malformed("tariffPeriod.singleRate.rates.volume")
        blocks.append(
            GasBlock(
                volume_mj=None if volume is None else decimal_of(volume, "tariffPeriod.singleRate.rates.volume"),
                unit_price=price_of(entry.get("unitPrice"), "tariffPeriod.singleRate.rates.unitPrice", divisor),
            )
        )
    block_period: GasBlockPeriod = "P1D"
    if len(blocks) > 1:
        period_text = single.get("period", "P1Y")
        if period_text == "P1M":
            found.approximations.add("block_month_prorated")
        elif period_text != "P1D":
            found.reasons.add("block_period_unclear")
            return start, end, None
        block_period = cast(GasBlockPeriod, period_text)
    try:
        return (
            start,
            end,
            GasSeason(start=start, end=end, daily_supply=supply, block_period=block_period, blocks=tuple(blocks)),
        )
    except ValidationError, InvalidInputError:
        raise Malformed("tariffPeriod") from None
