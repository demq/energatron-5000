"""`land` failures and recovery in a scratch workspace with real git and DVC (D3 §7.5, §13).

A fault is injected by wrapping one port of the real context and calling the service; the re-run is the real
command. In every case the re-run commits, naming the interrupted IDs, and leaves a clean tree and no marker.
"""

import dataclasses
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest

from energatron.adapters import wiring
from energatron.core.errors import StorageError, SubprocessError
from energatron.core.models.catalog import CatalogRecord
from energatron.core.models.storage import StoredBlob
from energatron.ports.blobs import BlobStore
from energatron.ports.catalog import Catalog
from energatron.ports.vcs import VersionControl
from energatron.services.context import ServiceContext
from energatron.services.land import land
from tests.integration._scratch import git
from tests.integration.test_land_cli import Workspace, envelope, make_land_workspace

pytestmark = pytest.mark.integration

BIN = Path(sys.executable).parent

# A hook that fails while the flag file exists. Committed before `pre-commit install`, so `land`'s precondition on
# `.pre-commit-config.yaml` holds.
FAILING_HOOK = """\
repos:
  - repo: local
    hooks:
      - id: synthetic-gate
        name: fails while a flag file exists
        entry: test ! -e {flag}
        language: system
        pass_filenames: false
        always_run: true
        stages: [pre-commit]
"""


