"""The `cost_gas` stage (P5 spec §9.3): every priced gas tariff applied to the reference year's gas usage.

Inputs: the gas offers mart (`tariffs.json`) and the gas profile (`periods.parquet`, `summary.json`). The billing
periods are the profile's reference periods with their MJ; each tariff is priced in both scenarios by
`core.gas_engine`. `annual_ongoing` scales the reference cost to 365 days; a reference shorter than
`gas.full_year_days` is a partial year and warns `W_GAS_PARTIAL_YEAR`. A workspace with no gas bills gets empty
outputs that pass. Outputs: `line_items.parquet`, `totals.parquet` and `summary.json`. Workspace data, never written to
the tool repo.
"""

import json
from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path
from typing import Final, cast

import pandas as pd

from energatron.core.canonical import JSONValue
from energatron.core.engine import Bill, money
from energatron.core.errors import InvalidInputError, SchemaTooNewError, WarningCode
from energatron.core.gas_engine import GasPeriod, price_gas
from energatron.core.gas_tariff import GasTariff
from energatron.core.models.costs import (
    COST_LINE_SORT,
    COST_SUMMARY_FILENAME,
    GAS_COST_LINE_KIND,
    GAS_COST_LINE_VERSION,
    GAS_COST_TOTAL_KIND,
    GAS_COST_TOTAL_SORT,
    GAS_COST_TOTAL_VERSION,
    LINE_ITEMS_FILENAME,
    SCENARIOS,
    TOTALS_FILENAME,
    GasCostSummary,
)
from energatron.core.models.gas import (
    GAS_PERIOD_KIND,
    GAS_PERIOD_VERSION,
    GAS_PERIODS_FILENAME,
    GAS_SUMMARY_FILENAME,
    GasProfileSummary,
)
from energatron.services.files import write_json, write_parquet
from energatron.services.stages import costing
from energatron.services.stages.runner import StageRun, StageSpec
from energatron.services.stages.tables import COST_LINE_SCHEMA, GAS_COST_TOTAL_SCHEMA, GAS_PERIOD_SCHEMA, load_table

DAYS_PER_YEAR: Final = Decimal(365)


def _run(run: StageRun) -> None:
    pricing = costing.pricing_of(run.params["pricing"])
    tariffs = costing.load_gas_tariffs(run.input("offers"), input="offers").tariffs
    profile = run.input("profile")
    summary = _profile_summary(profile)
    table = load_table(
        profile / GAS_PERIODS_FILENAME, GAS_PERIOD_SCHEMA, GAS_PERIOD_KIND, GAS_PERIOD_VERSION, input="profile"
    )
    periods = [
        GasPeriod(row["period_start"], row["period_end"], cast(Decimal, row["mj"]))
        for row in table.to_pylist()
        if row["in_reference"]
    ]
    has_bills = table.num_rows > 0
    reference_present = not has_bills or (bool(periods) and summary.reference_days > 0)
    priced = bool(periods) and reference_present
    lines: list[dict[str, object]] = []
    totals: list[dict[str, object]] = []
    conserved = True
    for tariff in tariffs if priced else ():
        for scenario in SCENARIOS:
            bill = price_gas(tariff, periods, scenario=scenario, pricing=pricing)
            lines.extend(costing.cost_line_records(tariff.offer_snapshot_id, scenario, bill.lines))
            totals.append(_total(tariff, scenario, bill, summary.reference_days))
            conserved = conserved and _energy_conserved(bill, periods)
    costing.rank(totals, SCENARIOS)
    sums_ok = _lines_sum(lines, totals, periods)

    output = run.output("costs")
    write_parquet(
        pd.DataFrame(lines, columns=list(COST_LINE_SCHEMA.names)),
        output / LINE_ITEMS_FILENAME,
        kind=GAS_COST_LINE_KIND,
        schema_version=GAS_COST_LINE_VERSION,
        sort_by=COST_LINE_SORT,
        schema=COST_LINE_SCHEMA,
    )
    write_parquet(
        pd.DataFrame(totals, columns=list(GAS_COST_TOTAL_SCHEMA.names)),
        output / TOTALS_FILENAME,
        kind=GAS_COST_TOTAL_KIND,
        schema_version=GAS_COST_TOTAL_VERSION,
        sort_by=GAS_COST_TOTAL_SORT,
        schema=GAS_COST_TOTAL_SCHEMA,
    )
    checks = {"reference_present": reference_present, "lines_sum": sums_ok, "energy_conserved": conserved}
    cost_summary = GasCostSummary(
        reference_start=summary.reference_start,
        reference_end=summary.reference_end,
        reference_days=summary.reference_days,
        partial_year=summary.partial_year,
        periods=len(periods),
        scenarios=SCENARIOS,
        tariffs=len(tariffs) if priced else 0,
        cheapest={scenario: costing.cheapest(totals, scenario) for scenario in SCENARIOS},
        checks=checks,
    )
    write_json(output / COST_SUMMARY_FILENAME, cast(JSONValue, cost_summary.model_dump(mode="json")))
    if has_bills and summary.partial_year:
        run.warn(
            WarningCode.W_GAS_PARTIAL_YEAR,
            "The gas reference covers less than a year: annual figures are scaled up and ignore the missing seasons.",
            reference_days=summary.reference_days,
        )
    run.check("reference_present", reference_present, periods=len(periods))
    run.check("lines_sum", sums_ok)
    run.check("energy_conserved", conserved)


