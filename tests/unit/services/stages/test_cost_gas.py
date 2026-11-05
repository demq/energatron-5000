"""The `cost_gas` stage (P5 spec §9.3): the gas tariffs priced over the reference periods, annualised, ranked, and its
three checks. The workspace is the fixture gas zone and offer zone with the upstream gas stages run."""

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from energatron.core.canonical import content_hash, sha256_hex
from energatron.core.errors import CheckFailedError, WarningCode
from energatron.core.models.costs import GasCostSummary
from energatron.core.models.provenance import StageCode
from energatron.services.stages.registry import get_stage
from energatron.services.stages.runner import StageResult, prepare_stage, run_stage
from tests.fixtures.gas_workspace import build_gas_zone
from tests.fixtures.offer_workspace import build_offer_zone
from tests.fixtures.params_text import params_v5_text
from tests.fixtures.pipeline_workspace import empty_raw_zone

OUT = Path("data/marts/costs/gas")


def run(root: Path, name: str) -> StageResult:
    spec = get_stage(name)
    prepared = prepare_stage(spec, root=root, params_file=root / "params.yaml")
    source = sha256_hex(name.encode())
    digest = content_hash({"source_sha256": source, "packages": {}, "python": "3.14.0"})
    code = StageCode(stage_sha256=digest, source_sha256=source, python="3.14.0", packages={}, files=["__init__.py"])
    return run_stage(
        spec, prepared.inputs, prepared.params, prepared.outputs, code=code, recorded_code=code.stage_sha256
    )


@pytest.fixture
def root(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    empty_raw_zone(workspace)
    (workspace / "params.yaml").write_text(params_v5_text(region=True), encoding="utf-8")
    build_gas_zone(workspace)
    build_offer_zone(workspace)
    for name in ("gas_bills", "gas_profile", "normalise_gas_offers"):
        run(workspace, name)
    return workspace


def summary(root: Path) -> GasCostSummary:
    return GasCostSummary.from_document(json.loads((root / OUT / "summary.json").read_bytes()))


def test_every_tariff_is_priced_in_both_scenarios_annualised_and_ranked(root: Path) -> None:
    result = run(root, "cost_gas")
    assert [(check.name, check.status) for check in result.checks] == [
        ("reference_present", "pass"),
        ("lines_sum", "pass"),
        ("energy_conserved", "pass"),
    ]
    assert [warning.code for warning in result.warnings] == [WarningCode.W_GAS_PARTIAL_YEAR]
    found = summary(root)
    assert (found.periods, found.reference_days, found.partial_year, found.tariffs) == (2, 92, True, 3)
    totals = pq.read_table(root / OUT / "totals.parquet").to_pylist()
    assert len(totals) == 6
    for row in totals:
        expected = (row["reference_cost"] * Decimal(365) / 92).quantize(Decimal("0.01"))
        assert row["annual_ongoing"] == expected
    for scenario in ("conditions_met", "conditions_not_met"):
        ranked = sorted((row for row in totals if row["scenario"] == scenario), key=lambda row: row["rank"])
        assert [row["rank"] for row in ranked] == [1, 2, 3]
        assert [row["annual_ongoing"] for row in ranked] == sorted(row["annual_ongoing"] for row in ranked)
        assert found.cheapest[scenario] == [row["offer_snapshot_id"] for row in ranked]
    lines = pq.read_table(root / OUT / "line_items.parquet")
    assert lines.schema.metadata[b"energatron.kind"] == b"gas_cost_line"
    assert {row["unit"] for row in lines.to_pylist() if row["line"] == "usage"} == {"MJ"}


def test_a_full_year_reference_does_not_warn(root: Path) -> None:
    text = (root / "params.yaml").read_text(encoding="utf-8").replace("full_year_days: 334", "full_year_days: 90")
    (root / "params.yaml").write_text(text, encoding="utf-8")
    run(root, "gas_profile")
    assert list(run(root, "cost_gas").warnings) == []


def test_the_outputs_are_byte_identical_on_a_rerun(root: Path) -> None:
    run(root, "cost_gas")
    first = {path.name: path.read_bytes() for path in (root / OUT).iterdir()}
    run(root, "cost_gas")
    assert {path.name: path.read_bytes() for path in (root / OUT).iterdir()} == first


def test_no_gas_bills_give_empty_outputs_that_pass(tmp_path: Path) -> None:
    empty_raw_zone(tmp_path)
    (tmp_path / "params.yaml").write_text(params_v5_text(region=True), encoding="utf-8")
    build_offer_zone(tmp_path)
    for name in ("gas_bills", "gas_profile", "normalise_gas_offers"):
        run(tmp_path, name)
    result = run(tmp_path, "cost_gas")
    assert all(check.status == "pass" for check in result.checks)
    assert (summary(tmp_path).tariffs, list(result.warnings)) == (0, [])


def rewrite(path: Path, change: Any) -> None:
    table = pq.read_table(path)
    pq.write_table(pa.Table.from_pylist(change(table.to_pylist()), schema=table.schema), path)


def test_a_profile_without_reference_periods_fails_reference_present(root: Path) -> None:
    rewrite(
        root / "data/marts/gas_profile/periods.parquet", lambda rows: [{**row, "in_reference": False} for row in rows]
    )
    with pytest.raises(CheckFailedError) as caught:
        run(root, "cost_gas")
    assert caught.value.details["failed"] == ["reference_present"]


@pytest.mark.parametrize("check", ["lines_sum", "energy_conserved"])
def test_a_broken_bill_fails_its_check(root: Path, monkeypatch: pytest.MonkeyPatch, check: str) -> None:
    from energatron.core.engine import Bill
    from energatron.services.stages import cost_gas

    real = cost_gas.price_gas

    def broken(*args: Any, **kwargs: Any) -> Bill:
        bill = real(*args, **kwargs)
        first, *rest = bill.lines
        if check == "lines_sum":
            moved = first.__class__(**{**first.__dict__, "period_start": first.period_start.replace(year=2000)})
        else:
            usage = next(line for line in bill.lines if line.line == "usage")
            moved = usage.__class__(**{**usage.__dict__, "quantity": usage.quantity + 1})
            rest = [line for line in bill.lines if line is not usage]
        return Bill(lines=(moved, *rest), one_off_credit=bill.one_off_credit)

    monkeypatch.setattr(cost_gas, "price_gas", broken)
    with pytest.raises(CheckFailedError) as caught:
        run(root, "cost_gas")
    assert check in caught.value.details["failed"]  # type: ignore[operator]
