"""Scratch repos for the git and DVC adapter tests: a workspace, a local DVC remote and a bundle folder.

Layout under the test's `tmp_path`:

    ws/                 git repo (main) with DVC initialised; `.energatron/` holds the marker and scratch space
    Backups/            stands in for `iCloud Drive/Backups/`
    Backups/git/        the bundle folder (`local.toml`'s `backup.bundle_dir`)
    Backups/dvc/        the DVC remote, set with `dvc remote add --local -d`
"""

import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from energatron.adapters.vcs.git_dvc import GitDvcVersionControl
from energatron.core.models.workspace import BackupPolicy, WorkspacePaths

DVC = str(Path(sys.executable).parent / "dvc")
START = datetime(2026, 10, 23, 9, 30, tzinfo=UTC)
GITIGNORE = "/.energatron/tmp/\n/.energatron/lock\n/.energatron/backup_state.json\n/.energatron/local.toml\n"


class StepClock:
    """A clock the test moves on by hand."""

    def __init__(self, start: datetime = START) -> None:
        self.current = start

    def now(self) -> datetime:
        return self.current

    def advance(self, **delta: float) -> None:
        self.current += timedelta(**delta)


def run(root: Path, *args: str) -> str:
    """Run a command in `root`, fail the test on a non-zero exit, return stdout."""
    return subprocess.run(args, cwd=root, check=True, capture_output=True, text=True).stdout


def git(root: Path, *args: str) -> str:
    return run(root, "git", *args)


def dvc(root: Path, *args: str) -> str:
    return run(root, DVC, *args)


@dataclass
class Scratch:
    root: Path
    backups: Path
    bundle_dir: Path
    remote_dir: Path
    clock: StepClock

    @property
    def paths(self) -> WorkspacePaths:
        return WorkspacePaths(root=self.root)

    def vcs(self, *, configured: bool = True, keep_latest: int = 3) -> GitDvcVersionControl:
        """The adapter under test; `configured=False` leaves `bundle_dir` unset, as a missing `local.toml` does."""
        return GitDvcVersionControl(
            self.paths,
            clock=self.clock,
            bundle_dir=self.bundle_dir if configured else None,
            policy=BackupPolicy(keep_latest=keep_latest),
        )

    def write(self, relative: str, text: str = "synthetic\n") -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def commit_all(self, message: str = "scratch") -> str:
        """Commit everything with plain git, as set-up for a test."""
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", message)
        return git(self.root, "rev-parse", "HEAD").strip()

    def bundles(self) -> list[str]:
        return sorted(path.name for path in self.bundle_dir.iterdir() if path.name.endswith(".bundle"))

    def remote_complete(self) -> bool:
        return dvc(self.root, "status", "--cloud", "--json").strip() == "{}"


def make_scratch(tmp_path: Path, *, with_dvc: bool = True, remote: bool = True, create_dirs: bool = True) -> Scratch:
    root = tmp_path / "ws"
    backups = tmp_path / "Backups"
    root.mkdir(parents=True)
    backups.mkdir()
    scratch = Scratch(root, backups, backups / "git", backups / "dvc", StepClock())
    if create_dirs:
        scratch.bundle_dir.mkdir()
        scratch.remote_dir.mkdir()
    git(root, "init", "-q", "-b", "main")
    (root / ".energatron").mkdir()
    scratch.write(".gitignore", GITIGNORE)
    scratch.write(".energatron/workspace.toml", 'kind = "workspace_config"\nschema_version = 1\nregion = "sa"\n')
    if with_dvc:
        dvc(root, "init", "-q")
        if remote:
            dvc(root, "remote", "add", "--local", "-d", "backup", str(scratch.remote_dir))
    return scratch
