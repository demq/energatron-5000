"""The `params.yaml` text builders of `tests/fixtures/params_text.py` produce documents `Params` accepts."""

import datetime
from decimal import Decimal

import yaml

from energatron.core.models.params import Params
from tests.fixtures.params_text import (
    DISTRIBUTOR,
    GAS_DISTRIBUTOR,
    RETAILERS,
    ZONE,
    params_v2_text,
    params_v3_text,
    params_v4_text,
    params_v5_text,
)
from tests.fixtures.pipeline_workspace import WINDOWS


def read(text: str) -> Params:
    return Params.from_document(yaml.safe_load(text))


def test_v2_text_is_still_a_version_2_document_that_reads_as_the_current_version() -> None:
    assert yaml.safe_load(params_v2_text())["schema_version"] == 2
    assert read(params_v2_text()).schema_version == Params.SCHEMA_VERSION


def test_v3_text_without_a_region_has_null_zone_and_windows() -> None:
    params = read(params_v3_text())
    assert yaml.safe_load(params_v3_text())["schema_version"] == 3
    assert params.meter.local_tz is None
    assert params.validation.timebase.windows is None
    assert params.analysis.tou.window_sets[0].windows is None


def test_v3_text_with_a_region_fills_the_zone_and_both_window_fields() -> None:
    params = read(params_v3_text(region=True))
    expected = {label: tuple(found) for label, found in WINDOWS.items()}
    assert params.meter.local_tz == ZONE
    assert params.validation.timebase.windows == expected
    assert params.analysis.tou.window_sets[0].windows == expected


def test_v3_text_takes_its_analysis_settings() -> None:
    analysis = read(params_v3_text(reference_months=6, coverage_threshold=0.5)).analysis
    assert (analysis.reference_months, analysis.coverage_threshold) == (6, 0.5)


def test_v3_text_appends_a_second_window_set() -> None:
    later = {"Peak": ["06:00-21:00"], "Off Peak": ["21:00-01:00"], "Shoulder": ["01:00-06:00"]}
    sets = read(params_v3_text(region=True, second_set=("2025-07-01", later))).analysis.tou.window_sets
    assert [found.effective_from for found in sets] == [None, datetime.date(2025, 7, 1)]
    assert sets[1].windows == {key: tuple(value) for key, value in later.items()}


def test_v4_text_is_a_version_4_document_with_null_offer_region_fields_by_default() -> None:
    text = params_v4_text()
    assert yaml.safe_load(text)["schema_version"] == 4
    params = read(text)
    assert (params.offers.retailers, params.offers.distributor) == (None, None)
    assert params.pricing.gst_rate == Decimal("0.10")
    assert params.backtest.tariffs == ()


def test_v4_text_with_a_region_fills_every_region_field() -> None:
    params = read(params_v4_text(region=True))
    assert params.meter.local_tz == ZONE
    assert params.offers.retailers == RETAILERS
    assert params.offers.distributor == DISTRIBUTOR


def test_v4_text_takes_its_v3_settings_and_tariffs_in_force() -> None:
    tariffs = [{"offer": "TEST1@EME~20250701T000000Z~0123abcd", "from": "2025-01-01", "to": None}]
    params = read(params_v4_text(region=True, reference_months=6, tariffs=tariffs, controlled_load=True))
    assert params.analysis.reference_months == 6
    assert params.offers.controlled_load is True
    assert [found.offer for found in params.backtest.tariffs] == [tariffs[0]["offer"]]


def test_v5_text_is_a_version_5_document_with_the_gas_keys() -> None:
    assert yaml.safe_load(params_v5_text())["schema_version"] == 5
    params = read(params_v5_text())
    assert params.offers.gas_distributor is None
    assert params.offers.fuel_types == ("ELECTRICITY", "GAS")
    gas_tariffs = [{"offer": "TEST9@EME~20251001T000000Z~0123abcd", "from": "2025-10-01", "to": None}]
    filled = read(params_v5_text(region=True, gas_tariffs=gas_tariffs, reference_bills=3, gas_tolerance="0.10"))
    assert filled.offers.gas_distributor == GAS_DISTRIBUTOR
    assert filled.offers.distributor == DISTRIBUTOR
    assert [found.offer for found in filled.backtest.gas_tariffs] == ["TEST9@EME~20251001T000000Z~0123abcd"]
    assert filled.gas.reference_bills == 3
    assert str(filled.validation.gas_bills.tolerance) == "0.10"
