"""The per-user launchd agent that runs `energatron watch --notify` weekly (P7 spec §8). macOS only.

`install` writes `~/Library/LaunchAgents/<label>.plist` atomically, boots out any loaded copy and bootstraps it into
`gui/<uid>`; `remove` boots it out and deletes the plist; `status` says whether the plist exists and whether launchd
has it loaded. `launchctl` and the file system are injected so tests run against fakes. Owner-run: agents use only
`status` and `install --dry-run` (P7 spec §14 Q8).
"""

import contextlib
import os
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from energatron.core.errors import StorageError, SubprocessError, UsageError

type Runner = Callable[..., subprocess.CompletedProcess[bytes]]

LAUNCHCTL: Final = "/bin/launchctl"
_TIMEOUT_SECONDS: Final = 60


@dataclass(frozen=True)
class AgentState:
    installed: bool
    loaded: bool
    plist: bytes | None


class Launchd:
    def __init__(self, *, home: Path, uid: int, platform: str, run: Runner = subprocess.run) -> None:
        self._home = home
        self._domain = f"gui/{uid}"
        self._platform = platform
        self._run = run

    def check_platform(self) -> None:
        if self._platform != "darwin":
            raise UsageError("launchd is macOS only: `energatron schedule` can't run here.")

    def plist_path(self, label: str) -> Path:
        return self._home / "Library" / "LaunchAgents" / f"{label}.plist"

    def shown(self, path: Path) -> str:
        """`path` with the home folder as `~`, so output never shows the account name."""
        try:
            return "~/" + path.relative_to(self._home).as_posix()
        except ValueError:
            return path.as_posix()

    def shown_text(self, text: str) -> str:
        """`text` with every mention of the home folder as `~`."""
        return text.replace(str(self._home), "~")

    def state(self, label: str) -> AgentState:
        path = self.plist_path(label)
        plist = path.read_bytes() if path.is_file() else None
        loaded = self._launchctl("print", f"{self._domain}/{label}").returncode == 0
        return AgentState(installed=plist is not None, loaded=loaded, plist=plist)

    def install(self, label: str, plist: bytes) -> None:
        path = self.plist_path(label)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            partial = path.with_name(path.name + ".partial")
            partial.write_bytes(plist)
            os.replace(partial, path)
        except OSError:
            raise StorageError("The launchd agent's plist couldn't be written.", reason="plist_write") from None
        self._launchctl("bootout", f"{self._domain}/{label}")  # not loaded → a non-zero exit, ignored
        done = self._launchctl("bootstrap", self._domain, str(path))
        if done.returncode != 0:
            raise SubprocessError("launchctl couldn't load the agent.", exit_code=done.returncode)

    def remove(self, label: str) -> None:
        self._launchctl("bootout", f"{self._domain}/{label}")
        with contextlib.suppress(FileNotFoundError):
            self.plist_path(label).unlink()

    def _launchctl(self, *args: str) -> subprocess.CompletedProcess[bytes]:
        try:
            return self._run([LAUNCHCTL, *args], capture_output=True, timeout=_TIMEOUT_SECONDS, check=False)
        except OSError, subprocess.SubprocessError:
            raise SubprocessError("launchctl couldn't be run.", command=args[0]) from None
