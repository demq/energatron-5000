"""`params` v3 (P2 spec §5.1, P3 spec §4.1): the `meter`, `validation` and `analysis` sections, window parsing and the
partition rule, `Params.defaults(profile)` and the migrations. Synthetic zone keys and labels."""

import copy
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from energatron.core.canonical import JSONValue
from energatron.core.errors import InvalidInputError
from energatron.core.models._common import window_minutes
from energatron.core.models.catalog import SourceType
from energatron.core.models.params import REGION_FIELDS, Params
from energatron.core.models.region import RegionProfile
from energatron.core.persisted import registered_migrations

WINDOWS_FIELD = "validation.timebase.windows"
FIRST_SET_FIELD = "analysis.tou.window_sets[0].windows"
TEST_WINDOWS: dict[str, tuple[str, ...]] = {"Alpha": ("08:00-20:00",), "Beta": ("20:00-02:00", "02:00-08:00")}


def profile(*, local_tz: str | None = "Test/Zone", windows: dict[str, tuple[str, ...]] | None = None) -> RegionProfile:
    return RegionProfile(
        name="sa",
        local_tz=local_tz,
        tou_windows=TEST_WINDOWS if windows is None else windows,
        distributor="Test Networks",
        cdr_retailers=("alpha",),
        parsers=(SourceType.SAPN_INTERVAL,),
    )


def doc_v2(**changes: Any) -> dict[str, Any]:
    """A valid v2 document; `changes` replace whole top-level sections."""
    doc: dict[str, Any] = {
        "kind": "params",
        "schema_version": 2,
        "meter": {"timebase": "nem_aest", "local_tz": "Test/Zone"},
        "validation": {
            "meter": {"total_tolerance_kwh": 0.01},
            "timebase": {
                "windows": {"Alpha": ["08:00-20:00"], "Beta": ["20:00-08:00"]},
                "register_tolerance_kwh": 0.05,
                "register_tolerance_pct": 0.1,
                "total_tolerance_kwh": 0.05,
                "min_fit_periods": 3,
            },
        },
    }
    return doc | changes


def with_timebase(**changes: Any) -> dict[str, Any]:
    doc = doc_v2()
    doc["validation"]["timebase"].update(changes)
    return doc


# The model


def test_a_valid_v2_document_validates() -> None:
    params = Params.from_document(doc_v2())
    assert params.meter.timebase == "nem_aest"
    assert params.meter.local_tz == "Test/Zone"
    assert params.validation.meter.total_tolerance_kwh == 0.01
    assert params.validation.timebase.min_fit_periods == 3
    assert params.validation.timebase.windows == {"Alpha": ("08:00-20:00",), "Beta": ("20:00-08:00",)}


def test_the_dump_keeps_the_specs_key_order() -> None:
    dumped = Params.from_document(doc_v2()).model_dump(mode="json")
    assert list(dumped) == [
        "kind",
        "schema_version",
        "meter",
        "validation",
        "analysis",
        "offers",
        "pricing",
        "backtest",
    ]
    assert list(dumped["meter"]) == ["timebase", "local_tz"]
    assert list(dumped["validation"]) == ["meter", "timebase", "backtest"]
    assert list(dumped["validation"]["timebase"]) == [
        "windows",
        "register_tolerance_kwh",
        "register_tolerance_pct",
        "total_tolerance_kwh",
        "min_fit_periods",
    ]


def test_the_zone_and_windows_allow_null() -> None:
    doc = with_timebase(windows=None)
    doc["meter"]["local_tz"] = None
    params = Params.from_document(doc)
    assert params.meter.local_tz is None
    assert params.validation.timebase.windows is None


def test_the_region_fields_are_the_zone_the_two_window_fields_and_the_two_offer_fields() -> None:
    assert REGION_FIELDS == ("meter.local_tz", WINDOWS_FIELD, FIRST_SET_FIELD, "offers.retailers", "offers.distributor")


