"""`energatron init --no-sync` with real git and DVC (P8 spec §4, §11): the test venv already has the tool and DVC.

The new workspace is committed once, holds exactly what the template and `pipeline sync` write, passes the privacy
guard over every tracked path, and `status` accepts it. Every private value is synthetic.
"""

import subprocess
import sys
from pathlib import Path

import pytest

from energatron.adapters.cli.app import main
from tests.fixtures.synthetic import SYNTHETIC_NMI11, SYNTHETIC_SERIAL
from tests.integration._scratch import git
from tests.integration.test_land_cli import envelope

pytestmark = pytest.mark.integration

REPO = Path(__file__).resolve().parents[2]


def init_args(root: Path, *extra: str) -> list[str]:
    return ["init", str(root), "--no-sync", "--tool-source", str(REPO), *extra]


def test_init_makes_a_committed_workspace_that_status_accepts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    for name in ("ENERGATRON_JSON", "ENERGATRON_WORKSPACE"):
        monkeypatch.delenv(name, raising=False)
    root = tmp_path / "ws"
    remote = tmp_path / "Backups" / "dvc"
    args = init_args(root, "--nmi", SYNTHETIC_NMI11, "--meter-serial", SYNTHETIC_SERIAL, "--remote", str(remote))
    code, result = envelope(capsys, *args)
    assert code == 0, result["error"]
    data = result["data"]
    assert (data["root_name"], data["private_config"], data["git_identity_set"]) == ("ws", True, True)
    assert str(tmp_path) not in str(result) and SYNTHETIC_NMI11 not in str(result)

    assert git(root, "log", "--format=%s").splitlines() == ["init: energatron workspace"]
    assert git(root, "status", "--porcelain", "--untracked-files=all") == ""
    tracked = set(git(root, "ls-files").split())
    assert {"dvc.yaml", "params.yaml", "private.dvc", ".energatron/template.json", "AGENTS.md"} <= tracked
    assert not any(path.startswith(("private/", ".venv/")) for path in tracked)
    assert (root / "AGENTS.md").is_symlink() and remote.is_dir()
    assert (root / ".energatron" / "local.toml").is_file()

    guard = subprocess.run(
        [sys.executable, str(root / ".energatron/scripts/privacy_guard.py"), "--mode", "workspace", "--tracked"],
        cwd=root,
        capture_output=True,
        text=True,
    )
    assert guard.returncode == 0, guard.stderr

    monkeypatch.chdir(root)
    code, status = envelope(capsys, "status")
    assert code == 0, status["error"]
    assert status["data"]["pipeline"]["synced"] is True


def test_init_without_an_nmi_leaves_the_private_config_to_the_user(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    code, result = envelope(capsys, *init_args(tmp_path / "ws"))
    assert code == 0, result["error"]
    assert result["data"]["private_config"] is False
    assert not (tmp_path / "ws" / "private" / "config.toml").exists()
    assert any("private.example.toml" in step for step in result["data"]["next_steps"])


def test_init_refuses_a_folder_that_is_not_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "ws").mkdir()
    (tmp_path / "ws" / "x").write_text("x", encoding="utf-8")
    code, result = envelope(capsys, *init_args(tmp_path / "ws"))
    assert (code, result["error"]["code"]) == (2, "E_USAGE")


def test_init_never_prompts_in_json_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert main([*init_args(tmp_path / "ws"), "--dry-run", "--json"]) == 0
