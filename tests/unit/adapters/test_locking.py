"""The workspace lock across processes (D1 §8.8; D2 §5.9, §14).

Another process holds `.energatron/lock` through `workspace_lock()`. `denylist` and `backup` (their dry runs and
`denylist --check` included) fail with `E_LOCKED` and change nothing; `backup --status` and `version` still run.
"""

import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from energatron.core.models.workspace import WorkspacePaths
from tests.unit.adapters._cli import FakeVcsState, run_cli
from tests.unit.adapters._workspace import make_workspace

HOLDER = """
import sys
from pathlib import Path
from energatron.adapters.workspace import workspace_lock
from energatron.core.models.workspace import WorkspacePaths
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
    assert fake_vcs.tracked == []
    assert fake_vcs.backups == 0


def test_backup_status_and_version_run_while_the_lock_is_held(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, fake_vcs: FakeVcsState
) -> None:
    paths = make_workspace(tmp_path)
    with held_by_another_process(paths):
        status = run_cli(capsys, "backup", "--status", "--json", "--workspace", str(paths.root))
        version = run_cli(capsys, "version", "--json", "--workspace", str(paths.root))
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
