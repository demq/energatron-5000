"""The engine (P4 spec §7) against the independent oracle (`tests/fixtures/oracle.oracle_bill`) and by hand.

Usage is generated: half-hourly (or 5-minute) intervals across South Australia's October daylight-saving change, with
made-up values. Tariffs come from `tests/fixtures/make_cdr.py` through `map_cdr`, and are handed to the oracle as plain
dicts.
"""

from collections.abc import Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from energatron.core.cdr_tariff import Household, Plan, map_cdr
from energatron.core.engine import Bill, Pricing, money, prepare_usage, price
from energatron.core.tariff import Tariff
from tests.fixtures import make_cdr as cdr
from tests.fixtures.oracle import UsageRow, oracle_bill

ZONE = ZoneInfo("Australia/Adelaide")
PRICING = Pricing(
    gst_rate=Decimal("0.10"), assume_conditions_met=frozenset({"PAY_ON_TIME", "DIRECT_DEBIT"}), demand_minutes=30
)
HOUSEHOLD = Household(unmet_eligibility=frozenset(), controlled_load=False)
FIRST = date(2025, 9, 20)  # SA's clocks go forward on 2025-10-05
PERIODS = [
    (date(2025, 9, 20), date(2025, 9, 30)),
    (date(2025, 10, 1), date(2025, 10, 31)),
    (date(2025, 11, 1), date(2025, 11, 9)),
]


def usage_frame(
    first: date = FIRST, days: int = 51, *, minutes: int = 30, seed: int = 3, scale: float = 1.0
) -> pd.DataFrame:
    """Placed intervals on the real clock: UTC starts every `minutes` from local midnight of `first`."""
    rng = np.random.default_rng(seed)
    start = datetime(first.year, first.month, first.day, tzinfo=ZONE).astimezone(UTC)
    count = days * 1440 // minutes
    utc = pd.date_range(start, periods=count, freq=f"{minutes}min", tz="UTC")
    local = utc.tz_convert(ZONE)
    hours = local.hour.to_numpy()
    shape = 0.2 + 0.6 * ((hours >= 17) & (hours < 22)) + 0.3 * ((hours >= 7) & (hours < 9))
    kwh = np.round(shape * rng.uniform(0.5, 1.5, count) * minutes / 30 * scale, 3)
    meter = utc.tz_convert(None) + pd.Timedelta(hours=10)
    return pd.DataFrame(
        {
            "suffix": "E1",
            "utc_start": utc,
            "local_start": local.tz_localize(None),
            "local_date": local.tz_localize(None).normalize().date,
            "meter_date": meter.normalize().date,
            "interval_minutes": minutes,
            "kwh": kwh,
        }
    )


def rows_of(frame: pd.DataFrame) -> list[UsageRow]:
    records: list[dict[str, Any]] = frame.to_dict("records")  # pyright: ignore[reportAssignmentType]
    return [
        UsageRow(
            utc_start=record["utc_start"].tz_convert(None).to_pydatetime(),
            local_start=record["local_start"].to_pydatetime(),
            local_date=record["local_date"],
            meter_date=record["meter_date"],
            minutes=int(record["interval_minutes"]),
            kwh=float(record["kwh"]),
        )
        for record in records
    ]


def tariff_of(data: dict[str, Any], household: Household = HOUSEHOLD) -> Tariff:
    found = map_cdr(
        data,
        Plan(offer_snapshot_id="TEST1@EME~20261001T000000Z~0123abcd", retailer="alpha"),
        household,
        as_of=date(2026, 11, 1),
    )
    assert isinstance(found, Tariff), found
    return found


