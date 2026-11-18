"""Progress on stderr for people (D1 §8.4; owner request M.2): one line per phase and per stage start and end.

Plain lines, no cursor tricks, so the output reads the same in a terminal, a log file or a pipe. Every line is
redacted with the invocation's denylist. In JSON mode nothing is printed (`progress_for` gives `NullProgress`), so
`--json` output and scripted callers are unchanged.
"""

import threading

from energatron.adapters.cli.output import Invocation, write_stderr
from energatron.ports.progress import Progress
from energatron.services.progress import NullProgress, duration


class StderrProgress:
    """Writes progress lines to stderr. Safe to call from the pipeline runner's watcher thread."""

    def __init__(self, invocation: Invocation) -> None:
        self._invocation = invocation
        self._lock = threading.Lock()

    def step(self, message: str) -> None:
        self._line(message)

    def stage_started(self, name: str, position: int, total: int) -> None:
        self._line(f"  {_counter(position, total)} {name} ...")

    def stage_finished(self, name: str, position: int, total: int, *, exit_code: int | None, seconds: float) -> None:
        counter, took = _counter(position, total), duration(seconds)
        if exit_code == 0:
            self._line(f"  {counter} {name} done in {took}")
        elif exit_code is None:
            self._line(f"  {counter} {name} stopped after {took}")
        else:
            self._line(f"  {counter} {name} FAILED after {took} (exit code {exit_code})")

    def _line(self, text: str) -> None:
        with self._lock:
            write_stderr(self._invocation, f"{text}\n")


def progress_for(invocation: Invocation) -> Progress:
    """`StderrProgress` in human mode; `NullProgress` with `--json`."""
    return NullProgress() if invocation.json_mode else StderrProgress(invocation)


def _counter(position: int, total: int) -> str:
    return f"[{position:>{len(str(total))}}/{total}]"
