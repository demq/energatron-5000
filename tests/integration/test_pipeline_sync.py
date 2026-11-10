"""`energatron pipeline sync` in a scratch workspace with real git, DVC and pre-commit (D3 §10.1, §13).

The written `dvc.yaml` equals the golden file; after `fingerprint`, `dvc repro` runs `catalog_index`; and the
workspace's `pipeline-sync` hook rejects a commit of a `dvc.yaml` edited by hand. Nothing here touches the real
workspace; pre-commit's cache lives under `tmp_path`.
"""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from energatron.adapters.cli.app import main
from energatron.core.models.catalog import SourceType
from tests.fixtures.params_text import set_reference_months
from tests.fixtures.pipeline_workspace import build_raw_zone
from tests.integration._scratch import Scratch, dvc, git, make_scratch
from tests.integration._stage_inputs import REFERENCE_MONTHS

pytestmark = pytest.mark.integration

GOLDEN = Path(__file__).resolve().parents[1] / "fixtures" / "golden" / "dvc.yaml"
BIN = Path(sys.executable).parent
SOURCES = sorted(source.value for source in SourceType)

# D3 §10.1's hook, with the venv's `energatron` in place of `uv run --frozen energatron`: the scratch workspace has
# no project of its own for uv to run.
PRE_COMMIT_CONFIG = """\
repos:
  - repo: local
    hooks:
      - id: pipeline-sync
        name: pipeline in sync with the tool
        entry: {energatron} pipeline sync --check
        language: system
        pass_filenames: false
        always_run: true
        stages: [pre-commit]
"""


def envelope(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, dict[str, Any]]:
    capsys.readouterr()
    code = main(list(argv))
    out = capsys.readouterr().out
    assert out.count("\n") == 1, out
    return code, json.loads(out)


@pytest.fixture
def scratch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Scratch:
    for name in ("ENERGATRON_JSON", "ENERGATRON_WORKSPACE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PRE_COMMIT_HOME", str(tmp_path / "pre-commit-home"))
    monkeypatch.setenv("PATH", f"{BIN}{os.pathsep}{os.environ.get('PATH', '')}")
    scratch = make_scratch(tmp_path, remote=False)
    monkeypatch.chdir(scratch.root)
    return scratch


def test_sync_writes_the_golden_dvc_yaml_and_dvc_repro_runs_catalog_index(
    capsys: pytest.CaptureFixture[str], scratch: Scratch
) -> None:
    code, synced = envelope(capsys, "pipeline", "sync", "--json")
    assert code == 0, synced["error"]
    assert synced["command"] == "pipeline"
    data = synced["data"]
    assert data["kind"] == "pipeline_sync_result"
    assert (data["dvc_yaml_written"], data["params_written"], data["dry_run"]) == (True, True, False)
    assert data["catalogs_created"] == SOURCES
    assert data["pointer_files"] == [
        *(f"data/raw/{source}.dvc" for source in SOURCES),
        "data/raw/.gitignore",
        "config/offer_rules.local/README.md",
        "config/bundle_rules.local/README.md",
        "data/runs.dvc",
        "data/decisions.dvc",
        "data/.gitignore",
    ]
    assert (scratch.root / "dvc.yaml").read_bytes() == GOLDEN.read_bytes()
    for pointer in data["pointer_files"]:
        assert (scratch.root / pointer).is_file()
    assert git(scratch.root, "log", "--oneline", "--all") == ""  # no commit

    assert envelope(capsys, "pipeline", "sync", "--check", "--json")[0] == 0
    set_reference_months(scratch.root / "params.yaml", REFERENCE_MONTHS)  # the fixture covers five whole months
    build_raw_zone(
        scratch.root
    )  # `localise` builds nothing unless the time basis is confirmed, so the pipeline needs data
    assert envelope(capsys, "fingerprint", "--json")[0] == 0
    dvc(scratch.root, "repro", "--quiet")
    assert (scratch.root / "data" / "marts" / "catalog" / "_provenance.json").is_file()
    assert json.loads(dvc(scratch.root, "status", "--json")) == {}

    code, again = envelope(capsys, "pipeline", "sync", "--json")
    assert code == 0
    assert again["data"]["dvc_yaml_written"] is False
    assert again["data"]["pointer_files"] == []


def test_check_and_dry_run_cannot_be_combined(capsys: pytest.CaptureFixture[str], scratch: Scratch) -> None:
    code, result = envelope(capsys, "pipeline", "sync", "--check", "--dry-run", "--json")
    assert code == 2
    assert result["error"]["code"] == "E_USAGE"
    assert not (scratch.root / "dvc.yaml").exists()


def test_check_fails_on_an_unsynced_workspace_naming_the_drift(
    capsys: pytest.CaptureFixture[str], scratch: Scratch
) -> None:
    code, result = envelope(capsys, "pipeline", "sync", "--check", "--json")
    assert code == 4
    assert result["error"]["code"] == "E_PIPELINE_NOT_SYNCED"
    assert result["error"]["details"]["drift"] == [
        "dvc.yaml",
        "params.yaml",
        "catalogs",
        "config/offer_rules.local",
        "config/bundle_rules.local",
        "data/runs",
        "data/decisions",
    ]


def test_the_pipeline_sync_hook_rejects_a_commit_of_a_hand_edited_dvc_yaml(
    capsys: pytest.CaptureFixture[str], scratch: Scratch
) -> None:
    assert envelope(capsys, "pipeline", "sync", "--json")[0] == 0
    scratch.write(".pre-commit-config.yaml", PRE_COMMIT_CONFIG.format(energatron=BIN / "energatron"))
    subprocess.run([str(BIN / "pre-commit"), "install"], cwd=scratch.root, check=True, capture_output=True)
    git(scratch.root, "add", "-A")
    git(scratch.root, "commit", "-q", "-m", "set up the pipeline")  # the hook passes

    dvc_yaml = scratch.root / "dvc.yaml"
    dvc_yaml.write_text(dvc_yaml.read_text(encoding="utf-8") + "# edited by hand\n", encoding="utf-8")
    git(scratch.root, "add", "dvc.yaml")
    rejected = subprocess.run(
        ["git", "commit", "-q", "-m", "edit dvc.yaml"], cwd=scratch.root, capture_output=True, text=True
    )
    assert rejected.returncode != 0
    assert "pipeline-sync" in rejected.stdout + rejected.stderr
    assert git(scratch.root, "rev-list", "--count", "HEAD").strip() == "1"
