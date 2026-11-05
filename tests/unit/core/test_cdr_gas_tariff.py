"""`core.cdr_gas_tariff` (P5 spec §5.2): one test per row of the support table, and the `GasTariff` rules (§5.1).
Every plan comes from `tests/fixtures/make_cdr.py`."""

from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from energatron.core.cdr_gas_tariff import map_cdr_gas
from energatron.core.cdr_tariff import Excluded, Household, Plan
from energatron.core.errors import InvalidInputError
from energatron.core.gas_tariff import GasBlock, GasSeason, GasTariff
from tests.fixtures import make_cdr as cdr

AS_OF = date(2026, 11, 1)
HOUSEHOLD = Household(unmet_eligibility=frozenset({"EXISTING_SOLAR"}), controlled_load=False)
PLAN = Plan(offer_snapshot_id="TEST-GAS@EME~20261001T000000Z~0123abcd", retailer="alpha")


def mapped(data: dict[str, Any], plan: Plan = PLAN) -> GasTariff | Excluded:
    return map_cdr_gas(data, plan, HOUSEHOLD, as_of=AS_OF)


def tariff(data: dict[str, Any]) -> GasTariff:
    found = mapped(data)
    assert isinstance(found, GasTariff), found
    return found


def reasons(data: dict[str, Any]) -> tuple[str, ...]:
    found = mapped(data)
    assert isinstance(found, Excluded), found
    return found.reasons


def test_a_daily_block_plan_maps_with_its_mj_allowances() -> None:
    found = tariff(cdr.gas_plan())
    (season,) = found.seasons
    assert (season.start, season.end, season.daily_supply, season.block_period) == (
        "07-01",
        "06-30",
        Decimal("0.80"),
        "P1D",
    )
    assert [(block.volume_mj, block.unit_price) for block in season.blocks] == [
        (Decimal("50"), Decimal("0.04")),
        (None, Decimal("0.03")),
    ]
    assert (found.status, found.distributors, found.pricing_model) == (
        "normalised",
        (cdr.GAS_DISTRIBUTOR,),
        "SINGLE_RATE",
    )


def test_two_seasons_map_when_they_partition_the_year() -> None:
    winter = cdr.gas_period(supply="0.90", start="06-01", end="09-30")
    summer = cdr.gas_period(supply="0.70", start="10-01", end="05-31")
    found = tariff(cdr.gas_plan(periods=[winter, summer]))
    assert [season.daily_supply for season in found.seasons] == [Decimal("0.90"), Decimal("0.70")]


def test_a_monthly_block_is_supported_as_an_approximation() -> None:
    found = tariff(cdr.gas_plan(periods=[cdr.gas_period(period="P1M")]))
    assert found.seasons[0].block_period == "P1M"
    assert (found.status, found.approximations) == ("approximate", ("block_month_prorated",))


def test_a_single_rate_with_any_period_is_supported() -> None:
    found = tariff(cdr.gas_plan(periods=[cdr.gas_period([cdr.rate("0.05", unit=None)], period="P1Y")]))
    assert [block.volume_mj for block in found.seasons[0].blocks] == [None]


def test_mj_written_out_is_mj() -> None:
    rates = [cdr.rate("0.04", volume=50, unit="MJ"), cdr.rate("0.03", unit="MJ")]
    assert tariff(cdr.gas_plan(periods=[cdr.gas_period(rates)])).status == "normalised"