@pytest.mark.parametrize(
    "mutate",
    [
        lambda doc: doc["meter"].update(timebase="utc"),
        lambda doc: doc["meter"].update(local_tz="not a zone"),
        lambda doc: doc["meter"].update(local_tz="../etc/passwd"),
        lambda doc: doc["meter"].update(extra=1),
        lambda doc: doc["validation"]["meter"].update(total_tolerance_kwh=-0.01),
        lambda doc: doc["validation"]["timebase"].update(register_tolerance_kwh=-1),
        lambda doc: doc["validation"]["timebase"].update(register_tolerance_pct=-0.1),
        lambda doc: doc["validation"]["timebase"].update(total_tolerance_kwh=-0.05),
        lambda doc: doc["validation"]["timebase"].update(min_fit_periods=0),
        lambda doc: doc["validation"]["timebase"].update(min_fit_periods=1.5),
        lambda doc: doc["validation"]["timebase"].pop("min_fit_periods"),
        lambda doc: doc["validation"].pop("meter"),
        lambda doc: doc.pop("meter"),
        lambda doc: doc.update(unknown=1),
    ],
)
def test_invalid_documents_are_invalid_input(mutate: Any) -> None:
    doc = doc_v2()
    mutate(doc)
    with pytest.raises(InvalidInputError):
        Params.from_document(doc)


@pytest.mark.parametrize("timebase", ["nem_aest", "local_wall", "local_standard"])
def test_the_three_time_bases_are_accepted(timebase: str) -> None:
    doc = doc_v2()
    doc["meter"]["timebase"] = timebase
    assert Params.from_document(doc).meter.timebase == timebase


# Window parsing and the partition rule


@pytest.mark.parametrize(
    "windows",
    [
        {"Alpha": ["00:00-12:00", "12:00-00:00"]},  # wraps at the end: the second ends at midnight
        {"Alpha": ["22:00-06:00"], "Beta": ["06:00-22:00"]},  # wraps past midnight
        {"Alpha": ["00:00-00:00"]},  # end <= start with equal edges is the whole day
        {"Alpha": ["06:05-18:35"], "Beta": ["18:35-06:05"]},  # edges that are multiples of 5 but not of 15
    ],
)
def test_valid_windows_that_partition_the_day(windows: dict[str, list[str]]) -> None:
    assert Params.from_document(with_timebase(windows=windows)).validation.timebase.windows is not None


@pytest.mark.parametrize(
    "windows",
    [
        {"Alpha": ["08:00-24:00"], "Beta": ["00:00-08:00"]},  # 24:00 isn't allowed
        {"Alpha": ["24:00-08:00"], "Beta": ["08:00-24:00"]},
        {"Alpha": ["08:03-20:00"], "Beta": ["20:00-08:03"]},  # edge not a multiple of 5
        {"Alpha": ["08:00-20:07"], "Beta": ["20:07-08:00"]},
        {"Alpha": ["8:00-20:00"], "Beta": ["20:00-08:00"]},  # not HH:MM
        {"Alpha": ["08:00 - 20:00"], "Beta": ["20:00-08:00"]},
        {"Alpha": ["08:00"], "Beta": ["20:00-08:00"]},
        {"Alpha": ["25:00-26:00"], "Beta": ["26:00-25:00"]},
        {"Alpha": ["08:60-20:00"], "Beta": ["20:00-08:60"]},
        {"Alpha": ["08:00-20:00"], "Beta": ["20:05-08:00"]},  # a gap of five minutes
        {"Alpha": ["08:00-20:00"], "Beta": ["19:55-08:00"]},  # an overlap of five minutes
        {"Alpha": ["08:00-20:00"]},  # leaves the night uncovered
        {"Alpha": ["08:00-20:00", "10:00-12:00"], "Beta": ["20:00-08:00"]},  # overlap inside one label
        {"Alpha": [], "Beta": ["00:00-00:00"]},  # a label with no window
        {},  # no label
    ],
)
def test_windows_that_are_malformed_or_do_not_partition_the_day(windows: dict[str, list[str]]) -> None:
    with pytest.raises(InvalidInputError) as caught:
        Params.from_document(with_timebase(windows=windows))
    assert caught.value.details["field"] == WINDOWS_FIELD


def test_the_window_error_never_repeats_the_window_text() -> None:
    with pytest.raises(InvalidInputError) as caught:
        Params.from_document(with_timebase(windows={"Alpha": ["08:03-20:00"], "Beta": ["20:00-08:03"]}))
    assert "08:03" not in caught.value.message
    assert "08:03" not in str(caught.value.details)


def test_window_minutes_wrap_past_midnight() -> None:
    assert window_minutes("23:55-00:05") == [1435, 1436, 1437, 1438, 1439, 0, 1, 2, 3, 4]
    assert window_minutes("00:00-00:05") == [0, 1, 2, 3, 4]
    assert len(window_minutes("06:00-06:00")) == 1440


