"""The `outcomes` stage (P6 spec §10): bills after a switch attributed to the latest decision for their fuel and
compared with the projection; `decisions_valid`. The workspace is the fixture raw, offer and gas zones with the backtest
and gas bills run, and one synthetic decision switching both fuels before every bill."""

import json
import shutil
from decimal import Decimal
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq
import pytest

from energatron.core.errors import CheckFailedError
from energatron.core.models.recommend import OutcomesSummary
from tests.integration._stage_inputs import SYNTHETIC_DECISION, build_inputs, run_stage_named

OUT = Path("data/reports/outcomes")
DECISIONS = Path("data/decisions")


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("outcomes") / "workspace"
    build_inputs("outcomes", root)
    return root


@pytest.fixture
def root(template: Path, tmp_path: Path) -> Path:
    copy = tmp_path / "workspace"
    shutil.copytree(template, copy)
    return copy


def summary(root: Path) -> OutcomesSummary:
    return OutcomesSummary.from_document(json.loads((root / OUT / "summary.json").read_bytes()))


def rows(root: Path) -> list[dict[str, Any]]:
    return pq.read_table(root / OUT / "outcomes.parquet").to_pylist()


def decision(**changes: Any) -> dict[str, Any]:
    return {**SYNTHETIC_DECISION, **changes}


def write(root: Path, doc: dict[str, Any], name: str | None = None) -> None:
    (root / DECISIONS / f"{name or doc['decision_id']}.json").write_text(json.dumps(doc), encoding="utf-8")


def gas_bill_totals(root: Path) -> list[tuple[Any, Any, Decimal]]:
    table = pq.read_table(root / "data/interim/gas_bills/bills.parquet").to_pylist()
    return sorted((row["period_start"], row["period_end"], row["total"]) for row in table)


def test_bills_after_the_switch_are_compared_with_the_projection(root: Path) -> None:
    result = run_stage_named("outcomes", root)
    assert [(check.name, check.status) for check in result.checks] == [("decisions_valid", "pass")]
    found = rows(root)
    gas = [row for row in found if row["fuel"] == "gas"]
    assert [(row["period_start"], row["period_end"], row["actual"]) for row in gas] == gas_bill_totals(root)
    for row in found:
        annual = Decimal(SYNTHETIC_DECISION["choices"][row["fuel"]]["annual_ongoing"])
        assert row["days"] == (row["period_end"] - row["period_start"]).days + 1
        assert row["projected"] == (annual * row["days"] / 365).quantize(Decimal("0.01"))
        assert row["diff"] == row["actual"] - row["projected"]
    totals = summary(root).outcomes["DEC-20250101-01"]["gas"]
    assert (totals.bills, totals.actual) == (len(gas), sum((row["actual"] for row in gas), Decimal(0)))


def test_each_bill_goes_to_the_latest_decision_switched_before_it(root: Path) -> None:
    bills = gas_bill_totals(root)
    later = bills[-1][0]
    second = decision(decision_id="DEC-20250102-01", switch_date=later.isoformat())
    second["choices"] = {"electricity": None, "gas": SYNTHETIC_DECISION["choices"]["gas"]}
    write(root, second)
    run_stage_named("outcomes", root)
    owners = {(row["fuel"], row["period_start"]): row["decision_id"] for row in rows(root)}
    assert owners[("gas", later)] == "DEC-20250102-01"
    assert owners[("gas", bills[0][0])] == "DEC-20250101-01"
    assert summary(root).decisions == ["DEC-20250101-01", "DEC-20250102-01"]


def test_no_decision_gives_empty_outputs_that_pass(root: Path) -> None:
    (root / DECISIONS / "DEC-20250101-01.json").unlink()
    result = run_stage_named("outcomes", root)
    assert all(check.status == "pass" for check in result.checks)
    assert (rows(root), summary(root).outcomes) == ([], {})


@pytest.mark.parametrize(
    ("doc", "name"),
    [
        (decision(), "DEC-20250101-02"),  # its file name isn't its ID
        ({"kind": "decision", "schema_version": 1}, "DEC-20250101-03"),
    ],
)
def test_an_invalid_decision_fails_decisions_valid(root: Path, doc: dict[str, Any], name: str) -> None:
    write(root, doc, name)
    with pytest.raises(CheckFailedError) as caught:
        run_stage_named("outcomes", root)
    assert caught.value.details["failed"] == ["decisions_valid"]


def test_the_readme_is_ignored(root: Path) -> None:
    (root / DECISIONS / "README.md").write_text("Decisions.\n", encoding="utf-8")
    assert all(check.status == "pass" for check in run_stage_named("outcomes", root).checks)
