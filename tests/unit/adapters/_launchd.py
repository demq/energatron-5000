"""A fake `launchctl` for the launchd tests, and for any test whose wiring reaches `schedule status`.

The fake keeps the set of loaded labels: `bootstrap` loads, `bootout` unloads (non-zero when not loaded, as launchctl
does), `print` succeeds only for a loaded label. Nothing touches the real `~/Library/LaunchAgents` or launchd.
"""

import subprocess
from pathlib import Path
from typing import Any

from energatron.adapters.schedule.launchd import LAUNCHCTL


class FakeLaunchctl:
    def __init__(self, *, fail_bootstrap: bool = False) -> None:
        self.loaded: set[str] = set()
        self.calls: list[list[str]] = []
        self.fail_bootstrap = fail_bootstrap

    def __call__(self, argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        assert argv[0] == LAUNCHCTL
        self.calls.append(argv[1:])
        verb, target = argv[1], argv[2]
        code = 0
        if verb == "bootstrap":
            label = Path(argv[3]).stem
            if self.fail_bootstrap:
                code = 5
            else:
                self.loaded.add(label)
        elif verb == "bootout":
            label = target.rsplit("/", 1)[1]
            code = 0 if label in self.loaded else 3
            self.loaded.discard(label)
        elif verb == "print":
            code = 0 if target.rsplit("/", 1)[1] in self.loaded else 113
        return subprocess.CompletedProcess(argv, code, b"", b"")