def _partition(cuts: list[int], labels: list[str]) -> dict[str, list[str]]:
    """Windows for the segments between `cuts` (minutes, sorted, distinct), the last wrapping to the first."""
    windows: dict[str, list[str]] = {}
    for index, start in enumerate(cuts):
        end = cuts[(index + 1) % len(cuts)]
        text = f"{start // 60:02d}:{start % 60:02d}-{end // 60:02d}:{end % 60:02d}"
        windows.setdefault(labels[index % len(labels)], []).append(text)
    return windows


@given(
    cuts=st.lists(st.integers(0, 287), min_size=1, max_size=12, unique=True).map(
        lambda values: sorted(value * 5 for value in values)
    ),
    labels=st.lists(st.sampled_from(["Alpha", "Beta", "Gamma", "Delta"]), min_size=1, max_size=12),
)
def test_any_valid_windows_assign_every_minute_of_a_day_to_exactly_one_label(
    cuts: list[int], labels: list[str]
) -> None:
    windows = _partition(cuts, labels)
    params = Params.from_document(with_timebase(windows=windows))
    parsed = params.validation.timebase.windows
    assert parsed is not None
    covered = sorted(minute for texts in parsed.values() for text in texts for minute in window_minutes(text))
    assert covered == list(range(1440))


@given(
    cuts=st.lists(st.integers(0, 287), min_size=2, max_size=12, unique=True).map(
        lambda values: sorted(value * 5 for value in values)
    ),
)
def test_dropping_any_one_window_of_a_partition_is_rejected(cuts: list[int]) -> None:
    windows = _partition(cuts, ["Alpha", "Beta"])
    windows["Alpha"] = windows["Alpha"][1:]  # the first segment is now uncovered
    if not windows["Alpha"]:
        del windows["Alpha"]
    with pytest.raises(InvalidInputError):
        Params.from_document(with_timebase(windows=windows))


# Defaults and migration


def test_defaults_take_the_zone_and_windows_from_the_profile_they_are_given() -> None:
    params = Params.defaults(profile(local_tz="Made/Up"))
    assert params.meter.timebase == "nem_aest"
    assert params.meter.local_tz == "Made/Up"
    assert params.validation.timebase.windows == TEST_WINDOWS
    assert params.validation.meter.total_tolerance_kwh == 0.01
    assert params.validation.timebase.register_tolerance_kwh == 0.05
    assert params.validation.timebase.register_tolerance_pct == 0.1
    assert params.validation.timebase.total_tolerance_kwh == 0.05
    assert params.validation.timebase.min_fit_periods == 3
    assert params.schema_version == 4
    assert params.analysis.tou.window_sets[0].windows == TEST_WINDOWS
    assert params.analysis.tou.window_sets[0].effective_from is None


def test_defaults_carry_a_null_profile_value_through() -> None:
    other = RegionProfile(name="sa", local_tz=None, tou_windows=None, distributor=None, cdr_retailers=None, parsers=())
    params = Params.defaults(other)
    assert params.meter.local_tz is None
    assert params.validation.timebase.windows is None
    assert params.analysis.tou.window_sets[0].windows is None


def test_defaults_round_trip_through_a_document() -> None:
    params = Params.defaults(profile())
    assert Params.from_document(params.model_dump(mode="json")) == params


def test_the_v1_to_v2_migration_inserts_the_defaults_and_null_zone_and_windows() -> None:
    v1: dict[str, JSONValue] = {"kind": "params", "schema_version": 1}
    frozen = copy.deepcopy(v1)
    assert registered_migrations()[("params", 1)](v1) == {
        "kind": "params",
        "schema_version": 2,
        "meter": {"timebase": "nem_aest", "local_tz": None},
        "validation": {
            "meter": {"total_tolerance_kwh": 0.01},
            "timebase": {
                "windows": None,
                "register_tolerance_kwh": 0.05,
                "register_tolerance_pct": 0.1,
                "total_tolerance_kwh": 0.05,
                "min_fit_periods": 3,
            },
        },
    }
    assert v1 == frozen  # the input is untouched


def test_a_v1_document_reads_as_v4_with_null_zone_and_windows() -> None:
    params = Params.from_document({"kind": "params", "schema_version": 1})
    assert params.schema_version == 4
    assert params.meter.local_tz is None
    assert params.validation.timebase.windows is None
    assert params.analysis.tou.window_sets[0].windows is None


def test_a_v1_document_with_an_unknown_key_is_still_invalid() -> None:
    with pytest.raises(InvalidInputError):
        Params.from_document({"kind": "params", "schema_version": 1, "unknown": 1})


