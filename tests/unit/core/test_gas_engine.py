"""The gas engine (P5 spec §9.2) by hand, against the independent oracle (`tests/fixtures/oracle.oracle_gas_totals`)
and by property. Tariffs come from `tests/fixtures/make_cdr.py` through `map_cdr_gas`; every value is made up."""

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from energatron.core.cdr_gas_tariff import map_cdr_gas
from energatron.core.cdr_tariff import Household, Plan
from energatron.core.engine import Pricing
from energatron.core.gas_engine import GasPeriod, price_gas
from energatron.core.gas_tariff import GasTariff
from tests.fixtures import make_cdr as cdr
from tests.fixtures.oracle import oracle_gas_totals

PRICING = Pricing(gst_rate=Decimal("0.10"), assume_conditions_met=frozenset({"PAY_ON_TIME"}), demand_minutes=30)
PLAN = Plan(offer_snapshot_id="TEST-GAS@EME~20261001T000000Z~0123abcd", retailer="alpha")


def gas_tariff(**changes: Any) -> GasTariff:
    found = map_cdr_gas(cdr.gas_plan(**changes), PLAN, Household(frozenset(), False), as_of=date(2026, 11, 1))
    assert isinstance(found, GasTariff), found
    return found


def seasonal(**changes: Any) -> GasTariff:
    winter = cdr.gas_period(
        [cdr.rate("0.05", volume=60, unit=None), cdr.rate("0.035", unit=None)],
        supply="0.90",
        start="06-01",
        end="09-30",
    )
    summer = cdr.gas_period(supply="0.70", start="10-01", end="05-31")
    return gas_tariff(periods=[winter, summer], **changes)


def plain(tariff: GasTariff) -> dict[str, Any]:
    return {
        "seasons": [
            {
                "days": set(season.month_days()),
                "supply": season.daily_supply,
                "period": season.block_period,
                "blocks": [(block.volume_mj, block.unit_price) for block in season.blocks],
            }
            for season in tariff.seasons
        ],
        "discounts": [
            {"kind": d.kind, "category": d.category, "method": d.method, "rate": d.rate}
            for d in tariff.discounts
            if d.method != "fixed"
        ],
        "fees": [{"term": fee.term, "amount": fee.amount_inc_gst} for fee in tariff.fees],
    }


def totals(
    tariff: GasTariff, periods: list[GasPeriod], scenario: str = "conditions_met"
) -> dict[tuple[date, date], Decimal]:
    return price_gas(tariff, periods, scenario=scenario, pricing=PRICING).period_totals()  # type: ignore[arg-type]


def test_a_one_season_bill_by_hand() -> None:
    period = GasPeriod(date(2026, 9, 1), date(2026, 9, 30), Decimal("2000"))
    lines = price_gas(gas_tariff(), [period], scenario="conditions_met", pricing=PRICING).lines
    assert [(line.line, line.quantity, line.amount) for line in lines] == [
        ("supply", Decimal(30), Decimal("24.00")),
        ("usage", Decimal("1500"), Decimal("60.00")),  # 50 MJ a day for 30 days
        ("usage", Decimal("500"), Decimal("15.00")),
        ("gst", Decimal("99.00"), Decimal("9.90")),
    ]


def test_usage_below_the_first_step_uses_one_block() -> None:
    lines = price_gas(gas_tariff(), [GasPeriod(date(2026, 9, 1), date(2026, 9, 30), Decimal("1234.56"))],
                      scenario="conditions_met", pricing=PRICING).lines  # fmt: skip
    assert [(line.block, line.quantity) for line in lines if line.line == "usage"] == [(0, Decimal("1234.56"))]


def test_a_period_across_two_seasons_is_split_by_days_and_conserves_mj() -> None:
    period = GasPeriod(date(2026, 9, 16), date(2026, 10, 15), Decimal("1000.01"))
    lines = price_gas(seasonal(), [period], scenario="conditions_met", pricing=PRICING).lines
    supply = [(line.season, line.quantity) for line in lines if line.line == "supply"]
    assert supply == [(0, Decimal(15)), (1, Decimal(15))]
    usage = [line for line in lines if line.line == "usage"]
    assert sum((line.quantity for line in usage), Decimal(0)) == period.mj
    assert sum((line.quantity for line in usage if line.season == 0), Decimal(0)) == Decimal("500.005000")


