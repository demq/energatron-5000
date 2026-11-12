"""`energatron status` (P7 spec §4): one read-only summary of the workspace, with reminders as warnings.

`status(ctx, code_stale=…, tool_dirty=…)` takes no lock and writes nothing. The CLI passes the stale stage names and
whether the tool is dirty, from the live code fingerprint (services can't compute it, D1 §11.4). A missing input gives
`null` or zero; an invalid run manifest or recommendation → `E_INVALID_INPUT`, as `run` does; an invalid drill record is
treated as missing. No figure appears in the result: plan and brand names are public.
"""

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import ClassVar, Final, Literal
from zoneinfo import ZoneInfo

from pydantic import AwareDatetime

from energatron.core.errors import InvalidInputError, PipelineNotSyncedError, SchemaTooNewError
from energatron.core.models._common import FrozenModel
from energatron.core.models.catalog import SourceType
from energatron.core.models.recommend import RECOMMENDATION_FILENAME, Recommendation, RunManifest
from energatron.core.models.vcs import DrillState
from energatron.core.operations import Facts, days_since, drill_due, option_names, reminders
from energatron.services import landing, pipeline_sync
from energatron.services.context import ServiceContext, ServiceResult

_log = logging.getLogger(__name__)

RUNS_DIR: Final = "data/runs"
DECISIONS_DIR: Final = "data/decisions"
RECOMMENDATION: Final = f"data/reports/recommendations/{RECOMMENDATION_FILENAME}"


class PipelineState(FrozenModel):
    synced: bool
    drift: list[str]
    code_stale: list[str]
    tool_dirty: bool


class LastRun(FrozenModel):
    run_id: str
    draft: bool
    finished_at: AwareDatetime
    recommendation_id: str | None
    action: str | None
    robust: bool | None


class TopOption(FrozenModel):
    electricity: str | None
    gas: str | None


class RecommendationState(FrozenModel):
    recommendation_id: str | None
    as_of: date | None
    recheck_by: date | None
    action: str
    robust: bool
    top: TopOption | None


class OffersState(FrozenModel):
    last_fetch_at: AwareDatetime | None
    days_since: int | None


class LandingState(FrozenModel):
    files: int
    landed: int
    new: int


class DecisionsState(FrozenModel):
    count: int
    latest: str | None


class BackupState(FrozenModel):
    refs_backed_up: bool
    commits_not_backed_up: int | None
    last_backup_at: AwareDatetime | None


class DrillInfo(FrozenModel):
    last_drill_at: AwareDatetime | None
    days_since: int | None
    due: bool


class StatusResult(ServiceResult):
    """`status_result` v1 (P7 spec §4)."""

    KIND: ClassVar[str] = "status_result"
    SCHEMA_VERSION: ClassVar[int] = 1
    kind: Literal["status_result"] = "status_result"
    schema_version: Literal[1] = 1

    today: date
    pipeline: PipelineState
    last_run: LastRun | None
    last_clean_run_id: str | None
    datasets_not_in_last_clean_run: int
    recommendation: RecommendationState | None
    offers: OffersState
    landing: LandingState
    decisions: DecisionsState
    backup: BackupState
    drill: DrillInfo
    reminders: list[str]


@dataclass(frozen=True)
class Snapshot:
    """What `watch` needs besides the result: the facts behind the reminders."""

    result: StatusResult
    facts: Facts


def status(
    ctx: ServiceContext, *, code_stale: list[str], tool_dirty: bool, tool_unpublished: bool | None = None
) -> StatusResult:
    """The summary, its reminders in `reminders` and as warnings (P7 spec §3.3, §4)."""
    return snapshot(ctx, code_stale=code_stale, tool_dirty=tool_dirty, tool_unpublished=tool_unpublished).result


