"""Where a workspace keeps its files (`WorkspacePaths`) and its two config kinds (D1 §4.8; D2 §6.1, §6.2)."""

from pathlib import Path
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from energatron.core.models._common import FrozenModel, RegionName
from energatron.core.models.catalog import CATALOG_FILENAME, SourceType
from energatron.core.models.catalog import blob_key as _blob_key
from energatron.core.models.storage import RAW_PREFIX
from energatron.core.persisted import Persisted


class WorkspacePaths(BaseModel):
    """The absolute workspace root for this run, and the paths under it. Never persisted (D1 §4.8).

    Building a path reads nothing: whether a file exists is the caller's question. The raw-zone layout is D3 §3.2's.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    root: Path

    @field_validator("root")
    @classmethod
    def _absolute(cls, root: Path) -> Path:
        if not root.is_absolute() or ".." in root.parts:
            raise ValueError("the workspace root must be an absolute path")
        return root

    @property
    def energatron_dir(self) -> Path:
        return self.root / ".energatron"

    @property
    def workspace_config(self) -> Path:
        """`.energatron/workspace.toml`, which marks the workspace (D1 §8.3)."""
        return self.energatron_dir / "workspace.toml"

    @property
    def local_config(self) -> Path:
        """`.energatron/local.toml`: git-ignored, machine-specific (D2 §6.2)."""
        return self.energatron_dir / "local.toml"

    @property
    def backup_state(self) -> Path:
        """`.energatron/backup_state.json`: what the last backup stored (D2 §6.5)."""
        return self.energatron_dir / "backup_state.json"

    @property
    def drill_state(self) -> Path:
        """`.energatron/drill_state.json`: when the last restore drill passed (P7 spec §9)."""
        return self.energatron_dir / "drill_state.json"

    @property
    def logs_dir(self) -> Path:
        """`.energatron/logs/`: git-ignored; the scheduled `watch` writes its log here (P7 spec §8)."""
        return self.energatron_dir / "logs"

    @property
    def lock(self) -> Path:
        """`.energatron/lock`: the workspace lock (D1 §8.8)."""
        return self.energatron_dir / "lock"

    @property
    def tmp_dir(self) -> Path:
        """`.energatron/tmp/`: git-ignored scratch space."""
        return self.energatron_dir / "tmp"

    @property
    def private_dir(self) -> Path:
        """`private/`: DVC-tracked, never git-tracked."""
        return self.root / "private"

    @property
    def private_config(self) -> Path:
        return self.private_dir / "config.toml"

    @property
    def denylist_generated(self) -> Path:
        """`private/denylist.generated.txt`, written by `energatron denylist` (D2 §7.2)."""
        return self.private_dir / "denylist.generated.txt"

    @property
    def denylist_manual(self) -> Path:
        """`private/denylist.txt`: optional and hand-written (D2 §7.2)."""
        return self.private_dir / "denylist.txt"

    @property
    def raw_root(self) -> Path:
        """`data/raw/`: the raw zone, one directory per source (D3 §3.2)."""
        return self.root / RAW_PREFIX.rstrip("/")

    def raw_dir(self, source: SourceType) -> Path:
        """`data/raw/<source>/`: the source's catalog and its stored files."""
        return self.raw_root / SourceType(source).value

    def catalog_path(self, source: SourceType) -> Path:
        """`data/raw/<source>/_catalog.jsonl` (D1 §4.1)."""
        return self.raw_dir(source) / CATALOG_FILENAME

    @staticmethod
    def blob_key(source: SourceType, dataset_id: str, ext: str) -> str:
        """The key a dataset is stored under, `data/raw/<source>/<YYYY>/<dataset_id><ext>` (D3 §3.2)."""
        return _blob_key(source, dataset_id, ext)

    def relative(self, path: Path) -> str:
        """`path` as a workspace-relative POSIX path, the only form output and persisted files use (D1 §12).

        Raises `ValueError`, without repeating the path, for a relative path, a `..` part or a path outside the root.
        """
        if not path.is_absolute() or ".." in path.parts or not path.is_relative_to(self.root):
            raise ValueError("expected an absolute path inside the workspace")
        return path.relative_to(self.root).as_posix()


class BackupPolicy(FrozenModel):
    """`[backup]` in `workspace.toml`: bundle retention (D2 §8.3)."""

    keep_latest: int = Field(default=10, ge=3, strict=True)  # bundles kept regardless of age
    keep_monthly: bool = Field(default=True, strict=True)  # also the newest bundle of every calendar month (UTC)


class WorkspaceConfig(Persisted):
    """`.energatron/workspace.toml`: git-tracked and non-personal (D2 §6.1)."""

    KIND: ClassVar[str] = "workspace_config"
    SCHEMA_VERSION: ClassVar[int] = 1
    kind: Literal["workspace_config"] = "workspace_config"
    schema_version: Literal[1] = 1

    region: RegionName  # a region profile name; P1 checks that the profile exists
    backup: BackupPolicy = Field(default_factory=BackupPolicy)


class LocalBackup(FrozenModel):
    """`[backup]` in `local.toml`."""

    # As written: the adapter expands `~` and checks that the result is absolute and outside the workspace. Missing
    # → `backup` raises E_BACKUP_NOT_CONFIGURED (D2 §8.1).
    bundle_dir: str | None = Field(default=None, min_length=1)


class LocalConfig(Persisted):
    """`.energatron/local.toml`: git-ignored and machine-specific (D2 §6.2). The DVC remote isn't repeated here."""

    KIND: ClassVar[str] = "local_config"
    SCHEMA_VERSION: ClassVar[int] = 1
    kind: Literal["local_config"] = "local_config"
    schema_version: Literal[1] = 1

    backup: LocalBackup = Field(default_factory=LocalBackup)
