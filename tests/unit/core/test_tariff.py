"""`core/tariff.py` (P4 spec §5.1): windows, blocks, seasons, the partition rules and the GST convention."""

import itertools
from decimal import Decimal
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from energatron.core import tariff
from energatron.core.errors import InvalidInputError
from energatron.core.tariff import (
    ALL_WEEK,
    DAYS,
    Block,
    EnergyCharge,
    Season,
    Tariff,
    Window,
    inclusive_end_to_half_open,
    month_days,
    seasons_partition_the_year,
    windows_partition_the_week,
)

ONE_BLOCK = (Block(volume_kwh=None, unit_price=Decimal("0.30")),)


def charge(label: str, *windows: Window, blocks: tuple[Block, ...] = ONE_BLOCK) -> EnergyCharge:
    return EnergyCharge(label=label, windows=windows, block_period="P1D", blocks=blocks)


def season(start: str = "01-01", end: str = "12-31", energy: tuple[EnergyCharge, ...] | None = None) -> Season:
    return Season(
        start=start,
        end=end,
        clock="LOCAL",
        daily_supply=Decimal("1.00"),
        energy=energy or (charge("ALL", ALL_WEEK),),
    )


def tariff_of(*seasons: Season, **changes: Any) -> Tariff:
    values: dict[str, Any] = {
        "offer_snapshot_id": "TEST1@EME~20261001T000000Z~0123abcd",
        "plan_id": "TEST1@EME",
        "brand": "alpha",
        "retailer": "alpha",
        "display_name": "Alpha Test",
        "source": "cdr",
        "customer_type": "RESIDENTIAL",
        "distributors": ("Test Networks",),
        "pricing_model": "SINGLE_RATE",
        "effective_from": None,
        "effective_to": None,
        "seasons": seasons or (season(),),
        "status": "normalised",
    }
    return Tariff(**(values | changes))


def test_the_gst_convention_is_two_named_constants() -> None:
    """P4 spec §5.1: CDR rates exclude GST; fees and fixed credits are taken as published (with GST)."""
    assert tariff.CDR_RATES_INCLUDE_GST is False
    assert tariff.CDR_FEES_INCLUDE_GST is True


def test_a_cdr_end_time_is_an_inclusive_minute() -> None:
    assert inclusive_end_to_half_open(22 * 60 + 59) == 23 * 60
    assert inclusive_end_to_half_open(23 * 60 + 59) == 1440
    with pytest.raises(ValueError):
        inclusive_end_to_half_open(1440)


def test_a_window_covers_its_half_open_range_on_its_days() -> None:
    evening = Window(days=("MON", "TUE"), start_minute=960, end_minute=1380)
    assert evening.covers("MON", 960)
    assert evening.covers("TUE", 1379)
    assert not evening.covers("MON", 1380)
    assert not evening.covers("WED", 1000)


def test_a_window_that_ends_before_it_starts_wraps_on_the_same_day() -> None:
    night = Window(days=("MON",), start_minute=1380, end_minute=420)
    assert night.covers("MON", 1439)
    assert night.covers("MON", 0)
    assert night.covers("MON", 419)
    assert not night.covers("MON", 420)
    assert not night.covers("TUE", 0)


def test_window_days_are_sorted_and_must_be_unique_and_present() -> None:
    assert Window(days=("SUN", "MON"), start_minute=0, end_minute=1440).days == ("MON", "SUN")
    for days in [(), ("MON", "MON")]:
        with pytest.raises(ValidationError):
            Window(days=days, start_minute=0, end_minute=1440)  # pyright: ignore[reportArgumentType]


def test_only_the_last_block_is_open() -> None:
    charge("ALL", ALL_WEEK, blocks=(Block(volume_kwh=Decimal(10), unit_price=Decimal("0.2")), *ONE_BLOCK))
    for blocks in [
        (Block(volume_kwh=Decimal(10), unit_price=Decimal("0.2")),),
        (*ONE_BLOCK, *ONE_BLOCK),
    ]:
        with pytest.raises((InvalidInputError, ValidationError)):
            charge("ALL", ALL_WEEK, blocks=blocks)


def test_a_block_volume_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        Block(volume_kwh=Decimal(0), unit_price=Decimal("0.2"))


def test_a_season_needs_windows_that_partition_the_week() -> None:
    day = Window(days=DAYS, start_minute=420, end_minute=1380)
    night = Window(days=DAYS, start_minute=1380, end_minute=420)
    season(energy=(charge("PEAK", day), charge("OFF_PEAK", night)))
    with pytest.raises(InvalidInputError):
        season(energy=(charge("PEAK", day),))  # the night is uncovered
    with pytest.raises(InvalidInputError):
        season(energy=(charge("PEAK", day), charge("OFF_PEAK", night), charge("SHOULDER", day)))  # covered twice


def test_weekday_and_weekend_windows_partition_together() -> None:
    weekdays = Window(days=("MON", "TUE", "WED", "THU", "FRI"), start_minute=0, end_minute=1440)
    weekend = Window(days=("SAT", "SUN"), start_minute=0, end_minute=1440)
    assert windows_partition_the_week([[weekdays], [weekend]])
    assert not windows_partition_the_week([[weekdays]])


def test_month_days_are_the_366_days_of_a_leap_year() -> None:
    days = month_days()
    assert len(days) == 366
    assert days[0] == "01-01"
    assert "02-29" in days
    assert days[-1] == "12-31"


def test_seasons_may_wrap_the_year_end() -> None:
    assert seasons_partition_the_year([("07-01", "06-30")])
    assert seasons_partition_the_year([("10-01", "05-31"), ("06-01", "09-30")])
    assert seasons_partition_the_year([("01-01", "12-31")])
    assert not seasons_partition_the_year([("01-01", "06-30")])  # half the year
    assert not seasons_partition_the_year([("01-01", "12-31"), ("06-01", "06-30")])  # June twice
    assert not seasons_partition_the_year([("02-30", "12-31")])  # not a day


def test_a_season_lists_its_days_in_order_across_the_year_end() -> None:
    days = season("12-30", "01-02").month_days()
    assert days == ["12-30", "12-31", "01-01", "01-02"]


def test_a_tariff_needs_seasons_that_partition_the_year() -> None:
    tariff_of(season("07-01", "06-30"))
    with pytest.raises(InvalidInputError):
        tariff_of(season("01-01", "06-30"))


def test_a_tariff_is_approximate_exactly_when_it_has_approximations() -> None:
    tariff_of(status="approximate", approximations=("demand_day_count",))
    for changes in [{"status": "approximate"}, {"status": "normalised", "approximations": ("x",)}]:
        with pytest.raises(InvalidInputError):
            tariff_of(**changes)


def test_money_is_decimal_and_survives_a_json_round_trip() -> None:
    found = tariff_of()
    dumped = found.model_dump(mode="json")
    assert dumped["seasons"][0]["daily_supply"] == "1.00"
    assert Tariff.model_validate(dumped) == found


@given(st.lists(st.integers(min_value=1, max_value=1439), min_size=1, max_size=6, unique=True))
def test_cutting_a_day_into_windows_always_partitions_it(cuts: list[int]) -> None:
    edges = [0, *sorted(cuts), 1440]
    windows = [[Window(days=DAYS, start_minute=a, end_minute=b)] for a, b in itertools.pairwise(edges)]
    assert windows_partition_the_week(windows)
    assert not windows_partition_the_week(windows[1:])
