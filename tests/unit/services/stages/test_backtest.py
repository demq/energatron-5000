"""The `backtest` stage (P4 spec §10): periods from the retailer reads, the tariff in force, invoice matching, the
statuses, the three checks and two warnings.

The workspace is the fixture raw zone (its usage exports give the periods) and offer zone, with the upstream stages
run. The tariff in force is the fixture's manual offer. Invoices are generated from the engine's own bills, so the
test checks the matching and the tolerance, not the engine (`test_engine.py` checks that against the oracle).
"""

import itertools
import json
import shutil
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq
import pytest

from energatron.core.errors import CheckFailedError, WarningCode
from energatron.core.models.catalog import SourceType
from energatron.core.models.costs import BacktestResult
from energatron.services.stages import backtest
from tests.fixtures.make_sources import make_retailer_txn_csv
from tests.fixtures.offer_workspace import MANUAL_ID
from tests.fixtures.params_text import params_v4_text
from tests.fixtures.pipeline_workspace import land_blob
from tests.integration._stage_inputs import REFERENCE_MONTHS, build_inputs, run_stage_named

CHECKS = ["blobs_match_catalog", "tariffs_resolve", "lines_sum"]


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("backtest")
    build_inputs("backtest", root)
    return root


@pytest.fixture
def root(template: Path, tmp_path: Path) -> Path:
    found = tmp_path / "workspace"
    shutil.copytree(template, found)
    return found


def manual_snapshot(root: Path) -> str:
    rows = pq.read_table(root / "data" / "marts" / "offers" / "offers.parquet").to_pylist()
    return next(row["offer_snapshot_id"] for row in rows if row["plan_id"] == MANUAL_ID)


def configure(root: Path, *tariffs: dict[str, Any]) -> None:
    (root / "params.yaml").write_text(
        params_v4_text(region=True, reference_months=REFERENCE_MONTHS, tariffs=list(tariffs))
    )


def in_force(root: Path, start: str = "2025-01-01", end: str | None = None) -> dict[str, Any]:
    return {"offer": manual_snapshot(root), "from": start, "to": end}


def bills(root: Path) -> list[dict[str, Any]]:
    return pq.read_table(root / "data" / "reports" / "validation" / "backtest" / "bills.parquet").to_pylist()


def result(root: Path) -> BacktestResult:
    path = root / "data" / "reports" / "validation" / "backtest" / "result.json"
    return BacktestResult.from_document(json.loads(path.read_text()))


def priced(root: Path) -> list[dict[str, Any]]:
    return [row for row in bills(root) if row["engine_amount"] is not None]


def invoices(root: Path, *entries: tuple[datetime, Decimal]) -> None:
    rows = [(f"Invoice - 00000{index}/1", when, f"{amount:.4f}") for index, (when, amount) in enumerate(entries)]
    land_blob(root, SourceType.RETAILER_TXN, "synthetic_Transactions_ALL.csv", make_retailer_txn_csv(rows))


def failed(root: Path) -> list[str]:
    with pytest.raises(CheckFailedError):
        run_stage_named("backtest", root)
    provenance = json.loads((root / "data" / "reports" / "validation" / "backtest" / "_provenance.json").read_text())
    return [check["name"] for check in provenance["checks"] if check["status"] == "fail"]


# Without a tariff


def test_without_a_tariff_every_period_is_unpriced_and_warned(root: Path) -> None:
    found = run_stage_named("backtest", root)
    assert [check.name for check in found.checks] == CHECKS
    assert all(check.status == "pass" for check in found.checks)
    assert [warning.code for warning in found.warnings] == [WarningCode.W_BACKTEST_NO_TARIFF]
    statuses = {row["status"] for row in bills(root)}
    assert statuses <= {"no_tariff", "not_covered"}
    assert "no_tariff" in statuses


def test_periods_run_from_the_day_after_one_read_to_the_next_reads_date(root: Path) -> None:
    run_stage_named("backtest", root)
    periods = [(row["period_start"], row["period_end"]) for row in bills(root)]
    assert periods
    for (_, end), (start, _) in itertools.pairwise(periods):
        assert start == end + timedelta(days=1)


# With the tariff in force


