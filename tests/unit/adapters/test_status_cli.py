"""`energatron status` (P7 spec §4): one JSON document, reminders as envelope warnings, and no lock taken."""

from pathlib import Path

import pytest

from tests.unit.adapters._cli import FakeVcsState, run_cli
from tests.unit.adapters._workspace import make_workspace
from tests.unit.adapters.test_locking import held_by_another_process


def test_status_prints_one_document_while_the_lock_is_held(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, fake_vcs: FakeVcsState
) -> None:
    paths = make_workspace(tmp_path)
    with held_by_another_process(paths):
        ran = run_cli(capsys, "status", "--json", "--workspace", str(paths.root))
    assert ran.code == 0, ran.out
    envelope = ran.envelope()
    data = envelope["data"]
    assert data["kind"] == "status_result"
    assert data["pipeline"]["synced"] is False  # a bare scratch workspace was never synced
    assert data["reminders"] == [warning["code"] for warning in envelope["warnings"]]
    assert "W_RUN_BEHIND" in data["reminders"] and "W_DRILL_DUE" in data["reminders"]
    assert not (paths.root / "data").exists()  # wrote nothing


def test_status_human_output_counts_the_reminders(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, fake_vcs: FakeVcsState
) -> None:
    paths = make_workspace(tmp_path)
    ran = run_cli(capsys, "status", "--workspace", str(paths.root))
    assert ran.code == 0
    assert "Last run: none" in ran.out and "Reminders: " in ran.out
