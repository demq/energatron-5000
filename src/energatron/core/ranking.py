"""Ranking a household's options (P6 spec §3.2, §5.2, §6.2): candidates, bundles of an electricity and a gas tariff,
the current plan, the action and the sensitivity verdicts.

Pure: the stages read the cost tables, offer marts, params and rules and pass plain values in. Money is `Decimal`
dollars with two places, GST-inclusive like every annual figure.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Final, Literal

from energatron.core.bundle_rules import BundleRule
from energatron.core.models.params import BacktestParams

type OptionKind = Literal["independent", "same_brand"]
type Action = Literal["switch", "stay", "no_current_plan"]
NONE_KEY: Final = "-"


@dataclass(frozen=True)
class Priced:
    """One tariff's annual figures in one variation, with what bundles need to know about it."""

    offer_snapshot_id: str
    plan_id: str
    brand: str
    annual_ongoing: Decimal
    annual_year1: Decimal


@dataclass(frozen=True)
class Option:
    """A pair of an electricity and a gas tariff (either may be absent), with its bundle adjustment."""

    option_key: str
    kind: OptionKind
    brand: str | None
    elec: Priced | None
    gas: Priced | None
    adjustment: Decimal
    rules: tuple[str, ...]
    annual_ongoing: Decimal
    annual_year1: Decimal


# Candidates (§3.2)


def history(backtest: BacktestParams) -> frozenset[str]:
    """The snapshots named only by closed backtest ranges (both fuels): the tariffs of the past."""
    ranges = (*backtest.tariffs, *backtest.gas_tariffs)
    closed = {found.offer for found in ranges if found.end is not None}
    open_ = {found.offer for found in ranges if found.end is None}
    return frozenset(closed - open_)


def is_candidate(snapshot: str, source: str, past: frozenset[str]) -> bool:
    """Every priced tariff but a manual offer that is history (§3.2)."""
    return not (source == "manual" and snapshot in past)


def current_from_backtest(backtest: BacktestParams) -> tuple[str | None, str | None]:
    """The snapshots of the open electricity and gas backtest ranges: the plans in force (§6.3)."""
    elec = next((found.offer for found in backtest.tariffs if found.end is None), None)
    gas = next((found.offer for found in backtest.gas_tariffs if found.end is None), None)
    return elec, gas


# Options (§5.2)


def option_key(elec: Priced | None, gas: Priced | None) -> str:
    return f"{elec.offer_snapshot_id if elec else NONE_KEY}|{gas.offer_snapshot_id if gas else NONE_KEY}"


def pair(elec: Priced | None, gas: Priced | None, rules: Sequence[BundleRule]) -> Option:
    """The pair's combined cost with every matching rule applied, in `id` order, each on the pre-adjustment base."""
    parts = [found for found in (elec, gas) if found is not None]
    base = sum((found.annual_ongoing for found in parts), Decimal("0.00"))
    base_year1 = sum((found.annual_year1 for found in parts), Decimal("0.00"))
    same_brand = elec is not None and gas is not None and elec.brand.casefold() == gas.brand.casefold()
    applied: list[str] = []
    adjustment = Decimal("0.00")
    if same_brand:
        assert elec is not None and gas is not None
        for rule in sorted(rules, key=lambda found: found.id):
            if rule.matches(elec_brand=elec.brand, elec_plan=elec.plan_id, gas_brand=gas.brand, gas_plan=gas.plan_id):
                adjustment += rule.effect.adjustment(base)
                applied.append(rule.id)
    return Option(
        option_key=option_key(elec, gas),
        kind="same_brand" if same_brand else "independent",
        brand=elec.brand if same_brand and elec is not None else None,
        elec=elec,
        gas=gas,
        adjustment=adjustment,
        rules=tuple(applied),
        annual_ongoing=base + adjustment,
        annual_year1=base_year1 + adjustment,
    )


def _cheapest(priced: Iterable[Priced]) -> Priced | None:
    return min(priced, key=lambda found: (found.annual_ongoing, found.offer_snapshot_id), default=None)


def options(elec: Sequence[Priced], gas: Sequence[Priced], rules: Sequence[BundleRule]) -> list[Option]:
    """The ranked options of one variation (§5.2): the independent pair, and per brand with both fuels its cheapest
    pair with the rules applied; de-duplicated by pair and ranked by `annual_ongoing`, then `option_key`."""
    found: dict[str, Option] = {}
    if elec or gas:
        independent = pair(_cheapest(elec), _cheapest(gas), rules)
        found[independent.option_key] = independent
    for brand in sorted({tariff.brand.casefold() for tariff in elec} & {tariff.brand.casefold() for tariff in gas}):
        pairs = [
            pair(e, g, rules) for e in elec if e.brand.casefold() == brand for g in gas if g.brand.casefold() == brand
        ]
        best = min(
            pairs,
            key=lambda option: (
                option.annual_ongoing,
                option.elec.offer_snapshot_id if option.elec else "",
                option.gas.offer_snapshot_id if option.gas else "",
            ),
        )
        found[best.option_key] = best  # a same-brand pair replaces an equal independent one: same pair, same rules
    return sorted(found.values(), key=lambda option: (option.annual_ongoing, option.option_key))


# The recommendation (§6.2)


def action(
    top: Option | None, current: Option | None, *, needs_current: bool, min_saving: Decimal
) -> tuple[Action, Decimal | None]:
    """What to do, and the top option's ongoing saving against the current pair (`None` without one)."""
    if needs_current and current is None:
        return "no_current_plan", None
    if top is None or current is None:
        return "stay", None
    saving = current.annual_ongoing - top.annual_ongoing
    if top.option_key == current.option_key or saving < min_saving:
        return "stay", saving
    return "switch", saving


def recheck_by_date(as_of: date, *, recheck_days: int, price_reset: str) -> date:
    """The earlier of `as_of + recheck_days` and the first `price_reset` (`MM-DD`) after `as_of`."""
    month, day = int(price_reset[:2]), int(price_reset[3:])
    year = as_of.year
    while True:
        try:
            reset = date(year, month, day)
        except ValueError:  # 02-29 in a year that has none: the next day
            reset = date(year, 3, 1)
        if reset > as_of:
            break
        year += 1
    return min(as_of + timedelta(days=recheck_days), reset)


@dataclass(frozen=True)
class Verdict:
    variation: str
    top_option_key: str | None
    base_top_rank: int | None
    holds: bool


def verdicts(base_top: str | None, ranked: Mapping[str, Sequence[str]]) -> list[Verdict]:
    """For each variation (other than `base`) its rank-1 option, the base top option's rank there, and whether it
    holds (is rank 1); `ranked` maps a variation to its option keys in rank order."""
    found: list[Verdict] = []
    for variation, keys in ranked.items():
        rank = keys.index(base_top) + 1 if base_top is not None and base_top in keys else None
        found.append(Verdict(variation, keys[0] if keys else None, rank, rank == 1))
    return found
