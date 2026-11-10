"""The whole P6 path in a scratch workspace with real git and DVC (P6 spec §12).

`pipeline sync`; the fixture raw and gas zones; `fetch-offers` against a CDR stand-in on localhost listing an
electricity and a gas plan of one brand; `energatron run` (through the service, with a clean tool state: the
development checkout may be dirty), whose manifest and commit name the run and the recommendation; `decide` on the top
option; a second run with the same recommendation ID and only the outcomes stage rerun; `verify`; and `run --draft
--json` printing one document. Every plan, price and reading is synthetic.
"""

import json
import os
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from energatron.adapters import wiring
from energatron.adapters.cli.app import main
from energatron.core.ids import parse_decision_id, parse_rec_id, parse_run_id
from energatron.core.models.recommend import Decision, Recommendation, RunManifest, ToolState
from energatron.services import run as run_service
from tests.fixtures import make_cdr as cdr
from tests.fixtures.gas_workspace import build_gas_zone
from tests.fixtures.params_text import set_reference_months
from tests.fixtures.pipeline_workspace import build_raw_zone
from tests.integration._scratch import git
from tests.integration._stage_inputs import REFERENCE_MONTHS
from tests.integration.test_land_cli import Workspace, envelope, make_land_workspace
from tests.unit.adapters.test_cdr_provider import LIST, Server

pytestmark = pytest.mark.integration

BIN = Path(sys.executable).parent
CLEAN = ToolState(energatron_version="0.1.0", install="editable", tool_commit="e" * 40, tool_dirty=False)
REC = Path("data/reports/recommendations/recommendation.json")


def plans() -> list[dict[str, Any]]:
    return [
        cdr.plan("P6-ELEC@EME", pricing_model="SINGLE_RATE", distributors=["SA Power Networks"]),
        cdr.gas_plan("P6-GAS@EME", distributors=["AGN Metro/Barossa/Peterborough"]),
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
    build_raw_zone(found.scratch.root)
    build_gas_zone(found.scratch.root)  # gas bills, so the gas plan is priced too
    found.scratch.commit_all("the fixture raw and gas zones and the reference window")
    return found


def run_clean(root: Path) -> run_service.RunResult:
    return run_service.run(wiring.build_context(wiring.WorkspacePaths(root=root)), tool=CLEAN)


def test_run_decide_run_again_and_verify(
    workspace: Workspace, server: Server, capsys: pytest.CaptureFixture[str]
) -> None:
    root = workspace.scratch.root
    fetch(server, capsys)
    first = run_clean(root)
    assert parse_run_id(first.run_id)
    assert first.recommendation_id is not None and parse_rec_id(first.recommendation_id)
    manifest = RunManifest.from_document(json.loads((root / "data/runs" / f"{first.run_id}.json").read_bytes()))
    assert (manifest.recommendation_id, manifest.draft) == (first.recommendation_id, False)
    assert manifest.datasets and manifest.fetch_batches
    assert "recommend" in manifest.stages.ran and "outcomes" in manifest.stages.ran
    assert git(root, "log", "-1", "--format=%s").strip() == f"run: {first.run_id} {first.recommendation_id}"
    assert git(root, "status", "--porcelain", "--untracked-files=no").strip() == ""
    recommendation = Recommendation.from_document(json.loads((root / REC).read_bytes()))
    assert recommendation.recommendation_id == first.recommendation_id
    assert {"P6-ELEC@EME", "P6-GAS@EME"} <= {plan.split("~")[0] for plan in recommendation.inputs.offer_snapshot_ids}
    assert set(manifest.datasets) >= set(recommendation.inputs.dataset_ids)
    top = recommendation.options[0]
    assert top.electricity is not None and top.gas is not None

    code, dry = envelope(
        capsys, "decide", first.recommendation_id, "--switch-date", "2026-12-01",
        "--elec", top.electricity.offer_snapshot_id, "--gas", top.gas.offer_snapshot_id, "--dry-run",
    )  # fmt: skip
    assert code == 0, dry["error"]
    assert (dry["data"]["dry_run"], dry["data"]["commit"], dry["data"]["run_id"]) == (True, None, first.run_id)
    code, decided = envelope(
        capsys, "decide", first.recommendation_id, "--switch-date", "2026-12-01",
        "--elec", top.electricity.offer_snapshot_id, "--gas", top.gas.offer_snapshot_id,
    )  # fmt: skip
    assert code == 0, decided["error"]
    decision_id = decided["data"]["decision_id"]
    assert parse_decision_id(decision_id)
    decision = Decision.from_document(json.loads((root / "data/decisions" / f"{decision_id}.json").read_bytes()))
    assert (decision.recommendation_id, decision.run_id) == (first.recommendation_id, first.run_id)
    electricity = decision.choices["electricity"]
    assert electricity is not None and electricity.annual_ongoing == top.electricity.annual_ongoing
    assert git(root, "log", "-1", "--format=%s").strip() == f"decide: {decision_id} {first.recommendation_id}"

    second = run_clean(root)
    assert second.recommendation_id == first.recommendation_id
    again = RunManifest.from_document(json.loads((root / "data/runs" / f"{second.run_id}.json").read_bytes()))
    assert again.stages.ran == ["outcomes"]  # only the decisions changed
    outcomes = json.loads((root / "data/reports/outcomes/summary.json").read_bytes())
    assert outcomes["decisions"] == [decision_id]

    code, verified = envelope(capsys, "verify")
    assert code == 0, verified["error"]


def test_run_json_prints_one_document(workspace: Workspace, capsys: pytest.CaptureFixture[str]) -> None:
    capsys.readouterr()
    code = main(["run", "--draft", "--json"])
    out = capsys.readouterr().out
    assert code == 0, out
    document = json.loads(out)  # exactly one JSON document, nothing from the dvc repro children
    assert (document["command"], document["data"]["kind"], document["data"]["draft"]) == ("run", "run_result", True)
    manifest = root_manifest(workspace.scratch.root, document["data"]["run_id"])
    assert manifest.draft is True


def root_manifest(root: Path, run_id: str) -> RunManifest:
    return RunManifest.from_document(json.loads((root / "data/runs" / f"{run_id}.json").read_bytes()))


def fetch(server: Server, capsys: pytest.CaptureFixture[str]) -> None:
    found = plans()
    server.json(LIST, cdr.list_body(found))
    for plan in found:
        server.json(f"/alpha/cds-au/v1/energy/plans/{plan['planId']}", cdr.detail_body(plan))
    code, fetched = envelope(capsys, "fetch-offers", "--retailer", "alpha")
    assert code == 0, fetched["error"]


def test_a_decision_on_a_draft_only_recommendation_is_refused(
    workspace: Workspace, server: Server, capsys: pytest.CaptureFixture[str]
) -> None:
    fetch(server, capsys)
    code, ran = envelope(capsys, "run", "--draft")
    assert code == 0, ran["error"]
    rec = Recommendation.from_document(json.loads((workspace.scratch.root / REC).read_bytes()))
    assert rec.recommendation_id is not None and rec.options
    top = rec.options[0]
    assert top.electricity is not None
    code, refused = envelope(
        capsys,
        "decide",
        rec.recommendation_id,
        "--switch-date",
        "2026-12-01",
        "--elec",
        top.electricity.offer_snapshot_id,
    )
    assert (code, refused["error"]["details"]["reason"]) == (3, "not_in_clean_run")
    code, refused = envelope(capsys, "decide", "REC-20260101-0123abcd", "--switch-date", "2026-12-01", "--elec", "x")
    assert (code, refused["error"]["details"]["reason"]) == (3, "not_current")
