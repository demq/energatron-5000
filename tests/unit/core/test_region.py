"""`region_profile` v2 (P2 spec §5.2): the zone and TOU windows, the v1 migration and the shipped `sa.yaml`."""

import importlib.resources
import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from energatron.core.errors import InvalidInputError
from energatron.core.models._common import window_minutes
from energatron.core.models.catalog import SourceType
from energatron.core.models.region import RegionProfile
from energatron.core.persisted import upgrade

GOLDEN_V1 = Path(__file__).resolve().parents[2] / "fixtures" / "schemas" / "region_profile" / "v1.json"
WINDOWS: dict[str, list[str]] = {"Alpha": ["08:00-20:00"], "Beta": ["20:00-08:00"]}


def doc_v2(**changes: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "kind": "region_profile",
        "schema_version": 2,
        "name": "sa",
        "local_tz": "Test/Zone",
        "tou_windows": WINDOWS,
        "parsers": ["sapn_interval"],
    }
    return doc | changes


def test_a_valid_v2_profile_validates() -> None:
    profile = RegionProfile.from_document(doc_v2())
    assert profile.local_tz == "Test/Zone"
    assert profile.tou_windows == {"Alpha": ("08:00-20:00",), "Beta": ("20:00-08:00",)}
    assert profile.parsers == (SourceType.SAPN_INTERVAL,)
    assert list(profile.model_dump(mode="json")) == [
        "kind",
        "schema_version",
        "name",
        "local_tz",
        "tou_windows",
        "parsers",
    ]


def test_the_zone_and_windows_allow_null() -> None:
    profile = RegionProfile.from_document(doc_v2(local_tz=None, tou_windows=None))
    assert profile.local_tz is None
    assert profile.tou_windows is None


@pytest.mark.parametrize(
    "changes",
    [
        {"local_tz": "not a zone"},
        {"local_tz": ""},
        {"local_tz": "/Test/Zone"},
        {"local_tz": "Test/../Zone"},
        {"tou_windows": {"Alpha": ["08:00-20:00"]}},
        {"tou_windows": {"Alpha": ["08:00-24:00"], "Beta": ["00:00-08:00"]}},
        {"tou_windows": {"Alpha": ["08:03-20:00"], "Beta": ["20:00-08:03"]}},
        {"tou_windows": {"Alpha": ["08:00-20:00"], "Beta": ["19:55-08:00"]}},
    ],
)
def test_invalid_profiles_are_invalid_input(changes: dict[str, Any]) -> None:
    with pytest.raises(InvalidInputError):
        RegionProfile.from_document(doc_v2(**changes))


def test_a_window_error_names_the_profile_field() -> None:
    with pytest.raises(InvalidInputError) as caught:
        RegionProfile.from_document(doc_v2(tou_windows={"Alpha": ["08:00-20:00"]}))
    assert caught.value.details["field"] == "tou_windows"


def test_the_golden_v1_profile_upgrades_with_a_null_zone_and_windows() -> None:
    v1 = json.loads(GOLDEN_V1.read_text(encoding="utf-8"))
    upgraded = upgrade(v1)
    assert upgraded["schema_version"] == 2
    assert upgraded["local_tz"] is None
    assert upgraded["tou_windows"] is None
    assert {
        key: value for key, value in upgraded.items() if key not in ("schema_version", "local_tz", "tou_windows")
    } == {key: value for key, value in v1.items() if key != "schema_version"}
    profile = RegionProfile.from_document(v1)
    assert (profile.local_tz, profile.tou_windows) == (None, None)


def shipped(name: str) -> dict[str, Any]:
    resource = importlib.resources.files("energatron").joinpath("regions", f"{name}.yaml")
    return yaml.safe_load(resource.read_text(encoding="utf-8"))


def test_sa_ships_as_v2_with_a_zone_in_tzdata_and_windows_that_partition_the_day() -> None:
    profile = RegionProfile.from_document(shipped("sa"))
    assert profile.schema_version == 2
    assert profile.local_tz is not None
    zone_file = importlib.resources.files("tzdata").joinpath("zoneinfo", *profile.local_tz.split("/"))
    assert zone_file.is_file()
    assert profile.tou_windows is not None
    covered = sorted(
        minute for texts in profile.tou_windows.values() for text in texts for minute in window_minutes(text)
    )
    assert covered == list(range(1440))


def test_the_shipped_document_is_v2_in_the_file_itself() -> None:
    assert shipped("sa")["schema_version"] == 2
