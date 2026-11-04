"""The `cost_elec` stage (P4 spec §9): every priced tariff over the reference window, the outputs and four checks.

The workspace is the fixture raw zone (five covered months) and offer zone, with the upstream stages run.
"""

import json
import shutil
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq
import pytest

from energatron.core.errors import CheckFailedError
from energatron.core.models.costs import CostSummary
from energatron.services.stages import cost_elec
from tests.integration._stage_inputs import REFERENCE_MONTHS, build_inputs, run_stage_named

CHECKS = ["one_consumption_register", "window_present", "lines_sum", "energy_conserved"]


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("cost")
    build_inputs("cost_elec", root)
    return root


@pytest.fixture
def root(template: Path, tmp_path: Path) -> Path:
    found = tmp_path / "workspace"
    shutil.copytree(template, found)
    return found


def out(root: Path) -> Path:
    return root / "data" / "marts" / "costs" / "elec"


def table(root: Path, name: str) -> list[dict[str, Any]]:
    return pq.read_table(out(root) / name).to_pylist()


def summary(root: Path) -> CostSummary:
    return CostSummary.from_document(json.loads((out(root) / "summary.json").read_text()))


def failed(root: Path) -> list[str]:
    with pytest.raises(CheckFailedError):
        run_stage_named("cost_elec", root)
    provenance = json.loads((out(root) / "_provenance.json").read_text())
    return [check["name"] for check in provenance["checks"] if check["status"] == "fail"]


def test_every_check_passes_and_every_tariff_is_priced_in_both_scenarios(root: Path) -> None:
    result = run_stage_named("cost_elec", root)
    assert [check.name for check in result.checks] == CHECKS
    assert all(check.status == "pass" for check in result.checks)
    totals = table(root, "totals.parquet")
    tariffs = json.loads((root / "data" / "marts" / "offers" / "tariffs.json").read_text())["tariffs"]
    assert len(totals) == 2 * len(tariffs) == 8
    assert {row["scenario"] for row in totals} == {"conditions_met", "conditions_not_met"}


def test_the_window_months_are_the_billing_periods(root: Path) -> None:
    run_stage_named("cost_elec", root)
    found = summary(root)
    assert (found.suffix, found.periods) == ("E1", REFERENCE_MONTHS)
    starts = sorted({row["period_start"] for row in table(root, "line_items.parquet")})
    assert len(starts) == REFERENCE_MONTHS
    assert all(start.day == 1 for start in starts)


def test_totals_equal_the_sum_of_their_lines_and_ranks_order_them(root: Path) -> None:
    run_stage_named("cost_elec", root)
    lines = table(root, "line_items.parquet")
    for row in table(root, "totals.parquet"):
        own = [
            line["amount"]
            for line in lines
            if (line["offer_snapshot_id"], line["scenario"]) == (row["offer_snapshot_id"], row["scenario"])
        ]
        assert sum(own, Decimal(0)) == row["annual_ongoing"]
    for scenario in ("conditions_met", "conditions_not_met"):
        ranked = sorted(
            (row for row in table(root, "totals.parquet") if row["scenario"] == scenario), key=lambda row: row["rank"]
        )
        assert [row["rank"] for row in ranked] == [1, 2, 3, 4]
        assert [row["annual_ongoing"] for row in ranked] == sorted(row["annual_ongoing"] for row in ranked)
        assert summary(root).cheapest[scenario] == [row["offer_snapshot_id"] for row in ranked[:3]]


def test_a_conditional_discount_makes_the_met_scenario_cheaper(root: Path) -> None:
    run_stage_named("cost_elec", root)
    by = {(row["offer_snapshot_id"], row["scenario"]): row["annual_ongoing"] for row in table(root, "totals.parquet")}
    tou = next(sid for sid, _ in by if sid.startswith("TEST-TOU@EME"))
    assert by[(tou, "conditions_met")] < by[(tou, "conditions_not_met")]


def test_outputs_are_byte_identical_on_a_rerun(root: Path) -> None:
    run_stage_named("cost_elec", root)
    first = {path.name: path.read_bytes() for path in out(root).iterdir()}
    run_stage_named("cost_elec", root)
    assert {path.name: path.read_bytes() for path in out(root).iterdir()} == first


def test_two_consumption_registers_fail_one_consumption_register(root: Path) -> None:
    path = root / "data" / "interim" / "elec_interval_local" / "intervals.parquet"
    original = pq.read_table(path)
    second = original.to_pandas().assign(suffix="E2")
    import pyarrow as pa

    combined = pa.concat_tables([original, pa.Table.from_pandas(second, schema=original.schema, preserve_index=False)])
    pq.write_table(combined.replace_schema_metadata(original.schema.metadata), path)
    assert failed(root) == ["one_consumption_register", "window_present"]
    assert table(root, "totals.parquet") == []


def test_a_summary_without_a_window_fails_window_present(root: Path) -> None:
    path = root / "data" / "marts" / "usage_profile" / "summary.json"
    document = json.loads(path.read_text())
    document["suffixes"]["E1"] |= {"window_start": None, "window_end": None}
    path.write_text(json.dumps(document))
    assert failed(root) == ["window_present"]


def test_a_tampered_line_fails_lines_sum(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real = cost_elec.costing.cost_line_records

    def tampered(snapshot: str, scenario: str, lines: Any) -> list[dict[str, object]]:
        records = real(snapshot, scenario, lines)
        records[0]["amount"] = Decimal("999.99")
        return records

    monkeypatch.setattr(cost_elec.costing, "cost_line_records", tampered)
    assert failed(root) == ["lines_sum"]


def test_lost_energy_fails_energy_conserved(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cost_elec, "ENERGY_TOLERANCE_KWH", -1.0)
    assert failed(root) == ["energy_conserved"]


def test_months_cross_the_year_end() -> None:
    assert cost_elec._months("2025-11", "2026-01") == [  # pyright: ignore[reportPrivateUsage]
        (date(2025, 11, 1), date(2025, 11, 30)),
        (date(2025, 12, 1), date(2025, 12, 31)),
        (date(2026, 1, 1), date(2026, 1, 31)),
    ]
