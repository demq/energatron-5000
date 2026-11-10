"""`energatron status` (P7 spec §4): where the workspace stands, with reminders. Read-only: no lock; agents may run it.

The command computes the live code fingerprint against the recorded one (services can't, D1 §11.4) and passes the
stale stage names and `tool_dirty` to the service.
"""

import typer

from energatron.adapters import wiring
from energatron.adapters.cli.common import DebugFlag, JsonFlag, VerboseFlag, WorkspaceOption, begin, open_workspace
from energatron.adapters.cli.output import CommandOutput
from energatron.services import status as status_service
from energatron.services.status import StatusResult

COMMAND = "status"


def status(
    ctx: typer.Context,
    json_output: JsonFlag = False,
    workspace: WorkspaceOption = None,
    verbose: VerboseFlag = False,
    debug: DebugFlag = False,
) -> CommandOutput:
    """Summarise the workspace: pipeline, last run, recommendation, offers, inbox, backup and drill, with reminders."""
    invocation = begin(ctx, verbose=verbose, debug=debug)
    context = open_workspace(invocation, workspace)
    stale, tool_dirty = wiring.code_state(context.paths)
    result = status_service.status(context, code_stale=stale, tool_dirty=tool_dirty)
    return CommandOutput(COMMAND, result, text(result))


def text(result: StatusResult) -> str:
    pipeline = result.pipeline
    sync = "in sync" if pipeline.synced else f"not in sync ({', '.join(pipeline.drift)})"
    code = f"{len(pipeline.code_stale)} stage(s) with changed code" if pipeline.code_stale else "code unchanged"
    lines = [
        f"Today: {result.today.isoformat()}",
        f"Pipeline: {sync}; {code}{'; tool dirty' if pipeline.tool_dirty else ''}",
    ]
    run = result.last_run
    if run is None:
        lines.append("Last run: none")
    else:
        lines.append(
            f"Last run: {run.run_id}{' (draft)' if run.draft else ''}; "
            f"{result.datasets_not_in_last_clean_run} dataset(s) not in the last clean run"
        )
    rec = result.recommendation
    if rec is None:
        lines.append("Recommendation: none")
    else:
        top = rec.top
        names = " + ".join(name for name in (top.electricity, top.gas) if name) if top else "no option"
        recheck = f"; recheck by {rec.recheck_by.isoformat()}" if rec.recheck_by else ""
        lines.append(
            f"Recommendation: {rec.recommendation_id or 'no ID'} {rec.action} "
            f"({'robust' if rec.robust else 'not robust'}): {names}{recheck}"
        )
    offers = result.offers
    lines.append(
        "Offers: never fetched" if offers.days_since is None else f"Offers: fetched {offers.days_since} day(s) ago"
    )
    inbox = result.landing
    lines.append(f"Inbox: {inbox.files} file(s), {inbox.landed} landed, {inbox.new} new")
    decisions = result.decisions
    lines.append(f"Decisions: {decisions.count}" + (f", latest {decisions.latest}" if decisions.latest else ""))
    backup = result.backup
    lines.append("Backup: nothing pending" if backup.refs_backed_up else "Backup: pending")
    drill = result.drill
    lines.append(
        "Restore drill: never recorded"
        if drill.days_since is None
        else f"Restore drill: {drill.days_since} day(s) ago{' (due)' if drill.due else ''}"
    )
    lines.append(f"Reminders: {len(result.reminders)} (listed as warnings)" if result.reminders else "Reminders: none")
    return "\n".join(lines) + "\n"
