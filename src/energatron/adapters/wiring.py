"""The composition root: `build_context()` builds the `ServiceContext` from the workspace's files (D1 §3, §4.8).

Driving adapters (the CLI now) reach the driven adapters only through here. The raw zone is `JsonlCatalog` and
`FsBlobStore` (P1.2.2); the `PipelineRunner` is `DvcRunner` (P1.3.3). `code_fingerprint()` computes the live code
fingerprint of every registered stage, and `stage_code()` one stage's live entry (D1 §11). `parsers` is empty until
P1.4.1.
"""

from datetime import UTC, datetime

from energatron.adapters.pipeline.dvc_runner import REPRO_LOG_ENV as REPRO_LOG_ENV  # for the CLI's main()
from energatron.adapters.pipeline.dvc_runner import DvcRunner
from energatron.adapters.pipeline.fingerprint import InstalledDistributions, compute_fingerprint, package_dir
from energatron.adapters.pipeline.fingerprint import read_fingerprint as read_fingerprint  # for the CLI
from energatron.adapters.pipeline.fingerprint import refresh_fingerprint as refresh_fingerprint  # for the CLI
from energatron.adapters.pipeline.fingerprint import stage_code as _stage_code
from energatron.adapters.storage.fs_blobs import FsBlobStore
from energatron.adapters.storage.jsonl_catalog import JsonlCatalog
from energatron.adapters.vcs.git_dvc import GitDvcVersionControl
from energatron.adapters.workspace import (
    backup_bundle_dir,
    read_local_config,
    read_private_config,
    read_workspace_config,
)
from energatron.core.models.provenance import CodeFingerprint, StageCode
from energatron.core.models.workspace import WorkspacePaths
from energatron.ports.clock import Clock
from energatron.services.context import ServiceContext
from energatron.services.stages.registry import STAGES, get_stage


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
        runner=DvcRunner(paths, fingerprint=lambda: code_fingerprint()),
        clock=clock,
    )


def code_fingerprint() -> CodeFingerprint:
    """The live code fingerprint: one code entry per registered stage, in `dvc.yaml` order (D1 §11.1, §11.2)."""
    return compute_fingerprint(STAGES)


def stage_code(name: str) -> StageCode:
    """The live code entry of the registered stage `name` (D1 §11.1); an unknown name → `E_USAGE`."""
    spec = get_stage(name)
    return _stage_code(
        spec.impl.__module__, spec.package_data, package_dir=package_dir(), distributions=InstalledDistributions()
    )
