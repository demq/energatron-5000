"""`params` v5 (P5 spec §4.1): `GAS` in the fuels, `offers.gas_distributor`, `gas`, `backtest.gas_tariffs`,
`validation.gas_bills` and the v4 to v5 migration. Synthetic distributors and snapshot IDs."""

import copy
from decimal import Decimal
from typing import Any

import pytest

from energatron.core.errors import InvalidInputError
from energatron.core.models.params import REGION_FIELDS, Params
from energatron.core.persisted import registered_migrations
from tests.unit.core.test_params_v4 import SNAPSHOT_A, SNAPSHOT_B, doc_v4, profile


def doc_v5(**changes: Any) -> dict[str, Any]:
    """A valid v5 document; `changes` replace whole top-level sections."""
    doc: dict[str, Any] = copy.deepcopy(doc_v4()) | {"schema_version": 5}
    doc["offers"] |= {"fuel_types": ["ELECTRICITY", "GAS"], "gas_distributor": "Test Gas Networks"}
    doc["backtest"] |= {"gas_tariffs": []}
    doc["validation"] |= {"gas_bills": {"tolerance": "0.05"}}
    doc["gas"] = {"reference_bills": 12, "full_year_days": 334}
    return doc | changes


def with_block(block: str, **changes: Any) -> dict[str, Any]:
    doc = doc_v5()
    doc[block] = doc[block] | changes
    return doc


def test_a_valid_v5_document_validates() -> None:
    params = Params.from_document(doc_v5())
    assert params.schema_version == 6
    assert params.offers.fuel_types == ("ELECTRICITY", "GAS")
    assert params.offers.gas_distributor == "Test Gas Networks"
    assert (params.gas.reference_bills, params.gas.full_year_days) == (12, 334)
    assert params.backtest.gas_tariffs == ()
    assert params.validation.gas_bills.tolerance == Decimal("0.05")
    dumped = params.model_dump(mode="json")
    assert dumped["validation"]["gas_bills"] == {"tolerance": "0.05"}
    assert list(dumped)[-2:] == ["gas", "recommend"]


def test_the_gas_distributor_is_a_region_field_that_may_be_null() -> None:
    assert "offers.gas_distributor" in REGION_FIELDS
    assert Params.from_document(with_block("offers", gas_distributor=None)).offers.gas_distributor is None


@pytest.mark.parametrize(
    ("block", "changes", "field"),
    [
        ("offers", {"gas_distributor": " padded "}, "offers.gas_distributor"),
        ("offers", {"gas_distributor": ""}, "offers.gas_distributor"),
        ("offers", {"fuel_types": ["GAS", "GAS"]}, "offers.fuel_types"),
        ("offers", {"fuel_types": ["DUAL"]}, "offers.fuel_types"),
        ("gas", {"reference_bills": 0}, "gas.reference_bills"),
        ("gas", {"full_year_days": 367}, "gas.full_year_days"),
        ("gas", {"reference_bills": "12"}, "gas.reference_bills"),
    ],
)
def test_invalid_gas_values_name_their_field(block: str, changes: dict[str, Any], field: str) -> None:
    with pytest.raises(InvalidInputError) as caught:
        Params.from_document(with_block(block, **changes))
    assert field in str(caught.value.details)


@pytest.mark.parametrize("tolerance", ["-0.01", "ten", "NaN"])
def test_an_invalid_gas_bill_tolerance_is_refused(tolerance: str) -> None:
    doc = doc_v5()
    doc["validation"]["gas_bills"] = {"tolerance": tolerance}
    with pytest.raises(InvalidInputError):
        Params.from_document(doc)


def test_gas_tariffs_in_force_parse_and_must_not_overlap() -> None:
    tariffs = [
        {"offer": SNAPSHOT_A, "from": "2025-01-01", "to": "2025-06-30"},
        {"offer": SNAPSHOT_B, "from": "2025-07-01", "to": None},
    ]
    params = Params.from_document(with_block("backtest", gas_tariffs=tariffs))
    assert [found.offer for found in params.backtest.gas_tariffs] == [SNAPSHOT_A, SNAPSHOT_B]
    overlapping = [{**tariffs[0], "to": "2025-07-01"}, tariffs[1]]
    with pytest.raises(InvalidInputError) as caught:
        Params.from_document(with_block("backtest", gas_tariffs=overlapping))
    assert caught.value.details["field"] == "backtest.gas_tariffs"


def test_the_gas_and_electricity_tariff_lists_are_checked_apart() -> None:
    same = [{"offer": SNAPSHOT_A, "from": "2025-01-01", "to": None}]
    params = Params.from_document(with_block("backtest", tariffs=same, gas_tariffs=same))
    assert len(params.backtest.tariffs) == len(params.backtest.gas_tariffs) == 1


def test_the_v4_to_v5_migration_adds_gas_and_inserts_the_defaults() -> None:
    v4 = doc_v4()
    frozen = copy.deepcopy(v4)
    upgraded = registered_migrations()[("params", 4)](v4)
    expected = doc_v5()
    expected["offers"]["gas_distributor"] = None
    assert upgraded == expected
    assert v4 == frozen


def test_the_migration_keeps_an_owner_list_that_already_has_gas_and_leaves_an_empty_one_invalid() -> None:
    owners = doc_v4()
    owners["offers"] = owners["offers"] | {"fuel_types": ["GAS"]}
    upgraded: Any = registered_migrations()[("params", 4)](owners)
    assert upgraded["offers"]["fuel_types"] == ["GAS"]
    empty = doc_v4()
    empty["offers"] = empty["offers"] | {"fuel_types": []}
    with pytest.raises(InvalidInputError):
        Params.from_document(empty)


def test_defaults_take_the_gas_distributor_from_the_profile() -> None:
    params = Params.defaults(profile())
    assert params.offers.gas_distributor == "Test Gas Networks"
    assert params.offers.fuel_types == ("ELECTRICITY", "GAS")
    migrated = Params.from_document(doc_v4())
    assert migrated.gas == params.gas
    assert migrated.validation.gas_bills == params.validation.gas_bills
    assert migrated.backtest.gas_tariffs == params.backtest.gas_tariffs == ()
