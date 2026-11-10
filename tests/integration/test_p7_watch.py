"""`energatron watch --notify --json` end to end in a scratch workspace with real git and DVC (P7 spec §11).

The P6 fixture zone, `offers.retailers` set to the one CDR stand-in retailer on localhost, the notifier redirected to
a JSON-lines file, and the tool reported clean (the development checkout may be dirty). The first `watch` fetches,
lands, runs and notifies a cheaper plan when the recommendation says switch; a second `watch` with no plan changed
lands only the new fetch log, runs again and notifies no plan. Every plan, price and reading is synthetic.
"""

import json
import os
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml

from energatron.adapters import wiring
from energatron.adapters.notify.macos import NOTIFY_LOG_ENV
from energatron.core.models.recommend import Recommendation
from tests.fixtures import make_cdr as cdr
from tests.fixtures.gas_workspace import build_gas_zone
from tests.fixtures.params_text import set_reference_months
from tests.fixtures.pipeline_workspace import build_raw_zone
from tests.integration._scratch import git
from tests.integration._stage_inputs import REFERENCE_MONTHS
from tests.integration.test_land_cli import Workspace, envelope, make_land_workspace
from tests.integration.test_p6_pipeline import plans
from tests.unit.adapters.test_cdr_provider import LIST, Server

pytestmark = pytest.mark.integration

BIN = Path(sys.executable).parent
REC = Path("data/reports/recommendations/recommendation.json")


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
    monkeypatch.setenv(NOTIFY_LOG_ENV, str(tmp_path / "notifications.jsonl"))
    real = wiring.code_fingerprint
    monkeypatch.setattr(
        wiring, "code_fingerprint", lambda: real().model_copy(update={"tool_dirty": False, "tool_commit": "e" * 40})
    )
    found = make_land_workspace(tmp_path, monkeypatch, capsys)
    params = found.scratch.root / "params.yaml"
    set_reference_months(params, REFERENCE_MONTHS)
    document = yaml.safe_load(params.read_text(encoding="utf-8"))
    document["offers"]["retailers"] = ["alpha"]
    params.write_text(yaml.safe_dump(document, sort_keys=False, allow_unicode=True), encoding="utf-8")
    build_raw_zone(found.scratch.root)
    build_gas_zone(found.scratch.root)
    found.scratch.commit_all("the fixture zones, the reference window and one retailer")
    for plan in plans():
        server.json(f"/alpha/cds-au/v1/energy/plans/{plan['planId']}", cdr.detail_body(plan))
    server.json(LIST, cdr.list_body(plans()))
    return found


def notified(tmp_path: Path) -> list[dict[str, str]]:
    log = tmp_path / "notifications.jsonl"
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


def test_watch_fetches_runs_and_notifies_then_stays_quiet(
    workspace: Workspace, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = workspace.scratch.root
    code, first = envelope(capsys, "watch", "--notify")
    assert code == 0, json.dumps(first)
    data = first["data"]
    assert data["kind"] == "watch_result"
    assert data["fetch"]["landed"] >= 3  # two plans and the fetch log
    assert (data["ran"], data["run_error"], data["fetch_error"], data["before"]) == (True, None, None, None)
    rec = Recommendation.from_document(json.loads((root / REC).read_bytes()))
    assert data["after"] == rec.recommendation_id is not None
    assert data["cheaper_plan"] == (rec.action == "switch")
    assert notified(tmp_path) == data["notifications"]
    if data["cheaper_plan"]:
        assert data["notifications"][0]["title"] == "energatron: cheaper plan"
    assert git(root, "log", "-1", "--format=%s").strip().startswith(f"run: {data['run_id']}")
    assert git(root, "status", "--porcelain", "--untracked-files=no").strip() == ""

    code, second = envelope(capsys, "watch", "--notify")
    assert code == 0, second["error"]
    again = second["data"]
    # every fetch lands a new fetch log (it decides which plans are current), so watch runs again; same plans
    assert (again["fetch"]["landed"], again["ran"], again["cheaper_plan"]) == (1, True, False)
    assert again["before"] == data["after"]
    assert all(item["title"] != "energatron: cheaper plan" for item in again["notifications"])