def oracle_tariff(tariff: Tariff) -> dict[str, Any]:
    """The tariff as the oracle's plain dict: values copied field by field."""
    return {
        "seasons": [
            {
                "start": season.start,
                "end": season.end,
                "clock": season.clock,
                "supply": season.daily_supply,
                "charges": [
                    {
                        "label": charge.label,
                        "windows": [
                            (set(window.days), window.start_minute, window.end_minute) for window in charge.windows
                        ],
                        "period": charge.block_period,
                        "blocks": [(block.volume_kwh, block.unit_price) for block in charge.blocks],
                    }
                    for charge in season.energy
                ],
                "demand": [
                    {
                        "days": set(demand.days),
                        "start": demand.start_minute,
                        "end": demand.end_minute,
                        "amount": demand.amount_per_kw,
                        "measurement": demand.measurement,
                        "charge": demand.charge,
                        "min_kw": demand.min_kw,
                        "max_kw": demand.max_kw,
                    }
                    for demand in season.demand
                ],
            }
            for season in tariff.seasons
        ],
        "discounts": [
            {"kind": discount.kind, "category": discount.category, "method": discount.method, "rate": discount.rate}
            for discount in tariff.discounts
            if discount.method != "fixed"
        ],
        "fees": [{"term": fee.term, "amount": fee.amount_inc_gst} for fee in tariff.fees],
    }


def engine_bill(
    tariff: Tariff, frame: pd.DataFrame, periods: Sequence[tuple[date, date]] = PERIODS, **kwargs: Any
) -> Bill:
    return price(
        tariff,
        prepare_usage(frame),
        periods,
        scenario=kwargs.pop("scenario", "conditions_met"),
        pricing=kwargs.pop("pricing", PRICING),
        **kwargs,
    )


def assert_matches_oracle(
    tariff: Tariff,
    frame: pd.DataFrame,
    *,
    scenario: str = "conditions_met",
    by: str = "local_date",
    pricing: Pricing = PRICING,
) -> Bill:
    bill = price(tariff, prepare_usage(frame), PERIODS, scenario=scenario, pricing=pricing, by=by)  # type: ignore[arg-type]
    expected = oracle_bill(
        oracle_tariff(tariff),
        rows_of(frame),
        PERIODS,
        gst_rate=pricing.gst_rate,
        conditions=pricing.assume_conditions_met,
        scenario=scenario,
        demand_minutes=pricing.demand_minutes,
        by=by,
    )
    assert bill.period_totals() == expected["totals"]
    for line in bill.lines:
        if line.line == "usage":
            key = (line.period_start, line.period_end, line.season, line.label, line.block)
            assert abs(float(line.quantity) - expected["usage_kwh"].get(key, 0.0)) < 1e-6
    return bill


# The engine equals the oracle


TOU_PLAN = cdr.plan(
    discounts=[cdr.discount("CONDITIONAL", "percentOfBill", category="PAY_ON_TIME", rate_or_amount="0.05")]
)


@pytest.mark.parametrize("scenario", ["conditions_met", "conditions_not_met"])
def test_a_tou_plan_with_a_conditional_discount(scenario: str) -> None:
    assert_matches_oracle(tariff_of(TOU_PLAN), usage_frame(), scenario=scenario)


def test_a_tou_plan_on_the_aest_clock() -> None:
    assert_matches_oracle(tariff_of(cdr.plan(time_zone="AEST")), usage_frame())


def test_daily_blocks_and_a_membership_fee() -> None:
    blocks = [cdr.rate("0.20", volume=3), cdr.rate("0.32", volume=4), cdr.rate("0.40")]
    data = cdr.plan(
        pricing_model="SINGLE_RATE",
        periods=[cdr.single_period(blocks, period="P1D")],
        fees=[cdr.fee("MEMBERSHIP", "ANNUAL", "60.00")],
    )
    assert_matches_oracle(tariff_of(data), usage_frame())


def test_monthly_blocks_in_a_tou_charge() -> None:
    rates = cdr.standard_tou()
    rates[1] = cdr.tou_rate(
        "OFF_PEAK", rates[1]["timeOfUse"], [cdr.rate("0.00", volume=20), cdr.rate("0.22")], period="P1M"
    )
    assert_matches_oracle(tariff_of(cdr.plan(periods=[cdr.tou_period(rates)])), usage_frame())


