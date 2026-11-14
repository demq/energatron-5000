"""`energatron diagnose`: a support report built from structure, never from values (P8 spec §8).

It holds versions, kinds and schema versions, booleans, counts, stage names, error and warning codes, and the newest
run's ID. Never file names under `data/`, catalog file names or notes, dataset IDs, plan names, kWh or $ figures, or a
path outside the workspace; the CLI also passes the whole envelope through the denylist redaction (D1 §8.7). Each
block that fails becomes a line in `problems` with its error code, so a broken workspace still gets a report.
Read-only; takes no lock.
"""

import json
import logging
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import ClassVar, Literal, TypeVar

import yaml

from energatron.core.errors import EnergatronError
from energatron.core.models._common import FrozenModel
from energatron.core.models.catalog import SourceType
from energatron.core.persisted import registered_kinds
from energatron.core.workspace_template import TEMPLATE_STATE_PATH
from energatron.services import denylist as denylist_service
from energatron.services import status as status_service
from energatron.services.context import ServiceContext, ServiceResult

_log = logging.getLogger(__name__)
_T = TypeVar("_T")

_CONFIGS: tuple[tuple[str, Literal["toml", "yaml", "json"]], ...] = (
    (".energatron/workspace.toml", "toml"),
    ("private/config.toml", "toml"),
    ("params.yaml", "yaml"),
    (TEMPLATE_STATE_PATH, "json"),
)


class ToolInfo(FrozenModel):
    """What the adapter found about the tool and this machine."""

    energatron_version: str
    install: str
    tool_dirty: bool
    python: str
    platform: str
    machine: str
    git: str | None
    uv: str | None
    dvc: str | None


class ConfigInfo(FrozenModel):
    path: str
    present: bool
    kind: str | None
    schema_version: int | None
    current: bool | None  # None when the kind is unknown or the file is missing


class WorkspaceInfo(FrozenModel):
    region: str
    template_version: int | None
    configs: list[ConfigInfo]
    pipeline_synced: bool | None
    pipeline_drift: list[str]
    code_stale: list[str]
    catalog_records: dict[str, int] | None
    runs: int | None
    decisions: int | None
    last_run_id: str | None
    last_run_draft: bool | None
    private_config: bool


class Problem(FrozenModel):
    check: str
    code: str


class DiagnoseResult(ServiceResult):
    """`diagnose_result` v1 (P8 spec §8)."""

    KIND: ClassVar[str] = "diagnose_result"
    SCHEMA_VERSION: ClassVar[int] = 1
    kind: Literal["diagnose_result"] = "diagnose_result"
    schema_version: Literal[1] = 1

    tool: ToolInfo
    workspace: WorkspaceInfo
    reminders: list[str]
    problems: list[Problem]


def _config(root: Path, relative: str, fmt: Literal["toml", "yaml", "json"]) -> ConfigInfo:
    """A config file's kind and version only: the rest of the document is never kept."""
    path = root / relative
    if not path.is_file():
        return ConfigInfo(path=relative, present=False, kind=None, schema_version=None, current=None)
    try:
        text = path.read_text(encoding="utf-8")
        document = tomllib.loads(text) if fmt == "toml" else yaml.safe_load(text) if fmt == "yaml" else json.loads(text)
    except OSError, UnicodeDecodeError, ValueError, yaml.YAMLError:
        return ConfigInfo(path=relative, present=True, kind=None, schema_version=None, current=None)
    if not isinstance(document, dict):
        return ConfigInfo(path=relative, present=True, kind=None, schema_version=None, current=None)
    kind = document.get("kind")
    version = document.get("schema_version")
    kind = kind if isinstance(kind, str) and kind in registered_kinds() else None
    version = version if type(version) is int else None
    current = None if kind is None or version is None else version == registered_kinds()[kind].SCHEMA_VERSION
    return ConfigInfo(path=relative, present=True, kind=kind, schema_version=version, current=current)


def diagnose(
    ctx: ServiceContext,
    *,
    tool: ToolInfo,
    code_stale: list[str],
    tool_unpublished: bool | None = None,
) -> DiagnoseResult:
    """The report (P8 spec §8)."""
    root = ctx.paths.root
    problems: list[Problem] = []

    def attempt(check: str, block: Callable[[], _T]) -> _T | None:
        try:
            return block()
        except EnergatronError as error:
            problems.append(Problem(check=check, code=error.code))
            return None

    configs = [_config(root, relative, fmt) for relative, fmt in _CONFIGS]
    template = next(item for item in configs if item.path == TEMPLATE_STATE_PATH)
    template_version: int | None = None
    if template.present and template.kind is not None:
        try:
            found = json.loads((root / TEMPLATE_STATE_PATH).read_text(encoding="utf-8")).get("template_version")
            template_version = found if type(found) is int else None
        except OSError, ValueError, AttributeError:
            template_version = None
    status = attempt(
        "status",
        lambda: status_service.status(
            ctx, code_stale=code_stale, tool_dirty=tool.tool_dirty, tool_unpublished=tool_unpublished
        ),
    )
    records = attempt("catalog", lambda: list(ctx.catalog.records()))
    counts = None if records is None else {source.value: 0 for source in SourceType}
    if counts is not None and records is not None:
        for record in records:
            counts[record.source.value] += 1
    if ctx.private is not None:
        attempt("denylist", lambda: denylist_service.refresh_denylist(ctx, check=True))
    if status is not None and not status.pipeline.synced:
        problems.append(Problem(check="pipeline", code="E_PIPELINE_NOT_SYNCED"))
    runs_dir, decisions_dir = root / "data" / "runs", root / "data" / "decisions"
    workspace = WorkspaceInfo(
        region=ctx.config.region,
        template_version=template_version,
        configs=configs,
        pipeline_synced=None if status is None else status.pipeline.synced,
        pipeline_drift=[] if status is None else list(status.pipeline.drift),
        code_stale=sorted(code_stale),
        catalog_records=counts,
        runs=len(list(runs_dir.glob("*.json"))) if runs_dir.is_dir() else 0,
        decisions=len(list(decisions_dir.glob("*.json"))) if decisions_dir.is_dir() else 0,
        last_run_id=None if status is None or status.last_run is None else status.last_run.run_id,
        last_run_draft=None if status is None or status.last_run is None else status.last_run.draft,
        private_config=ctx.private is not None,
    )
    _log.info("diagnose: %d problems", len(problems))
    return DiagnoseResult(
        tool=tool,
        workspace=workspace,
        reminders=[] if status is None else list(status.reminders),
        problems=problems,
    )