@pytest.fixture
def ws(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> Workspace:
    return make_land_workspace(tmp_path, monkeypatch, capsys)


class FailingPut:
    def __init__(self, blobs: BlobStore) -> None:
        self.blobs = blobs

    def put(self, key: str, data: bytes) -> StoredBlob:
        raise StorageError("synthetic failure before the put")

    def __getattr__(self, name: str) -> object:
        return getattr(self.blobs, name)


class FailingAppend:
    def __init__(self, catalog: Catalog) -> None:
        self.catalog = catalog

    def append(self, record: CatalogRecord) -> None:
        raise StorageError("synthetic failure after the put")

    def __getattr__(self, name: str) -> object:
        return getattr(self.catalog, name)


class FailingTrack:
    """Fails before `dvc add` runs (`after_dvc_add=False`) or after it has written the pointer files."""

    def __init__(self, vcs: VersionControl, *, after_dvc_add: bool) -> None:
        self.vcs = vcs
        self.after_dvc_add = after_dvc_add

    def track(self, paths: Sequence[str]) -> list[str]:
        if self.after_dvc_add:
            self.vcs.track(paths)
        raise SubprocessError("synthetic dvc failure", command="dvc add", exit_code=1)

    def __getattr__(self, name: str) -> object:
        return getattr(self.vcs, name)


def context(ws: Workspace, **ports: object) -> ServiceContext:
    built = wiring.build_context(ws.scratch.paths)
    wrapped = {name: wrap(getattr(built, name)) for name, wrap in ports.items()}  # pyright: ignore[reportCallIssue]
    return dataclasses.replace(built, **wrapped)  # pyright: ignore[reportArgumentType]


def assert_healed(capsys: pytest.CaptureFixture[str], ws: Workspace, path: Path, before: str) -> str:
    """Re-run the real command: one commit naming the interrupted ID, a clean tree, no marker."""
    code, result = envelope(capsys, "land", str(path))
    assert code == 0, result["error"]
    assert result["data"]["commit"] == ws.head() != before
    item = result["data"]["items"][0]
    message = git(ws.root, "log", "-1", "--format=%B").strip()
    assert message == f"land: {item['dataset_id']}"
    assert git(ws.root, "show", "--name-only", "--format=", "HEAD").split() == ["data/raw/sapn_interval.dvc"]
    assert ws.status() == ""
    assert not ws.marker().exists()
    return item["status"]


@pytest.mark.parametrize(
    ("port", "wrap", "rerun_status"),
    [
        ("blobs", FailingPut, "landed"),  # after the marker
        ("catalog", FailingAppend, "landed"),  # after blobs.put: the orphan blob is adopted
        ("vcs", lambda vcs: FailingTrack(vcs, after_dvc_add=False), "already_landed"),  # after catalog.append
        ("vcs", lambda vcs: FailingTrack(vcs, after_dvc_add=True), "already_landed"),  # in track
    ],
)
def test_a_fault_then_a_rerun_heals(
    capsys: pytest.CaptureFixture[str], ws: Workspace, port: str, wrap: object, rerun_status: str
) -> None:
    path = ws.sapn()
    before = ws.head()
    with pytest.raises((StorageError, SubprocessError)):
        land(context(ws, **{port: wrap}), [path])
    assert ws.marker().exists()
    assert ws.head() == before
    assert assert_healed(capsys, ws, path, before) == rerun_status


def test_a_commit_rejected_by_a_hook_then_a_rerun_heals(
    capsys: pytest.CaptureFixture[str], ws: Workspace, tmp_path: Path
) -> None:
    flag = tmp_path / "hook-fails"
    flag.write_text("x")
    ws.scratch.write(".pre-commit-config.yaml", FAILING_HOOK.format(flag=flag))
    ws.scratch.commit_all("add a hook")
    subprocess.run([str(BIN / "pre-commit"), "install"], cwd=ws.root, check=True, capture_output=True)
    before = ws.head()
    path = ws.sapn()

    code, result = envelope(capsys, "land", str(path))
    assert code == 4
    assert result["error"]["code"] == "E_COMMIT_REJECTED"
    assert result["error"]["details"]["hooks"] == ["synthetic-gate"]
    assert "re-run" in result["error"]["details"]["hint"]
    assert ws.marker().exists()

    flag.unlink()
    assert assert_healed(capsys, ws, path, before) == "already_landed"


def test_with_the_marker_deleted_by_hand_the_rerun_commits_a_resync(
    capsys: pytest.CaptureFixture[str], ws: Workspace
) -> None:
    path = ws.sapn()
    with pytest.raises(SubprocessError):
        land(context(ws, vcs=lambda vcs: FailingTrack(vcs, after_dvc_add=True)), [path])
    ws.marker().unlink()
    code, result = envelope(capsys, "land", str(path))
    assert code == 0, result["error"]
    assert git(ws.root, "log", "-1", "--format=%B").strip() == "land: resync raw pointers"
    assert ws.status() == ""


def test_a_stray_file_in_a_source_directory_fails_with_nothing_written(
    capsys: pytest.CaptureFixture[str], ws: Workspace
) -> None:
    ws.scratch.write("data/raw/sapn_interval/2025/stray.csv")
    before = ws.head()
    code, result = envelope(capsys, "land", str(ws.sapn()))
    assert code == 4
    assert result["error"]["code"] == "E_CATALOG_CORRUPT"
    assert result["error"]["details"]["reason"] == "uncatalogued_file"
    assert ws.head() == before
    assert (ws.root / "data" / "raw" / "sapn_interval" / "_catalog.jsonl").read_bytes() == b""
    assert not ws.marker().exists()


def test_after_an_uncommitted_pipeline_sync_land_stops_at_e_dirty(
    capsys: pytest.CaptureFixture[str], ws: Workspace
) -> None:
    dvc_yaml = ws.root / "dvc.yaml"
    dvc_yaml.write_text(dvc_yaml.read_text(encoding="utf-8") + "# edited by hand\n", encoding="utf-8")
    ws.scratch.commit_all("edit dvc.yaml")
    assert envelope(capsys, "pipeline", "sync")[0] == 0  # rewrites dvc.yaml, uncommitted
    code, result = envelope(capsys, "land", str(ws.sapn()))
    assert code == 4
    assert result["error"]["code"] == "E_DIRTY"
    assert result["error"]["details"]["paths"] == ["dvc.yaml"]
    assert (ws.root / "data" / "raw" / "sapn_interval" / "_catalog.jsonl").read_bytes() == b""
