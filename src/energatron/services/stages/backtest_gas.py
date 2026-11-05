"""The `backtest_gas` stage (P5 spec §10): the gas engine against the household's own gas bills.

Each current bill is priced as billed (its own period and MJ, not the profile's combined periods) with the
`backtest.gas_tariffs` entry whose dates hold the whole period (none: `no_tariff`; two: `split_tariff`, not priced),
scenario `backtest`, and compared with the bill's total: within `validation.backtest.tolerance_pct` is `within`, else
`outside`, a warning, not a failure (as P4's backtest). Outputs: `bills.parquet`, `line_items.parquet` and
`result.json`. Workspace data: the bills are the household's; nothing here reaches the tool repo.
"""

from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from typing import Final, cast

import pandas as pd

from energatron.core.canonical import JSONValue
from energatron.core.errors import WarningCode
from energatron.core.gas_engine import GasPeriod, price_gas
from energatron.core.models.costs import (
    BACKTEST_RESULT_FILENAME,
    BILLS_FILENAME,
    COST_LINE_SORT,
    GAS_BACKTEST_BILL_KIND,
    GAS_BACKTEST_BILL_SORT,
    GAS_BACKTEST_BILL_VERSION,
    GAS_COST_LINE_KIND,
    GAS_COST_LINE_VERSION,
    LINE_ITEMS_FILENAME,
    GasBacktestResult,
    GasBillStatus,
)
from energatron.core.models.gas import GAS_BILL_KIND, GAS_BILL_VERSION, GAS_BILLS_FILENAME
from energatron.core.models.params import BacktestValidation, TariffInForce
from energatron.services.files import write_json, write_parquet
from energatron.services.stages import costing
from energatron.services.stages.runner import StageRun, StageSpec
from energatron.services.stages.tables import COST_LINE_SCHEMA, GAS_BACKTEST_BILL_SCHEMA, GAS_BILL_SCHEMA, load_table

SCENARIO: Final = "backtest"
STATUSES: Final[tuple[GasBillStatus, ...]] = ("within", "outside", "no_tariff", "split_tariff")


def _run(run: StageRun) -> None:
    pricing = costing.pricing_of(run.params["pricing"])
    entries = tuple(
        TariffInForce.model_validate(item) for item in cast(list[object], run.params["backtest.gas_tariffs"])
    )
    validation = BacktestValidation.model_validate(run.params["validation.backtest"])
    tariffs = {
        tariff.offer_snapshot_id: tariff
        for tariff in costing.load_gas_tariffs(run.input("offers"), input="offers").tariffs
    }
    table = load_table(
        run.input("bills") / GAS_BILLS_FILENAME, GAS_BILL_SCHEMA, GAS_BILL_KIND, GAS_BILL_VERSION, input="bills"
    )
    unresolved = sorted({entry.offer for entry in entries if entry.offer not in tariffs})

    rows: list[dict[str, object]] = []
    lines: list[dict[str, object]] = []
    sums_ok = True
    for bill in table.to_pylist():
        first, last = cast(date, bill["period_start"]), cast(date, bill["period_end"])
        status, entry = _in_force(first, last, entries)
        engine: Decimal | None = None
        if entry is not None and entry.offer in tariffs:
            priced = price_gas(
                tariffs[entry.offer],
                [GasPeriod(first, last, cast(Decimal, bill["mj"]))],
                scenario=SCENARIO,
                pricing=pricing,
            )
            records = costing.cost_line_records(entry.offer, SCENARIO, priced.lines)
            lines.extend(records)
            engine = priced.total()
            sums_ok = sums_ok and sum((cast(Decimal, item["amount"]) for item in records), Decimal(0)) == engine
        elif entry is not None:
            status = "no_tariff"  # configured but not priced: `tariffs_resolve` fails
        rows.append(_row(first, last, entry, engine, cast(Decimal, bill["total"]), status, validation.tolerance_pct))

    output = run.output("backtest")
    write_parquet(
        pd.DataFrame(rows, columns=list(GAS_BACKTEST_BILL_SCHEMA.names)),
        output / BILLS_FILENAME,
        kind=GAS_BACKTEST_BILL_KIND,
        schema_version=GAS_BACKTEST_BILL_VERSION,
        sort_by=GAS_BACKTEST_BILL_SORT,
        schema=GAS_BACKTEST_BILL_SCHEMA,
    )
    write_parquet(
        pd.DataFrame(lines, columns=list(COST_LINE_SCHEMA.names)),
        output / LINE_ITEMS_FILENAME,
        kind=GAS_COST_LINE_KIND,
        schema_version=GAS_COST_LINE_VERSION,
        sort_by=COST_LINE_SORT,
        schema=COST_LINE_SCHEMA,
    )
    statuses: dict[GasBillStatus, int] = {status: sum(row["status"] == status for row in rows) for status in STATUSES}
    priced_pct = [abs(cast(float, row["diff_pct"])) for row in rows if row["diff_pct"] is not None]
    checks = {"tariffs_resolve": not unresolved, "lines_sum": sums_ok}
    result = GasBacktestResult(
        bills=len(rows),
        statuses=statuses,
        max_abs_diff_pct=max(priced_pct) if priced_pct else None,
        tolerance_pct=validation.tolerance_pct,
        tariffs_configured=len(entries),
        checks=checks,
    )
    write_json(output / BACKTEST_RESULT_FILENAME, cast(JSONValue, result.model_dump(mode="json")))
    if statuses["outside"]:
        run.warn(
            WarningCode.W_BACKTEST_OUTSIDE_TOLERANCE,
            "Some gas bills differ from the engine by more than the tolerance.",
            outside=statuses["outside"],
            within=statuses["within"],
        )
    if rows and not (statuses["within"] + statuses["outside"]):
        run.warn(
            WarningCode.W_BACKTEST_NO_TARIFF,
            "No gas bill was priced: set backtest.gas_tariffs to the tariff in force.",
            tariffs_configured=len(entries),
        )
    run.check("tariffs_resolve", checks["tariffs_resolve"], unresolved=cast(JSONValue, unresolved))
    run.check("lines_sum", sums_ok)


def _in_force(
    first: date, last: date, entries: Sequence[TariffInForce]
) -> tuple[GasBillStatus | None, TariffInForce | None]:
    holding = [entry for entry in entries if entry.start <= first and (entry.end is None or last <= entry.end)]
    if holding:
        return None, holding[0]
    touching = [entry for entry in entries if entry.start <= last and (entry.end is None or first <= entry.end)]
    return ("split_tariff" if touching else "no_tariff"), None


def _row(
    first: date,
    last: date,
    entry: TariffInForce | None,
    engine: Decimal | None,
    total: Decimal,
    status: GasBillStatus | None,
    tolerance_pct: float,
) -> dict[str, object]:
    diff = diff_pct = None
    if engine is not None:
        diff = engine - total
        diff_pct = float(diff / total * 100) if total else None
        status = "within" if diff_pct is not None and abs(diff_pct) <= tolerance_pct else "outside"
    return {
        "period_start": first,
        "period_end": last,
        "offer_snapshot_id": None if entry is None else entry.offer,
        "bill_total": total,
        "engine_total": engine,
        "diff": diff,
        "diff_pct": diff_pct,
        "status": status,
    }


SPEC: Final = StageSpec(
    name="backtest_gas",
    inputs={"offers": "data/marts/offers_gas", "bills": "data/interim/gas_bills"},
    params=("pricing", "backtest.gas_tariffs", "validation.backtest"),
    outputs={"backtest": "data/reports/validation/backtest_gas"},
    impl=_run,
)
