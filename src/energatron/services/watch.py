"""`energatron watch` (P7 spec §6): the weekly job. Fetch offers, run when something new landed or the run is behind,
compare the recommendation with the one before, and say what to notify.

The caller holds the workspace lock and passes the live tool state and stale stage names (D1 §11.4). A failed fetch or
run is recorded by its code, never retried, never turned into a draft run. Services never notify (no subprocesses,
D1 §3): the result lists the notifications and the CLI sends them with `--notify`.
"""

import logging
from typing import Annotated, ClassVar, Literal

from pydantic import Field

from energatron.core.errors import EnergatronError, PartialError, ResultWarning
from energatron.core.models._common import FrozenModel
from energatron.core.models.recommend import ToolState
from energatron.core.operations import cheaper_plan, notifications, run_behind
from energatron.services import fetch_offers as fetch_service
from energatron.services import run as run_service
from energatron.services import status as status_service
from energatron.services.context import ServiceContext, ServiceResult

_log = logging.getLogger(__name__)
_Count = Annotated[int, Field(ge=0)]


class FetchSummary(FrozenModel):
    fetch_batch_id: str
    landed: _Count
    failed_retailers: _Count


class NotificationText(FrozenModel):
    title: str
    message: str


class WatchResult(ServiceResult):
    """`watch_result` v1 (P7 spec §6)."""

    KIND: ClassVar[str] = "watch_result"
    SCHEMA_VERSION: ClassVar[int] = 1
    kind: Literal["watch_result"] = "watch_result"
    schema_version: Literal[1] = 1

    dry_run: bool
    fetch: FetchSummary | None
    fetch_error: str | None
    ran: bool
    run_id: str | None
    run_error: str | None
    before: str | None
    after: str | None
    cheaper_plan: bool
    reminders: list[str]
    notifications: list[NotificationText]


def watch(ctx: ServiceContext, *, tool: ToolState, code_stale: list[str], dry_run: bool = False) -> WatchResult:
    """P7 spec §6's steps 1-6."""
    before = status_service.read_recommendation(ctx)
    fetch: FetchSummary | None = None
    fetch_error: str | None = None
    passed_on: tuple[ResultWarning, ...] = ()
    try:
        fetched = fetch_service.fetch_offers(ctx, dry_run=dry_run)
    except EnergatronError as exc:
        fetch_error = exc.code
    else:
        failure = fetch_service.outcome(fetched)
        if failure is not None and not isinstance(failure, PartialError):
            fetch_error = failure.code
        else:
            passed_on = fetched.warnings
            fetch = FetchSummary(
                fetch_batch_id=fetched.fetch_batch_id,
                landed=len(fetched.landed),
                failed_retailers=sum(1 for found in fetched.retailers if found.error is not None),
            )
    behind = run_behind(status_service.snapshot(ctx, code_stale=code_stale, tool_dirty=tool.tool_dirty).facts)
    should_run = not dry_run and fetch_error is None and ((fetch is not None and fetch.landed > 0) or behind)
    ran = False
    run_id: str | None = None
    run_error: str | None = None
    if should_run:
        try:
            done = run_service.run(ctx, tool=tool, draft=False)
        except EnergatronError as exc:
            run_error = exc.code
        else:
            ran, run_id = True, done.run_id
    after = status_service.read_recommendation(ctx)
    final = status_service.snapshot(ctx, code_stale=[] if ran else code_stale, tool_dirty=tool.tool_dirty).result
    cheaper = cheaper_plan(before, after)
    failed = [(step, code) for step, code in (("fetch", fetch_error), ("run", run_error)) if code is not None]
    found = notifications(after=after, cheaper=cheaper, failed=failed, reminder_codes=final.reminders)
    _log.info("watch: fetch %s, ran %s, cheaper plan %s", fetch_error or "ok", ran, cheaper)
    return WatchResult(
        dry_run=dry_run,
        fetch=fetch,
        fetch_error=fetch_error,
        ran=ran,
        run_id=run_id,
        run_error=run_error,
        before=before.recommendation_id if before else None,
        after=after.recommendation_id if after else None,
        cheaper_plan=cheaper,
        reminders=final.reminders,
        notifications=[NotificationText(title=item.title, message=item.message) for item in found],
        warnings=(*(item for item in passed_on if item.code.value not in final.reminders), *final.warnings),
    )


def outcome(result: WatchResult) -> EnergatronError | None:
    """`E_PARTIAL` when the fetch or the run failed, so a scheduler's log shows it (P7 spec §6)."""
    if result.fetch_error is None and result.run_error is None:
        return None
    steps = [step for step, code in (("fetch", result.fetch_error), ("run", result.run_error)) if code is not None]
    return PartialError(f"The weekly check's {' and '.join(steps)} failed; see the result.", steps=steps)