def snapshot(
    ctx: ServiceContext, *, code_stale: list[str], tool_dirty: bool, tool_unpublished: bool | None = None
) -> Snapshot:
    today = local_date(ctx, ctx.clock.now())
    drift = _drift(ctx)
    runs = read_manifests(ctx)
    last = runs[-1] if runs else None
    clean = next((run for run in reversed(runs) if not run.draft), None)
    datasets = {record.dataset_id for record in ctx.catalog.records()}
    behind = len(datasets - set(clean.datasets)) if clean else len(datasets)
    recommendation = read_recommendation(ctx)
    last_fetch_at = max((record.landed_at for record in ctx.catalog.records(SourceType.OFFER_CDR_INDEX)), default=None)
    last_fetch = local_date(ctx, last_fetch_at) if last_fetch_at else None
    found = landing.inbox(ctx)
    backup = ctx.vcs.backup_status()
    drill_at = _drill_at(ctx.paths.drill_state)
    last_drill = local_date(ctx, drill_at) if drill_at else None
    facts = Facts(
        today=today,
        landing_new=found.new,
        landing_landed=found.landed,
        synced=drift is None,
        code_stale=tuple(code_stale),
        has_clean_run=clean is not None,
        datasets_not_in_last_clean_run=behind,
        last_fetch=last_fetch,
        recheck_by=recommendation.recheck_by if recommendation else None,
        refs_backed_up=backup.refs_backed_up,
        commits_not_backed_up=backup.commits_not_backed_up,
        last_drill=last_drill,
        tool_unpublished=tool_unpublished,
    )
    warnings = reminders(facts)
    result = StatusResult(
        today=today,
        pipeline=PipelineState(
            synced=drift is None, drift=drift or [], code_stale=sorted(code_stale), tool_dirty=tool_dirty
        ),
        last_run=_last_run(last) if last else None,
        last_clean_run_id=clean.run_id if clean else None,
        datasets_not_in_last_clean_run=behind,
        recommendation=_recommendation_state(recommendation) if recommendation else None,
        offers=OffersState(last_fetch_at=last_fetch_at, days_since=days_since(today, last_fetch)),
        landing=LandingState(files=found.files, landed=found.landed, new=found.new),
        decisions=_decisions(ctx.paths.root / DECISIONS_DIR),
        backup=BackupState(
            refs_backed_up=backup.refs_backed_up,
            commits_not_backed_up=backup.commits_not_backed_up,
            last_backup_at=backup.last_backup_at,
        ),
        drill=DrillInfo(
            last_drill_at=drill_at, days_since=days_since(today, last_drill), due=drill_due(today, last_drill)
        ),
        reminders=[warning.code.value for warning in warnings],
        warnings=tuple(warnings),
    )
    return Snapshot(result, facts)


def local_date(ctx: ServiceContext, instant: datetime) -> date:
    """`instant` as a date in the region's zone; UTC when the zone is null (P7 spec §3.2)."""
    zone = ctx.region.local_tz
    return instant.astimezone(ZoneInfo(zone)).date() if zone else instant.date()


def read_manifests(ctx: ServiceContext) -> list[RunManifest]:
    """Every run manifest, oldest first (run IDs start with their UTC start time)."""
    directory = ctx.paths.root / RUNS_DIR
    if not directory.is_dir():
        return []
    found: list[RunManifest] = []
    for path in sorted(directory.glob("run-*.json")):
        found.append(_read(path, RunManifest, f"{RUNS_DIR}/{path.name}"))
    return sorted(found, key=lambda manifest: manifest.run_id)


def read_recommendation(ctx: ServiceContext) -> Recommendation | None:
    path = ctx.paths.root / RECOMMENDATION
    if not path.exists():
        return None
    return _read(path, Recommendation, RECOMMENDATION)


def _read[T: (RunManifest, Recommendation)](path: Path, model: type[T], shown: str) -> T:
    try:
        return model.from_document(json.loads(path.read_bytes()))
    except OSError, ValueError, InvalidInputError, SchemaTooNewError:
        raise InvalidInputError("A workspace document isn't valid.", file=shown) from None


def _drift(ctx: ServiceContext) -> list[str] | None:
    try:
        pipeline_sync.check(ctx)
    except PipelineNotSyncedError as exc:
        drift = exc.details.get("drift")
        return [str(item) for item in drift] if isinstance(drift, list) else ["unknown"]
    return None


def _drill_at(path: Path) -> datetime | None:
    try:
        return DrillState.from_document(json.loads(path.read_bytes())).at
    except FileNotFoundError:
        return None
    except OSError, ValueError, InvalidInputError, SchemaTooNewError:
        _log.warning("The drill record isn't valid; it is treated as missing.")
        return None


def _last_run(manifest: RunManifest) -> LastRun:
    return LastRun(
        run_id=manifest.run_id,
        draft=manifest.draft,
        finished_at=manifest.finished_at,
        recommendation_id=manifest.recommendation_id,
        action=manifest.action,
        robust=manifest.robust,
    )


def _recommendation_state(recommendation: Recommendation) -> RecommendationState:
    top = TopOption(**option_names(recommendation.options[0])) if recommendation.options else None
    return RecommendationState(
        recommendation_id=recommendation.recommendation_id,
        as_of=recommendation.as_of,
        recheck_by=recommendation.recheck_by,
        action=recommendation.action,
        robust=recommendation.robust,
        top=top,
    )


def _decisions(directory: Path) -> DecisionsState:
    names = sorted(path.stem for path in directory.glob("DEC-*.json")) if directory.is_dir() else []
    return DecisionsState(count=len(names), latest=names[-1] if names else None)
