"""`map_cdr` (P4 spec §5.2): one test per row of the support table, plus the mapping of a supported plan.

Every plan is built by `tests/fixtures/make_cdr.py`; nothing comes from a real response.
"""

from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from energatron.core.cdr_tariff import Excluded, Household, Plan, map_cdr
from energatron.core.tariff import Tariff
from tests.fixtures import make_cdr as cdr

AS_OF = date(2026, 11, 1)
HOUSEHOLD = Household(unmet_eligibility=frozenset({"EXISTING_SOLAR", "EXISTING_BATTERY"}), controlled_load=False)
PLAN = Plan(offer_snapshot_id="TEST1@EME~20261001T000000Z~0123abcd", retailer="alpha")


def mapped(data: dict[str, Any], *, household: Household = HOUSEHOLD, plan: Plan = PLAN) -> Tariff | Excluded:
    return map_cdr(data, plan, household, as_of=AS_OF)


def tariff(data: dict[str, Any], **kwargs: Any) -> Tariff:
    found = mapped(data, **kwargs)
    assert isinstance(found, Tariff), found
    return found


def reasons(data: dict[str, Any], **kwargs: Any) -> tuple[str, ...]:
    found = mapped(data, **kwargs)
    assert isinstance(found, Excluded), found
    return found.reasons


# A supported plan


def test_a_tou_plan_maps_its_seasons_windows_rates_and_header() -> None:
    found = tariff(cdr.plan())
    assert (found.plan_id, found.brand, found.retailer, found.source) == ("TEST1@EME", "alpha", "alpha", "cdr")
    assert found.status == "normalised"
    assert found.distributors == (cdr.DISTRIBUTOR,)
    assert found.effective_from == date(2026, 7, 1)
    (season,) = found.seasons
    assert (season.start, season.end, season.clock, season.daily_supply) == ("07-01", "06-30", "LOCAL", Decimal("1.00"))
    peak = next(charge for charge in season.energy if charge.label == "PEAK")
    assert peak.blocks[0].unit_price == Decimal("0.40")
    assert (peak.windows[0].start_minute, peak.windows[0].end_minute) == (960, 1380)  # 16:00-22:59 inclusive


def test_a_brand_published_with_surrounding_whitespace_is_trimmed() -> None:
    assert tariff(cdr.plan(brand="alpha ")).brand == "alpha"


def test_a_single_rate_plan_has_one_all_week_charge() -> None:
    found = tariff(cdr.plan(pricing_model="SINGLE_RATE"))
    (charge,) = found.seasons[0].energy
    assert charge.label == "ALL"
    assert (charge.windows[0].start_minute, charge.windows[0].end_minute) == (0, 1440)


# The support table, row by row


def test_controlled_load_variant_is_excluded_without_a_controlled_load_register() -> None:
    data = cdr.plan(pricing_model="TIME_OF_USE_CONT_LOAD")
    assert reasons(data) == ("controlled_load_plan",)
    assert tariff(data, household=Household(frozenset(), controlled_load=True)).pricing_model == "TIME_OF_USE_CONT_LOAD"


@pytest.mark.parametrize("model", ["FLEXIBLE", "FLEXIBLE_CONT_LOAD", "QUOTA"])
def test_flexible_and_quota_pricing_models_are_excluded(model: str) -> None:
    assert reasons(cdr.plan(pricing_model=model, periods=[cdr.single_period()])) == (f"pricing_model:{model}",)


def test_an_unmet_eligibility_type_excludes() -> None:
    assert reasons(cdr.plan(eligibility=["EXISTING_SOLAR", "EXISTING_SMART_METER"])) == ("eligibility:EXISTING_SOLAR",)


def test_free_text_eligibility_is_an_approximation() -> None:
    found = tariff(cdr.plan(eligibility=["OTHER"]))
    assert (found.status, found.approximations) == ("approximate", ("eligibility_unverified",))


def test_banded_supply_is_excluded() -> None:
    period = cdr.tou_period(extra={"dailySupplyChargeType": "BAND", "bandedDailySupplyCharges": []})
    assert reasons(cdr.plan(periods=[period])) == ("banded_supply",)


def test_seasons_that_do_not_partition_the_year_are_excluded() -> None:
    assert reasons(cdr.plan(periods=[cdr.tou_period(start="01-01", end="06-30")])) == ("seasons_not_partition",)


