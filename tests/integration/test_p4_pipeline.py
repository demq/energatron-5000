"""The whole P4 path in a scratch workspace with real git and DVC (P4 spec §14).

`pipeline sync`, the fixture raw zone, then `energatron fetch-offers` against a CDR stand-in on localhost
(`ENERGATRON_CDR_ROOT`), `dvc repro` through every stage, a second fetch with one plan changed, `offers diff`, and
`verify`. Every plan, price and reading is synthetic.
"""

import json
import os
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq
import pytest

from energatron.adapters import wiring
from energatron.adapters.pipeline.dvc_runner import DvcRunner
from energatron.services.stages.registry import STAGES
from tests.fixtures import make_cdr as cdr
from tests.fixtures.params_text import set_reference_months
from tests.fixtures.pipeline_workspace import build_raw_zone
from tests.integration._scratch import git
from tests.integration._stage_inputs import REFERENCE_MONTHS
from tests.integration.test_land_cli import Workspace, envelope, make_land_workspace
from tests.unit.adapters.test_cdr_provider import LIST, Server

pytestmark = pytest.mark.integration

BIN = Path(sys.executable).parent
DISTRIBUTOR = "SA Power Networks"  # the shipped region's, which `pipeline sync` copies into params.yaml


def plans(*, peak: str = "0.40", updated: str = "2026-10-01T00:00:00Z") -> list[dict[str, Any]]:
    return [
        cdr.plan(
            "P4-TOU@EME",
            distributors=[DISTRIBUTOR],
            last_updated=updated,
            periods=[cdr.tou_period(cdr.standard_tou(peak=peak))],
        ),
        cdr.plan("P4-FLAT@EME", pricing_model="SINGLE_RATE", distributors=[DISTRIBUTOR]),
        cdr.plan("P4-CL@EME", pricing_model="TIME_OF_USE_CONT_LOAD", distributors=[DISTRIBUTOR]),
        cdr.plan("P4-ELSEWHERE@EME", distributors=["Other Networks"]),
    ]


def serve(server: Server, found: list[dict[str, Any]]) -> None:
    server.json(LIST, cdr.list_body(found))
    for plan in found:
        server.json(f"/alpha/cds-au/v1/energy/plans/{plan['planId']}", cdr.detail_body(plan))


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
    build_raw_zone(found.scratch.root)
    found.scratch.commit_all("the fixture raw zone and the reference window")
    return found


def test_fetch_repro_diff_and_verify(workspace: Workspace, server: Server, capsys: pytest.CaptureFixture[str]) -> None:
    root = workspace.scratch.root
    serve(server, plans())
    code, fetched = envelope(capsys, "fetch-offers", "--retailer", "alpha", "--json")
    assert code == 0, fetched["error"]
    (alpha,) = fetched["data"]["retailers"]
    assert (alpha["listed"], alpha["selected"], alpha["left_out"]) == (4, 3, {"distributor": 1})
    assert len(fetched["data"]["landed"]) == 4  # three plans and the fetch log
    assert git(root, "status", "--porcelain").strip() == ""  # `land` committed everything it wrote

    ran = DvcRunner(workspace.scratch.paths, fingerprint=wiring.code_fingerprint).repro().ran
    assert ran == list(STAGES)
    statuses = {
        row["plan_id"]: row["status"] for row in pq.read_table(root / "data/marts/offers/offers.parquet").to_pylist()
    }
    assert statuses == {"P4-TOU@EME": "normalised", "P4-FLAT@EME": "normalised", "P4-CL@EME": "excluded"}
    totals = pq.read_table(root / "data/marts/costs/elec/totals.parquet").to_pylist()
    assert len(totals) == 4  # two tariffs, two scenarios
    backtest = json.loads((root / "data/reports/validation/backtest/result.json").read_text())
    assert backtest["tariffs_configured"] == 0

    serve(server, plans(peak="0.43", updated="2026-10-20T00:00:00Z"))
    code, again = envelope(capsys, "fetch-offers", "--retailer", "alpha", "--json")
    assert code == 0, again["error"]
    assert again["data"]["retailers"][0]["fetched"] == 1  # only the changed plan
    code, diff = envelope(capsys, "offers", "diff", "--json")
    assert code == 0, diff["error"]
    deltas = [change["delta"] for change in diff["data"]["changes"] if change["path"].endswith("unit_price")]
    assert deltas == ["+0.03"]

    git(root, "add", "dvc.lock", ".energatron/code_fingerprint.json")
    git(root, "commit", "-q", "-m", "run")
    DvcRunner(workspace.scratch.paths, fingerprint=wiring.code_fingerprint).repro()
    git(root, "add", "dvc.lock")
    git(root, "commit", "-q", "-m", "run again", "--allow-empty")
    code, verified = envelope(capsys, "verify", "--json")
    assert code == 0, verified["error"]
