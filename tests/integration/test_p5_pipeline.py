"""The whole P5 path in a scratch workspace with real git and DVC (P5 spec §13).

`pipeline sync`, then: land a gas bill PDF, scaffold its record with `gas-bill new`, fill and land it; `fetch-offers`
against a CDR stand-in on localhost that lists gas and electricity plans; a gas `offer_manual` with the bill's own
prices as the tariff in force; `dvc repro` through all sixteen stages; `verify`. Every plan, price and reading is
synthetic.
"""

import json
import os
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq
import pytest
import yaml

from energatron.adapters import wiring
from energatron.adapters.pipeline.dvc_runner import DvcRunner
from energatron.services.stages.registry import STAGES
from tests.fixtures import make_cdr as cdr
from tests.fixtures import make_gas as gas
from tests.fixtures.make_sources import make_pdf
from tests.fixtures.params_text import set_reference_months
from tests.fixtures.pipeline_workspace import build_raw_zone
from tests.integration._scratch import git
from tests.integration._stage_inputs import REFERENCE_MONTHS
from tests.integration.test_land_cli import Workspace, envelope, make_land_workspace
from tests.unit.adapters.test_cdr_provider import LIST, Server

pytestmark = pytest.mark.integration

BIN = Path(sys.executable).parent
GAS_NETWORK = "AGN Metro/Barossa/Peterborough"  # the shipped region's gas distributor, copied by `pipeline sync`


def plans() -> list[dict[str, Any]]:
    return [
        cdr.gas_plan("P5-GAS@EME", distributors=[GAS_NETWORK]),
        cdr.gas_plan("P5-GAS-ELSEWHERE@EME", distributors=["AGN Mount Gambier"]),
        cdr.plan("P5-ELEC@EME", pricing_model="SINGLE_RATE", distributors=["SA Power Networks"]),
    ]


@pytest.fixture
def server() -> Iterator[Server]:
    found = Server()
    found.thread.start()
    yield found
    found.httpd.shutdown()
    found.httpd.server_close()


@pytest.fixture
def workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], server: Server
) -> Workspace:
    monkeypatch.setenv("PATH", f"{BIN}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv(wiring.CDR_ROOT_ENV, server.root)
    found = make_land_workspace(tmp_path, monkeypatch, capsys)
    set_reference_months(found.scratch.root / "params.yaml", REFERENCE_MONTHS)
    build_raw_zone(found.scratch.root)  # the meter stages need their inputs for `dvc repro` to reach the gas stages
    found.scratch.commit_all("the fixture raw zone and the reference window")
    return found


def test_land_a_gas_bill_fetch_repro_and_verify(
    workspace: Workspace, server: Server, capsys: pytest.CaptureFixture[str]
) -> None:
    root = workspace.scratch.root
    pdf = workspace.landing / "bill.pdf"
    pdf.write_bytes(make_pdf(creation_date="D:20261005120000+10'30'"))
    code, landed = envelope(capsys, "land", str(pdf), "--source", "gas_bill")
    assert code == 0, landed["error"]
    (pdf_id,) = landed["data"]["landed"]

    code, scaffold = envelope(capsys, "gas-bill", "new", pdf_id)
    assert code == 0, scaffold["error"]
    record = root / scaffold["data"]["path"]
    code, refused = envelope(capsys, "land", str(record))
    assert (code, refused["error"]["details"].get("reason")) == (3, "invalid_gas_bill_record")
    record.write_bytes(
        gas.record_bytes(gas.record(pdf_dataset_id=pdf_id, volumes=(17, 24), read_types=["actual", "estimate"]))
    )
    code, landed = envelope(capsys, "land", str(record))
    assert code == 0, landed["error"]

    found = plans()
    server.json(LIST, cdr.list_body(found))
    for plan in found:
        server.json(f"/alpha/cds-au/v1/energy/plans/{plan['planId']}", cdr.detail_body(plan))
    code, fetched = envelope(capsys, "fetch-offers", "--retailer", "alpha")
    assert code == 0, fetched["error"]
    (alpha,) = fetched["data"]["retailers"]
    assert (alpha["listed"], alpha["selected"], alpha["left_out"]) == (3, 2, {"distributor": 1})

    contract = cdr.gas_plan(
        periods=[cdr.gas_period([cdr.rate(str(gas.USAGE_RATE), unit=None)], supply=str(gas.SUPPLY_RATE))]
    )
    manual = workspace.landing / "tariff.yaml"
    manual.write_text(
        cdr.offer_manual_text(
            "MANUAL-P5-GAS", published="2026-07-01T00:00:00Z", effective_from="2026-07-01",
            gas_contract=contract["gasContract"],
        ),
        encoding="utf-8",
    )  # fmt: skip
    code, landed = envelope(capsys, "land", str(manual))
    assert code == 0, landed["error"]
    catalog = (root / "data/raw/offer_manual/_catalog.jsonl").read_text(encoding="utf-8").splitlines()
    snapshot = json.loads(catalog[-1])["extra"]["offer_snapshot_id"]
    params = yaml.safe_load((root / "params.yaml").read_text(encoding="utf-8"))
    params["backtest"]["gas_tariffs"] = [{"offer": snapshot, "from": "2026-07-01", "to": None}]
    (root / "params.yaml").write_text(yaml.safe_dump(params, sort_keys=False), encoding="utf-8")
    git(root, "add", "params.yaml")
    git(root, "commit", "-q", "-m", "the gas tariff in force")

    ran = DvcRunner(workspace.scratch.paths, fingerprint=wiring.code_fingerprint).repro().ran
    assert ran == list(STAGES) and len(ran) == len(STAGES)
    bills = json.loads((root / "data/interim/gas_bills/summary.json").read_text())
    assert (bills["current"], all(bills["checks"].values())) == (1, True)
    statuses = {
        row["plan_id"]: row["status"]
        for row in pq.read_table(root / "data/marts/offers_gas/offers.parquet").to_pylist()
    }
    assert statuses == {"P5-GAS@EME": "normalised", "MANUAL-P5-GAS": "normalised"}
    elec = {row["plan_id"] for row in pq.read_table(root / "data/marts/offers/offers.parquet").to_pylist()}
    assert elec == {"P5-ELEC@EME"}
    totals = pq.read_table(root / "data/marts/costs/gas/totals.parquet").to_pylist()
    assert len(totals) == 4  # two gas tariffs, two scenarios
    backtest = json.loads((root / "data/reports/validation/backtest_gas/result.json").read_text())
    assert backtest["statuses"]["within"] == 1

    git(root, "add", "dvc.lock", ".energatron/code_fingerprint.json")
    git(root, "commit", "-q", "-m", "run")
    code, verified = envelope(capsys, "verify")
    assert code == 0, verified["error"]
