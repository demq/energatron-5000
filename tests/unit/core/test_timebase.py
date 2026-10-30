"""`core/timebase.py` (P2 spec §9.1, §13.1): the three time-basis hypotheses, window parsing and bucketing.

The zone is South Australia's, from tzdata, and the windows are the fixture's made-up ones. Expected local times come
from the independent oracle (`tests/fixtures/oracle.py`), never from `core.timebase` itself.
"""

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import pytest
from hypothesis import given
from hypothesis import strategies as st

from energatron.core.errors import InvalidInputError
from energatron.core.timebase import NEM_UTC_OFFSET, Timebase, Window, bucket, local_clock, parse_windows, to_local
from tests.fixtures import oracle
from tests.fixtures.pipeline_workspace import WINDOWS

ZONE = ZoneInfo("Australia/Adelaide")
APRIL, OCTOBER = oracle.sa_dst_dates(2025)  # daylight saving ends on the first, starts on the second
WINTER = date(2025, 7, 15)
SUMMER = date(2025, 1, 15)


def series(*moments: datetime) -> pd.Series:
    return pd.Series(pd.to_datetime(list(moments)).astype("datetime64[us]"))


def at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute)


def local(moment: datetime, basis: Timebase) -> datetime:
    found = to_local(series(moment), basis, ZONE).local_start[0]
    assert not pd.isna(found)
    return found.to_pydatetime()


def test_the_nem_offset_is_ten_hours() -> None:
    assert timedelta(hours=10) == NEM_UTC_OFFSET


def test_the_basis_values_are_the_names_params_uses() -> None:
    assert [basis.value for basis in Timebase] == ["nem_aest", "local_wall", "local_standard"]


@pytest.mark.parametrize(
    ("basis", "expected"),
    [
        (Timebase.NEM_AEST, at(WINTER, 9, 30)),
        (Timebase.LOCAL_WALL, at(WINTER, 10)),
        (Timebase.LOCAL_STANDARD, at(WINTER, 10)),
    ],
)
def test_a_winter_meter_ten_o_clock(basis: Timebase, expected: datetime) -> None:
    assert local(at(WINTER, 10), basis) == expected


@pytest.mark.parametrize(
    ("basis", "expected"),
    [
        (Timebase.NEM_AEST, at(SUMMER, 10, 30)),
        (Timebase.LOCAL_WALL, at(SUMMER, 10)),
        (Timebase.LOCAL_STANDARD, at(SUMMER, 11)),
    ],
)
def test_a_summer_meter_ten_o_clock(basis: Timebase, expected: datetime) -> None:
    assert local(at(SUMMER, 10), basis) == expected


def test_utc_start_is_aware_utc_and_the_offset_is_the_zones() -> None:
    moment = at(SUMMER, 10)
    found = to_local(series(moment), Timebase.NEM_AEST, ZONE)
    assert str(found.utc_start.dtype) == "datetime64[us, UTC]"
    assert found.utc_start[0].to_pydatetime() == (moment - NEM_UTC_OFFSET).replace(tzinfo=UTC)
    assert found.utc_offset_minutes[0] == 630  # UTC+10:30 in daylight time
    assert str(found.utc_offset_minutes.dtype) == "Int16"
    assert not found.unplaceable[0]
    winter = to_local(series(at(WINTER, 10)), Timebase.NEM_AEST, ZONE)
    assert winter.utc_offset_minutes[0] == 570  # UTC+9:30


def test_the_local_standard_offset_is_the_standard_one_all_year() -> None:
    for day in (WINTER, SUMMER):
        moment = at(day, 12)
        found = to_local(series(moment), Timebase.LOCAL_STANDARD, ZONE)
        assert found.utc_start[0].to_pydatetime() == (moment - timedelta(hours=9, minutes=30)).replace(tzinfo=UTC)


def test_local_standard_takes_the_standard_offset_of_each_meter_date() -> None:
    both = to_local(series(at(WINTER, 0), at(SUMMER, 0)), Timebase.LOCAL_STANDARD, ZONE)
    assert [value.to_pydatetime() for value in both.utc_start] == [
        (at(WINTER, 0) - timedelta(hours=9, minutes=30)).replace(tzinfo=UTC),
        (at(SUMMER, 0) - timedelta(hours=9, minutes=30)).replace(tzinfo=UTC),
    ]


def test_local_wall_has_no_instant_for_the_spring_forward_hour() -> None:
    skipped = series(at(OCTOBER, 1, 55), at(OCTOBER, 2), at(OCTOBER, 2, 30), at(OCTOBER, 2, 55), at(OCTOBER, 3))
    found = to_local(skipped, Timebase.LOCAL_WALL, ZONE)
    assert list(found.unplaceable) == [False, True, True, True, False]
    assert found.utc_start.isna().tolist() == [False, True, True, True, False]
    assert found.local_start.isna().tolist() == [False, True, True, True, False]
    assert found.utc_offset_minutes.isna().tolist() == [False, True, True, True, False]


def test_local_wall_has_two_instants_for_the_fall_back_hour() -> None:
    repeated = series(at(APRIL, 1, 55), at(APRIL, 2), at(APRIL, 2, 30), at(APRIL, 2, 55), at(APRIL, 3))
    found = to_local(repeated, Timebase.LOCAL_WALL, ZONE)
    assert list(found.unplaceable) == [False, True, True, True, False]


def test_local_wall_places_an_ordinary_time_at_its_wall_offset() -> None:
    found = to_local(series(at(SUMMER, 10), at(WINTER, 10)), Timebase.LOCAL_WALL, ZONE)
    assert list(found.utc_offset_minutes) == [630, 570]
    assert found.local_start[0].to_pydatetime() == at(SUMMER, 10)


