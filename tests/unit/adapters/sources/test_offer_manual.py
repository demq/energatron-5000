"""`offer_manual` (P4 spec §5.3): the YAML parser, its snapshot ID and the CDR view `map_cdr` reads."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
import yaml

from energatron.adapters.sources.offer_manual import OFFER_MANUAL
from energatron.core.cdr_tariff import Household, Plan, map_cdr
from energatron.core.errors import InvalidInputError
from energatron.core.ids import parse_offer_snapshot_id
from energatron.core.models.catalog import SourceType
from energatron.core.models.offers import OfferManual
from energatron.core.tariff import Tariff
from tests.fixtures import make_cdr as cdr

TEXT = cdr.offer_manual_text()


def test_the_parser_is_a_source_parser_for_offer_manual_version_1() -> None:
    assert (OFFER_MANUAL.source, OFFER_MANUAL.version) == (SourceType.OFFER_MANUAL, 1)


def test_detect_is_content_only() -> None:
    assert OFFER_MANUAL.detect("anything.txt", TEXT.encode()) is not None
    assert OFFER_MANUAL.detect("offer.yaml", b"kind: params\nschema_version: 4\n") is None


def test_inspect_gives_the_published_time_and_the_snapshot_id() -> None:
    info = OFFER_MANUAL.inspect("offer.yaml", TEXT.encode())
    assert info.source_ts == datetime(2025, 7, 1, tzinfo=UTC)
    assert info.extension == ".yaml"
    assert info.identifiers == {}
    snapshot = info.extra["offer_snapshot_id"]
    assert isinstance(snapshot, str)
    assert parse_offer_snapshot_id(snapshot).plan_id == "MANUAL-TEST-TOU"
    assert info.extra["plan_id"] == "MANUAL-TEST-TOU"


def test_comments_and_key_order_do_not_change_the_snapshot_id() -> None:
    document = yaml.safe_load(TEXT)
    reordered = "# a comment\n" + yaml.safe_dump(dict(reversed(list(document.items()))), sort_keys=False)
    first = OFFER_MANUAL.inspect("a.yaml", TEXT.encode()).extra["offer_snapshot_id"]
    assert OFFER_MANUAL.inspect("b.yaml", reordered.encode()).extra["offer_snapshot_id"] == first


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("kind: offer_manual\n  bad: [indent\n", "bad_yaml"),
        ("- kind: offer_manual\n", "not_a_mapping"),
        (TEXT.replace("published: '2025-07-01T00:00:00Z'", "published: 2025-07-01T00:00:00Z"), "unquoted_date"),
        (TEXT.replace("effective_from: '2025-07-01'", "effective_from: 2025-07-01"), "unquoted_date"),
        (TEXT.replace("source: bill", "source: rumour"), "invalid_offer_manual"),
        (TEXT.replace("plan_id: MANUAL-TEST-TOU", "plan_id: has~tilde"), "invalid_offer_manual"),
        (TEXT.replace("prices_include_gst: false", "prices_include_gst: maybe"), "invalid_offer_manual"),
    ],
)
def test_malformed_documents_are_rejected_with_a_reason(text: str, reason: str) -> None:
    with pytest.raises(InvalidInputError) as caught:
        OFFER_MANUAL.inspect("offer.yaml", text.encode())
    assert caught.value.details["reason"] == reason


def test_a_manual_offer_maps_through_the_cdr_mapper_with_its_gst_divided_back() -> None:
    contract = cdr.plan(pricing_model="SINGLE_RATE", periods=[cdr.single_period([cdr.rate("0.33")], supply="1.10")])
    text = cdr.offer_manual_text(prices_include_gst=True, contract=contract["electricityContract"])
    manual = OfferManual.from_document(yaml.safe_load(text))
    data = manual.as_cdr_data()
    assert (data["fuelType"], data["customerType"], data["effectiveFrom"]) == (
        "ELECTRICITY",
        "RESIDENTIAL",
        "2025-07-01T00:00:00+00:00",
    )
    plan = Plan(
        offer_snapshot_id="MANUAL-TEST-TOU~20250701T000000Z~0123abcd",
        retailer="manual",
        source="manual",
        prices_include_gst=manual.prices_include_gst,
    )
    found = map_cdr(data, plan, Household(frozenset(), controlled_load=False), as_of=date(2025, 8, 1))
    assert isinstance(found, Tariff)
    assert found.effective_from == date(2025, 7, 1)
    assert found.seasons[0].daily_supply == Decimal("1")
    assert found.seasons[0].energy[0].blocks[0].unit_price == Decimal("0.3")


# offer_manual v2 (P5 spec §5.3)


def test_a_gas_manual_offer_lands_and_reads_as_gas_cdr_data() -> None:
    contract = cdr.gas_plan()["gasContract"]
    text = cdr.offer_manual_text("MANUAL-TEST-GAS", gas_contract=contract)
    info = OFFER_MANUAL.inspect("gas.yaml", text.encode())
    assert info.extra["plan_id"] == "MANUAL-TEST-GAS"
    manual = OfferManual.from_document(yaml.safe_load(text))
    data = manual.as_cdr_data()
    assert (manual.fuel, data["fuelType"], "electricityContract" in data) == ("GAS", "GAS", False)
    assert data["gasContract"] == contract


def test_a_v1_document_reads_as_v2_electricity_with_its_snapshot_id_unchanged() -> None:
    text = cdr.offer_manual_text(schema_version=1)
    manual = OfferManual.from_document(yaml.safe_load(text))
    assert (manual.schema_version, manual.fuel) == (2, "ELECTRICITY")
    v2 = cdr.offer_manual_text()
    first = OFFER_MANUAL.inspect("a.yaml", text.encode()).extra["offer_snapshot_id"]
    assert first != OFFER_MANUAL.inspect("b.yaml", v2.encode()).extra["offer_snapshot_id"]  # the document differs
    assert first == OFFER_MANUAL.inspect("c.yaml", text.encode()).extra["offer_snapshot_id"]


@pytest.mark.parametrize("contracts", ["both", "neither"])
def test_exactly_one_contract_is_required(contracts: str) -> None:
    document = yaml.safe_load(cdr.offer_manual_text())
    if contracts == "both":
        document["gasContract"] = cdr.gas_plan()["gasContract"]
    else:
        del document["electricityContract"]
    with pytest.raises(InvalidInputError):
        OfferManual.from_document(document)
