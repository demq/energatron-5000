"""The `cost_elec` stage (P4 spec §9): every priced tariff applied to the reference year's metered usage.

Inputs: the offers mart (`tariffs.json`), `localise`'s intervals and the usage profile's `summary.json` (for the
reference window). The register is the table's one consumption register; its window's calendar months are the billing
periods. Each tariff is priced in both scenarios by `core.engine`. Outputs: every line (`line_items.parquet`), the
annual totals with a rank per scenario (`totals.parquet`) and `summary.json`. Workspace data: it holds the household's
usage-derived costs, never written to the tool repo.
"""

from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from typing import Final, cast

import pandas as pd

from energatron.core.canonical import JSONValue
from energatron.core.engine import Bill, prepare_usage, price
from energatron.core.models.costs import (
    COST_LINE_KIND,
    COST_LINE_SORT,
    COST_LINE_VERSION,
    COST_SUMMARY_FILENAME,
    COST_TOTAL_KIND,
    COST_TOTAL_SORT,
    COST_TOTAL_VERSION,
    LINE_ITEMS_FILENAME,
    SCENARIOS,
    TOTALS_FILENAME,
    CostSummary,
)
from energatron.core.tariff import Tariff
from energatron.services.files import write_json, write_parquet
from energatron.services.stages import costing
from energatron.services.stages.runner import StageRun, StageSpec
from energatron.services.stages.tables import COST_LINE_SCHEMA, COST_TOTAL_SCHEMA

CHEAPEST: Final = 3
ENERGY_TOLERANCE_KWH: Final = 1e-4


def _run(run: StageRun) -> None:
    pricing = costing.pricing_of(run.params["pricing"])
    tariffs = costing.load_tariffs(run.input("offers"), input="offers").tariffs
    local = costing.load_local(run.input("local"), input="local")
    summary = costing.usage_summary(run.input("profile"))
    suffixes = costing.consumption_suffixes(local)
    one_register = len(suffixes) == 1
    suffix = suffixes[0] if one_register else None
    window = costing.window(summary, suffix)
    periods = costing.months(*window) if window is not None else []
    rows = local[local["suffix"] == suffix] if suffix is not None else local.iloc[0:0]
    present = [period for period in periods if costing.has_rows(rows, period)]
    window_present = window is not None and len(present) == len(periods)

    priced = one_register and window_present
    usage = prepare_usage(rows) if priced else None
    line_records: list[dict[str, object]] = []
    totals: list[dict[str, object]] = []
    conserved = True
    for tariff in tariffs if priced else ():
        assert usage is not None
        for scenario in SCENARIOS:
            bill = price(tariff, usage, periods, scenario=scenario, pricing=pricing)
            line_records.extend(costing.cost_line_records(tariff.offer_snapshot_id, scenario, bill.lines))
            totals.append(_total(tariff, scenario, bill))
            conserved = conserved and costing.energy_conserved(bill, rows, periods, tolerance=ENERGY_TOLERANCE_KWH)
    costing.rank(totals, SCENARIOS)
    sums_ok = _lines_sum(line_records, totals, periods)

    output = run.output("costs")
    write_parquet(
        pd.DataFrame(line_records, columns=list(COST_LINE_SCHEMA.names)),
        output / LINE_ITEMS_FILENAME,
        kind=COST_LINE_KIND,
        schema_version=COST_LINE_VERSION,
        sort_by=COST_LINE_SORT,
        schema=COST_LINE_SCHEMA,
    )
    write_parquet(
        pd.DataFrame(totals, columns=list(COST_TOTAL_SCHEMA.names)),
        output / TOTALS_FILENAME,
        kind=COST_TOTAL_KIND,
        schema_version=COST_TOTAL_VERSION,
        sort_by=COST_TOTAL_SORT,
        schema=COST_TOTAL_SCHEMA,
    )
    checks = {
        "one_consumption_register": one_register,
        "window_present": window_present,
        "lines_sum": sums_ok,
        "energy_conserved": conserved,
    }
    cost_summary = CostSummary(
        suffix=suffix,
        window_start=None if window is None else window[0],
        window_end=None if window is None else window[1],
        periods=len(periods),
        scenarios=SCENARIOS,
        tariffs=len(tariffs) if priced else 0,
        cheapest={scenario: costing.cheapest(totals, scenario, CHEAPEST) for scenario in SCENARIOS},
        checks=checks,
    )
    write_json(output / COST_SUMMARY_FILENAME, cast(JSONValue, cost_summary.model_dump(mode="json")))
    run.check("one_consumption_register", one_register, suffixes=list(suffixes))
    run.check("window_present", window_present, periods=len(periods), with_rows=len(present))
    run.check("lines_sum", sums_ok)
    run.check("energy_conserved", conserved, tolerance_kwh=ENERGY_TOLERANCE_KWH)


def _total(tariff: Tariff, scenario: str, bill: Bill) -> dict[str, object]:
    ongoing = bill.total()
    return {
        "offer_snapshot_id": tariff.offer_snapshot_id,
        "scenario": scenario,
        "status": tariff.status,
        "annual_ongoing": ongoing,
        "annual_year1": ongoing - bill.one_off_credit,
        "rank": 0,
        "approximations": list(tariff.approximations),
    }


def _lines_sum(
    lines: Sequence[dict[str, object]], totals: Sequence[dict[str, object]], periods: Sequence[tuple[date, date]]
) -> bool:
    """The two tables agree: each offer and scenario's line amounts add up to its `annual_ongoing`, and every line lies
    in one of the billing periods."""
    sums: dict[tuple[str, str], Decimal] = {}
    for line in lines:
        if (line["period_start"], line["period_end"]) not in periods:
            return False
        key = (cast(str, line["offer_snapshot_id"]), cast(str, line["scenario"]))
        sums[key] = sums.get(key, Decimal(0)) + cast(Decimal, line["amount"])
    return all(
        sums.get((cast(str, row["offer_snapshot_id"]), cast(str, row["scenario"])), Decimal(0)) == row["annual_ongoing"]
        for row in totals
    )


SPEC: Final = StageSpec(
    name="cost_elec",
    inputs={
        "offers": "data/marts/offers",
        "local": "data/interim/elec_interval_local",
        "profile": "data/marts/usage_profile",
    },
    params=("pricing",),
    outputs={"costs": "data/marts/costs/elec"},
    impl=_run,
)