@pytest.mark.parametrize(
    ("data", "reason"),
    [
        (cdr.gas_plan(fuel="ELECTRICITY"), "fuel:ELECTRICITY"),
        (cdr.gas_plan(pricing_model="TIME_OF_USE"), "pricing_model:TIME_OF_USE"),
        (
            cdr.gas_plan(periods=[cdr.gas_period(extra={"rateBlockUType": "timeOfUseRates"})]),
            "rate_type:timeOfUseRates",
        ),
        (cdr.gas_plan(periods=[cdr.gas_period(extra={"dailySupplyChargeType": "BAND"})]), "banded_supply"),
        (cdr.gas_plan(periods=[cdr.gas_period(start="07-01", end="12-31")]), "seasons_not_partition"),
        (
            cdr.gas_plan(periods=[cdr.gas_period([cdr.rate("0.04", volume=50, unit="KWH"), cdr.rate("0.03")])]),
            "measure_unit:KWH",
        ),
        (cdr.gas_plan(periods=[cdr.gas_period(period="P1Y")]), "block_period_unclear"),
        (cdr.gas_plan(periods=[cdr.gas_period(period=None)]), "block_period_unclear"),
        (cdr.gas_plan(eligibility=["EXISTING_SOLAR"]), "eligibility:EXISTING_SOLAR"),
        (cdr.gas_plan(effective_to="2026-10-01T00:00:00Z"), "not_effective"),
        (cdr.gas_plan(effective_from="2026-12-01T00:00:00Z"), "not_effective"),
        (
            cdr.gas_plan(
                periods=[cdr.gas_period([cdr.rate("0", volume=50, unit=None), cdr.rate("0", unit=None)], supply="0")]
            ),
            "zero_priced",
        ),
        (cdr.gas_plan(periods=[cdr.gas_period(supply="cheap")]), "malformed:tariffPeriod.dailySupplyCharge"),
    ],
)
def test_each_unsupported_feature_excludes_with_its_reason(data: dict[str, Any], reason: str) -> None:
    assert reasons(data) == (reason,)


def test_a_missing_gas_contract_is_malformed() -> None:
    data = cdr.gas_plan()
    del data["gasContract"]
    assert reasons(data) == ("malformed:gasContract",)


def test_eligibility_other_is_an_approximation_and_discounts_and_fees_map_as_p4() -> None:
    data = cdr.gas_plan(
        eligibility=["OTHER"],
        discounts=[cdr.discount("GUARANTEED", "percentOfBill", category=None, rate_or_amount="0.10")],
        fees=[cdr.fee("MEMBERSHIP", "ANNUAL", "24.00"), cdr.fee("DISCONNECTION", "FIXED", "50.00")],
        contract_extra={"incentives": [{"displayName": "A made-up incentive"}]},
    )
    found = tariff(data)
    assert found.approximations == ("eligibility_unverified",)
    assert [(discount.kind, discount.rate) for discount in found.discounts] == [("guaranteed", Decimal("0.10"))]
    assert [(fee.type, fee.amount_inc_gst) for fee in found.fees] == [("MEMBERSHIP", Decimal("24.00"))]
    assert found.notes == ("fee_not_periodic:DISCONNECTION", "incentives_unpriced")


def test_prices_written_with_gst_are_divided_back() -> None:
    plan = Plan(offer_snapshot_id=PLAN.offer_snapshot_id, retailer="manual", source="manual", prices_include_gst=True)
    found = tariff(cdr.gas_plan(periods=[cdr.gas_period(supply="1.10")]))
    divided = map_cdr_gas(cdr.gas_plan(periods=[cdr.gas_period(supply="1.10")]), plan, HOUSEHOLD, as_of=AS_OF)
    assert isinstance(divided, GasTariff)
    assert (found.seasons[0].daily_supply, divided.seasons[0].daily_supply) == (Decimal("1.10"), Decimal(1))


def test_the_time_zone_is_ignored() -> None:
    assert tariff(cdr.gas_plan(contract_extra={"timeZone": "AEST"})) == tariff(cdr.gas_plan())


# GasTariff (§5.1)


def season(
    start: str, end: str, blocks: tuple[GasBlock, ...] = (GasBlock(volume_mj=None, unit_price=Decimal(1)),)
) -> GasSeason:
    return GasSeason(start=start, end=end, daily_supply=Decimal(1), block_period="P1D", blocks=blocks)


def test_a_block_ladder_needs_volumes_on_all_but_the_last() -> None:
    with pytest.raises(InvalidInputError):
        season(
            "01-01",
            "12-31",
            (GasBlock(volume_mj=None, unit_price=Decimal(1)), GasBlock(volume_mj=None, unit_price=Decimal(1))),
        )
    with pytest.raises(InvalidInputError):
        season("01-01", "12-31", (GasBlock(volume_mj=Decimal(5), unit_price=Decimal(1)),))


def test_a_tariff_needs_seasons_that_partition_the_year_and_a_status_matching_its_approximations() -> None:
    base = tariff(cdr.gas_plan())
    with pytest.raises(InvalidInputError):
        GasTariff.model_validate(base.model_dump() | {"seasons": [season("01-01", "06-30").model_dump()]})
    with pytest.raises(InvalidInputError):
        GasTariff.model_validate(base.model_dump() | {"status": "approximate"})
    assert season("10-01", "05-31").month_days()[0] == "10-01"