def test_wrapping_seasons_that_partition_the_year_are_supported() -> None:
    summer = cdr.tou_period(start="11-01", end="03-31", supply="1.10")
    winter = cdr.tou_period(start="04-01", end="10-31", supply="0.90")
    found = tariff(cdr.plan(periods=[summer, winter]))
    assert [season.daily_supply for season in found.seasons] == [Decimal("1.10"), Decimal("0.90")]


def test_windows_that_do_not_partition_the_week_are_excluded() -> None:
    rates = cdr.standard_tou()[:2]  # no shoulder
    assert reasons(cdr.plan(periods=[cdr.tou_period(rates)])) == ("windows_not_partition",)


def test_weekday_and_weekend_windows_that_partition_together_are_supported() -> None:
    weekdays, weekend = ["MON", "TUE", "WED", "THU", "FRI"], ["SAT", "SUN"]
    rates = [
        cdr.tou_rate("PEAK", [cdr.window("07:00", "22:59", weekdays)], price="0.40"),
        cdr.tou_rate("OFF_PEAK", [cdr.window("23:00", "06:59", weekdays), cdr.window("00:00", "23:59", weekend)]),
    ]
    assert len(tariff(cdr.plan(periods=[cdr.tou_period(rates)])).seasons[0].energy) == 2


def test_a_public_holidays_token_is_dropped_as_an_approximation() -> None:
    rates = cdr.standard_tou()
    rates[0]["timeOfUse"][0]["days"].append("PUBLIC_HOLIDAYS")
    found = tariff(cdr.plan(periods=[cdr.tou_period(rates)]))
    assert found.approximations == ("public_holidays_ignored",)


@pytest.mark.parametrize("period", ["P1D", "P1M"])
def test_daily_and_monthly_blocks_are_supported(period: str) -> None:
    blocks = [cdr.rate("0.00", volume=10), cdr.rate("0.30")]
    data = cdr.plan(pricing_model="SINGLE_RATE", periods=[cdr.single_period(blocks, period=period)])
    (charge,) = tariff(data).seasons[0].energy
    assert charge.block_period == period
    assert [block.volume_kwh for block in charge.blocks] == [Decimal(10), None]


@pytest.mark.parametrize("period", ["P1Y", None])
def test_yearly_or_unstated_block_periods_are_excluded(period: str | None) -> None:
    blocks = [cdr.rate("0.00", volume=24), cdr.rate("0.30")]
    data = cdr.plan(pricing_model="SINGLE_RATE", periods=[cdr.single_period(blocks, period=period)])
    assert reasons(data) == ("block_period_unclear",)


def test_a_tou_block_with_a_yearly_period_is_excluded_too() -> None:
    rates = cdr.standard_tou()
    rates[2] = cdr.tou_rate(
        "SHOULDER", rates[2]["timeOfUse"], [cdr.rate("0.00", volume=24), cdr.rate("0.30")], period="P1Y"
    )
    assert reasons(cdr.plan(periods=[cdr.tou_period(rates)])) == ("block_period_unclear",)


@pytest.mark.parametrize("period", ["P1Y", "P1M", None])
def test_a_single_rate_with_any_period_is_supported(period: str | None) -> None:
    data = cdr.plan(pricing_model="SINGLE_RATE", periods=[cdr.single_period(period=period)])
    assert tariff(data).status == "normalised"


def test_a_usage_unit_other_than_kwh_is_excluded() -> None:
    data = cdr.plan(pricing_model="SINGLE_RATE", periods=[cdr.single_period([cdr.rate("0.30", unit="MJ")])])
    assert reasons(data) == ("measure_unit:MJ",)


def test_an_absent_usage_unit_is_kwh() -> None:
    data = cdr.plan(pricing_model="SINGLE_RATE", periods=[cdr.single_period([cdr.rate("0.30", unit=None)])])
    assert tariff(data).status == "normalised"


@pytest.mark.parametrize(("zone", "clock"), [("LOCAL", "LOCAL"), ("AEST", "AEST"), (None, "AEST")])
def test_the_clock_follows_the_time_zone_and_defaults_to_aest(zone: str | None, clock: str) -> None:
    assert tariff(cdr.plan(time_zone=zone)).seasons[0].clock == clock


def test_a_seasons_own_time_zone_wins() -> None:
    data = cdr.plan(time_zone="LOCAL", periods=[cdr.tou_period(extra={"timeZone": "AEST"})])
    assert tariff(data).seasons[0].clock == "AEST"


