"""What the `VersionControl` port reports about backups (D1 §4.4; D2 §1 changes 6 and 12, §6.5, §8)."""

from typing import ClassVar, Literal

from pydantic import Field

from energatron.core.models._common import BundleName, CommitSha, FrozenModel, Sha256Hex, UtcDatetime
from energatron.core.persisted import Persisted


class BackupReport(FrozenModel):
    """What `VersionControl.backup()` did. Counts and file names only, never absolute paths."""

    dvc_files_pushed: int = Field(ge=0)  # files `dvc push` uploaded; 0 when the remote was already complete
    bundle_name: BundleName  # the new, verified bundle, or the existing one when nothing changed; no directory
    bundles_kept: int = Field(ge=0)  # bundles left after retention
    bundles_removed: int = Field(ge=0)  # bundles retention deleted
    bundle_created: bool  # False when the refs equal the newest bundle's heads (D2 §8.1 step 3)
    dataless_files: int = Field(ge=0)  # iCloud placeholders under the backup locations (D2 §8.1 step 6)


class BackupState(Persisted):
    """`.energatron/backup_state.json`: what the last successful backup stored (D2 §6.5). A hint only."""

    KIND: ClassVar[str] = "backup_state"
    SCHEMA_VERSION: ClassVar[int] = 1
    kind: Literal["backup_state"] = "backup_state"
    schema_version: Literal[1] = 1

    head: CommitSha
    refs_sha256: Sha256Hex  # sha256 of the sorted `<objectname> <refname>` lines, HEAD included
    bundle_name: BundleName
    at: UtcDatetime


class BackupStatus(FrozenModel):
    """Whether a backup is pending, from `backup_state` and the current refs (D2 §8.5). Never touches iCloud."""

    head: CommitSha | None  # None before the first commit
    last_backup_head: CommitSha | None  # None when there's no marker
    last_backup_at: UtcDatetime | None
    refs_backed_up: bool  # the current refs hash equals the marker's
    commits_not_backed_up: int | None = Field(ge=0)  # None with no marker, or when the old head no longer exists