# `analysis` (P3 spec §4.1)


def doc_v3(**changes: Any) -> dict[str, Any]:
    """A valid v3 document; `changes` replace whole top-level sections."""
    doc = doc_v2() | {"schema_version": 3}
    doc["analysis"] = {
        "reference_months": 12,
        "coverage_threshold": 0.95,
        "seasons": {"DJF": [12, 1, 2], "MAM": [3, 4, 5], "JJA": [6, 7, 8], "SON": [9, 10, 11]},
        "weekend": ["saturday", "sunday"],
        "band_percentiles": [10, 50, 90],
        "base_load": {"from": "01:00", "to": "05:00", "percentile": 10},
        "demand_minutes": 30,
        "tou": {
            "window_sets": [{"effective_from": None, "windows": {"Alpha": ["08:00-20:00"], "Beta": ["20:00-08:00"]}}]
        },
    }
    return doc | changes


def with_analysis(**changes: Any) -> dict[str, Any]:
    doc = doc_v3()
    doc["analysis"].update(changes)
    return doc


def with_sets(*sets: dict[str, Any]) -> dict[str, Any]:
    return with_analysis(tou={"window_sets": list(sets)})


FIRST = {"effective_from": None, "windows": {"Alpha": ["08:00-20:00"], "Beta": ["20:00-08:00"]}}
SECOND = {"effective_from": "2025-07-01", "windows": {"Alpha": ["07:00-21:00"], "Beta": ["21:00-07:00"]}}


def test_a_valid_v3_document_validates() -> None:
    analysis = Params.from_document(doc_v3()).analysis
    assert analysis.reference_months == 12
    assert analysis.coverage_threshold == 0.95
    assert analysis.seasons["DJF"] == (12, 1, 2)
    assert analysis.weekend == ("saturday", "sunday")
    assert analysis.band_percentiles == (10, 50, 90)
    assert (analysis.base_load.start, analysis.base_load.end, analysis.base_load.percentile) == ("01:00", "05:00", 10)
    assert analysis.demand_minutes == 30
    assert analysis.tou.window_sets[0].windows == {"Alpha": ("08:00-20:00",), "Beta": ("20:00-08:00",)}


def test_the_v3_dump_uses_the_documents_keys_and_order() -> None:
    dumped = Params.from_document(doc_v3()).model_dump(mode="json")
    assert list(dumped["analysis"]) == [
        "reference_months",
        "coverage_threshold",
        "seasons",
        "weekend",
        "band_percentiles",
        "base_load",
        "demand_minutes",
        "tou",
    ]
    assert dumped["analysis"]["base_load"] == {"from": "01:00", "to": "05:00", "percentile": 10}
    v3 = doc_v3()  # a valid document's v3 values survive a load (which upgrades it) and a dump unchanged
    assert {
        key: value for key, value in dumped.items() if key in v3 and key not in ("schema_version", "validation")
    } == {key: value for key, value in v3.items() if key not in ("schema_version", "validation")}
    assert {key: value for key, value in dumped["validation"].items() if key != "backtest"} == v3["validation"]


def test_the_first_set_windows_allow_null() -> None:
    doc = with_sets({"effective_from": None, "windows": None})
    assert Params.from_document(doc).analysis.tou.window_sets[0].windows is None


@pytest.mark.parametrize(
    "mutate",
    [
        lambda a: a.update(reference_months=0),
        lambda a: a.update(reference_months=1.5),
        lambda a: a.update(coverage_threshold=0),
        lambda a: a.update(coverage_threshold=1.01),
        lambda a: a.update(weekend=[]),
        lambda a: a.update(weekend=["saturday", "sunday", "monday", "tuesday", "wednesday", "thursday", "friday"]),
        lambda a: a.update(weekend=["saturday", "saturday"]),
        lambda a: a.update(weekend=["Saturday"]),
        lambda a: a.update(weekend=["funday"]),
        lambda a: a.update(band_percentiles=[10, 50]),
        lambda a: a.update(band_percentiles=[50, 50, 90]),
        lambda a: a.update(band_percentiles=[90, 50, 10]),
        lambda a: a.update(band_percentiles=[-1, 50, 90]),
        lambda a: a.update(band_percentiles=[10, 50, 101]),
        lambda a: a["base_load"].update(percentile=101),
        lambda a: a["base_load"].update(percentile=-1),
        lambda a: a["base_load"].update({"from": "01:03"}),
        lambda a: a["base_load"].update(to="24:00"),
        lambda a: a["base_load"].update({"from": "1:00"}),
        lambda a: a["base_load"].pop("to"),
        lambda a: a.update(demand_minutes=0),
        lambda a: a.update(demand_minutes=25),
        lambda a: a.update(demand_minutes=7),
        lambda a: a.update(demand_minutes=120),
        lambda a: a.update(extra=1),
        lambda a: a.pop("tou"),
        lambda a: a["tou"]["window_sets"].clear(),
    ],
)
def test_invalid_analysis_documents_are_invalid_input(mutate: Any) -> None:
    doc = doc_v3()
    mutate(doc["analysis"])
    with pytest.raises(InvalidInputError):
        Params.from_document(doc)


