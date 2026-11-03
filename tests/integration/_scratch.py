"""Scratch repos for the git and DVC adapter tests: a workspace, a local DVC remote and a bundle folder.

Layout under the test's `tmp_path`:

    ws/                 git repo (main) with DVC initialised; `.energatron/` holds the marker and scratch space
    Backups/            stands in for `iCloud Drive/Backups/`
    Backups/git/        the bundle folder (`local.toml`'s `backup.bundle_dir`)
    Backups/dvc/        the DVC remote, the default in `.dvc/config.local`

`git init` and `dvc init` run once per test process, into a template repo that each scratch copies: a `dvc` call
starts Python and imports DVC, which made set-up the slowest part of these tests. Neither tool writes the repo's own
path anywhere, so a copy is the same repo. The remote is written into `.dvc/config.local` as
`dvc remote add --local -d` writes it, since its folder differs per test.
"""

import shutil
import subprocess
import sys
import tempfile
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
    shutil.copytree(_template(with_dvc=with_dvc), root, symlinks=True, dirs_exist_ok=True)
    (root / ".energatron").mkdir()
    scratch.write(".gitignore", GITIGNORE)
    scratch.write(".energatron/workspace.toml", 'kind = "workspace_config"\nschema_version = 1\nregion = "sa"\n')
    if with_dvc and remote:
        scratch.write(".dvc/config.local", _REMOTE_CONFIG.format(url=scratch.remote_dir))
    return scratch


_REMOTE_CONFIG = "[core]\n    remote = backup\n['remote \"backup\"']\n    url = {url}\n"
_templates: dict[bool, Path] = {}
_template_root: tempfile.TemporaryDirectory[str] | None = None


def _template(*, with_dvc: bool) -> Path:
    """A fresh repo after `git init -b main` (and `dvc init`), made once per test process and copied by each scratch.

    It is made under the first caller's isolated git and DVC configuration (the integration `conftest.py`)."""
    global _template_root
    if with_dvc not in _templates:
        if _template_root is None:
            _template_root = tempfile.TemporaryDirectory(prefix="energatron-scratch-template-")
        root = Path(_template_root.name) / ("dvc" if with_dvc else "git")
        root.mkdir()
        git(root, "init", "-q", "-b", "main")
        if with_dvc:
            dvc(root, "init", "-q")
        _templates[with_dvc] = root
    return _templates[with_dvc]
