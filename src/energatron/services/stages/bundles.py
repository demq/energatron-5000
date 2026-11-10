"""The `bundles` stage (P6 spec §5.3): electricity and gas costs combined into ranked options, per variation.

Inputs: the two cost marts and the sensitivity mart (annual figures), the two offers marts (each tariff's brand, plan
ID and source) and the local bundle rules beside the curated ones (package data `bundle_rules/`). A manual offer named
only by closed backtest ranges is history and not a candidate (§3.2). Variations: `base` and `conditions_not_met` from
the cost stages, `usage_up`, `usage_down` and `alt_window` from `sensitivity`; a variation unavailable for one fuel uses
that fuel's `base` figures, and one unavailable for every fuel with candidates is skipped. Outputs: `options.parquet`
and `summary.json`. Workspace data, never written to the tool repo.
"""

import json
from collections.abc import Mapping, Sequence
from decimal import Decimal
from pathlib import Path
from typing import Final, cast

import pandas as pd
import pyarrow as pa
import yaml

from energatron.core.bundle_rules import BundleRule, BundleRuleFile, load_bundle_rules
from energatron.core.canonical import JSONValue
from energatron.core.errors import InvalidInputError, SchemaTooNewError
from energatron.core.models.costs import (
    COST_TOTAL_KIND,
    COST_TOTAL_VERSION,
    GAS_COST_TOTAL_KIND,
    GAS_COST_TOTAL_VERSION,
    TOTALS_FILENAME,
)
from energatron.core.models.params import BacktestParams
from energatron.core.models.recommend import (
    BUNDLE_OPTION_KIND,
    BUNDLE_OPTION_SORT,
    BUNDLE_OPTION_VERSION,
    BUNDLE_VARIATIONS,
    BUNDLES_OPTIONS_FILENAME,
    BUNDLES_SUMMARY_FILENAME,
    FUELS,
    SENSITIVITY_SUMMARY_FILENAME,
    SENSITIVITY_TOTAL_KIND,
    SENSITIVITY_TOTAL_VERSION,
    SENSITIVITY_TOTALS_FILENAME,
    BundlesSummary,
    Fuel,
    SensitivitySummary,
)
from energatron.core.ranking import Option, Priced, history, is_candidate, options
from energatron.services.files import write_json, write_parquet
from energatron.services.stages import costing
from energatron.services.stages.runner import StageRun, StageSpec
from energatron.services.stages.tables import (
    BUNDLE_OPTION_SCHEMA,
    COST_TOTAL_SCHEMA,
    GAS_COST_TOTAL_SCHEMA,
    SENSITIVITY_TOTAL_SCHEMA,
    load_table,
)

RULES_PACKAGE_DATA: Final = "bundle_rules"
COST_SCENARIOS: Final = {"base": "conditions_met", "conditions_not_met": "conditions_not_met"}
type _Figures = tuple[Decimal, Decimal]  # annual_ongoing, annual_year1


