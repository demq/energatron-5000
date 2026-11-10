"""The `recommend` stage (P6 spec §6, §7): the recommendation document, its content-derived ID and its page.

Inputs: the bundles mart (ranked options per variation), the two cost marts (each candidate's base figures and the usage
window they priced), the two offers marts (tariff details, `as_of`), the catalog mart (to resolve cited dataset IDs),
the bundle rules (for the current pair) and the private config (only `current_plans`, public snapshot IDs, leaves it).
Outputs: `recommendation.json` (kind `recommendation`) and `index.html`. The ID hashes the document without its ID and
checks, so the same inputs, params and code give the same ID (D1 §6.3). Workspace data, never written to the tool repo.
"""

import json
import tomllib
from collections.abc import Mapping, Sequence
from decimal import Decimal
from pathlib import Path
from typing import Final, cast

import pyarrow.parquet as pq
import yaml

from energatron.core.bundle_rules import BundleRule, BundleRuleFile, load_bundle_rules
from energatron.core.canonical import JSONValue, content_hash
from energatron.core.errors import InvalidInputError, SchemaTooNewError, WarningCode
from energatron.core.gas_tariff import GasTariff
from energatron.core.ids import rec_id
from energatron.core.models.costs import (
    COST_SUMMARY_FILENAME,
    COST_TOTAL_KIND,
    COST_TOTAL_VERSION,
    GAS_COST_TOTAL_KIND,
    GAS_COST_TOTAL_VERSION,
    TOTALS_FILENAME,
    CostSummary,
    GasCostSummary,
)
from energatron.core.models.params import BacktestParams, RecommendParams
from energatron.core.models.private import PrivateConfig
from energatron.core.models.provenance import PROVENANCE_FILENAME, StageProvenance
from energatron.core.models.recommend import (
    BUNDLE_OPTION_KIND,
    BUNDLE_OPTION_VERSION,
    BUNDLES_OPTIONS_FILENAME,
    FUELS,
    RECOMMENDATION_FILENAME,
    RECOMMENDATION_PAGE,
    CurrentPair,
    CurrentPlan,
    CurrentSource,
    ElectricityUsage,
    Fuel,
    GasUsage,
    PricedTariff,
    Recommendation,
    RecommendationInputs,
    RecommendationUsage,
    RecommendedOption,
    SensitivityVerdict,
)
from energatron.core.ranking import (
    Option,
    OptionKind,
    Priced,
    action,
    current_from_backtest,
    history,
    is_candidate,
    pair,
    recheck_by_date,
)
from energatron.core.ranking import verdicts as rank_verdicts
from energatron.core.recommendation_report import render
from energatron.core.tariff import Tariff
from energatron.services.files import write_json
from energatron.services.stages import costing
from energatron.services.stages.runner import StageRun, StageSpec
from energatron.services.stages.tables import BUNDLE_OPTION_SCHEMA, COST_TOTAL_SCHEMA, GAS_COST_TOTAL_SCHEMA, load_table

RULES_PACKAGE_DATA: Final = "bundle_rules"
CATALOG_TABLE: Final = "datasets.parquet"
BASE_SCENARIO: Final = "conditions_met"
type _AnyTariff = Tariff | GasTariff


