"""The `WorkspaceSetup` port: the subprocesses `energatron init` runs in a new workspace (P8 spec §4.3).

Implemented by `adapters/setup/subprocesses.py`. Every method raises `E_SUBPROCESS` (with `step`) when its command
fails, and never prints a path or a value from the private config.
"""

from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from energatron.core.canonical import JSONValue


class WorkspaceSetup(Protocol):
    def missing_tools(self) -> list[str]:
        """The programs `init` needs (`git`, `uv`) that aren't on `PATH`."""
        ...

    def git_init(self, root: Path) -> None: ...

    def ensure_git_identity(self, root: Path) -> bool:
        """Set a repo-local identity when git has none for `root`; True when it did."""
        ...

    def uv_sync(self, root: Path) -> None: ...

    def dvc_executable(self, root: Path) -> Path | None:
        """The workspace venv's `dvc`, else the running interpreter's, else None (then `dvc` on `PATH`)."""
        ...

    def dvc_init(self, root: Path) -> None: ...

    def dvc_remote(self, root: Path, remote: Path) -> None:
        """`dvc remote add --local -d backup <remote>`."""
        ...

    def pre_commit_install(self, root: Path) -> None: ...

    def unstaged_paths(self, root: Path) -> Sequence[str]:
        """Every untracked or modified path git would commit (`.gitignore` respected), sorted."""
        ...

    def run_demo(self, root: Path) -> JSONValue:
        """`uv run --frozen energatron demo --json` in `root`: the envelope's `data` (P8 spec §5)."""
        ...
