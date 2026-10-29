"""The CLI end to end, with real git and DVC in a scratch workspace (D2 §5.9, §7.2, §8).

The backup goes to a scratch `Backups/` folder under `tmp_path`: nothing here touches iCloud or the real workspace.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from energatron.adapters.cli.app import main
from tests.integration._scratch import Scratch, make_scratch
from tests.unit.adapters._workspace import PRIVATE_TOML, local_toml

pytestmark = pytest.mark.integration


def envelope(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, dict[str, Any]]:
    capsys.readouterr()
    code = main(list(argv))
    out = capsys.readouterr().out
    assert out.count("\n") == 1
    return code, json.loads(out)


@pytest.fixture
def scratch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Scratch:
    for name in ("ENERGATRON_JSON", "ENERGATRON_WORKSPACE"):
        monkeypatch.delenv(name, raising=False)
    scratch = make_scratch(tmp_path)
    scratch.write(".energatron/local.toml", local_toml(str(scratch.bundle_dir)))
    scratch.write("private/config.toml", PRIVATE_TOML)
    scratch.write("README.md", "scratch\n")
    monkeypatch.chdir(scratch.root)
    return scratch


def test_denylist_then_backup_then_status(capsys: pytest.CaptureFixture[str], scratch: Scratch) -> None:
    code, before = envelope(capsys, "backup", "--status", "--json")
    assert code == 0
    assert before["data"]["refs_backed_up"] is False
    assert before["data"]["head"] is None

    code, denylist = envelope(capsys, "denylist", "--json")
    assert code == 0
    assert denylist["data"]["pointer_files"] == ["private.dvc"]
    assert (scratch.root / "private.dvc").is_file()
    assert envelope(capsys, "denylist", "--check", "--json")[0] == 0

    head = scratch.commit_all("set up the scratch workspace")
    code, status = envelope(capsys, "backup", "--status", "--json")
    assert (code, status["data"]["head"], status["data"]["refs_backed_up"]) == (0, head, False)

    code, backup = envelope(capsys, "backup", "--json")
    assert code == 0, backup["error"]
    report = backup["data"]["report"]
    assert report["bundle_created"] is True
    assert report["dvc_files_pushed"] > 0
    assert scratch.bundles() == [report["bundle_name"]]
    assert scratch.remote_complete()

    code, after = envelope(capsys, "backup", "--status", "--json")
    assert code == 0
    assert after["data"]["refs_backed_up"] is True
    assert after["data"]["commits_not_backed_up"] == 0


def test_backup_without_a_bundle_folder_is_not_configured(capsys: pytest.CaptureFixture[str], scratch: Scratch) -> None:
    (scratch.root / ".energatron" / "local.toml").unlink()
    scratch.commit_all()
    code, result = envelope(capsys, "backup", "--json")
    assert code == 4
    assert result["error"]["code"] == "E_BACKUP_NOT_CONFIGURED"
    assert scratch.bundles() == []


@pytest.mark.parametrize("value", ["Backups/git", "{root}/backups"])
def test_a_bad_bundle_folder_fails_only_the_real_backup(
    capsys: pytest.CaptureFixture[str], scratch: Scratch, value: str
) -> None:
    """D2 §6.2, §8.1 step 1, §8.5: `denylist`, `backup --status` and `--dry-run` never look at the bundle folder."""
    scratch.write(".energatron/local.toml", local_toml(value.format(root=scratch.root)))
    code, denylist = envelope(capsys, "denylist", "--json")
    assert code == 0, denylist["error"]
    head = scratch.commit_all("set up the scratch workspace")
    code, status = envelope(capsys, "backup", "--status", "--json")
    assert (code, status["data"]["head"], status["data"]["refs_backed_up"]) == (0, head, False)
    code, dry = envelope(capsys, "backup", "--dry-run", "--json")
    assert (code, dry["data"]["dry_run"]) == (0, True)

    code, result = envelope(capsys, "backup", "--json")
    assert code == 4
    assert result["error"]["code"] == "E_BACKUP_NOT_CONFIGURED"
    assert str(scratch.root) not in json.dumps(result)
    assert scratch.bundles() == []
    assert not (scratch.root / "backups").exists()
    assert not scratch.paths.backup_state.exists()
