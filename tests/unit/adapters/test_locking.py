"""The workspace lock across processes (D1 §8.8; D2 §5.9, §14).

Another process holds `.energatron/lock` through `workspace_lock()`. `denylist`, `backup`, `fingerprint`,
`pipeline sync` and `verify` (their dry runs and `denylist --check` included) fail with `E_LOCKED` and change nothing;
`backup --status`, `fingerprint --check`, `pipeline sync --check`, `stage` and `version` still run.
"""

import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from energatron.adapters import wiring
from energatron.core.models.catalog import SourceType
from energatron.core.models.workspace import WorkspacePaths
from energatron.services.files import dump_json
from energatron.services.stages.dvc_yaml import CODE_FINGERPRINT_PATH
from tests.unit.adapters._cli import FakeVcsState, run_cli
from tests.unit.adapters._workspace import make_workspace, write

HOLDER = """
import sys
from pathlib import Path
from energatron.adapters.workspace import workspace_lock
from energatron.core.models.workspace import WorkspacePaths
from energatron.services.stages.dvc_yaml import CODE_FINGERPRINT_PATH
with workspace_lock(WorkspacePaths(root=Path(sys.argv[1]))):
    print("held", flush=True)
    sys.stdin.read()
"""


@contextmanager
def held_by_another_process(paths: WorkspacePaths) -> Iterator[None]:
    """A child process holds the lock until the block ends."""
    child = subprocess.Popen(
        [sys.executable, "-c", HOLDER, str(paths.root)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout is not None
        assert child.stdout.readline() == "held\n"
        yield
    finally:
        assert child.stdin is not None
        child.stdin.close()
        assert child.wait(timeout=30) == 0


@pytest.mark.parametrize(
    "command",
    [
        ("denylist",),
        ("denylist", "--check"),
        ("denylist", "--dry-run"),
        ("backup",),
        ("backup", "--dry-run"),
        ("fingerprint",),
        ("fingerprint", "--dry-run"),
        ("pipeline", "sync"),
        ("pipeline", "sync", "--dry-run"),
        ("verify",),
    ],
)
def test_a_locking_command_fails_while_another_process_holds_the_lock(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, fake_vcs: FakeVcsState, command: tuple[str, ...]
) -> None:
    paths = make_workspace(tmp_path)
    with held_by_another_process(paths):
        ran = run_cli(capsys, *command, "--json", "--workspace", str(paths.root))
    assert ran.code == 4
    envelope = ran.envelope()
    assert envelope["error"]["code"] == "E_LOCKED"
    assert envelope["error"]["category"] == "state"
    assert not paths.denylist_generated.exists()
    assert not (paths.root / CODE_FINGERPRINT_PATH).exists()
    assert not (paths.root / "dvc.yaml").exists()
    assert not (paths.root / "data").exists()
    assert fake_vcs.tracked == []
    assert fake_vcs.backups == 0


def test_backup_status_and_version_run_while_the_lock_is_held(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, fake_vcs: FakeVcsState
) -> None:
    paths = make_workspace(tmp_path)
    with held_by_another_process(paths):
        status = run_cli(capsys, "backup", "--status", "--json", "--workspace", str(paths.root))
        version = run_cli(capsys, "version", "--json", "--workspace", str(paths.root))
        check = run_cli(capsys, "fingerprint", "--check", "--json", "--workspace", str(paths.root))
    assert check.envelope()["error"]["code"] == "E_FINGERPRINT_STALE"  # no recorded file yet; not E_LOCKED
    assert status.code == 0
    assert status.envelope()["data"]["kind"] == "backup_status_result"
    assert version.code == 0


def test_the_lock_is_free_again_after_a_command(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, fake_vcs: FakeVcsState
) -> None:
    paths = make_workspace(tmp_path)
    workspace = ("--workspace", str(paths.root))
    assert run_cli(capsys, "denylist", "--check", "--json", *workspace).code == 4  # stale: fails inside the lock
    assert run_cli(capsys, "denylist", "--json", *workspace).code == 0
    assert run_cli(capsys, "backup", "--json", *workspace).code == 0
    with held_by_another_process(paths):
        pass  # the child could take it: nothing was left holding it


def test_stage_runs_while_another_process_holds_the_lock(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, fake_vcs: FakeVcsState
) -> None:
    """`energatron stage` never locks: `dvc repro` runs it under a parent that holds the lock (D1 §8.8)."""
    paths = make_workspace(tmp_path)
    write(paths.root, "params.yaml", "kind: params\nschema_version: 1\n")
    for source in SourceType:
        write(paths.root, f"data/raw/{source.value}/_catalog.jsonl", "")
    write(paths.root, CODE_FINGERPRINT_PATH, dump_json(wiring.code_fingerprint().model_dump(mode="json")))
    with held_by_another_process(paths):
        ran = run_cli(capsys, "stage", "catalog_index", "--json", "--workspace", str(paths.root))
        emitted = run_cli(capsys, "stage", "--emit-dvc-yaml", "--json", "--workspace", str(paths.root))
    assert ran.code == 0, ran.out
    assert ran.envelope()["data"]["kind"] == "stage_result"
    assert emitted.code == 0


def test_pipeline_sync_check_runs_while_another_process_holds_the_lock(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, fake_vcs: FakeVcsState
) -> None:
    """As it does inside `land`'s commit hook, while `land` holds the lock (D3 §10.1, §13)."""
    paths = make_workspace(tmp_path)
    workspace = ("--workspace", str(paths.root))
    assert run_cli(capsys, "pipeline", "sync", "--json", *workspace).code == 0
    with held_by_another_process(paths):
        ran = run_cli(capsys, "pipeline", "sync", "--check", "--json", *workspace)
    assert ran.code == 0, ran.out
    assert ran.envelope()["data"]["kind"] == "pipeline_sync_result"