def test_the_fixed_offset_bases_place_the_dst_hours() -> None:
    for basis in (Timebase.NEM_AEST, Timebase.LOCAL_STANDARD):
        found = to_local(series(*(at(OCTOBER, hour) for hour in range(0, 6))), basis, ZONE)
        assert not found.unplaceable.any()
        assert found.utc_start.notna().all()


def test_local_clock_is_the_wall_time_even_on_a_dst_day() -> None:
    ambiguous = series(at(APRIL, 2, 30), at(OCTOBER, 2, 30))
    assert local_clock(ambiguous, Timebase.LOCAL_WALL, ZONE).tolist() == ambiguous.tolist()
    shifted = local_clock(ambiguous, Timebase.NEM_AEST, ZONE)
    assert shifted.notna().all()


def test_an_empty_series_converts_to_empty_results() -> None:
    empty = pd.Series(pd.to_datetime([]).astype("datetime64[us]"))
    for basis in Timebase:
        found = to_local(empty, basis, ZONE)
        assert len(found.local_start) == len(found.utc_start) == len(found.unplaceable) == 0
        assert len(local_clock(empty, basis, ZONE)) == 0


def test_the_results_keep_the_input_index() -> None:
    moments = pd.Series(pd.to_datetime([at(WINTER, 10), at(WINTER, 11)]).astype("datetime64[us]"), index=[7, 3])
    for basis in Timebase:
        found = to_local(moments, basis, ZONE)
        assert list(found.local_start.index) == [7, 3]
        assert list(found.unplaceable.index) == [7, 3]


# --- windows -----------------------------------------------------------------------------------------------------


def test_parse_windows_reads_start_end_and_wrapping() -> None:
    parsed = parse_windows(WINDOWS)
    assert parsed["Off Peak"] == (Window(start_minute=60, end_minute=360),)
    assert parsed["Shoulder"] == (Window(start_minute=600, end_minute=900),)
    assert parsed["Peak"] == (Window(start_minute=360, end_minute=600), Window(start_minute=900, end_minute=60))


def test_parse_windows_refuses_a_gap_an_overlap_and_a_bad_window() -> None:
    bad = [
        {"A": ["00:00-10:00"], "B": ["11:00-00:00"]},  # a gap
        {"A": ["00:00-10:00"], "B": ["09:00-00:00"]},  # an overlap
        {"A": ["00:00-24:00"]},
        {"A": ["00:00-10:07"], "B": ["10:07-00:00"]},  # not a multiple of 5
        {},
    ]
    for windows in bad:
        with pytest.raises(InvalidInputError) as caught:
            parse_windows(windows)
        assert caught.value.details["field"] == "validation.timebase.windows"


EDGES = [
    (at(WINTER, 0, 55), "Peak"),
    (at(WINTER, 1, 0), "Off Peak"),
    (at(WINTER, 5, 55), "Off Peak"),
    (at(WINTER, 6, 0), "Peak"),
    (at(WINTER, 9, 55), "Peak"),
    (at(WINTER, 10, 0), "Shoulder"),
    (at(WINTER, 14, 55), "Shoulder"),
    (at(WINTER, 15, 0), "Peak"),
    (at(WINTER, 23, 55), "Peak"),
    (at(WINTER, 0, 0), "Peak"),
]


def test_bucket_at_every_window_edge() -> None:
    labels = bucket(series(*(moment for moment, _ in EDGES)), parse_windows(WINDOWS))
    assert labels.tolist() == [label for _, label in EDGES]


def test_bucket_leaves_an_unplaceable_time_without_a_label() -> None:
    labels = bucket(pd.Series([pd.Timestamp(at(WINTER, 10)), pd.NaT], dtype="datetime64[us]"), parse_windows(WINDOWS))
    assert labels.tolist()[0] == "Shoulder"
    assert labels.isna().tolist() == [False, True]


def test_bucket_of_nothing_is_nothing() -> None:
    assert len(bucket(pd.Series(pd.to_datetime([]).astype("datetime64[us]")), parse_windows(WINDOWS))) == 0


# --- properties --------------------------------------------------------------------------------------------------

FIVE_MINUTES = st.integers(min_value=0, max_value=(date(2025, 12, 31) - date(2025, 1, 1)).days * 288 + 287)


@given(steps=st.lists(FIVE_MINUTES, min_size=1, max_size=40))
def test_the_fixed_offset_bases_agree_with_the_oracle_and_keep_every_row(steps: list[int]) -> None:
    moments = [datetime(2025, 1, 1) + timedelta(minutes=5 * step) for step in steps]
    for basis in (Timebase.NEM_AEST, Timebase.LOCAL_STANDARD):
        found = to_local(series(*moments), basis, ZONE)
        assert len(found.local_start) == len(moments)
        assert not found.unplaceable.any()
        assert [value.to_pydatetime() for value in found.local_start] == [
            oracle.local_time(moment, basis.value) for moment in moments
        ]


@given(steps=st.lists(FIVE_MINUTES, min_size=1, max_size=40))
def test_the_wall_clock_basis_is_unplaceable_exactly_in_the_two_dst_hours(steps: list[int]) -> None:
    moments = [datetime(2025, 1, 1) + timedelta(minutes=5 * step) for step in steps]
    found = to_local(series(*moments), Timebase.LOCAL_WALL, ZONE)
    in_hour = [
        moment.date() in (APRIL, OCTOBER) and moment.hour == 2 for moment in moments
    ]  # both changes are at 02:00 standard time
    assert list(found.unplaceable) == in_hour
