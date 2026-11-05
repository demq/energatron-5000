"""The `backtest_gas` stage (P5 spec §10): each gas bill priced with the tariff in force and compared with its total.

The workspace is the fixture gas zone, whose bills are priced at `make_gas`'s single usage rate and daily supply,
and a gas `offer_manual` with exactly those prices, so the engine reproduces each bill."""

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq
import pytest

from energatron.core.canonical import content_hash, sha256_hex
from energatron.core.errors import CheckFailedError, WarningCode
from energatron.core.models.catalog import SourceType
from energatron.core.models.costs import GasBacktestResult
from energatron.core.models.provenance import StageCode
from energatron.services.stages.registry import get_stage
from energatron.services.stages.runner import StageResult, prepare_stage, run_stage
from tests.fixtures import make_cdr as cdr
from tests.fixtures import make_gas as gas
from tests.fixtures.gas_workspace import build_gas_zone
from tests.fixtures.params_text import params_v5_text
from tests.fixtures.pipeline_workspace import empty_raw_zone, land_blob

OUT = Path("data/reports/validation/backtest_gas")


def run(root: Path, name: str) -> StageResult:
    spec = get_stage(name)
    prepared = prepare_stage(spec, root=root, params_file=root / "params.yaml")
    source = sha256_hex(name.encode())
    digest = content_hash({"source_sha256": source, "packages": {}, "python": "3.14.0"})
    code = StageCode(stage_sha256=digest, source_sha256=source, python="3.14.0", packages={}, files=["__init__.py"])
    return run_stage(
        spec, prepared.inputs, prepared.params, prepared.outputs, code=code, recorded_code=code.stage_sha256
    )


def land_manual(root: Path, *, rate: str = str(gas.USAGE_RATE)) -> str:
    contract = cdr.gas_plan(periods=[cdr.gas_period([cdr.rate(rate, unit=None)], supply=str(gas.SUPPLY_RATE))])
    text = cdr.offer_manual_text(
        "MANUAL-TEST-GAS",
        published="2026-07-01T00:00:00Z",
        effective_from="2026-07-01",
        gas_contract=contract["gasContract"],
    )
    landed = land_blob(root, SourceType.OFFER_MANUAL, f"gas-{rate}.yaml", text.encode("utf-8"))
    catalog = (root / "data/raw/offer_manual/_catalog.jsonl").read_text(encoding="utf-8").splitlines()
    (line,) = [json.loads(found) for found in catalog if json.loads(found)["dataset_id"] == landed.dataset_id]
    return str(line["extra"]["offer_snapshot_id"])


def workspace(tmp_path: Path, tariffs: list[dict[str, str | None]], *, rate: str = str(gas.USAGE_RATE)) -> Path:
    root = tmp_path / "workspace"
    empty_raw_zone(root)
    (root / "config/offer_rules.local").mkdir(parents=True, exist_ok=True)
    build_gas_zone(root)
    snapshot = land_manual(root, rate=rate)
    entries = [{**entry, "offer": snapshot if entry["offer"] == "MANUAL" else entry["offer"]} for entry in tariffs]
    (root / "params.yaml").write_text(params_v5_text(region=True, gas_tariffs=entries), encoding="utf-8")
    run(root, "gas_bills")
    run(root, "normalise_gas_offers")
    return root


def result(root: Path) -> GasBacktestResult:
    return GasBacktestResult.from_document(json.loads((root / OUT / "result.json").read_bytes()))


def test_bills_on_their_own_prices_are_reproduced_within_a_cent(tmp_path: Path) -> None:
    root = workspace(tmp_path, [{"offer": "MANUAL", "from": "2026-07-01", "to": None}])
    found = run(root, "backtest_gas")
    assert all(check.status == "pass" for check in found.checks)
    assert list(found.warnings) == []
    rows = pq.read_table(root / OUT / "bills.parquet").to_pylist()
    assert [row["status"] for row in rows] == ["within"] * 3
    assert all(abs(row["diff"]) <= Decimal("0.01") for row in rows)
    assert result(root).statuses == {"within": 3, "outside": 0, "no_tariff": 0, "split_tariff": 0}
    lines = pq.read_table(root / OUT / "line_items.parquet").to_pylist()
    assert {row["scenario"] for row in lines} == {"backtest"}


def test_a_wrong_rate_is_outside_and_warned(tmp_path: Path) -> None:
    root = workspace(tmp_path, [{"offer": "MANUAL", "from": "2026-07-01", "to": None}], rate="0.05")
    found = run(root, "backtest_gas")
    assert [warning.code for warning in found.warnings] == [WarningCode.W_BACKTEST_OUTSIDE_TOLERANCE]
    assert result(root).statuses["outside"] == 3


def test_periods_without_a_tariff_or_split_by_two_are_not_priced(tmp_path: Path) -> None:
    root = workspace(
        tmp_path,
        [
            {"offer": "MANUAL", "from": "2026-07-01", "to": "2026-09-15"},
            {"offer": "MANUAL", "from": "2026-09-16", "to": "2026-09-30"},
        ],
    )
    run(root, "backtest_gas")
    rows = pq.read_table(root / OUT / "bills.parquet").to_pylist()
    assert [(row["period_start"], row["status"]) for row in rows] == [
        (date(2026, 8, 1), "within"),
        (date(2026, 9, 1), "split_tariff"),
        (date(2026, 10, 1), "no_tariff"),
    ]


def test_no_tariff_configured_warns(tmp_path: Path) -> None:
    root = workspace(tmp_path, [])
    found = run(root, "backtest_gas")
    assert [warning.code for warning in found.warnings] == [WarningCode.W_BACKTEST_NO_TARIFF]


def test_an_unknown_tariff_fails_tariffs_resolve(tmp_path: Path) -> None:
    root = workspace(tmp_path, [{"offer": "NOPE@EME~20260101T000000Z~0123abcd", "from": "2026-07-01", "to": None}])
    with pytest.raises(CheckFailedError) as caught:
        run(root, "backtest_gas")
    assert caught.value.details["failed"] == ["tariffs_resolve"]


def test_a_broken_bill_fails_lines_sum(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from energatron.core.engine import Bill
    from energatron.services.stages import backtest_gas

    root = workspace(tmp_path, [{"offer": "MANUAL", "from": "2026-07-01", "to": None}])
    real = backtest_gas.price_gas

    class TotalOff(Bill):
        """A bill whose total disagrees with its lines by a dollar."""

        def total(self) -> Decimal:
            return super().total() + 1

    def broken(*args: Any, **kwargs: Any) -> Bill:
        bill = real(*args, **kwargs)
        return TotalOff(lines=bill.lines, one_off_credit=bill.one_off_credit)

    monkeypatch.setattr(backtest_gas, "price_gas", broken)
    with pytest.raises(CheckFailedError) as caught:
        run(root, "backtest_gas")
    assert caught.value.details["failed"] == ["lines_sum"]