def test_the_base_load_window_may_wrap_past_midnight_and_need_not_partition_the_day() -> None:
    doc = with_analysis(base_load={"from": "23:00", "to": "02:00", "percentile": 5})
    assert Params.from_document(doc).analysis.base_load.start == "23:00"


@pytest.mark.parametrize(
    "seasons",
    [
        {"DJF": [12, 1, 2], "MAM": [3, 4, 5], "JJA": [6, 7, 8]},  # September to November left out
        {"DJF": [12, 1, 2], "MAM": [3, 4, 5], "JJA": [6, 7, 8], "SON": [8, 9, 10, 11]},  # August twice
        {"DJF": [12, 1, 2], "MAM": [3, 4, 5], "JJA": [6, 7, 8], "SON": [9, 10, 11, 13]},  # month 13
        {"DJF": [12, 1, 2], "MAM": [3, 4, 5], "JJA": [6, 7, 8], "SON": [9, 10, 11, 0]},  # month 0
        {"DJF": [12, 1, 2, 2], "MAM": [3, 4, 5], "JJA": [6, 7, 8], "SON": [9, 10, 11]},  # twice in one season
        {"DJF": [12, 1, 2], "MAM": [3, 4, 5], "JJA": [6, 7, 8], "SON": [9, 10, 11], "XXX": []},  # an empty season
        {"": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]},  # an empty label
        {},
    ],
)
def test_seasons_that_do_not_partition_the_months_name_their_field(seasons: dict[str, list[int]]) -> None:
    with pytest.raises(InvalidInputError) as caught:
        Params.from_document(with_analysis(seasons=seasons))
    assert caught.value.details["field"] == "analysis.seasons"


@pytest.mark.parametrize(
    "seasons",
    [
        {"DJF": [12, 1, 2], "MAM": [3, 4, 5], "JJA": [6, 7, 8], "SON": [9, 10, 11]},  # DJF wraps the year
        {"Warm": [10, 11, 12, 1, 2, 3], "Cool": [4, 5, 6, 7, 8, 9]},
        {"All": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]},
    ],
)
def test_seasons_that_partition_the_months_are_accepted(seasons: dict[str, list[int]]) -> None:
    assert Params.from_document(with_analysis(seasons=seasons)).analysis.seasons.keys() == seasons.keys()


@pytest.mark.parametrize("minutes", [5, 10, 15, 20, 30, 60])
def test_the_demand_block_may_be_any_multiple_of_five_that_divides_an_hour(minutes: int) -> None:
    assert Params.from_document(with_analysis(demand_minutes=minutes)).analysis.demand_minutes == minutes


# Window sets (P3 spec §4.1, §6)


def test_one_set_and_two_sets_are_accepted() -> None:
    one = Params.from_document(with_sets(FIRST)).analysis.tou.window_sets
    two = Params.from_document(with_sets(FIRST, SECOND)).analysis.tou.window_sets
    assert [found.effective_from for found in one] == [None]
    assert [str(found.effective_from) if found.effective_from else None for found in two] == [None, "2025-07-01"]


def test_a_date_written_unquoted_in_yaml_is_accepted() -> None:
    import datetime

    doc = with_sets(FIRST, SECOND | {"effective_from": datetime.date(2025, 7, 1)})
    assert Params.from_document(doc).analysis.tou.window_sets[1].effective_from == datetime.date(2025, 7, 1)
    assert Params.from_document(doc).model_dump(mode="json")["analysis"]["tou"]["window_sets"][1] == SECOND


def test_three_sets_with_increasing_dates_are_accepted() -> None:
    third = SECOND | {"effective_from": "2026-01-01"}
    assert len(Params.from_document(with_sets(FIRST, SECOND, third)).analysis.tou.window_sets) == 3


