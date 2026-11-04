"""The `backtest` stage (P4 spec §10): the engine's bill for each past billing period against the actual invoice.

- **Periods** come from the current `retailer_usage` datasets as in P2 spec §9.3: for each read datetime the complete
  row of the highest-precedence dataset, midnight reads only, and each consecutive pair gives the meter dates
  `r0.date + 1 ... r1.date`. A period missing any interval of the consumption register is `not_covered`.
- **The tariff in force** is the `backtest.tariffs` entry whose meter-date range holds the whole period (`no_tariff`
  when none does, `split_tariff` when two would).
- **Invoices** come from the current `retailer_txn` datasets (`core.retailer_txn`; descriptions never leave it). The
  earliest unmatched invoice dated within `invoice_window_days` on or after the period's last date matches it.
- Each priced period is billed by `core.engine` (scenario `backtest`, periods on `meter_date`) and compared: within
  `validation.backtest.tolerance_pct` is `within`, else `outside` (a warning, not a failure: §16 Q5).

Workspace data: invoice amounts and bills are the household's; nothing here reaches the tool repo.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from itertools import pairwise
from pathlib import Path
from typing import Final, cast

import pandas as pd

from energatron.core.canonical import JSONValue, sha256_hex
from energatron.core.engine import Bill, money, prepare_usage, price
from energatron.core.errors import InvalidInputError, WarningCode
from energatron.core.models.catalog import (
    CATALOG_FILENAME,
    CatalogRecord,
    SourceType,
    parse_catalog,
    precedence_order,
    raw_key,
)
from energatron.core.models.costs import (
    BACKTEST_RESULT_FILENAME,
    BILL_KIND,
    BILL_SORT,
    BILL_VERSION,
    BILLS_FILENAME,
    COST_LINE_KIND,
    COST_LINE_SORT,
    COST_LINE_VERSION,
    LINE_ITEMS_FILENAME,
    BacktestResult,
    BillStatus,
)
from energatron.core.models.params import BacktestParams, BacktestValidation, TariffInForce
from energatron.core.retailer_txn import read_txn
from energatron.core.retailer_usage import read_usage_export
from energatron.services.files import write_json, write_parquet
from energatron.services.stages import costing
from energatron.services.stages.runner import StageRun, StageSpec
from energatron.services.stages.tables import BILL_SCHEMA, COST_LINE_SCHEMA

KNOWN_DETECTOR_VERSIONS: Final = {SourceType.RETAILER_USAGE: frozenset({1}), SourceType.RETAILER_TXN: frozenset({1})}
SCENARIO: Final = "backtest"
_STATUSES: Final[tuple[BillStatus, ...]] = (
    "within",
    "outside",
    "unmatched",
    "no_tariff",
    "split_tariff",
    "not_covered",
)
_MIDNIGHT: Final = time()


@dataclass
class _Findings:
    mismatched: list[str] = field(default_factory=lambda: list[str]())
    unknown_detector: list[str] = field(default_factory=lambda: list[str]())


@dataclass(frozen=True)
class _Invoice:
    day: date
    amount: Decimal


def _run(run: StageRun) -> None:
    pricing = costing.pricing_of(run.params["pricing"])
    settings = BacktestParams.model_validate(run.params["backtest"])
    validation = BacktestValidation.model_validate(run.params["validation.backtest"])
    tariffs = {
        tariff.offer_snapshot_id: tariff for tariff in costing.load_tariffs(run.input("offers"), input="offers").tariffs
    }
    local = costing.load_local(run.input("local"), input="local")
    findings = _Findings()
    reads = _reads(_current(run.input("usage"), SourceType.RETAILER_USAGE, findings))
    invoices = _invoices(_current(run.input("txn"), SourceType.RETAILER_TXN, findings))

    suffixes = costing.consumption_suffixes(local)
    rows = local[local["suffix"] == suffixes[0]] if len(suffixes) == 1 else local.iloc[0:0]
    usage = prepare_usage(rows)
    complete = _complete_days(rows)
    unresolved = sorted({entry.offer for entry in settings.tariffs if entry.offer not in tariffs})

    bills: list[dict[str, object]] = []
    lines: list[dict[str, object]] = []
    sums_ok = True
    unmatched = sorted(invoices, key=lambda invoice: invoice.day)
    for first, last in pairwise_periods(reads):
        status, entry = _status_before_pricing(first, last, settings.tariffs, complete)
        offer = None if entry is None else entry.offer
        bill: Bill | None = None
        if status is None and offer in tariffs:
            bill = price(tariffs[offer], usage, [(first, last)], scenario=SCENARIO, pricing=pricing, by="meter_date")
            records = costing.cost_line_records(offer, SCENARIO, bill.lines)
            lines.extend(records)
            sums_ok = sums_ok and sum((cast(Decimal, item["amount"]) for item in records), Decimal(0)) == bill.total()
        elif status is None:
            status = "no_tariff"
        invoice = _match(unmatched, last, validation.invoice_window_days) if bill is not None else None
        bills.append(_bill_row(first, last, offer, bill, invoice, status, validation.tolerance_pct))

    output = run.output("backtest")
    write_parquet(
        pd.DataFrame(bills, columns=list(BILL_SCHEMA.names)),
        output / BILLS_FILENAME,
        kind=BILL_KIND,
        schema_version=BILL_VERSION,
        sort_by=BILL_SORT,
        schema=BILL_SCHEMA,
    )
    write_parquet(
        pd.DataFrame(lines, columns=list(COST_LINE_SCHEMA.names)),
        output / LINE_ITEMS_FILENAME,
        kind=COST_LINE_KIND,
        schema_version=COST_LINE_VERSION,
        sort_by=COST_LINE_SORT,
        schema=COST_LINE_SCHEMA,
    )
    statuses: dict[BillStatus, int] = {status: sum(row["status"] == status for row in bills) for status in _STATUSES}
    matched = [abs(cast(float, row["diff_pct"])) for row in bills if row["diff_pct"] is not None]
    checks = {
        "blobs_match_catalog": not findings.mismatched and not findings.unknown_detector,
        "tariffs_resolve": not unresolved,
        "lines_sum": sums_ok,
    }
    result = BacktestResult(
        periods=len(bills),
        statuses=statuses,
        max_abs_diff_pct=max(matched) if matched else None,
        tolerance_pct=validation.tolerance_pct,
        tariffs_configured=len(settings.tariffs),
        checks=checks,
    )
    write_json(output / BACKTEST_RESULT_FILENAME, cast(JSONValue, result.model_dump(mode="json")))
    if statuses["outside"]:
        run.warn(
            WarningCode.W_BACKTEST_OUTSIDE_TOLERANCE,
            "Some billing periods differ from their invoice by more than the tolerance.",
            outside=statuses["outside"],
            within=statuses["within"],
        )
    if not settings.tariffs or not (statuses["within"] + statuses["outside"] + statuses["unmatched"]):
        run.warn(
            WarningCode.W_BACKTEST_NO_TARIFF,
            "No billing period was priced: set backtest.tariffs to the tariff in force.",
            tariffs_configured=len(settings.tariffs),
        )
    run.check(
        "blobs_match_catalog",
        checks["blobs_match_catalog"],
        mismatched=list(findings.mismatched),
        unknown=list(findings.unknown_detector),
    )
    run.check("tariffs_resolve", checks["tariffs_resolve"], unresolved=cast(JSONValue, unresolved))
    run.check("lines_sum", sums_ok)


# Raw datasets


def _current(raw: Path, source: SourceType, findings: _Findings) -> list[bytes]:
    """The blobs of the current (not superseded) datasets, in precedence order, each checked against its record."""
    try:
        catalog = (raw / CATALOG_FILENAME).read_bytes()
    except OSError:
        raise InvalidInputError("A raw directory has no readable catalog.", input=source.value) from None
    records = parse_catalog(catalog, source)
    superseded = {record.supersedes for record in records if record.supersedes is not None}
    by_id = {record.dataset_id: record for record in records}
    found: list[bytes] = []
    for ident in precedence_order(records):
        record = by_id[ident]
        data = _blob(raw, record)
        if data is None or sha256_hex(data) != record.sha256:
            findings.mismatched.append(ident)
            continue
        if record.detector.name != source or record.detector.version not in KNOWN_DETECTOR_VERSIONS[source]:
            findings.unknown_detector.append(ident)
            continue
        if ident not in superseded:
            found.append(data)
    return found


def _blob(raw: Path, record: CatalogRecord) -> bytes | None:
    relative = record.stored_path.removeprefix(raw_key(record.source))
    try:
        return raw.joinpath(*relative.split("/")).read_bytes()
    except OSError:
        return None


def _reads(blobs: Sequence[bytes]) -> list[datetime]:
    """The midnight read datetimes with a complete row in some current dataset, sorted (P2 spec §9.3 steps 1-2)."""
    found: set[datetime] = set()
    for data in blobs:
        for row in read_usage_export(data).rows:
            if row.total is not None and all(value is not None for value in row.values.values()):
                found.add(row.read_dt)
    return sorted(read for read in found if read.time() == _MIDNIGHT)


def pairwise_periods(reads: Sequence[datetime]) -> list[tuple[date, date]]:
    """Each consecutive pair of reads as the meter dates `r0.date + 1 ... r1.date` (P2 spec §9.3 step 3)."""
    return [(before.date() + timedelta(days=1), after.date()) for before, after in pairwise(reads)]


def _invoices(blobs: Sequence[bytes]) -> list[_Invoice]:
    """Every invoice of the current exports, once: the same date and amount in two exports is one invoice."""
    found = {(row.at.date(), row.amount) for data in blobs for row in read_txn(data) if row.kind == "invoice"}
    return [_Invoice(day, money(amount)) for day, amount in sorted(found)]


# Periods


def _complete_days(rows: pd.DataFrame) -> set[date]:
    """The meter dates whose every interval is present."""
    if rows.empty:
        return set()
    minutes = rows.groupby("meter_date", sort=True)["interval_minutes"].sum()
    return {cast(date, day) for day, total in minutes.items() if int(total) == 1440}


def _status_before_pricing(
    first: date, last: date, tariffs: Sequence[TariffInForce], complete: set[date]
) -> tuple[BillStatus | None, TariffInForce | None]:
    days = {first + timedelta(days=offset) for offset in range((last - first).days + 1)}
    if not days <= complete:
        return "not_covered", None
    holding = [entry for entry in tariffs if entry.start <= first and (entry.end is None or last <= entry.end)]
    if holding:
        return None, holding[0]
    touching = [entry for entry in tariffs if entry.start <= last and (entry.end is None or first <= entry.end)]
    return ("split_tariff" if touching else "no_tariff"), None


def _match(unmatched: list[_Invoice], last: date, window_days: int) -> _Invoice | None:
    for index, invoice in enumerate(unmatched):
        if last <= invoice.day <= last + timedelta(days=window_days):
            return unmatched.pop(index)
    return None


def _bill_row(
    first: date,
    last: date,
    offer: str | None,
    bill: Bill | None,
    invoice: _Invoice | None,
    status: BillStatus | None,
    tolerance_pct: float,
) -> dict[str, object]:
    engine = None if bill is None else bill.total()
    diff = diff_pct = None
    if bill is not None and invoice is not None and engine is not None:
        diff = engine - invoice.amount
        diff_pct = float(diff / invoice.amount * 100) if invoice.amount else None
        status = "within" if diff_pct is not None and abs(diff_pct) <= tolerance_pct else "outside"
    elif bill is not None:
        status = "unmatched"
    return {
        "period_start": first,
        "period_end": last,
        "offer_snapshot_id": offer,
        "invoice_date": None if invoice is None else invoice.day,
        "invoice_amount": None if invoice is None else invoice.amount,
        "engine_amount": engine,
        "diff": diff,
        "diff_pct": diff_pct,
        "status": status,
    }


SPEC: Final = StageSpec(
    name="backtest",
    inputs={
        "offers": "data/marts/offers",
        "local": "data/interim/elec_interval_local",
        "usage": "data/raw/retailer_usage",
        "txn": "data/raw/retailer_txn",
    },
    params=("pricing", "backtest", "validation.backtest"),
    outputs={"backtest": "data/reports/validation/backtest"},
    impl=_run,
)
