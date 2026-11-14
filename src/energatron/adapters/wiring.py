"""The composition root: `build_context()` builds the `ServiceContext` from the workspace's files (D1 §3, §4.8).

Driving adapters (the CLI now) reach the driven adapters only through here. The raw zone is `JsonlCatalog` and
`FsBlobStore` (P1.2.2); the `PipelineRunner` is `DvcRunner` (P1.3.3). `code_fingerprint()` computes the live code
fingerprint of every registered stage, and `stage_code()` one stage's live entry (D1 §11). `parsers` are the
registered parsers the workspace region's profile lists (D3 §10.3); `region` is that profile (P2 spec §5.3).
"""

import importlib.metadata
import importlib.resources
import json
import os
import sys
import urllib.parse
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, Literal

import yaml

from energatron import __version__
from energatron.adapters.notify.macos import Notifier
from energatron.adapters.offers.cdr import DEFAULT_ROOT, CdrProvider
from energatron.adapters.offers.frozen import FrozenProvider
from energatron.adapters.pipeline.dvc_runner import REPRO_LOG_ENV as REPRO_LOG_ENV  # for the CLI's main()
from energatron.adapters.pipeline.dvc_runner import DvcRunner
from energatron.adapters.pipeline.fingerprint import (
    InstalledDistributions,
    commit_published,
    compute_fingerprint,
    package_dir,
)
from energatron.adapters.pipeline.fingerprint import read_fingerprint as read_fingerprint  # for the CLI
from energatron.adapters.pipeline.fingerprint import refresh_fingerprint as refresh_fingerprint  # for the CLI
from energatron.adapters.pipeline.fingerprint import stage_code as _stage_code
from energatron.adapters.schedule.launchd import Launchd as Launchd  # for the CLI
from energatron.adapters.setup.subprocesses import SubprocessSetup
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


def build_context(paths: WorkspacePaths, *, clock: Clock | None = None, dvc: Path | None = None) -> ServiceContext:
    """Read the workspace's config files and build every port for one run.

    `workspace.toml` gives the region, whose profile picks the parsers (D3 §10.3), and the backup policy;
    `local.toml` the bundle folder, `~` expanded and unchecked (None when unset; the real `backup` alone checks it,
    D2 §8.1 step 1); `private/config.toml` the private config, or None when it's missing. The CDR offer provider is
    `offers["cdr"]` (P4 spec §3.1). `dvc` names the DVC executable git and DVC use (`init` passes the new workspace's
    venv's, P8 spec §4.3); by default the one beside the running interpreter.
    """
    clock = clock if clock is not None else SystemClock()
    config = read_workspace_config(paths)
    bundle_dir = backup_bundle_dir(read_local_config(paths))
    private = read_private_config(paths)
    region = load_region_profile(config.region)
    parsers = parsers_for(region)
    vcs = GitDvcVersionControl(paths, clock=clock, bundle_dir=bundle_dir, policy=config.backup, dvc=dvc)
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


def tool_unpublished(paths: WorkspacePaths) -> bool | None:
    """Whether the tool commit the workspace last recorded (an editable install's) is on no remote branch yet.

    The restore drill clones the tool from its remote and checks that commit out, so it must be pushed first
    (P7.4.2). `None` when it can't be told: no recorded commit, another install kind, no remote, git fails.
    """
    try:
        recorded = read_fingerprint(paths.root / CODE_FINGERPRINT_PATH)
    except InvalidInputError:
        return None
    if recorded is None or recorded.install != "editable" or recorded.tool_commit is None:
        return None
    published = commit_published(recorded.tool_commit)
    return None if published is None else not published


def stage_code(name: str) -> StageCode:
    """The live code entry of the registered stage `name` (D1 §11.1); an unknown name → `E_USAGE`."""
    spec = get_stage(name)
    return _stage_code(
        spec.impl.__module__, spec.package_data, package_dir=package_dir(), distributions=InstalledDistributions()
    )


# P8: init (P8 spec §4)


def workspace_setup() -> SubprocessSetup:
    """The `WorkspaceSetup` adapter for `init`."""
    return SubprocessSetup()


def region_names() -> list[str]:
    """The region profiles this version ships, sorted (P8 spec §9)."""
    folder = importlib.resources.files("energatron").joinpath("regions")
    return sorted(item.name.removesuffix(".yaml") for item in folder.iterdir() if item.name.endswith(".yaml"))


def template_sources() -> dict[str, str]:
    """Every workspace template file's text, by its name under `templates/workspace/` (P8 spec §3.1)."""
    from energatron.core.workspace_template import TEMPLATE_FILES

    folder = importlib.resources.files("energatron").joinpath("templates", "workspace")
    found: dict[str, str] = {}
    for spec in TEMPLATE_FILES:
        resource = folder.joinpath(*spec.source.split("/"))
        if resource.is_file():
            found[spec.source] = resource.read_text(encoding="utf-8")
    return found


def running_tool_source() -> tuple[Literal["path", "git", "index"], str | None, str | None]:
    """Where the running tool came from, as a new workspace should depend on it (P8 spec §4.2): an editable folder,
    a git URL and commit, or nothing (an index install). Read from the install's `direct_url.json` (PEP 610)."""
    try:
        text = importlib.metadata.distribution("energatron-5000").read_text("direct_url.json")
    except importlib.metadata.PackageNotFoundError:
        return "index", None, None
    if text is None:
        return "index", None, None
    try:
        document = json.loads(text)
    except ValueError:
        return "index", None, None
    url = document.get("url") if isinstance(document, dict) else None
    if not isinstance(url, str):
        return "index", None, None
    vcs = document.get("vcs_info")
    if isinstance(vcs, dict) and vcs.get("vcs") == "git":
        commit = vcs.get("commit_id")
        return "git", url.removeprefix("git+"), commit if isinstance(commit, str) else None
    directory = document.get("dir_info")
    if isinstance(directory, dict) and url.startswith("file://"):
        return "path", urllib.parse.unquote(urllib.parse.urlparse(url).path), None
    return "index", None, None


def demo_provider(clock: Clock, region: RegionProfile) -> FrozenProvider:
    """The demo's frozen offer set as an offer provider (P8 spec §5.3)."""
    from energatron.services import demo

    plans = demo.demo_plans(region)
    return FrozenProvider(
        clock=clock,
        lists={demo.DEMO_BASE: demo.list_body(plans)},
        details={(demo.DEMO_BASE, plan["planId"]): demo.detail_body(plan) for plan in plans},
    )