def test_demand_charges_with_the_season_only_in_free_text_are_excluded() -> None:
    demand = [cdr.demand_charge("0.40"), cdr.demand_charge("0.20")]  # same days and window, seasons only described
    assert reasons(cdr.plan(periods=[cdr.tou_period(demand=demand)])) == ("demand_season_unstated",)


def test_other_demand_charges_are_supported_and_approximate() -> None:
    demand = [cdr.demand_charge("0.40", end="21:00", measurement="DAY", charge="DAY", max_demand="10")]
    found = tariff(cdr.plan(periods=[cdr.tou_period(demand=demand)]))
    (charge,) = found.seasons[0].demand
    assert (charge.start_minute, charge.end_minute) == (960, 1261)  # an `HH:00` end is read as inclusive too
    assert (charge.measurement, charge.charge, charge.max_kw) == ("DAY", "DAY", Decimal(10))
    assert found.approximations == ("demand_day_count",)


@pytest.mark.parametrize(("measurement", "charge"), [("TARIFF_PERIOD", "DAY"), ("MONTH", "TARIFF_PERIOD")])
def test_demand_over_the_tariff_period_is_excluded(measurement: str, charge: str) -> None:
    demand = [cdr.demand_charge(measurement=measurement, charge=charge)]
    assert reasons(cdr.plan(periods=[cdr.tou_period(demand=demand)])) == ("demand_period:TARIFF_PERIOD",)


@pytest.mark.parametrize(
    ("method", "expected"), [("percentOfBill", "percent_of_bill"), ("percentOfUse", "percent_of_use")]
)
def test_percentage_discounts_are_supported(method: str, expected: str) -> None:
    found = tariff(cdr.plan(discounts=[cdr.discount(method=method, category="DIRECT_DEBIT")]))
    (discount,) = found.discounts
    assert (discount.kind, discount.category, discount.method, discount.rate) == (
        "conditional",
        "DIRECT_DEBIT",
        expected,
        Decimal("0.03"),
    )


def test_a_guaranteed_percentage_discount_is_guaranteed() -> None:
    found = tariff(cdr.plan(discounts=[cdr.discount("GUARANTEED", category=None)]))
    assert found.discounts[0].kind == "guaranteed"


def test_a_guaranteed_fixed_amount_is_a_one_off_credit() -> None:
    found = tariff(cdr.plan(discounts=[cdr.discount("GUARANTEED", "fixedAmount", category=None, rate_or_amount="50")]))
    (credit,) = found.discounts
    assert (credit.method, credit.amount) == ("fixed", Decimal(50))


@pytest.mark.parametrize("method", ["fixedAmount", "percentOfBill"])
def test_a_conditional_discount_with_category_other_is_not_priced_and_noted(method: str) -> None:
    found = tariff(cdr.plan(discounts=[cdr.discount(method=method, category="OTHER", rate_or_amount="0.01")]))
    assert found.discounts == ()
    assert found.notes == ("conditional_discount_unpriced",)
    assert found.status == "normalised"


def test_a_percent_over_threshold_discount_is_an_approximation() -> None:
    found = tariff(cdr.plan(discounts=[cdr.discount("GUARANTEED", "percentOverThreshold", category=None)]))
    assert found.approximations == ("discount_unpriced:percentOverThreshold",)


def test_a_discount_that_has_ended_is_dropped_and_noted() -> None:
    found = tariff(cdr.plan(discounts=[cdr.discount(end_date="2026-01-31")]))
    assert (found.discounts, found.notes) == ((), ("discount_ended",))


@pytest.mark.parametrize("term", ["ANNUAL", "BIANNUAL", "MONTHLY", "WEEKLY", "DAILY"])
def test_periodic_fees_are_supported(term: str) -> None:
    (fee,) = tariff(cdr.plan(fees=[cdr.fee(term=term)])).fees
    assert (fee.type, fee.term, fee.amount_inc_gst) == ("MEMBERSHIP", term, Decimal("60.00"))


@pytest.mark.parametrize(
    ("kind", "term"),
    [
        ("DISCONNECTION", "FIXED"),
        ("LATE_PAYMENT", "FIXED"),
        ("EXIT", "1_YEAR"),
        ("CC_PROCESSING", "PERCENT_OF_BILL"),
        ("PAPER_BILL", "FIXED"),
        ("PAPER_BILL", "MONTHLY"),
    ],
)
def test_other_fees_are_not_priced_and_noted(kind: str, term: str) -> None:
    found = tariff(cdr.plan(fees=[cdr.fee(kind, term)]))
    assert (found.fees, found.notes) == ((), (f"fee_not_periodic:{kind}",))


