"""Offer rules (P4 spec §6): the model, loading two directories, matching, effects and the curated rules."""

import importlib.resources
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
import yaml

from energatron.core.cdr_tariff import Excluded
from energatron.core.errors import InvalidInputError
from energatron.core.offer_rules import OfferRule, apply_rules, load_rules, parse_rules
from energatron.core.tariff import ALL_WEEK, Block, EnergyCharge, Season, Tariff

SNAPSHOT = "TEST1@EME~20261001T000000Z~0123abcd"


def rule_text(
    rule_id: str = "test-rule",
    *,
    match: dict[str, Any] | None = None,
    effect: dict[str, Any] | None = None,
    **extra: Any,
) -> str:
    document = {
        "kind": "offer_rule",
        "schema_version": 1,
        "id": rule_id,
        "description": "A made-up rule.",
        "match": match or {"brand": "alpha"},
        "effect": effect or {"approximate": "test_reason"},
        "verified_on": "2026-11-01",
        "cites": [SNAPSHOT],
    } | extra
    return yaml.safe_dump(document, sort_keys=False)


def parsed(name: str, text: str) -> tuple[str, Any]:
    """A rule file as the stage passes it: its name and its YAML document, `None` when it isn't YAML."""
    try:
        return name, yaml.safe_load(text)
    except yaml.YAMLError:
        return name, None


def rule(**kwargs: Any) -> OfferRule:
    return OfferRule.from_document(yaml.safe_load(rule_text(**kwargs)))


def a_tariff(**changes: Any) -> Tariff:
    season = Season(
        start="01-01",
        end="12-31",
        clock="LOCAL",
        daily_supply=Decimal(1),
        energy=(
            EnergyCharge(
                label="ALL",
                windows=(ALL_WEEK,),
                block_period="P1D",
                blocks=(Block(volume_kwh=None, unit_price=Decimal("0.3")),),
            ),
        ),
    )
    values: dict[str, Any] = {
        "offer_snapshot_id": SNAPSHOT,
        "plan_id": "TEST1@EME",
        "brand": "alpha",
        "retailer": "alpha",
        "display_name": "Alpha Test",
        "source": "cdr",
        "customer_type": "RESIDENTIAL",
        "distributors": (),
        "pricing_model": "SINGLE_RATE",
        "effective_from": None,
        "effective_to": None,
        "seasons": (season,),
        "status": "normalised",
    }
    return Tariff(**(values | changes))


def applied(found: Tariff | Excluded, *rules: OfferRule, **where: Any) -> Any:
    facts = {"brand": "alpha", "plan_id": "TEST1@EME", "display_name": "Alpha Test", "last_updated": date(2026, 10, 1)}
    return apply_rules(found, rules, **(facts | where))


@pytest.mark.parametrize(
    ("match", "hit"),
    [
        ({"brand": "alpha"}, True),
        ({"brand": "beta"}, False),
        ({"plan_id": "TEST[0-9]@EME"}, True),
        ({"plan_id": "TEST"}, False),  # matched whole
        ({"display_name": "(?i)alpha.*"}, True),
        ({"brand": "alpha", "from": "2026-10-02"}, False),
        ({"brand": "alpha", "to": "2026-09-30"}, False),
        ({"brand": "alpha", "from": "2026-10-01", "to": "2026-10-01"}, True),
    ],
)
def test_matching(match: dict[str, Any], hit: bool) -> None:
    assert applied(a_tariff(), rule(match=match)).rules == (("test-rule",) if hit else ())


def test_an_approximate_rule_marks_the_tariff_approximate() -> None:
    found = applied(a_tariff(), rule()).result
    assert isinstance(found, Tariff)
    assert (found.status, found.approximations) == ("approximate", ("rule:test-rule:test_reason",))


def test_an_exclude_rule_excludes_a_tariff_and_adds_to_an_exclusion() -> None:
    exclude = rule(effect={"exclude": "not_for_sale"})
    found = applied(a_tariff(notes=("feed_in_unpriced",)), exclude).result
    assert found == Excluded(reasons=("rule:test-rule:not_for_sale",), notes=("feed_in_unpriced",))
    again = applied(Excluded(reasons=("banded_supply",)), exclude).result
    assert again.reasons == ("banded_supply", "rule:test-rule:not_for_sale")


