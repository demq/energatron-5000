"""The `cost_elec` stage (P4 spec §9): every priced tariff applied to the reference year's metered usage.

Inputs: the offers mart (`tariffs.json`), `localise`'s intervals and the usage profile's `summary.json` (for the
reference window). The register is the table's one consumption register; its window's calendar months are the billing
periods. Each tariff is priced in both scenarios by `core.engine`. Outputs: every line (`line_items.parquet`), the
annual totals with a rank per scenario (`totals.parquet`) and `summary.json`. Workspace data: it holds the household's
usage-derived costs, never written to the tool repo.
"""

import json
import math
from collections.abc import Sequence
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Final, cast

import pandas as pd

from energatron.core.canonical import JSONValue
from energatron.core.engine import Bill, prepare_usage, price
from energatron.core.errors import InvalidInputError, SchemaTooNewError
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
from energatron.core.models.usage import UsageSummary
from energatron.core.tariff import Tariff
from energatron.services.files import write_json, write_parquet
from energatron.services.stages import costing
from energatron.services.stages.runner import StageRun, StageSpec
from energatron.services.stages.tables import COST_LINE_SCHEMA, COST_TOTAL_SCHEMA, SUMMARY_FILENAME

CHEAPEST: Final = 3
ENERGY_TOLERANCE_KWH: Final = 1e-4


def _run(run: StageRun) -> None:
    pricing = costing.pricing_of(run.params["pricing"])
    tariffs = costing.load_tariffs(run.input("offers"), input="offers").tariffs
    local = costing.load_local(run.input("local"), input="local")
    summary = _usage_summary(run.input("profile"))
    suffixes = costing.consumption_suffixes(local)
    one_register = len(suffixes) == 1
    suffix = suffixes[0] if one_register else None
    window = _window(summary, suffix)
    periods = _months(*window) if window is not None else []
    rows = local[local["suffix"] == suffix] if suffix is not None else local.iloc[0:0]
    present = [period for period in periods if _has_rows(rows, period)]
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
            conserved = conserved and _energy_conserved(bill, rows, periods)
    _rank(totals)
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
        cheapest={scenario: _cheapest(totals, scenario) for scenario in SCENARIOS},
        checks=checks,
    )
    write_json(output / COST_SUMMARY_FILENAME, cast(JSONValue, cost_summary.model_dump(mode="json")))
    run.check("one_consumption_register", one_register, suffixes=list(suffixes))
    run.check("window_present", window_present, periods=len(periods), with_rows=len(present))
    run.check("lines_sum", sums_ok)
    run.check("energy_conserved", conserved, tolerance_kwh=ENERGY_TOLERANCE_KWH)


def _usage_summary(directory: Path) -> UsageSummary:
    try:
        return UsageSummary.from_document(json.loads((directory / SUMMARY_FILENAME).read_bytes()))
    except OSError, ValueError, InvalidInputError, SchemaTooNewError:
        raise InvalidInputError("The usage profile has no valid summary.json.", input="profile") from None


def _window(summary: UsageSummary, suffix: str | None) -> tuple[str, str] | None:
    found = summary.suffixes.get(suffix) if suffix is not None else None
    if found is None or found.window_start is None or found.window_end is None:
        return None
    return found.window_start, found.window_end


def _months(first: str, last: str) -> list[tuple[date, date]]:
    """The calendar months from `first` to `last` (`YYYY-MM`, inclusive) as `(first day, last day)`."""
    year, month = int(first[:4]), int(first[5:])
    end = (int(last[:4]), int(last[5:]))
    months: list[tuple[date, date]] = []
    while (year, month) <= end:
        start = date(year, month, 1)
        following = date(year + month // 12, month % 12 + 1, 1)
        months.append((start, following - timedelta(days=1)))
        year, month = following.year, following.month
    return months


def _has_rows(rows: pd.DataFrame, period: tuple[date, date]) -> bool:
    dates = rows["local_date"]
    return bool(((dates >= period[0]) & (dates <= period[1])).any())


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


def _rank(totals: list[dict[str, object]]) -> None:
    """Rank 1 is the lowest `annual_ongoing` in its scenario; ties go to the smaller snapshot ID."""
    for scenario in SCENARIOS:
        ordered = sorted(
            (row for row in totals if row["scenario"] == scenario),
            key=lambda row: (cast(Decimal, row["annual_ongoing"]), cast(str, row["offer_snapshot_id"])),
        )
        for rank, row in enumerate(ordered, start=1):
            row["rank"] = rank


def _cheapest(totals: Sequence[dict[str, object]], scenario: str) -> list[str]:
    ranked = sorted((row for row in totals if row["scenario"] == scenario), key=lambda row: cast(int, row["rank"]))
    return [cast(str, row["offer_snapshot_id"]) for row in ranked[:CHEAPEST]]


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


def _energy_conserved(bill: Bill, rows: pd.DataFrame, periods: Sequence[tuple[date, date]]) -> bool:
    """The usage lines' quantities add up to the metered energy of each period."""
    for first, last in periods:
        metered = math.fsum(rows[(rows["local_date"] >= first) & (rows["local_date"] <= last)]["kwh"].tolist())
        billed = math.fsum(
            float(line.quantity) for line in bill.lines if line.line == "usage" and line.period_start == first
        )
        if abs(billed - metered) > ENERGY_TOLERANCE_KWH:
            return False
    return True


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