def _run(run: StageRun) -> None:
    params = RecommendParams.model_validate(run.params["recommend"])
    backtest = BacktestParams.model_validate(run.params["backtest"])
    elec_mart = costing.load_tariffs(run.input("offers"), input="offers")
    gas_mart = costing.load_gas_tariffs(run.input("gas_offers"), input="gas_offers")
    tariffs: dict[Fuel, dict[str, _AnyTariff]] = {
        "electricity": {tariff.offer_snapshot_id: tariff for tariff in elec_mart.tariffs},
        "gas": {tariff.offer_snapshot_id: tariff for tariff in gas_mart.tariffs},
    }
    as_of = max((found for found in (elec_mart.as_of, gas_mart.as_of) if found is not None), default=None)
    figures: dict[Fuel, dict[str, tuple[Decimal, Decimal]]] = {
        "electricity": _base_figures(run.input("elec"), COST_TOTAL_KIND, COST_TOTAL_VERSION, elec=True),
        "gas": _base_figures(run.input("gas"), GAS_COST_TOTAL_KIND, GAS_COST_TOTAL_VERSION, elec=False),
    }
    past = history(backtest)
    priced: dict[Fuel, list[PricedTariff]] = {fuel: _priced(tariffs[fuel], figures[fuel], past) for fuel in FUELS}
    by_snapshot: dict[Fuel, dict[str, PricedTariff]] = {
        fuel: {found.offer_snapshot_id: found for found in priced[fuel]} for fuel in FUELS
    }
    ranked = _bundle_options(run.input("bundles"))
    rules = _rules(run)

    current, current_option, missing = _current(run, backtest, tariffs, figures, rules)
    needs_current = any(priced[fuel] and getattr(current, fuel) is None for fuel in FUELS)
    for fuel in missing:
        run.warn(WarningCode.W_REC_CURRENT_NOT_PRICED, f"The current {fuel} plan named isn't priced.", fuel=fuel)
    for fuel in FUELS:
        if priced[fuel] and getattr(current, fuel) is None:
            run.warn(WarningCode.W_REC_NO_CURRENT_PLAN, f"There is no current {fuel} plan to compare with.", fuel=fuel)

    base = ranked.get("base", [])
    options = tuple(_option(row, by_snapshot, current) for row in base[: params.top])
    top_key = cast(str, base[0]["option_key"]) if base else None
    variations = {
        name: [cast(str, row["option_key"]) for row in rows] for name, rows in ranked.items() if name != "base"
    }
    verdicts = tuple(
        SensitivityVerdict(
            variation=found.variation,
            top_option_key=found.top_option_key,
            base_top_rank=found.base_top_rank,
            holds=found.holds,
        )
        for found in rank_verdicts(top_key, variations)
    )
    top_option = _row_option(base[0], by_snapshot) if base else None
    chosen, _ = action(top_option, current_option, needs_current=bool(needs_current), min_saving=params.min_saving)

    dataset_ids = _cited_datasets(run)
    snapshots = sorted(
        {found.offer_snapshot_id for fuel in FUELS for found in priced[fuel]}
        | {plan.offer_snapshot_id for plan in (current.electricity, current.gas) if plan is not None}
    )
    document = Recommendation(
        recommendation_id=None,
        as_of=as_of,
        recheck_by=None
        if as_of is None
        else recheck_by_date(as_of, recheck_days=params.recheck_days, price_reset=params.price_reset),
        action=chosen,
        robust=all(found.holds for found in verdicts),
        usage=_usage(run),
        current=current,
        options=options,
        sensitivity=verdicts,
        priced={fuel: tuple(priced[fuel]) for fuel in FUELS},
        inputs=RecommendationInputs(
            dataset_ids=tuple(dataset_ids),
            offer_snapshot_ids=tuple(snapshots),
            params_sha256=content_hash(dict(run.params)),
            stage_sha256=run.stage_sha256,
        ),
        checks={},
    )
    identified = (
        document
        if as_of is None
        else document.model_copy(update={"recommendation_id": rec_id(as_of, document.payload())})
    )
    resolved = _ids_resolve(identified, tariffs, dataset_ids, run.input("catalog"))
    candidates = any(priced[fuel] for fuel in FUELS)  # with none (no offers yet) there is nothing to recommend
    checks = {
        "as_of_known": as_of is not None or not candidates,
        "options_present": bool(options) or not candidates,
        "ids_resolve": resolved,
        "id_recomputes": _recomputes(identified) if as_of is not None else not candidates,
    }
    final = identified.model_copy(update={"checks": checks})
    output = run.output("recommendation")
    write_json(output / RECOMMENDATION_FILENAME, cast(JSONValue, final.model_dump(mode="json")))
    (output / RECOMMENDATION_PAGE).write_text(render(final), encoding="utf-8")
    for name, passed in checks.items():
        run.check(name, passed)


# The candidates and options