@pytest.mark.parametrize(
    ("contract", "data", "note"),
    [
        ({"incentives": [{"displayName": "x"}]}, {}, "incentives_unpriced"),
        ({"greenPowerCharges": [{"displayName": "x"}]}, {}, "green_power_unpriced"),
        ({"solarFeedInTariff": [{"displayName": "x"}]}, {}, "feed_in_unpriced"),
        ({}, {"meteringCharges": [{"displayName": "x"}]}, "metering_charges_unpriced"),
    ],
)
def test_unpriced_features_are_noted(contract: dict[str, Any], data: dict[str, Any], note: str) -> None:
    found = tariff(cdr.plan(contract_extra=contract, data_extra=data))
    assert (found.notes, found.status) == ((note,), "normalised")


def test_controlled_load_charges_are_noted_when_the_household_has_one() -> None:
    data = cdr.plan(pricing_model="TIME_OF_USE_CONT_LOAD", contract_extra={"controlledLoad": [{"displayName": "CL"}]})
    found = tariff(data, household=Household(frozenset(), controlled_load=True))
    assert found.notes == ("controlled_load_unpriced",)


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"effective_to": "2026-10-31T13:00:00Z"}, "not_effective"),
        ({"effective_from": "2026-11-02T00:00:00Z"}, "not_effective"),
        ({"fuel": "GAS"}, "fuel:GAS"),
    ],
)
def test_plans_not_in_effect_or_of_another_fuel_are_excluded(changes: dict[str, Any], expected: str) -> None:
    assert expected in reasons(cdr.plan(**changes))


def test_an_effective_end_on_the_as_of_date_still_counts() -> None:
    assert tariff(cdr.plan(effective_to="2026-11-01T12:00:00Z")).effective_to == AS_OF


@pytest.mark.parametrize(
    ("mutate", "where"),
    [
        (
            lambda data: data["electricityContract"]["tariffPeriod"][0].update(dailySupplyCharge="lots"),
            "dailySupplyCharge",
        ),
        (
            lambda data: data["electricityContract"]["tariffPeriod"][0]["timeOfUseRates"][0]["timeOfUse"][0].update(
                startTime="4pm"
            ),
            "startTime",
        ),
        (lambda data: data["electricityContract"].update(pricingModel="SOMETHING"), "pricingModel"),
        (lambda data: data.update(lastUpdated=None, electricityContract=None), "electricityContract"),
        (lambda data: data["electricityContract"].update(tariffPeriod=[]), "tariffPeriod"),
        (lambda data: data["electricityContract"]["tariffPeriod"][0].update(rateBlockUType="other"), "rateBlockUType"),
    ],
)
def test_malformed_values_are_excluded_naming_the_field(mutate: Any, where: str) -> None:
    data = cdr.plan()
    mutate(data)
    (reason,) = [found for found in reasons(data) if found.startswith("malformed:")]
    assert where in reason


def test_reasons_are_sorted_and_unique() -> None:
    data = cdr.plan(pricing_model="TIME_OF_USE_CONT_LOAD", eligibility=["EXISTING_SOLAR", "EXISTING_BATTERY"])
    assert reasons(data) == ("controlled_load_plan", "eligibility:EXISTING_BATTERY", "eligibility:EXISTING_SOLAR")


def test_mapping_never_raises_on_odd_shapes() -> None:
    for data in [{}, {"fuelType": "ELECTRICITY"}, {"fuelType": "ELECTRICITY", "electricityContract": []}]:
        assert isinstance(mapped(data), Excluded)


# offer_manual prices written with GST


def test_prices_written_with_gst_are_divided_back() -> None:
    plan = Plan(offer_snapshot_id=PLAN.offer_snapshot_id, retailer="manual", source="manual", prices_include_gst=True)
    data = cdr.plan(pricing_model="SINGLE_RATE", periods=[cdr.single_period([cdr.rate("0.33")], supply="1.10")])
    found = tariff(data, plan=plan)
    assert found.source == "manual"
    assert found.seasons[0].daily_supply == Decimal("1")
    assert found.seasons[0].energy[0].blocks[0].unit_price == Decimal("0.3")


def test_fees_written_with_gst_are_not_divided() -> None:
    plan = Plan(offer_snapshot_id=PLAN.offer_snapshot_id, retailer="manual", source="manual", prices_include_gst=True)
    found = tariff(cdr.plan(fees=[cdr.fee(amount="66.00")]), plan=plan)
    assert found.fees[0].amount_inc_gst == Decimal("66.00")
