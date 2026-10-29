"""`energatron land` in a scratch workspace with real git and DVC (D3 §7, §8, §13).

The workspace gets a synthetic private config, `pipeline sync` and a first commit; the inputs are generated NEM12
files with synthetic identifiers, in a landing folder outside the workspace. Nothing here touches the real
workspace.
"""

import json
import stat
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pytest

from energatron.adapters.cli.app import main
from energatron.core.models.catalog import SourceType
from tests.fixtures.make_nem12 import Nem12Spec, make_nem12, sapn_filename
from tests.fixtures.synthetic import SYNTHETIC_NMI, SYNTHETIC_NMI11, SYNTHETIC_SERIAL
from tests.integration._scratch import Scratch, dvc, git, make_scratch

pytestmark = pytest.mark.integration

WINDOW = (date(2025, 1, 1), date(2025, 1, 3))
POINTERS = [f"data/raw/{source}.dvc" for source in sorted(source.value for source in SourceType)]
PRIVATE_TOML = f"""\
kind = "private_config"
schema_version = 1
salt = "0123456789abcdef0123456789abcdef"

[premises]
nmi = "{SYNTHETIC_NMI11}"
meter_serials = ["{SYNTHETIC_SERIAL}"]
"""


@dataclass
class Workspace:
    scratch: Scratch
    landing: Path

    @property
    def root(self) -> Path:
        return self.scratch.root

    def sapn(self, seed: int = 0, *, hour: int = 10, nmi: str = SYNTHETIC_NMI, name: str | None = None) -> Path:
        name = name or sapn_filename("DETAILED", nmi, WINDOW, datetime(2025, 1, 4, hour))
        path = self.landing / name
        path.write_bytes(make_nem12(Nem12Spec(start=WINDOW[0], days=3, seed=seed, nmi=nmi)))
        return path

    def head(self) -> str:
        return git(self.root, "rev-parse", "HEAD").strip()

    def status(self) -> str:
        return git(self.root, "status", "--porcelain", "--untracked-files=all")

    def marker(self) -> Path:
        return self.root / ".energatron" / "tmp" / "land_pending.json"


def envelope(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, dict[str, Any]]:
    """Run the CLI in-process with `--json`: the exit code and the one envelope on stdout."""
    capsys.readouterr()
    code = main([*argv, "--json"])
    out = capsys.readouterr().out
    assert out.count("\n") == 1, out
    return code, json.loads(out)


def make_land_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> Workspace:
    """A committed scratch workspace with a synthetic private config and the pipeline synced."""
    for name in ("ENERGATRON_JSON", "ENERGATRON_WORKSPACE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PRE_COMMIT_HOME", str(tmp_path / "pre-commit-home"))
    scratch = make_scratch(tmp_path, remote=False)
    gitignore = scratch.root / ".gitignore"
    gitignore.write_text(gitignore.read_text(encoding="utf-8") + "/private/\n", encoding="utf-8")
    scratch.write("private/config.toml", PRIVATE_TOML)
    monkeypatch.chdir(scratch.root)
    code, synced = envelope(capsys, "pipeline", "sync")
    assert code == 0, synced["error"]
    scratch.commit_all("set up the pipeline")
    landing = tmp_path / "landing"
    landing.mkdir()
    return Workspace(scratch, landing)