def _base_figures(directory: Path, kind: str, version: int, *, elec: bool) -> dict[str, tuple[Decimal, Decimal]]:
    schema = COST_TOTAL_SCHEMA if elec else GAS_COST_TOTAL_SCHEMA
    rows = load_table(directory / TOTALS_FILENAME, schema, kind, version, input="elec" if elec else "gas").to_pylist()
    return {
        row["offer_snapshot_id"]: (row["annual_ongoing"], row["annual_year1"])
        for row in rows
        if row["scenario"] == BASE_SCENARIO
    }


def _priced(
    tariffs: Mapping[str, _AnyTariff], figures: Mapping[str, tuple[Decimal, Decimal]], past: frozenset[str]
) -> list[PricedTariff]:
    """Every candidate with its base figures, ranked among the candidates (ties: the smaller snapshot ID)."""
    found = [
        (snapshot, figures[snapshot])
        for snapshot, tariff in tariffs.items()
        if snapshot in figures and is_candidate(snapshot, tariff.source, past)
    ]
    found.sort(key=lambda item: (item[1][0], item[0]))
    return [
        _priced_tariff(tariffs[snapshot], ongoing, year1, rank)
        for rank, (snapshot, (ongoing, year1)) in enumerate(found, 1)
    ]


def _priced_tariff(tariff: _AnyTariff, ongoing: Decimal, year1: Decimal, rank: int) -> PricedTariff:
    return PricedTariff(
        offer_snapshot_id=tariff.offer_snapshot_id,
        plan_id=tariff.plan_id,
        brand=tariff.brand,
        display_name=tariff.display_name,
        source=tariff.source,
        status=tariff.status,
        approximations=tuple(tariff.approximations),
        notes=tuple(tariff.notes),
        annual_ongoing=ongoing,
        annual_year1=year1,
        rank=rank,
    )


def _bundle_options(directory: Path) -> dict[str, list[dict[str, object]]]:
    """The bundles mart's options by variation, in rank order."""
    rows = load_table(
        directory / BUNDLES_OPTIONS_FILENAME,
        BUNDLE_OPTION_SCHEMA,
        BUNDLE_OPTION_KIND,
        BUNDLE_OPTION_VERSION,
        input="bundles",
    ).to_pylist()
    found: dict[str, list[dict[str, object]]] = {}
    for row in sorted(rows, key=lambda row: (row["variation"], row["rank"])):
        found.setdefault(cast(str, row["variation"]), []).append(row)
    return found


def _as_priced(found: PricedTariff) -> Priced:
    return Priced(found.offer_snapshot_id, found.plan_id, found.brand, found.annual_ongoing, found.annual_year1)


def _tariffs_of(
    row: Mapping[str, object], by_snapshot: Mapping[Fuel, Mapping[str, PricedTariff]]
) -> tuple[PricedTariff | None, PricedTariff | None]:
    elec, gas = row["elec_snapshot_id"], row["gas_snapshot_id"]
    return (
        by_snapshot["electricity"].get(cast(str, elec)) if elec else None,
        by_snapshot["gas"].get(cast(str, gas)) if gas else None,
    )


def _row_option(row: Mapping[str, object], by_snapshot: Mapping[Fuel, Mapping[str, PricedTariff]]) -> Option:
    """A bundles row as a ranking option, for the action."""
    elec, gas = _tariffs_of(row, by_snapshot)
    return Option(
        option_key=cast(str, row["option_key"]),
        kind=cast(OptionKind, row["kind"]),
        brand=cast(str | None, row["brand"]),
        elec=_as_priced(elec) if elec else None,
        gas=_as_priced(gas) if gas else None,
        adjustment=cast(Decimal, row["adjustment"]),
        rules=tuple(cast(list[str], row["rules"])),
        annual_ongoing=cast(Decimal, row["annual_ongoing"]),
        annual_year1=cast(Decimal, row["annual_year1"]),
    )