def _run(run: StageRun) -> None:
    backtest = BacktestParams.model_validate(run.params["backtest"])
    past = history(backtest)
    tariffs = _tariff_facts(run)
    cost: dict[Fuel, dict[tuple[str, str], _Figures]] = {
        "electricity": _totals(
            run.input("elec") / TOTALS_FILENAME, COST_TOTAL_SCHEMA, COST_TOTAL_KIND, COST_TOTAL_VERSION, input="elec"
        ),
        "gas": _totals(
            run.input("gas") / TOTALS_FILENAME,
            GAS_COST_TOTAL_SCHEMA,
            GAS_COST_TOTAL_KIND,
            GAS_COST_TOTAL_VERSION,
            input="gas",
        ),
    }
    sensitivity = _sensitivity(run.input("sensitivity"))
    rules, rules_error = _rules(run)

    excluded: dict[Fuel, int] = {}
    figures: dict[str, dict[Fuel, list[Priced]]] = {}
    available: dict[str, dict[Fuel, bool]] = {}
    for fuel in FUELS:
        facts = tariffs[fuel]
        keep = {snapshot for snapshot in facts if is_candidate(snapshot, facts[snapshot][2], past)}
        excluded[fuel] = len({snapshot for (snapshot, _) in cost[fuel]} - keep)
        for variation in BUNDLE_VARIATIONS:
            if variation in COST_SCENARIOS:
                source = {s: v for (s, scenario), v in cost[fuel].items() if scenario == COST_SCENARIOS[variation]}
                ok = bool(source)
            else:
                source = sensitivity[1].get((fuel, variation), {})
                ok = bool(source) and sensitivity[0].fuels[fuel].variations[variation].available
                if not ok:
                    source = {s: v for (s, scenario), v in cost[fuel].items() if scenario == "conditions_met"}
            figures.setdefault(variation, {})[fuel] = [
                Priced(snapshot, facts[snapshot][0], facts[snapshot][1], ongoing, year1)
                for snapshot, (ongoing, year1) in sorted(source.items())
                if snapshot in keep
            ]
            available.setdefault(variation, {})[fuel] = ok and bool(figures[variation][fuel])

    rows: list[dict[str, object]] = []
    used: list[str] = []
    skipped: list[str] = []
    counts: dict[str, int] = {}
    applied: set[str] = set()
    eligible = True
    adds_up = True
    for variation in BUNDLE_VARIATIONS:
        with_candidates: list[Fuel] = [fuel for fuel in FUELS if figures["base"][fuel]]
        if variation != "base" and not any(available[variation][fuel] for fuel in with_candidates):
            skipped.append(variation)
            continue
        ranked = options(figures[variation]["electricity"], figures[variation]["gas"], rules)
        used.append(variation)
        counts[variation] = len(ranked)
        for rank, option in enumerate(ranked, start=1):
            rows.append(_row(variation, rank, option))
            applied.update(option.rules)
            eligible = eligible and _eligible(option, figures[variation])
            adds_up = adds_up and _adds_up(option)

    output = run.output("bundles")
    write_parquet(
        pd.DataFrame(rows, columns=list(BUNDLE_OPTION_SCHEMA.names)),
        output / BUNDLES_OPTIONS_FILENAME,
        kind=BUNDLE_OPTION_KIND,
        schema_version=BUNDLE_OPTION_VERSION,
        sort_by=BUNDLE_OPTION_SORT,
        schema=BUNDLE_OPTION_SCHEMA,
    )
    checks = {"rules_valid": rules_error is None, "eligibility_applied": eligible, "totals_add_up": adds_up}
    summary = BundlesSummary(
        variations=used,
        skipped=skipped,
        candidates={fuel: len(figures["base"][fuel]) for fuel in FUELS},
        history_excluded=excluded,
        rules=[rule.id for rule in rules],
        rules_applied=sorted(applied),
        options=counts,
        checks=checks,
    )
    write_json(output / BUNDLES_SUMMARY_FILENAME, cast(JSONValue, summary.model_dump(mode="json")))
    run.check("rules_valid", checks["rules_valid"], **(rules_error or {}))
    run.check("eligibility_applied", eligible)
    run.check("totals_add_up", adds_up)


def _row(variation: str, rank: int, option: Option) -> dict[str, object]:
    return {
        "variation": variation,
        "rank": rank,
        "option_key": option.option_key,
        "kind": option.kind,
        "brand": option.brand,
        "elec_snapshot_id": option.elec.offer_snapshot_id if option.elec else None,
        "gas_snapshot_id": option.gas.offer_snapshot_id if option.gas else None,
        "elec_annual_ongoing": option.elec.annual_ongoing if option.elec else None,
        "gas_annual_ongoing": option.gas.annual_ongoing if option.gas else None,
        "adjustment": option.adjustment,
        "annual_ongoing": option.annual_ongoing,
        "annual_year1": option.annual_year1,
        "rules": list(option.rules),
    }


