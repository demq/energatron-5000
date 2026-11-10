"""Notifications through macOS Notification Center (P7 spec §7).

`osascript` gets the title and message as arguments to an `on run argv` handler, so the text is never spliced into
AppleScript. A failure logs a warning and returns false: a notification never fails a command. Elsewhere than macOS
nothing is shown. `ENERGATRON_NOTIFY_LOG=<path>` appends `{"title", "message"}` JSON lines to that file instead: the
test seam, as `ENERGATRON_CDR_ROOT` is for the CDR provider.
"""

import json
import logging
import subprocess
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Final

_log = logging.getLogger(__name__)

NOTIFY_LOG_ENV: Final = "ENERGATRON_NOTIFY_LOG"
SCRIPT: Final = (
    "on run argv",
    "display notification (item 2 of argv) with title (item 1 of argv)",
    "end run",
)
_TIMEOUT_SECONDS: Final = 30

type Runner = Callable[..., subprocess.CompletedProcess[bytes]]


class Notifier:
    """`notifier(title, message) -> bool`: whether the notification was handed over."""

    def __init__(self, env: Mapping[str, str], *, platform: str = sys.platform, run: Runner = subprocess.run) -> None:
        self._log_path = env.get(NOTIFY_LOG_ENV) or None
        self._platform = platform
        self._run = run

    def __call__(self, title: str, message: str) -> bool:
        if self._log_path is not None:
            return self._append(Path(self._log_path), title, message)
        if self._platform != "darwin":
            _log.info("Notifications need macOS; not shown: %s", title)
            return False
        argv = ["osascript"]
        for line in SCRIPT:
            argv += ["-e", line]
        argv += [title, message]
        try:
            done = self._run(argv, capture_output=True, timeout=_TIMEOUT_SECONDS, check=False)
        except OSError, subprocess.SubprocessError:
            _log.warning("The notification couldn't be shown (osascript failed to start).")
            return False
        if done.returncode != 0:
            _log.warning("The notification couldn't be shown (osascript exit %d).", done.returncode)
            return False
        return True

    @staticmethod
    def _append(path: Path, title: str, message: str) -> bool:
        try:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"title": title, "message": message}, sort_keys=True) + "\n")
        except OSError:
            _log.warning("The notification log couldn't be written.")
            return False
        return True
