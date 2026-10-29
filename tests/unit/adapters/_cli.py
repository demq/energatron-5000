"""Helpers for the CLI tests: run `main()` in-process, and a fake `VersionControl` the wiring builds instead of git.

Everything runs against scratch folders. The fake never touches git, DVC or a backup location.
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import pytest

from energatron.adapters.cli.app import main
from energatron.core.canonical import JSONValue
from energatron.core.models.vcs import BackupReport, BackupStatus

SHA = "1a2b3c4" + "d" * 33
BUNDLE = "20261023T093000Z-1a2b3c4.bundle"


@dataclass
class Ran:
    code: int
    out: str
    err: str

    def envelope(self) -> dict[str, Any]:
        """The one JSON document on stdout. Fails the test unless stdout is exactly that document and a newline."""
        assert self.out.endswith("\n")
        assert self.out.count("\n") == 1, "stdout must hold exactly one JSON document"
        document = json.loads(self.out)
        assert isinstance(document, dict)
        return document


def run_cli(capsys: pytest.CaptureFixture[str], *argv: str) -> Ran:
    capsys.readouterr()  # drop anything printed before
    code = main(list(argv))
    captured = capsys.readouterr()
    return Ran(code, captured.out, captured.err)


@dataclass
class FakeVcsState:
    tracked: list[list[str]] = field(default_factory=list)
    backups: int = 0
    dataless: int = 0
    head: str | None = SHA


class FakeVcs:
    """What `wiring.build_context()` builds in place of `GitDvcVersionControl` (same constructor)."""

    state = FakeVcsState()

    def __init__(self, paths: Any, **kwargs: Any) -> None:
        self.paths = paths
        self.kwargs = kwargs

    def head(self) -> str | None:
        return self.state.head

    def dirty_paths(self, paths: Sequence[str] = ()) -> list[str]:
        return []

    def track(self, paths: Sequence[str]) -> list[str]:
        self.state.tracked.append(list(paths))
        return [f"{path}.dvc" for path in paths]

    def commit(self, paths: Sequence[str], message: str) -> str:
        raise AssertionError("no P0 command commits")

    def backup(self) -> BackupReport:
        self.state.backups += 1
        return BackupReport(
            dvc_files_pushed=2,
            bundle_name=BUNDLE,
            bundles_kept=1,
            bundles_removed=0,
            bundle_created=True,
            dataless_files=self.state.dataless,
        )

    def backup_status(self) -> BackupStatus:
        return BackupStatus(
            head=self.state.head,
            last_backup_head=None,
            last_backup_at=None,
            refs_backed_up=False,
            commits_not_backed_up=None,
        )


def data_of(envelope: dict[str, Any]) -> dict[str, JSONValue]:
    data = envelope["data"]
    assert isinstance(data, dict)
    return data
