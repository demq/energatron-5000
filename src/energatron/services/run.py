"""`energatron run` (P6 spec §8; plan §9.4): run the pipeline, record the run, commit.

`run(ctx, tool=…, draft=…)` checks that the pipeline files are in sync, that no tracked config is dirty and the tool is
clean (unless `draft`), runs `dvc repro` through the `PipelineRunner` (which refreshes the code fingerprint first),
writes the run manifest `data/runs/<run_id>.json`, tracks `data/runs` and commits exactly the files a run changes. The
run ID and the draft flag live only in the manifest (D1 §9.4): no stage sees them. Each phase is announced through
`ctx.progress`; the runner reports the stages. The caller holds the workspace lock
and passes the live tool state (services can't compute code fingerprints, D1 §11.4).
"""

import json
import logging
from typing import ClassVar, Final, Literal

from energatron.core.errors import DirtyError, InvalidInputError, ResultWarning, SchemaTooNewError, WarningCode
from energatron.core.ids import run_id as make_run_id
from energatron.core.models.costs import OFFERS_SUMMARY_FILENAME, OffersSummary
from energatron.core.models.recommend import (
    RECOMMENDATION_FILENAME,
    Recommendation,
    RunManifest,
    RunStages,
    ToolState,
)
from energatron.services import pipeline_sync
from energatron.services.context import ServiceContext, ServiceResult
from energatron.services.files import write_json

_log = logging.getLogger(__name__)

RUNS_DIR: Final = "data/runs"
RUNS_POINTER: Final = "data/runs.dvc"
RUN_PATHS: Final = ("dvc.lock", ".energatron/code_fingerprint.json", RUNS_POINTER)
CONFIG_PATHS: Final = ("params.yaml", "dvc.yaml", "config", "private.dvc", "data")
RECOMMENDATION: Final = f"data/reports/recommendations/{RECOMMENDATION_FILENAME}"
OFFERS_SUMMARIES: Final = ("data/marts/offers", "data/marts/offers_gas")


class RunResult(ServiceResult):
    """`run_result` v1 (P6 spec §8.3)."""

    KIND: ClassVar[str] = "run_result"
    SCHEMA_VERSION: ClassVar[int] = 1
    kind: Literal["run_result"] = "run_result"
    schema_version: Literal[1] = 1

    run_id: str
    draft: bool
    recommendation_id: str | None
    action: str | None
    robust: bool | None
    ran: int
    restored: int
    cached: int
    commit: str


def run(ctx: ServiceContext, *, tool: ToolState, draft: bool = False) -> RunResult:
    """Repro, record and commit one run (P6 spec §8.1)."""
    ctx.progress.step("Checking that the pipeline files match this tool")
    pipeline_sync.check(ctx)
    if not draft:
        ctx.progress.step("Checking for uncommitted changes")
        dirty = sorted(path for path in ctx.vcs.dirty_paths(list(CONFIG_PATHS)) if _is_config(path))
        if dirty:
            raise DirtyError("Tracked config has uncommitted changes: commit them, or run with --draft.", paths=dirty)
        if tool.tool_dirty:
            raise DirtyError(
                "The energatron checkout has uncommitted changes: commit them, or run with --draft.",
                reason="tool_dirty",
            )
    head = ctx.vcs.head()
    if head is None:
        raise DirtyError("The workspace has no commit yet.", reason="no_commit")
    started = ctx.clock.now()
    identifier = make_run_id(started, head)
    report = ctx.runner.repro()
    recommendation = _recommendation(ctx)
    manifest = RunManifest(
        run_id=identifier,
        draft=draft,
        started_at=started,
        finished_at=ctx.clock.now(),
        workspace_head=head,
        tool=tool,
        stages=RunStages(ran=report.ran, restored=report.restored, cached=report.cached),
        datasets=sorted(record.dataset_id for record in ctx.catalog.records()),
        fetch_batches=_fetch_batches(ctx),
        recommendation_id=recommendation.recommendation_id if recommendation else None,
        action=recommendation.action if recommendation else None,
        robust=recommendation.robust if recommendation else None,
    )
    ctx.progress.step("Recording the run")
    write_json(ctx.paths.root / RUNS_DIR / f"{identifier}.json", manifest.model_dump(mode="json"))
    pointers = ctx.vcs.track([RUNS_DIR])
    ignores = [path for path in ctx.vcs.dirty_paths(["data"]) if path.endswith(".gitignore")]
    paths = sorted({*RUN_PATHS, *pointers, *ignores})
    message = f"run: {identifier}" + (f" {manifest.recommendation_id}" if manifest.recommendation_id else "")
    ctx.progress.step("Committing the run")
    commit = ctx.vcs.commit(paths, message)
    status = ctx.vcs.backup_status()
    warning = ResultWarning(
        code=WarningCode.W_BACKUP_PENDING,
        message="The run's commit isn't backed up yet: run `energatron backup` when convenient.",
        details={"commits_not_backed_up": status.commits_not_backed_up},
    )
    _log.info(
        "run %s: %d ran, %d restored, %d cached", identifier, len(report.ran), len(report.restored), len(report.cached)
    )
    return RunResult(
        run_id=identifier,
        draft=draft,
        recommendation_id=manifest.recommendation_id,
        action=manifest.action,
        robust=manifest.robust,
        ran=len(report.ran),
        restored=len(report.restored),
        cached=len(report.cached),
        commit=commit,
        warnings=(warning,),
    )


def _is_config(path: str) -> bool:
    """Tracked config a clean run must not start from dirty: everything but what a run itself writes."""
    if path in ("params.yaml", "dvc.yaml", "private.dvc") or path.startswith("config/"):
        return True
    return path.startswith("data/") and path.endswith(".dvc") and path != RUNS_POINTER


def _recommendation(ctx: ServiceContext) -> Recommendation | None:
    path = ctx.paths.root / RECOMMENDATION
    try:
        return Recommendation.from_document(json.loads(path.read_bytes()))
    except FileNotFoundError:
        return None
    except OSError, ValueError, InvalidInputError, SchemaTooNewError:
        raise InvalidInputError("The recommendation the run produced isn't valid.", file=RECOMMENDATION) from None


def _fetch_batches(ctx: ServiceContext) -> list[str]:
    found: set[str] = set()
    for directory in OFFERS_SUMMARIES:
        path = ctx.paths.root / directory / OFFERS_SUMMARY_FILENAME
        try:
            summary = OffersSummary.from_document(json.loads(path.read_bytes()))
        except FileNotFoundError:
            continue
        except OSError, ValueError, InvalidInputError, SchemaTooNewError:
            raise InvalidInputError(
                "An offers summary isn't valid.", file=f"{directory}/{OFFERS_SUMMARY_FILENAME}"
            ) from None
        found.update(summary.batches.values())
    return sorted(found)
