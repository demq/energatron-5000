"""`SubprocessSetup`: the `WorkspaceSetup` port over git, uv, DVC and pre-commit subprocesses (P8 spec §4.3).

Output of the commands is never logged or returned: a failure names the step and the exit code only.
"""

import json
import logging
import os
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Final

from energatron.core.canonical import JSONValue
from energatron.core.errors import SubprocessError

_log = logging.getLogger(__name__)

IDENTITY: Final = ("energatron workspace", "workspace@localhost")
"""The repo-local identity `init` sets when git has none: the history never leaves the machine (D2 §11.4)."""


class SubprocessSetup:
    def __init__(self, *, bin_dir: Path | None = None) -> None:
        self._bin = bin_dir if bin_dir is not None else Path(sys.executable).parent

    def _run(
        self, step: str, args: Sequence[str], cwd: Path, *, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        env = {key: value for key, value in os.environ.items() if key != "VIRTUAL_ENV"}
        try:
            result = subprocess.run(
                list(args), cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace"
            )
        except OSError:
            raise SubprocessError(f"`{args[0]}` couldn't be started.", step=step) from None
        _log.debug("init step %s exited %d", step, result.returncode)
        if check and result.returncode != 0:
            raise SubprocessError(f"The {step} step failed.", step=step, exit_code=result.returncode)
        return result

    def missing_tools(self) -> list[str]:
        return [tool for tool in ("git", "uv") if shutil.which(tool) is None]

    def git_init(self, root: Path) -> None:
        self._run("git_init", ["git", "init", "-q", "-b", "main"], root)

    def ensure_git_identity(self, root: Path) -> bool:
        missing = [
            key
            for key in ("user.name", "user.email")
            if self._run("git_identity", ["git", "config", key], root, check=False).returncode != 0
        ]
        if not missing:
            return False
        for key, value in zip(("user.name", "user.email"), IDENTITY, strict=True):
            self._run("git_identity", ["git", "config", "--local", key, value], root)
        return True

    def uv_sync(self, root: Path) -> None:
        self._run("uv_sync", ["uv", "sync", "--quiet"], root)

    def dvc_executable(self, root: Path) -> Path | None:
        for candidate in (root / ".venv" / "bin" / "dvc", self._bin / "dvc"):
            if candidate.is_file():
                return candidate
        return None

    def _dvc(self, root: Path) -> str:
        found = self.dvc_executable(root)
        return str(found) if found is not None else "dvc"

    def dvc_init(self, root: Path) -> None:
        self._run("dvc_init", [self._dvc(root), "init", "-q"], root)

    def dvc_remote(self, root: Path, remote: Path) -> None:
        self._run("dvc_remote", [self._dvc(root), "remote", "add", "--local", "-d", "backup", str(remote)], root)

    def pre_commit_install(self, root: Path) -> None:
        self._run("pre_commit", ["uv", "run", "--frozen", "pre-commit", "install"], root)

    def unstaged_paths(self, root: Path) -> list[str]:
        output = self._run(
            "commit", ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all", "--no-renames"], root
        ).stdout
        return sorted({entry[3:] for entry in output.split("\0") if entry})

    def run_demo(self, root: Path) -> JSONValue:
        result = self._run("demo", ["uv", "run", "--frozen", "energatron", "demo", "--json"], root, check=False)
        try:
            envelope = json.loads(result.stdout)
        except ValueError:
            raise SubprocessError("The demo printed no result.", step="demo", exit_code=result.returncode) from None
        if result.returncode != 0 or not isinstance(envelope, dict) or not envelope.get("ok"):
            error = envelope.get("error") if isinstance(envelope, dict) else None
            code = error.get("code") if isinstance(error, dict) else None
            raise SubprocessError("The demo failed.", step="demo", exit_code=result.returncode, demo_error=code)
        data: JSONValue = envelope.get("data")
        return data
