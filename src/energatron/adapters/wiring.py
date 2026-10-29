"""The composition root: `build_context()` builds the `ServiceContext` from the workspace's files (D1 §3, §4.8).

Driving adapters (the CLI now) reach the driven adapters only through here. The raw zone is `JsonlCatalog` and
`FsBlobStore` (P1.2.2). `code_fingerprint()` computes the live code fingerprint of every registered stage (D1 §11,
P1.3.2). `PipelineRunner` is still a `_NotYetPipelineRunner` placeholder whose every method raises
`E_INTERNAL("PipelineRunner arrives in P1")` until P1.3.3, and `parsers` is empty until P1.4.1 (D2 §5.9).
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import NoReturn

from energatron.adapters.pipeline.fingerprint import compute_fingerprint
from energatron.adapters.pipeline.fingerprint import refresh_fingerprint as refresh_fingerprint  # for the CLI
from energatron.adapters.storage.fs_blobs import FsBlobStore
from energatron.adapters.storage.jsonl_catalog import JsonlCatalog
from energatron.adapters.vcs.git_dvc import GitDvcVersionControl
from energatron.adapters.workspace import (
    backup_bundle_dir,
    read_local_config,
    read_private_config,
    read_workspace_config,
)
from energatron.core.errors import InternalError
from energatron.core.models.pipeline import ReproReport, StageStatus
from energatron.core.models.provenance import CodeFingerprint
from energatron.core.models.workspace import WorkspacePaths
from energatron.ports.clock import Clock
from energatron.services.context import ServiceContext
from energatron.services.stages.registry import STAGES


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
        catalog=JsonlCatalog(paths),
        blobs=FsBlobStore(paths),
        parsers=(),
        vcs=vcs,
        runner=_NotYetPipelineRunner(),
        clock=clock,
    )


def code_fingerprint() -> CodeFingerprint:
    """The live code fingerprint: one code entry per registered stage, in `dvc.yaml` order (D1 §11.1, §11.2)."""
    return compute_fingerprint(STAGES)


# The P0 placeholder left until P1.3.3 (D2 §5.9)


def _not_yet(port: str) -> NoReturn:
    raise InternalError(f"{port} arrives in P1")


class _NotYetPipelineRunner:
    def status(self) -> list[StageStatus]:
        _not_yet("PipelineRunner")

    def repro(self, targets: Sequence[str] = ()) -> ReproReport:
        _not_yet("PipelineRunner")
