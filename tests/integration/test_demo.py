"""The demo end to end (P8 spec §5.4): `init --demo --no-sync` in a scratch folder, with real git and DVC.

The test venv runs DVC and the stages. The development checkout may be dirty, so the live fingerprint's `tool_dirty` is
reported clean here (as `test_p6_pipeline` passes a clean `ToolState`); in CI the checkout is clean anyway. Everything
the demo lands is synthetic.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from energatron.adapters import wiring
from energatron.core.models.recommend import Recommendation, RunManifest
from energatron.services import demo
from tests.integration._scratch import git
from tests.integration.test_init_cli import init_args
from tests.integration.test_land_cli import envelope

pytestmark = pytest.mark.integration

BIN = Path(sys.executable).parent


@pytest.fixture
def clean_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    real = wiring.code_fingerprint
    monkeypatch.setattr(wiring, "code_fingerprint", lambda: real().model_copy(update={"tool_dirty": False}))
    monkeypatch.setenv("PATH", f"{BIN}{os.pathsep}{os.environ.get('PATH', '')}")
    for name in ("ENERGATRON_JSON", "ENERGATRON_WORKSPACE"):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.usefixtures("clean_tool")
def test_init_demo_reaches_a_recommendation_that_verifies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    root = tmp_path / "demo"
    code, result = envelope(capsys, *init_args(root, "--demo"))
    assert code == 0, result["error"]
    found = result["data"]["demo"]
    assert found["kind"] == "demo_result" and found["recommendation_id"]
    assert found["landed"] == 2 + 2 * len(demo.gas_periods())
    assert found["offers"] == len(demo.demo_plans(wiring.load_region_profile("sa"))) + 1  # and the fetch log

    manifest = RunManifest.from_document(json.loads((root / "data/runs" / f"{found['run_id']}.json").read_bytes()))
    assert (manifest.draft, manifest.recommendation_id) == (False, found["recommendation_id"])
    for report in found["reports"]:
        assert (root / report).is_file()
    rec = Recommendation.from_document(
        json.loads((root / "data/reports/recommendations/recommendation.json").read_bytes())
    )
    assert rec.options and all(plan.endswith("@DEMO") or "~" in plan for plan in rec.inputs.offer_snapshot_ids)
    assert git(root, "status", "--porcelain", "--untracked-files=all") == ""
    assert not (root / demo.LANDING).exists()

    monkeypatch.chdir(root)
    code, status = envelope(capsys, "status")
    assert code == 0, status["error"]
    codes = {warning["code"] for warning in status["warnings"]}
    assert not codes & {"W_RUN_BEHIND", "W_LANDING_NEW", "W_LANDING_LANDED", "W_OFFERS_STALE"}
    code, verified = envelope(capsys, "verify")
    assert code == 0, verified["error"]
    code, upgraded = envelope(capsys, "upgrade", "--dry-run")
    assert code == 0, upgraded["error"]
    assert {item["status"] for item in upgraded["data"]["files"]} == {"unchanged"}

    guard = subprocess.run(
        [sys.executable, str(root / ".energatron/scripts/privacy_guard.py"), "--mode", "workspace", "--history"],
        cwd=root,
        capture_output=True,
        text=True,
    )
    assert guard.returncode == 0, guard.stderr


def test_demo_refuses_a_workspace_that_is_not_the_demo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    for name in ("ENERGATRON_JSON", "ENERGATRON_WORKSPACE"):
        monkeypatch.delenv(name, raising=False)
    code, result = envelope(capsys, *init_args(tmp_path / "ws"))
    assert code == 0, result["error"]
    monkeypatch.chdir(tmp_path / "ws")
    code, refused = envelope(capsys, "demo")
    assert (code, refused["error"]["code"], refused["error"]["details"]["reason"]) == (2, "E_USAGE", "not_demo")
    assert git(tmp_path / "ws", "log", "--format=%s").splitlines() == ["init: energatron workspace"]
