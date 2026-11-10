"""The composition root: `build_context()` builds the `ServiceContext` from the workspace's files (D1 §3, §4.8).

Driving adapters (the CLI now) reach the driven adapters only through here. The raw zone is `JsonlCatalog` and
`FsBlobStore` (P1.2.2); the `PipelineRunner` is `DvcRunner` (P1.3.3). `code_fingerprint()` computes the live code
fingerprint of every registered stage, and `stage_code()` one stage's live entry (D1 §11). `parsers` are the
registered parsers the workspace region's profile lists (D3 §10.3); `region` is that profile (P2 spec §5.3).
"""

import importlib.resources
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import yaml

from energatron import __version__
from energatron.adapters.notify.macos import Notifier
from energatron.adapters.offers.cdr import DEFAULT_ROOT, CdrProvider
from energatron.adapters.pipeline.dvc_runner import REPRO_LOG_ENV as REPRO_LOG_ENV  # for the CLI's main()
from energatron.adapters.pipeline.dvc_runner import DvcRunner
from energatron.adapters.pipeline.fingerprint import InstalledDistributions, compute_fingerprint, package_dir
from energatron.adapters.pipeline.fingerprint import read_fingerprint as read_fingerprint  # for the CLI
from energatron.adapters.pipeline.fingerprint import refresh_fingerprint as refresh_fingerprint  # for the CLI
from energatron.adapters.pipeline.fingerprint import stage_code as _stage_code
from energatron.adapters.schedule.launchd import Launchd as Launchd  # for the CLI
from energatron.adapters.sources.registry import parsers_for
from energatron.adapters.storage.fs_blobs import FsBlobStore
from energatron.adapters.storage.jsonl_catalog import JsonlCatalog
from energatron.adapters.vcs.git_dvc import GitDvcVersionControl
from energatron.adapters.workspace import (
    backup_bundle_dir,
    read_local_config,
    read_private_config,
    read_workspace_config,
)
from energatron.core.errors import InternalError, InvalidInputError, SchemaTooNewError
from energatron.core.models.provenance import CodeFingerprint, StageCode, stale_stages
from energatron.core.models.region import RegionProfile
from energatron.core.models.workspace import WorkspacePaths
from energatron.ports.clock import Clock
from energatron.services.context import ServiceContext
from energatron.services.stages.dvc_yaml import CODE_FINGERPRINT_PATH
from energatron.services.stages.registry import STAGES, get_stage


class SystemClock:
    """The production `Clock`: the wall clock, aware, in UTC (D1 §4.7)."""

    def now(self) -> datetime:
        return datetime.now(UTC)


def build_context(paths: WorkspacePaths, *, clock: Clock | None = None) -> ServiceContext:
    """Read the workspace's config files and build every port for one run.

    `workspace.toml` gives the region, whose profile picks the parsers (D3 §10.3), and the backup policy;
    `local.toml` the bundle folder, `~` expanded and unchecked (None when unset; the real `backup` alone checks it,
    D2 §8.1 step 1); `private/config.toml` the private config, or None when it's missing. The CDR offer provider is
    `offers["cdr"]` (P4 spec §3.1).
    """
    clock = clock if clock is not None else SystemClock()
    config = read_workspace_config(paths)
    bundle_dir = backup_bundle_dir(read_local_config(paths))
    private = read_private_config(paths)
    region = load_region_profile(config.region)
    parsers = parsers_for(region)
    vcs = GitDvcVersionControl(paths, clock=clock, bundle_dir=bundle_dir, policy=config.backup)
    return ServiceContext(
        paths=paths,
        config=config,
        private=private,
        catalog=JsonlCatalog(paths),
        blobs=FsBlobStore(paths),
        parsers=parsers,
        region=region,
        vcs=vcs,
        runner=DvcRunner(paths, fingerprint=lambda: code_fingerprint()),
        clock=clock,
        offers={"cdr": cdr_provider(clock)},
    )


CDR_ROOT_ENV: Final = "ENERGATRON_CDR_ROOT"
"""Points the CDR provider at another root, such as a local test server (P4 spec §14)."""


def cdr_provider(clock: Clock) -> CdrProvider:
    """The CDR provider, at `ENERGATRON_CDR_ROOT` when set, else the AER's host."""
    root = os.environ.get(CDR_ROOT_ENV) or DEFAULT_ROOT
    return CdrProvider(clock=clock, user_agent=f"energatron/{__version__}", root=root)


def load_region_profile(name: str) -> RegionProfile:
    """The region profile `regions/<name>.yaml` from package data (D3 §10.3).

    No such profile → `E_INVALID_INPUT` with `details.field: "region"` (D2 §6.1). A profile the package ships that
    can't be read or validated, is newer than this tool, leaves its time zone, TOU windows, distributors or CDR
    retailers `null`, or names a zone the installed `tzdata` doesn't have, is a bug → `E_INTERNAL` (P2 spec §5.2).
    """
    resource = importlib.resources.files("energatron").joinpath("regions", f"{name}.yaml")
    if not resource.is_file():
        raise InvalidInputError("The workspace names a region with no profile in this version.", field="region")
    try:
        document = yaml.safe_load(resource.read_text(encoding="utf-8"))
        if not isinstance(document, dict):
            raise InternalError("A region profile isn't a YAML mapping.", region=name)
        profile = RegionProfile.from_document(document)
    except (yaml.YAMLError, InvalidInputError, SchemaTooNewError) as error:
        raise InternalError("A region profile in the package is invalid.", region=name) from error
    if profile.local_tz is None or profile.tou_windows is None:
        raise InternalError("A region profile in the package leaves its time zone or TOU windows unset.", region=name)
    if profile.distributor is None or profile.cdr_retailers is None or profile.gas_distributor is None:
        raise InternalError(
            "A region profile in the package leaves its distributor, gas distributor or CDR retailers unset.",
            region=name,
        )
    if not importlib.resources.files("tzdata").joinpath("zoneinfo", *profile.local_tz.split("/")).is_file():
        raise InternalError("A region profile in the package names a time zone that tzdata doesn't have.", region=name)
    return profile


def code_fingerprint() -> CodeFingerprint:
    """The live code fingerprint: one code entry per registered stage, in `dvc.yaml` order (D1 §11.1, §11.2)."""
    return compute_fingerprint(STAGES)


def notifier() -> Notifier:
    """The desktop notifier (P7 spec §7); `ENERGATRON_NOTIFY_LOG` redirects it to a file."""
    return Notifier(os.environ)


def launchd() -> Launchd:
    """This user's launchd agents (P7 spec §8)."""
    return Launchd(home=Path.home(), uid=os.getuid(), platform=sys.platform)


def code_state(paths: WorkspacePaths) -> tuple[list[str], bool]:
    """`(stale stage names, tool_dirty)`: the live fingerprint against the workspace's recorded one (P7 spec §4).

    An invalid or missing recorded file makes every stage stale.
    """
    live = code_fingerprint()
    try:
        recorded = read_fingerprint(paths.root / CODE_FINGERPRINT_PATH)
    except InvalidInputError:
        recorded = None
    return stale_stages(live, recorded), live.tool_dirty


def stage_code(name: str) -> StageCode:
    """The live code entry of the registered stage `name` (D1 §11.1); an unknown name → `E_USAGE`."""
    spec = get_stage(name)
    return _stage_code(
        spec.impl.__module__, spec.package_data, package_dir=package_dir(), distributions=InstalledDistributions()
    )
