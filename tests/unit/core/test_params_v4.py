"""`params` v4 (P4 spec §4.1): the `offers`, `pricing`, `backtest` and `validation.backtest` blocks, the region fields
they add and the v3 to v4 migration. Synthetic retailers, distributor and snapshot IDs."""

import copy
from decimal import Decimal
from typing import Any

import pytest

from energatron.core.errors import InvalidInputError
from energatron.core.models.catalog import SourceType
from energatron.core.models.params import REGION_FIELDS, Params
from energatron.core.models.region import RegionProfile
from energatron.core.persisted import registered_migrations
from tests.unit.core.test_params import doc_v3

SNAPSHOT_A = "TEST1@EME~20250701T000000Z~0123abcd"
SNAPSHOT_B = "TEST2@EME~20250901T000000Z~89abcdef"


def profile(
    *, retailers: tuple[str, ...] | None = ("alpha", "beta-energy"), distributor: str | None = "Test Networks"
) -> RegionProfile:
    return RegionProfile(
        name="sa",
        local_tz="Test/Zone",
        tou_windows={"Alpha": ("08:00-20:00",), "Beta": ("20:00-08:00",)},
        distributor=distributor,
        cdr_retailers=retailers,
        gas_distributor="Test Gas Networks",
        parsers=(SourceType.SAPN_INTERVAL,),
    )


def doc_v4(**changes: Any) -> dict[str, Any]:
    """A valid v4 document; `changes` replace whole top-level sections."""
    doc: dict[str, Any] = doc_v3() | {"schema_version": 4}
    doc["validation"] = copy.deepcopy(doc["validation"]) | {
        "backtest": {"tolerance_pct": 1.0, "invoice_window_days": 14}
    }
    doc["offers"] = {
        "retailers": ["alpha", "beta-energy"],
        "distributor": "Test Networks",
        "customer_type": "RESIDENTIAL",
        "fuel_types": ["ELECTRICITY"],
        "unmet_eligibility": ["EXISTING_SOLAR", "EXISTING_BATTERY"],
        "controlled_load": False,
    }
    doc["pricing"] = {
        "gst_rate": "0.10",
        "assume_conditions_met": ["PAY_ON_TIME", "DIRECT_DEBIT"],
        "demand_minutes": 30,
    }
    doc["backtest"] = {"tariffs": []}
    return doc | changes


def with_block(block: str, **changes: Any) -> dict[str, Any]:
    doc = doc_v4()
    doc[block] = doc[block] | changes
    return doc


def test_a_valid_v4_document_validates() -> None:
    params = Params.from_document(doc_v4())
    assert params.schema_version == 6
    assert params.offers.retailers == ("alpha", "beta-energy")
    assert params.offers.distributor == "Test Networks"
    assert params.offers.customer_type == "RESIDENTIAL"
    assert params.offers.fuel_types == ("ELECTRICITY", "GAS")  # the v4 to v5 migration adds GAS
    assert params.offers.unmet_eligibility == ("EXISTING_SOLAR", "EXISTING_BATTERY")
    assert params.offers.controlled_load is False
    assert params.pricing.gst_rate == Decimal("0.10")
    assert params.pricing.assume_conditions_met == ("PAY_ON_TIME", "DIRECT_DEBIT")
    assert params.pricing.demand_minutes == 30
    assert params.backtest.tariffs == ()
    assert params.validation.backtest.tolerance_pct == 1.0
    assert params.validation.backtest.invoice_window_days == 14


def test_the_gst_rate_dumps_as_a_decimal_string() -> None:
    dumped = Params.from_document(doc_v4()).model_dump(mode="json")
    assert dumped["pricing"]["gst_rate"] == "0.10"
    assert list(dumped)[-5:] == ["offers", "pricing", "backtest", "gas", "recommend"]


def test_the_region_fields_include_the_retailers_and_the_distributor() -> None:
    assert "offers.retailers" in REGION_FIELDS
    assert "offers.distributor" in REGION_FIELDS


def test_the_region_fields_allow_null() -> None:
    params = Params.from_document(with_block("offers", retailers=None, distributor=None))
    assert params.offers.retailers is None
    assert params.offers.distributor is None


def test_tariffs_in_force_parse_with_from_and_to() -> None:
    tariffs = [
        {"offer": SNAPSHOT_A, "from": "2025-01-01", "to": "2025-06-30"},
        {"offer": SNAPSHOT_B, "from": "2025-07-01", "to": None},
    ]
    params = Params.from_document(doc_v4(backtest={"tariffs": tariffs}))
    first, second = params.backtest.tariffs
    assert (first.offer, first.start.isoformat(), first.end and first.end.isoformat()) == (
        SNAPSHOT_A,
        "2025-01-01",
        "2025-06-30",
    )
    assert second.end is None
    assert params.model_dump(mode="json")["backtest"]["tariffs"] == tariffs