def _option(
    row: Mapping[str, object], by_snapshot: Mapping[Fuel, Mapping[str, PricedTariff]], current: CurrentPair
) -> RecommendedOption:
    elec, gas = _tariffs_of(row, by_snapshot)
    ongoing, year1 = cast(Decimal, row["annual_ongoing"]), cast(Decimal, row["annual_year1"])
    covered = (elec is None or current.electricity is not None) and (gas is None or current.gas is not None)
    comparable = covered and current.annual_ongoing is not None and current.annual_year1 is not None
    return RecommendedOption(
        rank=cast(int, row["rank"]),
        option_key=cast(str, row["option_key"]),
        kind=cast(OptionKind, row["kind"]),
        brand=cast(str | None, row["brand"]),
        electricity=elec,
        gas=gas,
        adjustment=cast(Decimal, row["adjustment"]),
        rules=tuple(cast(list[str], row["rules"])),
        annual_ongoing=ongoing,
        annual_year1=year1,
        saving_ongoing=cast(Decimal, current.annual_ongoing) - ongoing if comparable else None,
        saving_year1=cast(Decimal, current.annual_year1) - year1 if comparable else None,
    )


# The current plan (§6.3)


def _private_config(path: Path) -> PrivateConfig:
    """The private config, parsed; only `current_plans` is used. Nothing of it reaches an error message."""
    try:
        return PrivateConfig.from_document(tomllib.loads(path.read_text(encoding="utf-8")))
    except OSError, UnicodeDecodeError, tomllib.TOMLDecodeError, InvalidInputError, SchemaTooNewError:
        raise InvalidInputError("The private config can't be read.", input="private_config") from None


def _current(
    run: StageRun,
    backtest: BacktestParams,
    tariffs: Mapping[Fuel, Mapping[str, _AnyTariff]],
    figures: Mapping[Fuel, Mapping[str, tuple[Decimal, Decimal]]],
    rules: Sequence[BundleRule],
) -> tuple[CurrentPair, Option | None, list[Fuel]]:
    """The current plan per fuel (private config, else the open backtest range), the current pair as an option, and
    the fuels whose named plan isn't priced."""
    configured = _private_config(run.input("private_config")).current_plans
    open_elec, open_gas = current_from_backtest(backtest)
    named: dict[Fuel, tuple[str | None, str | None]] = {
        "electricity": (configured.electricity, open_elec),
        "gas": (configured.gas, open_gas),
    }
    plans: dict[Fuel, CurrentPlan | None] = {}
    parts: dict[Fuel, Priced | None] = {}
    missing: list[Fuel] = []
    for fuel in FUELS:
        from_config, from_backtest = named[fuel]
        snapshot = from_config or from_backtest
        source: CurrentSource = "private_config" if from_config else "backtest"
        plans[fuel], parts[fuel] = None, None
        if snapshot is None:
            continue
        if snapshot not in figures[fuel] or snapshot not in tariffs[fuel]:
            missing.append(fuel)
            continue
        ongoing, year1 = figures[fuel][snapshot]
        tariff = tariffs[fuel][snapshot]
        plans[fuel] = CurrentPlan(offer_snapshot_id=snapshot, source=source, annual_ongoing=ongoing, annual_year1=year1)
        parts[fuel] = Priced(snapshot, tariff.plan_id, tariff.brand, ongoing, year1)
    any_plan = parts["electricity"] is not None or parts["gas"] is not None
    option = pair(parts["electricity"], parts["gas"], rules) if any_plan else None
    current = CurrentPair(
        electricity=plans["electricity"],
        gas=plans["gas"],
        adjustment=option.adjustment if option else Decimal("0.00"),
        rules=option.rules if option else (),
        annual_ongoing=option.annual_ongoing if option else None,
        annual_year1=option.annual_year1 if option else None,
    )
    return current, option, missing


# Usage, citations and checks


