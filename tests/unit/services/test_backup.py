"""`services.backup`: `backup` and `backup_status` over a fake `VersionControl` (D2 §5.9, §6.4, §8.1, §8.5)."""

from collections.abc import Sequence
from datetime import UTC, datetime

import pytest

from energatron.core.errors import ResultWarning, UsageError, WarningCode
from energatron.core.models.vcs import BackupReport, BackupStatus
from energatron.services.backup import BackupResult, BackupStatusResult, backup, backup_status
from tests.unit.services.test_context import build

SHA = "1a2b3c4" + "d" * 33
OLD = "9f8e7d6" + "c" * 33
BUNDLE = "20261023T093000Z-1a2b3c4.bundle"
AT = datetime(2026, 10, 23, 9, 30, tzinfo=UTC)


class RecordingVcs:
    def __init__(self, *, head: str | None = SHA, dataless: int = 0, status: BackupStatus | None = None) -> None:
        self._head = head
        self._dataless = dataless
        self._status = status
        self.backups = 0

    def head(self) -> str | None:
        return self._head

    def dirty_paths(self, paths: Sequence[str] = ()) -> list[str]:
        return []

    def track(self, paths: Sequence[str]) -> list[str]:
        return []

    def commit(self, paths: Sequence[str], message: str) -> str:
        raise AssertionError("backup never commits")

    def backup(self) -> BackupReport:
        self.backups += 1
        return BackupReport(
            dvc_files_pushed=3,
            bundle_name=BUNDLE,
            bundles_kept=2,
            bundles_removed=1,
            bundle_created=True,
            dataless_files=self._dataless,
        )

    def backup_status(self) -> BackupStatus:
        assert self._status is not None
        return self._status


def test_backup_returns_the_report_without_warnings() -> None:
    vcs = RecordingVcs()
    result = backup(build(vcs=vcs))
    assert vcs.backups == 1
    assert result.dry_run is False
    assert result.report == vcs.backup()
    assert result.warnings == ()
    assert result.model_dump(mode="json") == {
        "kind": "backup_result",
        "schema_version": 1,
        "report": {
            "dvc_files_pushed": 3,
            "bundle_name": BUNDLE,
            "bundles_kept": 2,
            "bundles_removed": 1,
            "bundle_created": True,
            "dataless_files": 0,
        },
        "dry_run": False,
    }


def test_dataless_files_add_a_warning_with_the_count() -> None:
    result = backup(build(vcs=RecordingVcs(dataless=4)))
    assert result.warnings == (
        ResultWarning(code=WarningCode.W_BACKUP_DATALESS, message=result.warnings[0].message, details={"count": 4}),
    )
    assert "Keep Downloaded" in result.warnings[0].message
    assert "warnings" not in result.model_dump(mode="json")  # the CLI moves them to the envelope (D1 §8.5)


def test_a_dry_run_checks_the_head_and_never_backs_up() -> None:
    vcs = RecordingVcs()
    result = backup(build(vcs=vcs), dry_run=True)
    assert vcs.backups == 0
    assert result.model_dump(mode="json") == {
        "kind": "backup_result",
        "schema_version": 1,
        "report": None,
        "dry_run": True,
    }


def test_a_dry_run_before_the_first_commit_is_a_usage_error() -> None:
    vcs = RecordingVcs(head=None)
    with pytest.raises(UsageError):
        backup(build(vcs=vcs), dry_run=True)
    assert vcs.backups == 0


def test_the_real_run_leaves_the_checks_to_the_adapter() -> None:
    vcs = RecordingVcs(head=None)  # the real adapter raises E_USAGE itself, after its configuration checks
    backup(build(vcs=vcs))
    assert vcs.backups == 1


def test_backup_status_wraps_the_port_status() -> None:
    status = BackupStatus(
        head=SHA, last_backup_head=OLD, last_backup_at=AT, refs_backed_up=False, commits_not_backed_up=2
    )
    result = backup_status(build(vcs=RecordingVcs(status=status)))
    assert isinstance(result, BackupStatusResult)
    assert result.model_dump(mode="json") == {
        "kind": "backup_status_result",
        "schema_version": 1,
        "head": SHA,
        "last_backup_head": OLD,
        "last_backup_at": "2026-10-23T09:30:00Z",
        "refs_backed_up": False,
        "commits_not_backed_up": 2,
    }
    assert result.warnings == ()


def test_backup_status_with_no_marker() -> None:
    status = BackupStatus(
        head=None, last_backup_head=None, last_backup_at=None, refs_backed_up=False, commits_not_backed_up=None
    )
    result = backup_status(build(vcs=RecordingVcs(status=status)))
    assert result.model_dump(mode="json")["commits_not_backed_up"] is None
    assert result.model_dump(mode="json")["last_backup_at"] is None


def test_the_result_kinds_read_back_from_their_documents() -> None:
    dumped = backup(build(vcs=RecordingVcs())).model_dump(mode="json")
    assert BackupResult.from_document(dumped).model_dump(mode="json") == dumped
