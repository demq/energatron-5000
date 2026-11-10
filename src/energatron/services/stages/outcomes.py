"""The `outcomes` stage (P6 spec §10): bills after a switch against what the recommendation projected.

Inputs: the decisions (`data/decisions/DEC-*.json`; other files there, such as the readme, are ignored), the electricity
backtest's bill periods (those with a matched invoice) and the gas bills. Each fuel's bills are attributed to the latest
decision for that fuel whose switch date is on or before the bill's first day; the projection is the chosen plan's
`annual_ongoing` x days / 365. No params. Outputs: `outcomes.parquet` and `summary.json`; with no decision both are
empty and the stage passes. Workspace data, never written to the tool repo.
"""

import json
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Final, cast

import pandas as pd

from energatron.core.canonical import JSONValue
from energatron.core.engine import money
from energatron.core.errors import InvalidInputError, SchemaTooNewError
from energatron.core.ids import parse_decision_id
from energatron.core.models.costs import BILL_KIND, BILL_VERSION, BILLS_FILENAME
from energatron.core.models.gas import GAS_BILL_KIND, GAS_BILL_VERSION, GAS_BILLS_FILENAME
from energatron.core.models.recommend import (
    FUELS,
    OUTCOME_BILL_KIND,
    OUTCOME_BILL_SORT,
    OUTCOME_BILL_VERSION,
    OUTCOMES_FILENAME,
    OUTCOMES_SUMMARY_FILENAME,
    Decision,
    Fuel,
    FuelOutcome,
    OutcomesSummary,
)
from energatron.services.files import write_json, write_parquet
from energatron.services.stages.runner import StageRun, StageSpec
from energatron.services.stages.tables import BILL_SCHEMA, GAS_BILL_SCHEMA, OUTCOME_BILL_SCHEMA, load_table

DAYS_PER_YEAR: Final = Decimal(365)
type _Bill = tuple[date, date, Decimal]  # first day, last day, amount billed


def _run(run: StageRun) -> None:
    decisions, valid = _decisions(run.input("decisions"))
    bills: dict[Fuel, list[_Bill]] = {
        "electricity": _elec_bills(run.input("backtest")),
        "gas": _gas_bills(run.input("gas_bills")),
    }
    rows: list[dict[str, object]] = []
    for fuel in FUELS:
        chosen = sorted(
            (decision for decision in decisions if decision.choices.get(fuel) is not None),
            key=lambda decision: (decision.switch_date, decision.decision_id),
        )
        for first, last, actual in bills[fuel]:
            owner = _owner(chosen, first)
            if owner is None:
                continue
            choice = owner.choices[fuel]
            assert choice is not None
            days = (last - first).days + 1
            projected = money(choice.annual_ongoing * days / DAYS_PER_YEAR)
            diff = actual - projected
            rows.append(
                {
                    "decision_id": owner.decision_id,
                    "fuel": fuel,
                    "period_start": first,
                    "period_end": last,
                    "days": days,
                    "actual": actual,
                    "projected": projected,
                    "diff": diff,
                    "diff_pct": float(diff / projected * 100) if projected else None,
                }
            )
    output = run.output("outcomes")
    write_parquet(
        pd.DataFrame(rows, columns=list(OUTCOME_BILL_SCHEMA.names)),
        output / OUTCOMES_FILENAME,
        kind=OUTCOME_BILL_KIND,
        schema_version=OUTCOME_BILL_VERSION,
        sort_by=OUTCOME_BILL_SORT,
        schema=OUTCOME_BILL_SCHEMA,
    )
    summary = OutcomesSummary(
        decisions=sorted(decision.decision_id for decision in decisions),
        outcomes=_totals(rows),
        checks={"decisions_valid": valid},
    )
    write_json(output / OUTCOMES_SUMMARY_FILENAME, cast(JSONValue, summary.model_dump(mode="json")))
    run.check("decisions_valid", valid)


def _owner(chosen: Sequence[Decision], first: date) -> Decision | None:
    """The latest decision whose switch date is on or before `first`."""
    found = [decision for decision in chosen if decision.switch_date <= first]
    return found[-1] if found else None


def _totals(rows: Sequence[dict[str, object]]) -> dict[str, dict[Fuel, FuelOutcome]]:
    sums: dict[tuple[str, Fuel], list[Decimal]] = {}
    for row in rows:
        key = (cast(str, row["decision_id"]), cast(Fuel, row["fuel"]))
        found = sums.setdefault(key, [Decimal(0), Decimal(0), Decimal(0)])
        found[0] += 1
        found[1] += cast(Decimal, row["actual"])
        found[2] += cast(Decimal, row["projected"])
    totals: dict[str, dict[Fuel, FuelOutcome]] = {}
    for (decision, fuel), (count, actual, projected) in sorted(sums.items()):
        totals.setdefault(decision, {})[fuel] = FuelOutcome(
            bills=int(count),
            actual=actual,
            projected=projected,
            diff_pct=float((actual - projected) / projected * 100) if projected else None,
        )
    return totals


# Inputs


def _decisions(directory: Path) -> tuple[list[Decision], bool]:
    """Every valid decision, and whether all were valid (each validates and its file name is its ID)."""
    found: list[Decision] = []
    valid = True
    for path in sorted(directory.glob("DEC-*.json")):
        try:
            decision = Decision.from_document(json.loads(path.read_bytes()))
            parse_decision_id(decision.decision_id)
        except OSError, ValueError, InvalidInputError, SchemaTooNewError:
            valid = False
            continue
        if decision.decision_id != path.stem:
            valid = False
            continue
        found.append(decision)
    return found, valid


def _elec_bills(directory: Path) -> list[_Bill]:
    rows = load_table(directory / BILLS_FILENAME, BILL_SCHEMA, BILL_KIND, BILL_VERSION, input="backtest").to_pylist()
    return sorted(
        (row["period_start"], row["period_end"], row["invoice_amount"])
        for row in rows
        if row["invoice_amount"] is not None
    )


def _gas_bills(directory: Path) -> list[_Bill]:
    rows = load_table(
        directory / GAS_BILLS_FILENAME, GAS_BILL_SCHEMA, GAS_BILL_KIND, GAS_BILL_VERSION, input="gas_bills"
    ).to_pylist()
    return sorted((row["period_start"], row["period_end"], row["total"]) for row in rows)


SPEC: Final = StageSpec(
    name="outcomes",
    inputs={
        "decisions": "data/decisions",
        "backtest": "data/reports/validation/backtest",
        "gas_bills": "data/interim/gas_bills",
    },
    params=(),
    outputs={"outcomes": "data/reports/outcomes"},
    impl=_run,
)
