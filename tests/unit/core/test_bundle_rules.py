"""Bundle rules (P6 spec §5.1): the model's rules, matching, effects and loading. Synthetic brands and plans only."""

from decimal import Decimal
from typing import Any

import pytest

from energatron.core.bundle_rules import BundleRule, load_bundle_rules, parse_bundle_rules
from energatron.core.errors import InvalidInputError


def rule_doc(**changes: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "kind": "bundle_rule",
        "schema_version": 1,
        "id": "exa-dual",
        "description": "Synthetic.",
        "brand": "Example Energy",
        "electricity_plan": None,
        "gas_plan": None,
        "effect": {"annual_credit": "25"},
        "verified_on": "2026-11-10",
        "cites": [],
    }
    return doc | changes


def test_a_valid_rule_validates() -> None:
    rule = BundleRule.from_document(rule_doc())
    assert rule.effect.annual_credit == Decimal("25")
    assert rule.effect.percent_off is None


@pytest.mark.parametrize(
    "changes",
    [
        {"id": "Not Kebab"},
        {"brand": "  "},
        {"electricity_plan": "("},
        {"effect": {}},
        {"effect": {"annual_credit": "5", "percent_off": "5"}},
        {"effect": {"annual_credit": "0"}},
        {"effect": {"percent_off": "100"}},
        {"effect": {"percent_off": "NaN"}},
        {"cites": ["not-a-snapshot"]},
        {"account": "anything"},
    ],
)
def test_invalid_rules_are_invalid_input(changes: dict[str, Any]) -> None:
    with pytest.raises(InvalidInputError):
        BundleRule.from_document(rule_doc(**changes))


def test_matching_needs_both_brands_and_both_patterns() -> None:
    rule = BundleRule.from_document(rule_doc(electricity_plan="EXA-E-[0-9]+", gas_plan=None))
    assert rule.matches(elec_brand="example energy", elec_plan="EXA-E-1", gas_brand="EXAMPLE ENERGY", gas_plan="G")
    assert not rule.matches(elec_brand="Example Energy", elec_plan="EXA-E-1x", gas_brand="Example Energy", gas_plan="G")
    assert not rule.matches(elec_brand="Example Energy", elec_plan="EXA-E-1", gas_brand="Other", gas_plan="G")
    assert not rule.matches(elec_brand="Other", elec_plan="EXA-E-1", gas_brand="Example Energy", gas_plan="G")


def test_effects_give_negative_cent_adjustments() -> None:
    credit = BundleRule.from_document(rule_doc(effect={"annual_credit": "25"})).effect
    assert credit.adjustment(Decimal("1000.00")) == Decimal("-25.00")
    percent = BundleRule.from_document(rule_doc(effect={"percent_off": "2.5"})).effect
    assert percent.adjustment(Decimal("1000.10")) == Decimal("-25.00")  # 25.0025 rounds half up to cents
    assert percent.adjustment(Decimal("0.20")) == Decimal("-0.01")  # 0.005 rounds half up


def test_local_rules_replace_curated_ones_by_id_and_come_in_id_order() -> None:
    curated = [("b.yaml", rule_doc(id="b-rule")), ("a.yaml", rule_doc(id="a-rule"))]
    local = [("x.yaml", rule_doc(id="b-rule", effect={"percent_off": "5"}))]
    rules = load_bundle_rules(curated, local)
    assert [rule.id for rule in rules] == ["a-rule", "b-rule"]
    assert rules[1].effect.percent_off == Decimal("5")


@pytest.mark.parametrize(
    "files",
    [
        [("a.yaml", None)],
        [("a.yaml", ["a", "list"])],
        [("a.yaml", rule_doc()), ("b.yaml", rule_doc())],
        [("a.yaml", rule_doc(schema_version=2))],
    ],
)
def test_bad_files_name_the_file_and_never_the_content(files: list[tuple[str, Any]]) -> None:
    with pytest.raises(InvalidInputError) as caught:
        parse_bundle_rules(files, where="local")
    assert caught.value.details["rules"] == "local"
    assert caught.value.details["file"] in {"a.yaml", "b.yaml"}
    assert "Example Energy" not in str(caught.value) + str(caught.value.details)
