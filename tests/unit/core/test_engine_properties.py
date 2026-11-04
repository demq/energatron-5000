"""The engine's invariants (P4 spec §7.9), over generated tariffs and usage."""

from datetime import date
from decimal import Decimal

import pandas as pd
from hypothesis import given, settings
from hypothesis import strategies as st

from energatron.core.engine import money, prepare_usage, price, quantity
from energatron.core.tariff import Tariff
from tests.fixtures import make_cdr as cdr
from tests.unit.core.test_engine import PERIODS, PRICING, tariff_of, usage_frame

PRICES = st.decimals(min_value=Decimal("0.01"), max_value=Decimal("0.99"), places=4)


def tou_tariff(peak: Decimal, shoulder: Decimal, off_peak: Decimal, volume: int | None) -> Tariff:
    rates = cdr.standard_tou(peak=str(peak), shoulder=str(shoulder), off_peak=str(off_peak))
    if volume is not None:
        rates[2] = cdr.tou_rate(
            "SHOULDER", rates[2]["timeOfUse"], [cdr.rate("0.05", volume=volume), cdr.rate(str(shoulder))]
        )
    return tariff_of(cdr.plan(periods=[cdr.tou_period(rates)], discounts=[cdr.discount(rate_or_amount="0.03")]))


tariffs = st.builds(tou_tariff, PRICES, PRICES, PRICES, st.one_of(st.none(), st.integers(min_value=1, max_value=8)))
frames = st.builds(
    lambda seed, scale: usage_frame(days=20, seed=seed, scale=scale), st.integers(0, 50), st.floats(0.1, 5)
)


@settings(max_examples=25, deadline=None)
@given(tariffs, frames)
def test_usage_quantities_conserve_the_metered_energy(tariff: Tariff, frame: pd.DataFrame) -> None:
    bill = price(tariff, prepare_usage(frame), PERIODS, scenario="conditions_met", pricing=PRICING)
    for first, last in PERIODS:
        metered = frame[(frame["local_date"] >= first) & (frame["local_date"] <= last)]["kwh"].sum()
        lines = [line for line in bill.lines if line.line == "usage" and line.period_start == first]
        assert abs(float(sum(line.quantity for line in lines)) - metered) < 1e-6 * max(1, len(lines))


@settings(max_examples=25, deadline=None)
@given(tariffs, frames)
def test_lines_sum_to_the_totals_and_every_amount_is_in_cents(tariff: Tariff, frame: pd.DataFrame) -> None:
    bill = price(tariff, prepare_usage(frame), PERIODS, scenario="conditions_not_met", pricing=PRICING)
    assert sum(bill.period_totals().values(), Decimal(0)) == bill.total()
    assert all(isinstance(line.amount, Decimal) and line.amount == money(line.amount) for line in bill.lines)


@settings(max_examples=15, deadline=None)
@given(PRICES, PRICES, PRICES, frames)
def test_doubling_every_price_doubles_each_usage_line_before_rounding(
    peak: Decimal, shoulder: Decimal, off_peak: Decimal, frame: pd.DataFrame
) -> None:
    usage = prepare_usage(frame)
    once = price(tou_tariff(peak, shoulder, off_peak, None), usage, PERIODS, scenario="conditions_met", pricing=PRICING)
    twice = price(
        tou_tariff(2 * peak, 2 * shoulder, 2 * off_peak, None),
        usage,
        PERIODS,
        scenario="conditions_met",
        pricing=PRICING,
    )
    pairs = zip(
        [line for line in once.lines if line.line == "usage"],
        [line for line in twice.lines if line.line == "usage"],
        strict=True,
    )
    for single, double in pairs:
        assert single.quantity == double.quantity
        assert double.amount == money(double.quantity * double.rate)
        assert abs(double.amount - 2 * single.amount) <= Decimal("0.01")


@settings(max_examples=15, deadline=None)
@given(tariffs, frames)
def test_the_order_of_the_rows_changes_nothing(tariff: Tariff, frame: pd.DataFrame) -> None:
    shuffled = frame.sample(frac=1.0, random_state=7).reset_index(drop=True)
    first = price(tariff, prepare_usage(frame), PERIODS, scenario="conditions_met", pricing=PRICING)
    second = price(tariff, prepare_usage(shuffled), PERIODS, scenario="conditions_met", pricing=PRICING)
    assert first == second


@settings(max_examples=20, deadline=None)
@given(PRICES, frames)
def test_one_charge_without_blocks_is_the_rounded_product_per_season(rate: Decimal, frame: pd.DataFrame) -> None:
    tariff = tariff_of(cdr.plan(pricing_model="SINGLE_RATE", periods=[cdr.single_period([cdr.rate(str(rate))])]))
    period = [(date(2025, 9, 20), date(2025, 10, 9))]
    bill = price(tariff, prepare_usage(frame), period, scenario="conditions_met", pricing=PRICING)
    (usage,) = [line for line in bill.lines if line.line == "usage"]
    in_period = frame[(frame["local_date"] >= period[0][0]) & (frame["local_date"] <= period[0][1])]
    assert usage.amount == money(quantity(float(in_period["kwh"].sum())) * rate)
