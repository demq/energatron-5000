"""The `VersionControl` port: `dvc add`, git commit and backups (D1 §4.4; D2 §1 change 12, §8)."""

from collections.abc import Sequence
from typing import Protocol

from energatron.core.models.vcs import BackupReport, BackupStatus


class VersionControl(Protocol):
    """Six methods: D1 §4.4's five, plus `backup_status()` (D2 §1 change 12). Paths are workspace-relative."""

    def head(self) -> str | None:
        """The full hex SHA of HEAD; None before the first commit."""
        ...

    def dirty_paths(self, paths: Sequence[str] = ()) -> list[str]:
        """Tracked-but-modified and untracked files under `paths` (the whole repo if empty)."""
        ...

    def track(self, paths: Sequence[str]) -> list[str]:
        """`dvc add`; returns the pointer files written."""
        ...

    def commit(self, paths: Sequence[str], message: str) -> str:
        """Stage exactly `paths`, commit with the normal hooks, and return the new commit SHA.

        Nothing to commit returns the current `head()`. A rejecting hook raises `E_COMMIT_REJECTED`, or
        `E_DENYLIST_HIT` when the privacy guard found a hit (D2 §8.6).
        """
        ...

    def backup(self) -> BackupReport:
        """`dvc push`, then a verified git bundle (D2 §8.1). Only the owner runs it."""
        ...

    def backup_status(self) -> BackupStatus:
        """Whether a backup is pending, from the local marker and the current refs; never touches iCloud (D2 §8.5)."""
        ...
