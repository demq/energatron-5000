"""`adapters/notify/macos.py` (P7 spec §7): text as arguments, never script; the log seam; failures return false."""

import json
import subprocess
from pathlib import Path
from typing import Any

from energatron.adapters.notify.macos import NOTIFY_LOG_ENV, SCRIPT, Notifier


class Recorder:
    def __init__(self, returncode: int = 0, raises: Exception | None = None) -> None:
        self.calls: list[list[str]] = []
        self.returncode = returncode
        self.raises = raises

    def __call__(self, argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        self.calls.append(argv)
        if self.raises is not None:
            raise self.raises
        return subprocess.CompletedProcess(argv, self.returncode, b"", b"")


def test_the_text_travels_as_arguments() -> None:
    run = Recorder()
    title, message = 'energatron: "quoted"', 'say "hi" & do shell script "x"'
    assert Notifier({}, platform="darwin", run=run)(title, message)
    (argv,) = run.calls
    assert argv[0] == "osascript"
    assert argv[1::2][: len(SCRIPT)] == ["-e"] * len(SCRIPT)
    assert [argv[2 + 2 * i] for i in range(len(SCRIPT))] == list(SCRIPT)
    assert argv[-2:] == [title, message]
    assert all(message not in line for line in SCRIPT)


def test_a_failing_osascript_returns_false() -> None:
    assert not Notifier({}, platform="darwin", run=Recorder(returncode=1))("t", "m")
    assert not Notifier({}, platform="darwin", run=Recorder(raises=FileNotFoundError()))("t", "m")


def test_other_platforms_show_nothing() -> None:
    run = Recorder()
    assert not Notifier({}, platform="linux", run=run)("t", "m")
    assert run.calls == []


def test_the_log_seam_appends_json_lines(tmp_path: Path) -> None:
    log = tmp_path / "notify.jsonl"
    run = Recorder()
    notifier = Notifier({NOTIFY_LOG_ENV: str(log)}, platform="darwin", run=run)
    assert notifier("a", "b") and notifier("c", "d")
    assert [json.loads(line) for line in log.read_text().splitlines()] == [
        {"title": "a", "message": "b"},
        {"title": "c", "message": "d"},
    ]
    assert run.calls == []