def test_two_seasons_split_inside_a_period() -> None:
    summer = cdr.tou_period(cdr.standard_tou(peak="0.50"), start="10-15", end="04-30", supply="1.20")
    winter = cdr.tou_period(cdr.standard_tou(peak="0.35"), start="05-01", end="10-14", supply="0.90")
    bill = assert_matches_oracle(tariff_of(cdr.plan(periods=[summer, winter])), usage_frame())
    october = [line for line in bill.lines if line.line == "supply" and line.period_start == date(2025, 10, 1)]
    assert [(line.season, line.quantity) for line in october] == [(0, Decimal(17)), (1, Decimal(14))]


@pytest.mark.parametrize(
    ("measurement", "charge", "minimum", "maximum"),
    [
        ("MONTH", "DAY", "0.00", None),
        ("DAY", "DAY", "0.50", None),
        ("MONTH", "MONTH", "0.00", "1.5"),
        ("DAY", "MONTH", "0.00", None),
    ],
)
def test_demand_charges(measurement: str, charge: str, minimum: str, maximum: str | None) -> None:
    demand = [
        cdr.demand_charge(
            "0.45",
            start="17:00",
            end="20:59",
            days=["MON", "TUE", "WED", "THU", "FRI"],
            measurement=measurement,
            charge=charge,
            min_demand=minimum,
            max_demand=maximum,
        )
    ]
    bill = assert_matches_oracle(tariff_of(cdr.plan(periods=[cdr.tou_period(demand=demand)])), usage_frame())
    assert any(line.line == "demand" and line.amount > 0 for line in bill.lines)


def test_five_minute_data_and_meter_date_periods() -> None:
    assert_matches_oracle(tariff_of(TOU_PLAN), usage_frame(days=40, minutes=5), by="meter_date")


def test_a_guaranteed_discount_of_usage_applies_in_every_scenario() -> None:
    data = cdr.plan(discounts=[cdr.discount("GUARANTEED", "percentOfUse", category=None, rate_or_amount="0.10")])
    assert_matches_oracle(tariff_of(data), usage_frame(), scenario="conditions_not_met")


# By hand


def flat_tariff(price_text: str = "0.30", supply: str = "1.00") -> Tariff:
    return tariff_of(
        cdr.plan(pricing_model="SINGLE_RATE", periods=[cdr.single_period([cdr.rate(price_text)], supply=supply)])
    )


def constant_frame(days: int, kwh: float, minutes: int = 30) -> pd.DataFrame:
    frame = usage_frame(date(2025, 6, 1), days, minutes=minutes)
    return frame.assign(kwh=kwh)


def test_a_flat_tariff_by_hand() -> None:
    """Ten winter days, 48 half-hours of 0.5 kWh: 240 kWh at 0.30 is 72.00, supply 10.00, GST 8.20, total 90.20."""
    bill = engine_bill(flat_tariff(), constant_frame(10, 0.5), [(date(2025, 6, 1), date(2025, 6, 10))])
    lines = {line.line: line for line in bill.lines}
    assert (lines["usage"].quantity, lines["usage"].amount) == (Decimal("240.000000"), Decimal("72.00"))
    assert lines["supply"].amount == Decimal("10.00")
    assert lines["gst"].amount == Decimal("8.20")
    assert bill.total() == Decimal("90.20")


def test_a_daily_free_block_by_hand() -> None:
    """Two days of 24 kWh each; the first 10 kWh a day are free, the rest 0.40: 2 x 14 x 0.40 = 11.20."""
    blocks = [cdr.rate("0.00", volume=10), cdr.rate("0.40")]
    tariff = tariff_of(
        cdr.plan(pricing_model="SINGLE_RATE", periods=[cdr.single_period(blocks, supply="0", period="P1D")])
    )
    bill = engine_bill(tariff, constant_frame(2, 0.5), [(date(2025, 6, 1), date(2025, 6, 2))])
    usage = [line for line in bill.lines if line.line == "usage"]
    assert [(line.block, line.quantity, line.amount) for line in usage] == [
        (0, Decimal("20.000000"), Decimal("0.00")),
        (1, Decimal("28.000000"), Decimal("11.20")),
    ]