@pytest.mark.parametrize(
    ("sets", "field"),
    [
        ([FIRST | {"effective_from": "2024-01-01"}], "analysis.tou.window_sets[0].effective_from"),  # first has a start
        ([FIRST, SECOND | {"effective_from": None}], "analysis.tou.window_sets[1].effective_from"),
        ([FIRST, SECOND, SECOND], "analysis.tou.window_sets[2].effective_from"),  # equal dates
        (
            [FIRST, SECOND, SECOND | {"effective_from": "2025-01-01"}],
            "analysis.tou.window_sets[2].effective_from",  # decreasing
        ),
        ([FIRST, SECOND | {"windows": {"Alpha": ["00:00-00:00"]}}], "analysis.tou.window_sets[1].windows"),  # labels
        (
            [FIRST, SECOND | {"windows": {"Alpha": ["07:00-21:00"], "Gamma": ["21:00-07:00"]}}],
            "analysis.tou.window_sets[1].windows",
        ),
        (
            [FIRST, SECOND | {"windows": {"Alpha": ["07:00-21:00"], "Beta": ["21:05-07:00"]}}],  # a gap
            "analysis.tou.window_sets[1].windows",
        ),
        (
            [FIRST | {"windows": {"Alpha": ["08:00-20:00"]}}],  # the first set leaves the night uncovered
            "analysis.tou.window_sets[0].windows",
        ),
        ([FIRST, SECOND | {"windows": None}], "analysis.tou.window_sets[1].windows"),  # only the first may be null
    ],
)
def test_window_sets_that_break_a_rule_name_the_entry(sets: list[dict[str, Any]], field: str) -> None:
    with pytest.raises(InvalidInputError) as caught:
        Params.from_document(with_sets(*sets))
    assert caught.value.details["field"] == field


def test_a_null_first_set_does_not_hide_a_label_mismatch_among_the_others() -> None:
    third = SECOND | {"effective_from": "2026-01-01", "windows": {"Alpha": ["00:00-12:00"], "Gamma": ["12:00-00:00"]}}
    with pytest.raises(InvalidInputError) as caught:
        Params.from_document(with_sets(FIRST | {"windows": None}, SECOND, third))
    assert caught.value.details["field"] == "analysis.tou.window_sets[2].windows"


def test_the_window_set_error_never_repeats_the_window_text() -> None:
    with pytest.raises(InvalidInputError) as caught:
        Params.from_document(with_sets(FIRST | {"windows": {"Alpha": ["08:03-20:00"], "Beta": ["20:00-08:03"]}}))
    assert "08:03" not in caught.value.message
    assert "08:03" not in str(caught.value.details)


# The v2 to v3 migration


def test_the_v2_to_v3_migration_inserts_the_analysis_defaults_with_a_null_first_set() -> None:
    v2 = doc_v2()
    frozen = copy.deepcopy(v2)
    upgraded = registered_migrations()[("params", 2)](v2)
    assert upgraded == doc_v3(analysis={**doc_v3()["analysis"], "tou": {"window_sets": [FIRST | {"windows": None}]}})
    assert v2 == frozen  # the input is untouched


def test_the_migration_keeps_every_v2_value() -> None:
    doc = doc_v2()
    doc["meter"] = {"timebase": "local_wall", "local_tz": "Other/Zone"}
    doc["validation"]["meter"]["total_tolerance_kwh"] = 0.5
    upgraded = Params.from_document(doc)
    assert upgraded.meter.timebase == "local_wall"
    assert upgraded.meter.local_tz == "Other/Zone"
    assert upgraded.validation.meter.total_tolerance_kwh == 0.5
    assert upgraded.validation.timebase.windows == {"Alpha": ("08:00-20:00",), "Beta": ("20:00-08:00",)}
    assert upgraded.analysis.tou.window_sets[0].windows is None  # the validation windows are not copied


def test_a_v2_document_with_an_unknown_key_is_still_invalid() -> None:
    with pytest.raises(InvalidInputError):
        Params.from_document(doc_v2(unknown=1))


def test_a_v3_document_without_analysis_is_invalid() -> None:
    doc = doc_v3()
    del doc["analysis"]
    with pytest.raises(InvalidInputError):
        Params.from_document(doc)


def test_the_analysis_defaults_hold_no_zone_and_the_defaults_function_matches_the_migration() -> None:
    migrated = Params.from_document(doc_v2())
    defaults = Params.defaults(profile())
    assert migrated.analysis.model_copy(update={"tou": defaults.analysis.tou}) == defaults.analysis