def test_a_monthly_block_is_prorated_by_days() -> None:
    tariff = gas_tariff(
        periods=[cdr.gas_period([cdr.rate("0.04", volume=1520, unit=None), cdr.rate("0.03", unit=None)], period="P1M")]
    )
    lines = price_gas(
        tariff,
        [GasPeriod(date(2026, 9, 1), date(2026, 9, 30), Decimal("2000"))],
        scenario="conditions_met",
        pricing=PRICING,
    ).lines
    first = next(line for line in lines if line.line == "usage")
    assert first.quantity == (Decimal(1520) * 30 * 12 / 365).quantize(Decimal("0.000001"))


def test_conditional_and_guaranteed_discounts_and_fees_settle_as_for_electricity() -> None:
    tariff = gas_tariff(
        discounts=[
            cdr.discount("GUARANTEED", "percentOfUse", category=None, rate_or_amount="0.10"),
            cdr.discount("CONDITIONAL", "percentOfBill", category="PAY_ON_TIME", rate_or_amount="0.05"),
        ],
        fees=[cdr.fee("MEMBERSHIP", "ANNUAL", "36.50")],
    )
    period = [GasPeriod(date(2026, 9, 1), date(2026, 9, 30), Decimal("1000"))]
    met = price_gas(tariff, period, scenario="conditions_met", pricing=PRICING).lines
    unmet = price_gas(tariff, period, scenario="conditions_not_met", pricing=PRICING).lines
    assert [line.amount for line in met if line.line == "discount"] == [Decimal("-4.00"), Decimal("-3.20")]
    assert [line.amount for line in unmet if line.line == "discount"] == [Decimal("-4.00")]
    assert [line.amount for line in met if line.line == "fee"] == [Decimal("3.00")]


def test_zero_mj_gives_supply_and_no_usage() -> None:
    lines = price_gas(
        gas_tariff(),
        [GasPeriod(date(2026, 9, 1), date(2026, 9, 3), Decimal(0))],
        scenario="conditions_met",
        pricing=PRICING,
    ).lines
    assert [line.line for line in lines] == ["supply", "gst"]


@pytest.mark.parametrize("make", [gas_tariff, seasonal])
def test_the_engine_matches_the_oracle(make: Any) -> None:
    tariff = make(
        discounts=[cdr.discount("CONDITIONAL", "percentOfBill", category="PAY_ON_TIME", rate_or_amount="0.07")]
    )
    periods = [
        GasPeriod(date(2026, 1, 3), date(2026, 2, 1), Decimal("812.40")),
        GasPeriod(date(2026, 5, 20), date(2026, 7, 2), Decimal("5432.10")),
        GasPeriod(date(2026, 9, 25), date(2026, 10, 24), Decimal("1999.99")),
    ]
    for scenario in ("conditions_met", "conditions_not_met"):
        expected = oracle_gas_totals(
            plain(tariff), [(p.first, p.last, p.mj) for p in periods], scenario=scenario,
            gst_rate=PRICING.gst_rate, conditions=PRICING.assume_conditions_met,
        )  # fmt: skip
        found = totals(tariff, periods, scenario)
        assert all(abs(found[key] - expected[key]) <= Decimal("0.02") for key in expected), (found, expected)


@settings(max_examples=60, deadline=None)
@given(
    start=st.dates(min_value=date(2025, 1, 1), max_value=date(2027, 12, 1)),
    days=st.integers(min_value=1, max_value=100),
    mj=st.decimals(min_value=0, max_value=20000, places=2),
)
def test_properties_hold(start: date, days: int, mj: Decimal) -> None:
    period = GasPeriod(start, start + timedelta(days=days - 1), mj)
    bill = price_gas(seasonal(), [period], scenario="conditions_met", pricing=PRICING)
    usage = [line for line in bill.lines if line.line == "usage"]
    assert sum((line.quantity for line in usage), Decimal(0)) == mj
    assert sum((line.quantity for line in bill.lines if line.line == "supply"), Decimal(0)) == days
    assert bill.total() == sum(bill.period_totals().values())
    assert all(isinstance(line.amount, Decimal) and isinstance(line.quantity, Decimal) for line in bill.lines)


def test_a_single_rate_tariff_charges_mj_times_the_rate() -> None:
    tariff = gas_tariff(periods=[cdr.gas_period([cdr.rate("0.04321", unit=None)])])
    lines = price_gas(
        tariff,
        [GasPeriod(date(2026, 9, 16), date(2026, 10, 15), Decimal("1234.56"))],
        scenario="conditions_met",
        pricing=PRICING,
    ).lines
    (usage,) = [line for line in lines if line.line == "usage"]
    assert usage.amount == (Decimal("1234.56") * Decimal("0.04321")).quantize(Decimal("0.01"))