def test_a_monthly_peak_demand_by_hand() -> None:
    """One weekday with a 2 kWh half-hour (4 kW) in the window: 4 kW x 0.50 x 1 day = 2.00."""
    frame = usage_frame(date(2025, 6, 2), 1).assign(kwh=0.1)  # 2025-06-02 is a Monday
    frame.loc[frame["local_start"] == datetime(2025, 6, 2, 18, 0), "kwh"] = 2.0
    demand = [cdr.demand_charge("0.50", start="17:00", end="20:59", measurement="MONTH", charge="DAY")]
    tariff = tariff_of(cdr.plan(periods=[cdr.tou_period(supply="0", demand=demand)]))
    (line,) = [
        found
        for found in engine_bill(tariff, frame, [(date(2025, 6, 2), date(2025, 6, 2))]).lines
        if found.line == "demand"
    ]
    assert (line.quantity, line.unit, line.amount) == (Decimal("4.000000"), "kW-day", Decimal("2.00"))


def test_a_guaranteed_fixed_amount_is_a_one_off_credit_outside_the_periods() -> None:
    data = cdr.plan(
        pricing_model="SINGLE_RATE",
        discounts=[cdr.discount("GUARANTEED", "fixedAmount", category=None, rate_or_amount="50")],
    )
    bill = engine_bill(tariff_of(data), constant_frame(3, 0.1), [(date(2025, 6, 1), date(2025, 6, 3))])
    assert bill.one_off_credit == Decimal(50)
    assert all(line.line != "discount" for line in bill.lines)


@pytest.mark.parametrize(
    ("term", "expected"),
    [("ANNUAL", "4.93"), ("MONTHLY", "59.18"), ("WEEKLY", "257.14"), ("DAILY", "180.00"), ("BIANNUAL", "9.86")],
)
def test_fees_are_prorated_by_days(term: str, expected: str) -> None:
    """A 60.00 fee (6.00 daily) over 30 days: annual 60 x 30 / 365, biannual 60 x 30 / 182.5, monthly 60 x 30 x 12 /
    365, weekly 60 x 30 / 7, daily 6 x 30."""
    data = cdr.plan(
        pricing_model="SINGLE_RATE", fees=[cdr.fee("MEMBERSHIP", term, "60.00" if term != "DAILY" else "6.00")]
    )
    bill = engine_bill(tariff_of(data), constant_frame(30, 0.1), [(date(2025, 6, 1), date(2025, 6, 30))])
    (fee,) = [line for line in bill.lines if line.line == "fee"]
    assert fee.amount == Decimal(expected)


def test_an_aest_window_differs_from_local_in_daylight_time() -> None:
    """In summer local time is AEST + 30 minutes in SA: a 17:00 local reading is 16:30 on the AEST clock."""
    peak_only_at = cdr.tou_period(
        [
            cdr.tou_rate("PEAK", [cdr.window("17:00", "17:29")], price="1.00"),
            cdr.tou_rate("OFF_PEAK", [cdr.window("17:30", "16:59")], price="0.00"),
        ],
        supply="0",
    )
    frame = usage_frame(date(2025, 12, 1), 1).assign(kwh=0.0)
    frame.loc[frame["local_start"] == datetime(2025, 12, 1, 17, 0), "kwh"] = 1.0
    period = [(date(2025, 12, 1), date(2025, 12, 1))]
    local = engine_bill(tariff_of(cdr.plan(periods=[peak_only_at], time_zone="LOCAL")), frame, period)
    aest = engine_bill(tariff_of(cdr.plan(periods=[peak_only_at], time_zone="AEST")), frame, period)
    assert [line.amount for line in local.lines if line.label == "PEAK"] == [Decimal("1.00")]
    assert [line.amount for line in aest.lines if line.label == "PEAK"] == [Decimal("0.00")]


def test_money_rounds_half_up() -> None:
    assert money(Decimal("0.005")) == Decimal("0.01")
    assert money(Decimal("0.0049")) == Decimal("0.00")
    assert money(Decimal("-0.005")) == Decimal("-0.01")