def test_priced_periods_without_an_invoice_are_unmatched(root: Path) -> None:
    configure(root, in_force(root))
    found = run_stage_named("backtest", root)
    assert priced(root)
    assert {row["status"] for row in priced(root)} == {"unmatched"}
    assert found.warnings == []


def test_an_invoice_equal_to_the_bill_is_within_and_one_two_percent_off_is_outside(root: Path) -> None:
    configure(root, in_force(root))
    run_stage_named("backtest", root)
    first, second, *_ = priced(root)
    when = lambda row: datetime.combine(row["period_end"] + timedelta(days=2), datetime.min.time())  # noqa: E731
    invoices(root, (when(first), first["engine_amount"]), (when(second), second["engine_amount"] * Decimal("1.02")))
    found = run_stage_named("backtest", root)
    by_start = {row["period_start"]: row for row in bills(root)}
    assert by_start[first["period_start"]]["status"] == "within"
    assert by_start[first["period_start"]]["diff"] == Decimal("0.00")
    outside = by_start[second["period_start"]]
    assert outside["status"] == "outside"
    assert outside["diff_pct"] == pytest.approx(-100 * 0.02 / 1.02, abs=0.01)
    assert [warning.code for warning in found.warnings] == [WarningCode.W_BACKTEST_OUTSIDE_TOLERANCE]
    summary = result(root)
    assert (summary.statuses["within"], summary.statuses["outside"]) == (1, 1)
    assert summary.max_abs_diff_pct == pytest.approx(abs(outside["diff_pct"]))


def test_an_invoice_outside_the_window_does_not_match(root: Path) -> None:
    configure(root, in_force(root))
    run_stage_named("backtest", root)
    first = priced(root)[0]
    late = datetime.combine(first["period_end"] + timedelta(days=30), datetime.min.time())
    invoices(root, (late, first["engine_amount"]))
    run_stage_named("backtest", root)
    assert {row["period_start"]: row for row in bills(root)}[first["period_start"]]["status"] == "unmatched"


def test_a_period_split_by_two_tariffs_is_not_priced(root: Path) -> None:
    configure(root, in_force(root))
    run_stage_named("backtest", root)
    target = priced(root)[0]
    middle = target["period_start"] + timedelta(days=10)
    configure(
        root,
        in_force(root, "2025-01-01", (middle - timedelta(days=1)).isoformat()),
        in_force(root, middle.isoformat()),
    )
    run_stage_named("backtest", root)
    assert {row["period_start"]: row for row in bills(root)}[target["period_start"]]["status"] == "split_tariff"


def test_line_items_are_written_for_priced_periods(root: Path) -> None:
    configure(root, in_force(root))
    run_stage_named("backtest", root)
    lines = pq.read_table(root / "data" / "reports" / "validation" / "backtest" / "line_items.parquet").to_pylist()
    assert {line["scenario"] for line in lines} == {"backtest"}
    for row in priced(root):
        own = [line["amount"] for line in lines if line["period_start"] == row["period_start"]]
        assert sum(own, Decimal(0)) == row["engine_amount"]


# Failing checks


def test_an_unknown_tariff_fails_tariffs_resolve(root: Path) -> None:
    configure(root, {"offer": "NOPE@EME~20250101T000000Z~00000000", "from": "2025-01-01", "to": None})
    assert failed(root) == ["tariffs_resolve"]


def test_a_tampered_usage_blob_fails_blobs_match_catalog(root: Path) -> None:
    catalog = root / "data" / "raw" / "retailer_usage" / "_catalog.jsonl"
    record = json.loads(catalog.read_text().splitlines()[0])
    (root / record["stored_path"]).write_bytes(b"tampered")
    assert failed(root) == ["blobs_match_catalog"]


def test_a_tampered_line_fails_lines_sum(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    configure(root, in_force(root))
    real = backtest.costing.cost_line_records

    def tampered(snapshot: str, scenario: str, lines: Any) -> list[dict[str, object]]:
        records = real(snapshot, scenario, lines)
        records[0]["amount"] = Decimal("999.99")
        return records

    monkeypatch.setattr(backtest.costing, "cost_line_records", tampered)
    assert failed(root) == ["lines_sum"]