@pytest.mark.parametrize(
    ("block", "changes", "field"),
    [
        ("offers", {"retailers": ["Alpha"]}, "offers.retailers"),
        ("offers", {"retailers": ["alpha", "alpha"]}, "offers.retailers"),
        ("offers", {"retailers": ["has space"]}, "offers.retailers"),
        ("offers", {"distributor": " padded "}, "offers.distributor"),
        ("offers", {"distributor": ""}, "offers.distributor"),
        ("offers", {"customer_type": "SOMEONE"}, "offers.customer_type"),
        ("offers", {"fuel_types": []}, "offers.fuel_types"),
        ("offers", {"fuel_types": ["COAL"]}, "offers.fuel_types"),
        ("offers", {"unmet_eligibility": ["NOT_A_TYPE"]}, "offers.unmet_eligibility"),
        ("offers", {"unmet_eligibility": ["EXISTING_SOLAR", "EXISTING_SOLAR"]}, "offers.unmet_eligibility"),
        ("offers", {"controlled_load": "no"}, "offers.controlled_load"),
        ("pricing", {"gst_rate": "-0.1"}, "pricing.gst_rate"),
        ("pricing", {"gst_rate": "1"}, "pricing.gst_rate"),
        ("pricing", {"gst_rate": "ten"}, "pricing.gst_rate"),
        ("pricing", {"assume_conditions_met": ["OTHER"]}, "pricing.assume_conditions_met"),
        ("pricing", {"assume_conditions_met": ["PAY_ON_TIME", "PAY_ON_TIME"]}, "pricing.assume_conditions_met"),
        ("pricing", {"demand_minutes": 7}, "pricing.demand_minutes"),
    ],
)
def test_invalid_offer_and_pricing_values_name_their_field(block: str, changes: dict[str, Any], field: str) -> None:
    with pytest.raises(InvalidInputError) as caught:
        Params.from_document(with_block(block, **changes))
    assert field in str(caught.value.details) or field.split(".")[-1] in str(caught.value.details)


@pytest.mark.parametrize(
    "tariffs",
    [
        [{"offer": "not a snapshot", "from": "2025-01-01", "to": None}],
        [{"offer": SNAPSHOT_A, "from": "2025-02-01", "to": "2025-01-01"}],
        [
            {"offer": SNAPSHOT_A, "from": "2025-01-01", "to": "2025-06-30"},
            {"offer": SNAPSHOT_B, "from": "2025-06-30", "to": None},
        ],
        [
            {"offer": SNAPSHOT_A, "from": "2025-01-01", "to": None},
            {"offer": SNAPSHOT_B, "from": "2025-07-01", "to": None},
        ],
    ],
)
def test_tariffs_in_force_that_are_malformed_or_overlap_are_refused(tariffs: list[dict[str, Any]]) -> None:
    with pytest.raises(InvalidInputError) as caught:
        Params.from_document(doc_v4(backtest={"tariffs": tariffs}))
    assert "backtest.tariffs" in str(caught.value.details)


def test_tariffs_in_force_may_be_listed_out_of_order() -> None:
    tariffs = [
        {"offer": SNAPSHOT_B, "from": "2025-07-01", "to": None},
        {"offer": SNAPSHOT_A, "from": "2025-01-01", "to": "2025-06-30"},
    ]
    assert len(Params.from_document(doc_v4(backtest={"tariffs": tariffs})).backtest.tariffs) == 2


@pytest.mark.parametrize(("changes"), [{"tolerance_pct": -1}, {"invoice_window_days": -1}, {"invoice_window_days": 90}])
def test_invalid_backtest_validation_values_are_refused(changes: dict[str, Any]) -> None:
    doc = doc_v4()
    doc["validation"]["backtest"] |= changes
    with pytest.raises(InvalidInputError):
        Params.from_document(doc)


def test_the_v3_to_v4_migration_inserts_the_defaults_with_null_region_fields() -> None:
    v3 = doc_v3()
    frozen = copy.deepcopy(v3)
    upgraded = registered_migrations()[("params", 3)](v3)
    expected = doc_v4()
    expected["offers"] |= {"retailers": None, "distributor": None}
    assert upgraded == expected
    assert v3 == frozen


def test_a_v3_document_reads_as_v4_with_null_region_fields_and_keeps_its_values() -> None:
    doc = doc_v3()
    doc["meter"]["timebase"] = "local_wall"
    params = Params.from_document(doc)
    assert params.schema_version == 6
    assert params.meter.timebase == "local_wall"
    assert params.offers.retailers is None
    assert params.offers.distributor is None


def test_defaults_take_the_retailers_and_distributor_from_the_profile() -> None:
    params = Params.defaults(profile())
    assert params.offers.retailers == ("alpha", "beta-energy")
    assert params.offers.distributor == "Test Networks"
    migrated = Params.from_document(doc_v3())
    assert migrated.pricing == params.pricing
    assert migrated.backtest == params.backtest
    assert migrated.validation.backtest == params.validation.backtest
    unset = {"retailers": None, "distributor": None, "gas_distributor": None}
    assert migrated.offers.model_copy(update=unset) == params.offers.model_copy(update=unset)


def test_defaults_carry_a_null_profile_value_through() -> None:
    params = Params.defaults(profile(retailers=None, distributor=None))
    assert params.offers.retailers is None
    assert params.offers.distributor is None


def test_a_v4_document_with_an_unknown_key_is_invalid() -> None:
    with pytest.raises(InvalidInputError):
        Params.from_document(with_block("offers", brands=["alpha"]))