def _eligible(option: Option, candidates: Mapping[Fuel, Sequence[Priced]]) -> bool:
    """A same-brand option's tariffs share a brand, and every tariff of an option is a candidate."""
    if option.kind == "same_brand" and (
        option.elec is None or option.gas is None or option.elec.brand.casefold() != option.gas.brand.casefold()
    ):
        return False
    return (option.elec is None or option.elec in candidates["electricity"]) and (
        option.gas is None or option.gas in candidates["gas"]
    )


def _adds_up(option: Option) -> bool:
    parts = sum((found.annual_ongoing for found in (option.elec, option.gas) if found is not None), Decimal("0.00"))
    return option.annual_ongoing == parts + option.adjustment


# Inputs


def _tariff_facts(run: StageRun) -> dict[Fuel, dict[str, tuple[str, str, str]]]:
    """Per fuel, each tariff's (plan ID, brand, source) by snapshot."""
    elec = costing.load_tariffs(run.input("offers"), input="offers").tariffs
    gas = costing.load_gas_tariffs(run.input("gas_offers"), input="gas_offers").tariffs
    return {
        "electricity": {t.offer_snapshot_id: (t.plan_id, t.brand, t.source) for t in elec},
        "gas": {t.offer_snapshot_id: (t.plan_id, t.brand, t.source) for t in gas},
    }


def _totals(path: Path, schema: pa.Schema, kind: str, version: int, *, input: str) -> dict[tuple[str, str], _Figures]:
    """A cost mart's `(snapshot, scenario)` → `(annual_ongoing, annual_year1)`."""
    rows = load_table(path, schema, kind, version, input=input).to_pylist()
    return {(row["offer_snapshot_id"], row["scenario"]): (row["annual_ongoing"], row["annual_year1"]) for row in rows}


def _sensitivity(directory: Path) -> tuple[SensitivitySummary, dict[tuple[Fuel, str], dict[str, _Figures]]]:
    try:
        summary = SensitivitySummary.from_document(json.loads((directory / SENSITIVITY_SUMMARY_FILENAME).read_bytes()))
    except OSError, ValueError, InvalidInputError, SchemaTooNewError:
        raise InvalidInputError("The sensitivity mart has no valid summary.json.", input="sensitivity") from None
    rows = load_table(
        directory / SENSITIVITY_TOTALS_FILENAME,
        SENSITIVITY_TOTAL_SCHEMA,
        SENSITIVITY_TOTAL_KIND,
        SENSITIVITY_TOTAL_VERSION,
        input="sensitivity",
    ).to_pylist()
    found: dict[tuple[Fuel, str], dict[str, _Figures]] = {}
    for row in rows:
        found.setdefault((row["fuel"], row["variation"]), {})[row["offer_snapshot_id"]] = (
            row["annual_ongoing"],
            row["annual_year1"],
        )
    return summary, found


def _rules(run: StageRun) -> tuple[tuple[BundleRule, ...], dict[str, JSONValue] | None]:
    try:
        return load_bundle_rules(
            _rule_files(run.package_data(RULES_PACKAGE_DATA)), _rule_files(run.input("rules"))
        ), None
    except InvalidInputError as error:
        return (), dict(error.details)


def _rule_files(directory: Path) -> list[BundleRuleFile]:
    """Each `*.yaml` file directly in `directory`, by name, with its parsed document (`None` when not YAML)."""
    files: list[BundleRuleFile] = []
    for path in sorted(directory.glob("*.yaml")):
        try:
            files.append((path.name, cast(JSONValue, yaml.safe_load(path.read_text(encoding="utf-8")))))
        except UnicodeDecodeError, yaml.YAMLError:
            files.append((path.name, None))
    return files


SPEC: Final = StageSpec(
    name="bundles",
    inputs={
        "elec": "data/marts/costs/elec",
        "gas": "data/marts/costs/gas",
        "sensitivity": "data/marts/costs/sensitivity",
        "offers": "data/marts/offers",
        "gas_offers": "data/marts/offers_gas",
        "rules": "config/bundle_rules.local",
    },
    params=("backtest",),
    outputs={"bundles": "data/marts/costs/bundles"},
    impl=_run,
    package_data=(RULES_PACKAGE_DATA,),
)
