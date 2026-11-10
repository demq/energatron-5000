"""`energatron watch [--notify] [--dry-run]` (P7 spec §6, §7): the weekly offers check.

Takes the workspace lock for the whole call, computes the tool state and the stale stages as `run` and `status` do,
and with `--notify` sends the result's notifications after the service returns. Exit 0; `E_PARTIAL` (exit 8, with the
result as `data`) when the fetch or the run failed. `schedule install` runs it weekly.
"""

from typing import Annotated

import typer

from energatron.adapters import wiring
from energatron.adapters.cli.common import (
    DebugFlag,
    DryRunFlag,
    JsonFlag,
    VerboseFlag,
    WorkspaceOption,
    begin,
    open_workspace,
)
from energatron.adapters.cli.output import CommandOutput
from energatron.adapters.workspace import workspace_lock
from energatron.core.models.recommend import ToolState
from energatron.services import watch as watch_service
from energatron.services.watch import WatchResult

COMMAND = "watch"

NotifyFlag = Annotated[bool, typer.Option("--notify", help="Show the result's notifications (macOS).")]


def watch(
    ctx: typer.Context,
    notify: NotifyFlag = False,
    dry_run: DryRunFlag = False,
    json_output: JsonFlag = False,
    workspace: WorkspaceOption = None,
    verbose: VerboseFlag = False,
    debug: DebugFlag = False,
) -> CommandOutput:
    """Fetch offers, run when something new landed, and say whether a cheaper plan appeared."""
    invocation = begin(ctx, verbose=verbose, debug=debug)
    context = open_workspace(invocation, workspace)
    with workspace_lock(context.paths):
        live = wiring.code_fingerprint()
        tool = ToolState(
            energatron_version=live.energatron_version,
            install=live.install,
            tool_commit=live.tool_commit,
            tool_dirty=live.tool_dirty,
        )
        stale, _ = wiring.code_state(context.paths)
        result = watch_service.watch(context, tool=tool, code_stale=stale, dry_run=dry_run)
    sent = 0
    if notify:
        send = wiring.notifier()
        sent = sum(1 for item in result.notifications if send(item.title, item.message))
    return CommandOutput(COMMAND, result, _text(result, sent=sent, notify=notify), error=watch_service.outcome(result))


def _text(result: WatchResult, *, sent: int, notify: bool) -> str:
    lines: list[str] = []
    if result.fetch is not None:
        lines.append(
            f"Fetch {result.fetch.fetch_batch_id}: {result.fetch.landed} landed, "
            f"{result.fetch.failed_retailers} retailer(s) failed{' (dry run)' if result.dry_run else ''}"
        )
    else:
        lines.append(f"Fetch failed: {result.fetch_error}")
    if result.ran:
        lines.append(f"Ran {result.run_id}")
    elif result.run_error:
        lines.append(f"Run failed: {result.run_error}")
    else:
        lines.append("No run needed" if not result.dry_run else "No run (dry run)")
    lines.append(f"Recommendation: {result.before or 'none'} -> {result.after or 'none'}")
    lines.append("A cheaper plan appeared." if result.cheaper_plan else "No new cheaper plan.")
    for item in result.notifications:
        lines.append(f"Notification: {item.title}: {item.message}")
    if notify:
        lines.append(f"Sent {sent} of {len(result.notifications)} notification(s).")
    return "\n".join(lines) + "\n"
