"""`core.ranking` (P6 spec §3.2, §5.2, §6.2): candidates and history, options, rule effects, de-duplication, ties, the
action and recheck date, and the sensitivity verdicts. Synthetic brands, plans and snapshot IDs only."""

from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from energatron.core.bundle_rules import BundleRule
from energatron.core.models.params import BacktestParams
from energatron.core.ranking import (
    Priced,
    action,
    current_from_backtest,
    history,
    is_candidate,
    option_key,
    options,
    pair,
    recheck_by_date,
    verdicts,
)


def snap(name: str) -> str:
    return f"{name}~20261101T000000Z~0123abcd"


def priced(name: str, brand: str, ongoing: str, year1: str | None = None) -> Priced:
    return Priced(snap(name), name, brand, Decimal(ongoing), Decimal(year1 or ongoing))


def rule(**changes: Any) -> BundleRule:
    doc: dict[str, Any] = {
        "kind": "bundle_rule",
        "schema_version": 1,
        "id": "dual",
        "description": "Synthetic.",
        "brand": "Alpha",
        "effect": {"annual_credit": "30"},
        "verified_on": "2026-11-10",
        "cites": [],
    }
    return BundleRule.from_document(doc | changes)


def backtest(tariffs: list[dict[str, Any]], gas: list[dict[str, Any]] | None = None) -> BacktestParams:
    return BacktestParams.model_validate({"tariffs": tariffs, "gas_tariffs": gas or []})


# Candidates and the current plan (§3.2, §6.3)


def test_history_is_what_only_closed_ranges_name() -> None:
    found = backtest(
        [
            {"offer": snap("OLD"), "from": "2024-01-01", "to": "2024-12-31"},
            {"offer": snap("NOW"), "from": "2025-01-01", "to": "2025-06-30"},
            {"offer": snap("NOW"), "from": "2025-07-01", "to": None},
        ],
        [{"offer": snap("GASOLD"), "from": "2024-01-01", "to": "2024-12-31"}],
    )
    assert history(found) == {snap("OLD"), snap("GASOLD")}
    assert current_from_backtest(found) == (snap("NOW"), None)
    assert not is_candidate(snap("OLD"), "manual", history(found))
    assert is_candidate(snap("OLD"), "cdr", history(found))  # a CDR plan in the list is still on offer
    assert is_candidate(snap("NOW"), "manual", history(found))


# Pairs and rules (§5.2)


def test_a_same_brand_pair_gets_its_matching_rules_from_the_pre_adjustment_base() -> None:
    elec, gas = priced("E1", "alpha", "1000.00", "900.00"), priced("G1", "ALPHA", "500.00")
    found = pair(elec, gas, [rule(id="b-percent", effect={"percent_off": "10"}), rule(id="a-credit")])
    assert found.kind == "same_brand"
    assert found.brand == "alpha"
    assert found.rules == ("a-credit", "b-percent")
    assert found.adjustment == Decimal("-180.00")  # 30 + 10% of 1500, both from the base
    assert found.annual_ongoing == Decimal("1320.00")
    assert found.annual_year1 == Decimal("1220.00")
    assert found.option_key == f"{snap('E1')}|{snap('G1')}"


def test_rules_need_the_plan_patterns_and_never_apply_across_brands() -> None:
    elec, gas = priced("E1", "alpha", "100.00"), priced("G1", "alpha", "50.00")
    assert pair(elec, gas, [rule(electricity_plan="E2")]).rules == ()
    other = pair(elec, priced("G2", "beta", "50.00"), [rule()])
    assert (other.kind, other.brand, other.rules, other.adjustment) == ("independent", None, (), Decimal("0.00"))


def test_a_single_fuel_pair_is_that_tariff() -> None:
    found = pair(priced("E1", "alpha", "100.00"), None, [rule()])
    assert (found.kind, found.annual_ongoing, found.option_key) == ("independent", Decimal("100.00"), f"{snap('E1')}|-")
    assert option_key(None, None) == "-|-"


def test_options_are_the_independent_pair_and_each_brands_cheapest_pair_ranked() -> None:
    elec = [priced("EA1", "alpha", "1000.00"), priced("EA2", "alpha", "1100.00"), priced("EB1", "beta", "900.00")]
    gas = [priced("GA1", "alpha", "500.00"), priced("GC1", "gamma", "450.00")]
    found = options(elec, gas, [rule(electricity_plan="EA2", effect={"annual_credit": "200"})])
    assert [(option.kind, option.option_key, option.annual_ongoing) for option in found] == [
        ("independent", f"{snap('EB1')}|{snap('GC1')}", Decimal("1350.00")),
        ("same_brand", f"{snap('EA2')}|{snap('GA1')}", Decimal("1400.00")),  # the rule makes EA2 alpha's best
    ]


def test_an_independent_pair_of_one_brand_keeps_the_same_brand_form() -> None:
    elec, gas = [priced("EA1", "alpha", "100.00")], [priced("GA1", "alpha", "50.00")]
    found = options(elec, gas, [rule()])
    assert [(option.kind, option.rules, option.annual_ongoing) for option in found] == [
        ("same_brand", ("dual",), Decimal("120.00"))
    ]


def test_ties_go_to_the_smaller_snapshot_and_option_key() -> None:
    elec = [priced("E2", "alpha", "100.00"), priced("E1", "beta", "100.00")]
    found = options(elec, [], [])
    assert [option.option_key for option in found] == [f"{snap('E1')}|-"]
    assert options([], [], []) == []


# The action, the recheck date and the verdicts (§6.2)


@pytest.mark.parametrize(
    ("top", "current", "needs", "expected"),
    [
        ("100.00", None, True, ("no_current_plan", None)),
        ("100.00", "100.00", False, ("stay", Decimal("0.00"))),
        ("100.00", "120.00", False, ("stay", Decimal("20.00"))),
        ("100.00", "200.00", False, ("switch", Decimal("100.00"))),
    ],
)
def test_the_action(top: str, current: str | None, needs: bool, expected: tuple[str, Decimal | None]) -> None:
    best = pair(priced("TOP", "alpha", top), None, [])
    now = pair(priced("NOW", "beta", current), None, []) if current is not None else None
    assert action(best, now, needs_current=needs, min_saving=Decimal("50")) == expected


def test_the_top_option_being_the_current_pair_is_stay() -> None:
    best = pair(priced("SAME", "alpha", "100.00"), None, [])
    assert action(best, best, needs_current=True, min_saving=Decimal("0")) == ("stay", Decimal("0.00"))


@pytest.mark.parametrize(
    ("as_of", "days", "reset", "expected"),
    [
        (date(2026, 11, 10), 90, "07-01", date(2027, 2, 8)),
        (date(2026, 5, 1), 90, "07-01", date(2026, 7, 1)),
        (date(2026, 7, 1), 400, "07-01", date(2027, 7, 1)),  # the reset on as_of itself is past
        (date(2026, 1, 1), 365, "02-29", date(2026, 3, 1)),  # no 29 February in 2026
    ],
)
def test_recheck_by(as_of: date, days: int, reset: str, expected: date) -> None:
    assert recheck_by_date(as_of, recheck_days=days, price_reset=reset) == expected


def test_verdicts_say_where_the_base_top_option_ranks() -> None:
    found = verdicts("a", {"usage_up": ["a", "b"], "usage_down": ["b", "a"], "alt_window": ["c"]})
    assert [(v.variation, v.top_option_key, v.base_top_rank, v.holds) for v in found] == [
        ("usage_up", "a", 1, True),
        ("usage_down", "b", 2, False),
        ("alt_window", "c", None, False),
    ]
