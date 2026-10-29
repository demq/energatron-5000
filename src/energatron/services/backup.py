"""Backups: `backup` and `backup_status`, with their result kinds (D2 §5.9, §6.4, §8).

`backup` is owner-run: agents can't reach iCloud (D2 §4). The CLI holds the workspace lock around it. `backup_status`
takes no lock and never touches the backup locations; agents may run it.
"""

from typing import ClassVar, Literal

from pydantic import Field

from energatron.core.errors import ResultWarning, UsageError, WarningCode
from energatron.core.models._common import CommitSha, UtcDatetime
from energatron.core.models.vcs import BackupReport
from energatron.services.context import ServiceContext, ServiceResult


class BackupResult(ServiceResult):
    """`backup_result` v1: the adapter's report, or null in a dry run (D2 §6.4)."""

    KIND: ClassVar[str] = "backup_result"
    SCHEMA_VERSION: ClassVar[int] = 1
    kind: Literal["backup_result"] = "backup_result"
    schema_version: Literal[1] = 1

    report: BackupReport | None
    dry_run: bool


class BackupStatusResult(ServiceResult):
    """`backup_status_result` v1: whether a backup is pending (D2 §6.4, §8.5)."""

    KIND: ClassVar[str] = "backup_status_result"
    SCHEMA_VERSION: ClassVar[int] = 1
    kind: Literal["backup_status_result"] = "backup_status_result"
    schema_version: Literal[1] = 1

    head: CommitSha | None
    last_backup_head: CommitSha | None
    last_backup_at: UtcDatetime | None
    refs_backed_up: bool
    commits_not_backed_up: int | None = Field(ge=0)


def backup(ctx: ServiceContext, *, dry_run: bool = False) -> BackupResult:
    """Push DVC data and store a verified git bundle, through `ctx.vcs.backup()` (D2 §8.1, §8.5).

    A dry run only checks that there is a commit to back up (`E_USAGE` before the first commit) and touches no
    backup location; the real run leaves every check to the adapter, in D2 §8.1's order. Dataless files in the
    backup locations add `W_BACKUP_DATALESS`.
    """
    if dry_run:
        if ctx.vcs.head() is None:
            raise UsageError("Nothing to back up yet: the workspace has no commit.")
        return BackupResult(report=None, dry_run=True)
    report = ctx.vcs.backup()
    warnings: list[ResultWarning] = []
    if report.dataless_files > 0:
        warnings.append(
            ResultWarning(
                code=WarningCode.W_BACKUP_DATALESS,
                message=(
                    "Some backed-up files are iCloud placeholders, not downloaded: turn on Keep Downloaded for "
                    "Backups/, or the restore drill will fail."
                ),
                details={"count": report.dataless_files},
            )
        )
    return BackupResult(report=report, dry_run=False, warnings=tuple(warnings))


def backup_status(ctx: ServiceContext) -> BackupStatusResult:
    """Whether a backup is pending, from the local marker and the refs (D2 §8.5). No lock, no backup location."""
    status = ctx.vcs.backup_status()
    return BackupStatusResult(
        head=status.head,
        last_backup_head=status.last_backup_head,
        last_backup_at=status.last_backup_at,
        refs_backed_up=status.refs_backed_up,
        commits_not_backed_up=status.commits_not_backed_up,
    )