def test_an_approximate_rule_leaves_an_exclusion_alone() -> None:
    assert applied(Excluded(reasons=("banded_supply",)), rule()).result == Excluded(reasons=("banded_supply",))


def test_a_note_rule_adds_a_note() -> None:
    found = applied(a_tariff(), rule(effect={"note": "Read the terms."})).result
    assert isinstance(found, Tariff)
    assert (found.notes, found.status) == (("rule:test-rule",), "normalised")


def test_rules_apply_in_id_order_and_are_all_recorded() -> None:
    rules = load_rules(
        [parsed("b.yaml", rule_text("b-rule")), parsed("a.yaml", rule_text("a-rule", effect={"note": "x"}))], []
    )
    assert [found.id for found in rules] == ["a-rule", "b-rule"]
    assert applied(a_tariff(), *rules).rules == ("a-rule", "b-rule")


def test_a_local_rule_replaces_the_curated_one_with_its_id() -> None:
    curated = [parsed("x.yaml", rule_text("shared", effect={"exclude": "curated"}))]
    local = [parsed("y.yaml", rule_text("shared", effect={"note": "local"}))]
    (found,) = load_rules(curated, local)
    assert found.effect.note == "local"


@pytest.mark.parametrize(
    "text",
    [
        "kind: [unclosed",
        "- a list",
        rule_text(match={"from": "2026-01-01"}),  # names nothing
        rule_text(match={"brand": "alpha", "from": "2026-02-01", "to": "2026-01-01"}),
        rule_text(effect={"exclude": "x", "note": "y"}),
        rule_text(effect={"note": None}),  # no effect at all
        rule_text(effect={"exclude": "Not A Reason"}),
        rule_text(match={"plan_id": "("}),
        rule_text("Not_Kebab"),
        rule_text(cites=["not a snapshot id"]),
    ],
)
def test_an_invalid_rule_names_its_file_but_not_its_content(text: str) -> None:
    with pytest.raises(InvalidInputError) as caught:
        parse_rules([parsed("bad.yaml", text)], where="local")
    assert caught.value.details["file"] == "bad.yaml"
    assert caught.value.details["rules"] == "local"


def test_two_rules_with_one_id_in_one_directory_are_refused() -> None:
    with pytest.raises(InvalidInputError):
        parse_rules([parsed("a.yaml", rule_text("same")), parsed("b.yaml", rule_text("same"))], where="curated")


def curated_files() -> list[tuple[str, Any]]:
    directory = importlib.resources.files("energatron").joinpath("offer_rules")
    return [
        parsed(entry.name, entry.read_text(encoding="utf-8"))
        for entry in directory.iterdir()
        if entry.name.endswith(".yaml")
    ]


def test_the_curated_rules_load_cite_snapshots_and_were_verified() -> None:
    rules = load_rules(curated_files(), [])
    assert [found.id for found in rules] == [
        "amber-wholesale",
        "flow-power-variable-estimate",
        "globird-wholesave-estimate",
        "io-energy-first-bill-discount",
    ]
    for found in rules:
        assert found.cites
        assert found.verified_on <= date(2026, 11, 5)
        assert (found.effect.approximate or found.effect.exclude) is not None


def test_the_curated_wholesale_rules_match_as_intended() -> None:
    rules = load_rules(curated_files(), [])
    matched = applied(a_tariff(brand="amber"), *rules, brand="amber").rules
    assert matched == ("amber-wholesale",)
    estimate = "Flow Home Battery + Solar: Variable Prices (estimate)"
    assert applied(a_tariff(), *rules, brand="flow-power", display_name=estimate).rules == (
        "flow-power-variable-estimate",
    )
    assert applied(a_tariff(), *rules, brand="flow-power", display_name="Flow Home").rules == ()
    assert applied(
        a_tariff(), *rules, brand="globird", display_name="WHOLESAVE Resi (Two Rate) estimate-SAPN"
    ).rules == ("globird-wholesave-estimate",)


def test_the_curated_first_bill_rule_excludes_only_its_two_plans() -> None:
    rules = load_rules(curated_files(), [])
    hit = applied(a_tariff(), *rules, brand="io-energy", plan_id="IOR1159318MRE1@EME")
    assert hit.rules == ("io-energy-first-bill-discount",)
    assert isinstance(hit.result, Excluded)
    assert applied(a_tariff(), *rules, brand="io-energy", plan_id="IOR1159319MRE1@EME").rules == ()
