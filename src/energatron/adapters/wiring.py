"""The composition root: `build_context()` builds the `ServiceContext` from the workspace's files (D1 §3, §4.8).

Driving adapters (the CLI now) reach the driven adapters only through here. In P0 the ports that arrive in P1
(`Catalog`, `BlobStore`, `PipelineRunner`) are `_NotYet<Port>` placeholders whose every method raises
`E_INTERNAL("<Port> arrives in P1")`, and `parsers` is empty (D2 §5.9). P1 removes them.
"""

from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from typing import NoReturn

from energatron.adapters.vcs.git_dvc import GitDvcVersionControl
from energatron.adapters.workspace import (
    backup_bundle_dir,
    read_local_config,
    read_private_config,
    read_workspace_config,
)
from energatron.core.errors import InternalError
from energatron.core.models.catalog import CatalogRecord, SourceType
from energatron.core.models.pipeline import ReproReport, StageStatus
from energatron.core.models.storage import StoredBlob
from energatron.core.models.workspace import WorkspacePaths
from energatron.ports.clock import Clock
from energatron.services.context import ServiceContext


class SystemClock:
    """The production `Clock`: the wall clock, aware, in UTC (D1 §4.7)."""

    def now(self) -> datetime:
        return datetime.now(UTC)


def build_context(paths: WorkspacePaths, *, clock: Clock | None = None) -> ServiceContext:
    """Read the workspace's config files and build every port for one run.

    `workspace.toml` gives the region and the backup policy; `local.toml` the bundle folder, `~` expanded and
    unchecked (None when unset; the real `backup` alone checks it, D2 §8.1 step 1); `private/config.toml` the private
    config, or None when it's missing.
    """
    clock = clock if clock is not None else SystemClock()
    config = read_workspace_config(paths)
    bundle_dir = backup_bundle_dir(read_local_config(paths))
    private = read_private_config(paths)
    vcs = GitDvcVersionControl(paths, clock=clock, bundle_dir=bundle_dir, policy=config.backup)
    return ServiceContext(
        paths=paths,
        config=config,
        private=private,
        catalog=_NotYetCatalog(),
        blobs=_NotYetBlobStore(),
        parsers=(),
        vcs=vcs,
        runner=_NotYetPipelineRunner(),
        clock=clock,
    )


# P0 placeholders for the ports that arrive in P1 (D2 §5.9)


def _not_yet(port: str) -> NoReturn:
    raise InternalError(f"{port} arrives in P1")


class _NotYetCatalog:
    def get(self, dataset_id: str) -> CatalogRecord | None:
        _not_yet("Catalog")

    def find_sha256(self, sha256: str) -> CatalogRecord | None:
        _not_yet("Catalog")

    def records(self, source: SourceType | None = None) -> Iterator[CatalogRecord]:
        _not_yet("Catalog")

    def append(self, record: CatalogRecord) -> None:
        _not_yet("Catalog")


class _NotYetBlobStore:
    def put(self, key: str, data: bytes) -> StoredBlob:
        _not_yet("BlobStore")

    def get(self, key: str) -> bytes:
        _not_yet("BlobStore")

    def exists(self, key: str) -> bool:
        _not_yet("BlobStore")

    def stat(self, key: str) -> StoredBlob:
        _not_yet("BlobStore")


class _NotYetPipelineRunner:
    def status(self) -> list[StageStatus]:
        _not_yet("PipelineRunner")

    def repro(self, targets: Sequence[str] = ()) -> ReproReport:
        _not_yet("PipelineRunner")
