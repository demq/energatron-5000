"""`params` v2 (P2 spec §5.1): the `meter` and `validation` sections, window parsing and the partition rule,
`Params.defaults(profile)` and the v1 to v2 migration. Synthetic zone keys and labels."""

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
from energatron.core.persisted import upgrade

WINDOWS_FIELD = "validation.timebase.windows"
TEST_WINDOWS: dict[str, tuple[str, ...]] = {"Alpha": ("08:00-20:00",), "Beta": ("20:00-02:00", "02:00-08:00")}


def profile(*, local_tz: str | None = "Test/Zone", windows: dict[str, tuple[str, ...]] | None = None) -> RegionProfile:
    return RegionProfile(
        name="sa",
        local_tz=local_tz,
        tou_windows=TEST_WINDOWS if windows is None else windows,
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
    assert list(dumped) == ["kind", "schema_version", "meter", "validation"]
    assert list(dumped["meter"]) == ["timebase", "local_tz"]
    assert list(dumped["validation"]) == ["meter", "timebase"]
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


def test_the_region_fields_are_the_zone_and_the_windows() -> None:
    assert REGION_FIELDS == ("meter.local_tz", WINDOWS_FIELD)


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
    assert params.schema_version == 2


def test_defaults_carry_a_null_profile_value_through() -> None:
    other = RegionProfile(name="sa", local_tz=None, tou_windows=None, parsers=())
    params = Params.defaults(other)
    assert params.meter.local_tz is None
    assert params.validation.timebase.windows is None


def test_defaults_round_trip_through_a_document() -> None:
    params = Params.defaults(profile())
    assert Params.from_document(params.model_dump(mode="json")) == params


def test_the_v1_to_v2_migration_inserts_the_defaults_and_null_zone_and_windows() -> None:
    v1: dict[str, JSONValue] = {"kind": "params", "schema_version": 1}
    frozen = copy.deepcopy(v1)
    assert upgrade(v1) == {
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


def test_a_v1_document_reads_as_v2_with_null_zone_and_windows() -> None:
    params = Params.from_document({"kind": "params", "schema_version": 1})
    assert params.schema_version == 2
    assert params.meter.local_tz is None
    assert params.validation.timebase.windows is None


def test_a_v1_document_with_an_unknown_key_is_still_invalid() -> None:
    with pytest.raises(InvalidInputError):
        Params.from_document({"kind": "params", "schema_version": 1, "unknown": 1})
