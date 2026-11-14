"""`energatron upgrade` in a workspace `init --no-sync` made, with real git (P8 spec §6)."""

import json
from pathlib import Path
from typing import Any

import pytest

from energatron.core.canonical import sha256_hex
from energatron.core.workspace_template import SKILLS, TEMPLATE_STATE_PATH, TEMPLATE_VERSION
from tests.fixtures.synthetic import SYNTHETIC_NMI11
from tests.integration._scratch import git
from tests.integration.test_init_cli import init_args
from tests.integration.test_land_cli import envelope

pytestmark = pytest.mark.integration


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> Path:
    for name in ("ENERGATRON_JSON", "ENERGATRON_WORKSPACE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    code, result = envelope(capsys, *init_args(tmp_path / "ws", "--nmi", SYNTHETIC_NMI11))  # `dvc add private` runs
    assert code == 0, result["error"]
    monkeypatch.chdir(tmp_path / "ws")
    return tmp_path / "ws"


def statuses(result: dict[str, Any]) -> dict[str, str]:
    return {item["path"]: item["status"] for item in result["data"]["files"]}


def commit_all(root: Path, message: str) -> None:
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", message)


def test_a_fresh_workspace_is_current(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    head = git(root, "rev-parse", "HEAD")
    code, result = envelope(capsys, "upgrade")
    assert code == 0, result["error"]
    assert set(statuses(result).values()) == {"unchanged"}  # `dvc add private`'s ignore line is the template's
    assert (root / ".gitignore").read_text(encoding="utf-8").count("/private") == 1
    assert ".gitignore" in statuses(result) and "pyproject.toml" not in statuses(result)
    assert (result["data"]["commit"], git(root, "rev-parse", "HEAD")) == (None, head)


def test_an_older_template_file_is_rewritten_and_committed(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    template = (root / ".dvcignore").read_bytes()
    old = b"# an older template\n.DS_Store\n"
    (root / ".dvcignore").write_bytes(old)
    state_path = root / TEMPLATE_STATE_PATH
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["files"][".dvcignore"] = sha256_hex(old)  # as the older template wrote it
    state["files"]["retired.txt"] = sha256_hex(b"gone")
    state_path.write_text(json.dumps(state), encoding="utf-8")
    commit_all(root, "an older template")

    code, dry = envelope(capsys, "upgrade", "--dry-run")
    assert code == 0 and statuses(dry)[".dvcignore"] == "updated" and dry["data"]["commit"] is None
    assert (root / ".dvcignore").read_bytes() == old

    code, result = envelope(capsys, "upgrade")
    assert code == 0, result["error"]
    assert (statuses(result)[".dvcignore"], statuses(result)["retired.txt"]) == ("updated", "removed")
    assert (root / ".dvcignore").read_bytes() == template
    assert git(root, "log", "-1", "--format=%s").strip() == f"upgrade: template v{TEMPLATE_VERSION}"
    assert git(root, "status", "--porcelain") == ""
    assert "retired.txt" not in json.loads(state_path.read_text(encoding="utf-8"))["files"]


def test_an_edited_file_is_left_alone_unless_forced(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    edited = (root / ".gitignore").read_text(encoding="utf-8") + "my-notes/\n"
    (root / ".gitignore").write_text(edited, encoding="utf-8")
    commit_all(root, "my own ignore")
    code, result = envelope(capsys, "upgrade")
    assert code == 0, result["error"]
    assert statuses(result)[".gitignore"] == "modified"
    assert [warning["code"] for warning in result["warnings"]] == ["W_TEMPLATE_MODIFIED"]
    assert (root / ".gitignore").read_text(encoding="utf-8") == edited

    code, forced = envelope(capsys, "upgrade", "--force")
    assert code == 0, forced["error"]
    assert statuses(forced)[".gitignore"] == "updated"
    assert "my-notes/" not in (root / ".gitignore").read_text(encoding="utf-8")


def test_a_missing_managed_file_is_added_back(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (root / "config" / "private.example.toml").unlink()
    git(root, "rm", "-q", "--cached", "config/private.example.toml")
    git(root, "commit", "-q", "-m", "drop the example")
    code, result = envelope(capsys, "upgrade")
    assert code == 0, result["error"]
    assert statuses(result)["config/private.example.toml"] == "added"
    assert (root / "config" / "private.example.toml").is_file()


def test_a_workspace_not_made_by_init_is_refused(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    git(root, "rm", "-q", TEMPLATE_STATE_PATH)
    git(root, "commit", "-q", "-m", "hand-built")
    code, result = envelope(capsys, "upgrade")
    assert (code, result["error"]["details"]["reason"]) == (2, "not_templated")


def test_a_version_1_workspace_gains_the_skills_their_links_and_the_mcp_registration(
    root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """P9 spec §7.2: what template v2 introduced is added; an edited seed file and a user's own path are left alone."""
    for name in SKILLS:
        (root / ".agents/skills" / name).unlink()
        (root / ".claude/skills" / name / "SKILL.md").unlink()
    (root / ".mcp.json").unlink()
    (root / ".agents/skills/record-decision").write_text("my own\n", encoding="utf-8")  # not the template's link
    (root / "CLAUDE.md").write_text("# my own instructions\n", encoding="utf-8")
    state_path = root / TEMPLATE_STATE_PATH
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["template_version"] = 1
    state["files"] = {path: digest for path, digest in state["files"].items() if not path.startswith(".claude/skills/")}
    state_path.write_text(json.dumps(state), encoding="utf-8")
    commit_all(root, "a version 1 workspace")

    code, result = envelope(capsys, "upgrade")
    assert code == 0, result["error"]
    found = statuses(result)
    assert all(found[f".claude/skills/{name}/SKILL.md"] == "added" for name in SKILLS)
    assert found[".mcp.json"] == "added" and found[".agents/skills/record-decision"] == "modified"
    assert all(found[f".agents/skills/{name}"] == "added" for name in SKILLS if name != "record-decision")
    assert [warning["code"] for warning in result["warnings"]] == ["W_TEMPLATE_MODIFIED"]
    assert (root / ".agents/skills/monthly-check-in").readlink() == Path("../../.claude/skills/monthly-check-in")
    assert (root / ".agents/skills/monthly-check-in/SKILL.md").is_file()
    assert json.loads((root / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]["energatron"]["command"] == "uv"
    assert (root / "CLAUDE.md").read_text(encoding="utf-8") == "# my own instructions\n"
    assert "CLAUDE.md" not in found
    assert git(root, "status", "--porcelain") == ""

    code, again = envelope(capsys, "upgrade")
    assert code == 0 and again["data"]["commit"] is None
    assert set(statuses(again).values()) == {"unchanged"}  # at version 2, introduced paths aren't listed again