@pytest.fixture
def ws(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> Workspace:
    return make_land_workspace(tmp_path, monkeypatch, capsys)


def test_land_two_files_and_a_copy_then_again(capsys: pytest.CaptureFixture[str], ws: Workspace) -> None:
    one, two = ws.sapn(seed=1, hour=9), ws.sapn(seed=2, hour=10)
    copy = ws.landing / "copy.csv"
    copy.write_bytes(one.read_bytes())
    before = ws.head()

    code, result = envelope(capsys, "land", str(one), str(two), str(copy))
    assert code == 0, result["error"]
    assert (result["command"], result["ok"]) == ("land", True)
    data = result["data"]
    assert [item["status"] for item in data["items"]] == ["landed", "landed", "duplicate_in_batch"]
    assert data["items"][2]["dataset_id"] == data["items"][0]["dataset_id"]
    assert len(data["landed"]) == 2
    assert [warning["code"] for warning in result["warnings"]] == ["W_BACKUP_PENDING"]

    # One commit, holding exactly the pointer files, naming both IDs
    assert git(ws.root, "rev-list", "--count", f"{before}..HEAD").strip() == "1"
    assert data["commit"] == ws.head()
    committed = git(ws.root, "show", "--name-only", "--format=", "HEAD").split()
    assert committed == data["pointer_files"] == ["data/raw/sapn_interval.dvc"]
    message = git(ws.root, "log", "-1", "--format=%B").strip()
    assert message == "land: 2 datasets\n\n" + "\n".join(data["landed"])
    assert ws.status() == ""
    assert not ws.marker().exists()

    # The stored blobs: read-only after `dvc add` (D3 §14 check 3), and DVC sees them tracked
    for item in data["items"][:2]:
        blob = ws.root / item["stored_path"]
        assert blob.read_bytes() == (one if item is data["items"][0] else two).read_bytes()
        assert stat.S_IMODE(blob.stat().st_mode) == 0o444
    assert json.loads(dvc(ws.root, "status", "--json", *POINTERS)) == {}

    # Landing again: every item already landed, exit 0, HEAD unchanged
    code, again = envelope(capsys, "land", str(one), str(two), str(copy))
    assert code == 0
    assert [item["status"] for item in again["data"]["items"]] == ["already_landed"] * 3
    assert (again["data"]["commit"], again["warnings"]) == (None, [])
    assert ws.head() == data["commit"]


def test_a_dry_run_writes_nothing(capsys: pytest.CaptureFixture[str], ws: Workspace) -> None:
    before = ws.head()
    code, result = envelope(capsys, "land", str(ws.sapn()), "--dry-run")
    assert code == 0
    assert result["data"]["dry_run"] is True
    assert [item["status"] for item in result["data"]["items"]] == ["landed"]
    assert ws.head() == before
    assert ws.status() == ""
    assert (ws.root / "data" / "raw" / "sapn_interval" / "_catalog.jsonl").read_bytes() == b""


def test_one_rejected_input_exits_with_its_own_error_and_no_data(
    capsys: pytest.CaptureFixture[str], ws: Workspace
) -> None:
    notes = ws.landing / "notes.txt"
    notes.write_text("synthetic\n")
    code, result = envelope(capsys, "land", str(notes))
    assert code == 3
    assert result["error"]["code"] == "E_UNKNOWN_SOURCE"
    assert result["error"]["details"] == {"position": 1}
    assert result["data"] is None


def test_mixed_results_exit_8_with_the_accepted_items_committed(
    capsys: pytest.CaptureFixture[str], ws: Workspace
) -> None:
    notes = ws.landing / "notes.txt"
    notes.write_text("synthetic\n")
    ws.sapn()
    code, result = envelope(capsys, "land", str(ws.landing))
    assert code == 8
    assert result["error"]["code"] == "E_PARTIAL"
    assert result["ok"] is False
    statuses = sorted(item["status"] for item in result["data"]["items"])
    assert statuses == ["landed", "rejected"]
    assert result["data"]["commit"] == ws.head()
    assert ws.status() == ""


def test_a_foreign_file_exits_5_with_nothing_written(capsys: pytest.CaptureFixture[str], ws: Workspace) -> None:
    before = ws.head()
    ws.sapn()
    ws.sapn(seed=2, nmi="ZZTEST0002")
    code, result = envelope(capsys, "land", str(ws.landing))
    assert code == 5
    assert result["error"]["code"] == "E_FOREIGN_PREMISES"
    assert (ws.head(), ws.status()) == (before, "")


def test_human_output_lists_items_then_totals(capsys: pytest.CaptureFixture[str], ws: Workspace) -> None:
    path = ws.sapn()
    capsys.readouterr()
    assert main(["land", str(path)]) == 0
    captured = capsys.readouterr()
    lines = captured.out.splitlines()
    assert lines[0].startswith("1 landed sapn_interval sapn_interval-20250104T100000-")
    assert lines[1] == f"landed 1, unchanged 0, rejected 0; commit {ws.head()[:7]}"
    assert "W_BACKUP_PENDING" in captured.err
    for text in (captured.out, captured.err):
        assert path.name not in text
        assert SYNTHETIC_NMI not in text


def test_an_unknown_source_option_is_a_usage_error(capsys: pytest.CaptureFixture[str], ws: Workspace) -> None:
    code, result = envelope(capsys, "land", str(ws.sapn()), "--source", "not_a_source")
    assert code == 2
    assert result["error"]["code"] == "E_USAGE"
    assert "not_a_source" not in json.dumps(result)