def _usage(run: StageRun) -> RecommendationUsage:
    try:
        elec = CostSummary.from_document(json.loads((run.input("elec") / COST_SUMMARY_FILENAME).read_bytes()))
        gas = GasCostSummary.from_document(json.loads((run.input("gas") / COST_SUMMARY_FILENAME).read_bytes()))
    except OSError, ValueError, InvalidInputError, SchemaTooNewError:
        raise InvalidInputError("A cost mart has no valid summary.json.", input="elec") from None
    return RecommendationUsage(
        electricity=ElectricityUsage(window_start=elec.window_start, window_end=elec.window_end)
        if elec.tariffs
        else None,
        gas=GasUsage(
            reference_start=gas.reference_start,
            reference_end=gas.reference_end,
            reference_days=gas.reference_days,
            partial_year=gas.partial_year,
        )
        if gas.tariffs
        else None,
    )


_CITING: Final = ("bundles", "elec", "gas", "offers", "gas_offers")


def _cited_datasets(run: StageRun) -> list[str]:
    """The union of the dataset IDs the inputs' provenance names (D1 §10), sorted; the catalog mart is only for
    resolving them, so it isn't cited."""
    found: set[str] = set()
    for name in _CITING:
        try:
            provenance = StageProvenance.from_document(json.loads((run.input(name) / PROVENANCE_FILENAME).read_bytes()))
        except OSError, ValueError, InvalidInputError, SchemaTooNewError:
            raise InvalidInputError(f"An input's {PROVENANCE_FILENAME} is invalid.", input=name) from None
        found.update(dataset for item in provenance.inputs for dataset in item.dataset_ids)
    return sorted(found)


def _ids_resolve(
    document: Recommendation,
    tariffs: Mapping[Fuel, Mapping[str, _AnyTariff]],
    dataset_ids: Sequence[str],
    catalog: Path,
) -> bool:
    cited: dict[Fuel, set[str]] = {fuel: set() for fuel in FUELS}
    for fuel in FUELS:
        cited[fuel].update(found.offer_snapshot_id for found in document.priced[fuel])
        plan = getattr(document.current, fuel)
        if plan is not None:
            cited[fuel].add(plan.offer_snapshot_id)
    for option in document.options:
        if option.electricity is not None:
            cited["electricity"].add(option.electricity.offer_snapshot_id)
        if option.gas is not None:
            cited["gas"].add(option.gas.offer_snapshot_id)
    if any(cited[fuel] - set(tariffs[fuel]) for fuel in FUELS):
        return False
    try:
        known = set(pq.read_table(catalog / CATALOG_TABLE, columns=["dataset_id"]).column("dataset_id").to_pylist())
    except OSError, ValueError, KeyError:
        return False
    return set(dataset_ids) <= known


def _recomputes(document: Recommendation) -> bool:
    """The document as written, read back, gives its own ID."""
    if document.recommendation_id is None or document.as_of is None:
        return False
    written = json.loads(json.dumps(document.model_dump(mode="json")))
    read = Recommendation.from_document(written)
    return read.as_of is not None and rec_id(read.as_of, read.payload()) == document.recommendation_id


def _rules(run: StageRun) -> tuple[BundleRule, ...]:
    """The bundle rules, for the current pair; `bundles` already failed on invalid ones, so here they must load."""
    return load_bundle_rules(_rule_files(run.package_data(RULES_PACKAGE_DATA)), _rule_files(run.input("rules")))


def _rule_files(directory: Path) -> list[BundleRuleFile]:
    files: list[BundleRuleFile] = []
    for path in sorted(directory.glob("*.yaml")):
        try:
            files.append((path.name, cast(JSONValue, yaml.safe_load(path.read_text(encoding="utf-8")))))
        except UnicodeDecodeError, yaml.YAMLError:
            files.append((path.name, None))
    return files


SPEC: Final = StageSpec(
    name="recommend",
    inputs={
        "bundles": "data/marts/costs/bundles",
        "elec": "data/marts/costs/elec",
        "gas": "data/marts/costs/gas",
        "offers": "data/marts/offers",
        "gas_offers": "data/marts/offers_gas",
        "catalog": "data/marts/catalog",
        "rules": "config/bundle_rules.local",
        "private_config": "private/config.toml",
    },
    params=("recommend", "backtest"),
    outputs={"recommendation": "data/reports/recommendations"},
    impl=_run,
    package_data=(RULES_PACKAGE_DATA,),
)