def _profile_summary(directory: Path) -> GasProfileSummary:
    try:
        return GasProfileSummary.from_document(json.loads((directory / GAS_SUMMARY_FILENAME).read_bytes()))
    except OSError, ValueError, InvalidInputError, SchemaTooNewError:
        raise InvalidInputError("The gas profile has no valid summary.json.", input="profile") from None


def _total(tariff: GasTariff, scenario: str, bill: Bill, reference_days: int) -> dict[str, object]:
    reference = bill.total()
    annual = money(reference * DAYS_PER_YEAR / reference_days)
    return {
        "offer_snapshot_id": tariff.offer_snapshot_id,
        "scenario": scenario,
        "status": tariff.status,
        "reference_cost": reference,
        "annual_ongoing": annual,
        "annual_year1": annual - bill.one_off_credit,
        "rank": 0,
        "approximations": list(tariff.approximations),
    }


def _lines_sum(
    lines: Sequence[dict[str, object]], totals: Sequence[dict[str, object]], periods: Sequence[GasPeriod]
) -> bool:
    """Each offer and scenario's lines add up to its `reference_cost`, and every line lies in a reference period."""
    keys = {(period.first, period.last) for period in periods}
    sums: dict[tuple[str, str], Decimal] = {}
    for line in lines:
        if (line["period_start"], line["period_end"]) not in keys:
            return False
        key = (cast(str, line["offer_snapshot_id"]), cast(str, line["scenario"]))
        sums[key] = sums.get(key, Decimal(0)) + cast(Decimal, line["amount"])
    return all(
        sums.get((cast(str, row["offer_snapshot_id"]), cast(str, row["scenario"])), Decimal(0)) == row["reference_cost"]
        for row in totals
    )


def _energy_conserved(bill: Bill, periods: Sequence[GasPeriod]) -> bool:
    """The usage lines' MJ add up to each reference period's MJ, exactly."""
    for period in periods:
        billed = sum(
            (line.quantity for line in bill.lines if line.line == "usage" and line.period_start == period.first),
            Decimal(0),
        )
        if billed != period.mj:
            return False
    return True


SPEC: Final = StageSpec(
    name="cost_gas",
    inputs={"offers": "data/marts/offers_gas", "profile": "data/marts/gas_profile"},
    params=("pricing",),  # the reference and its partial-year flag come from the profile
    outputs={"costs": "data/marts/costs/gas"},
    impl=_run,
)
