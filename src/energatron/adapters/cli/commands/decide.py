"""`energatron decide <REC-id> --switch-date … [--elec …] [--gas …]`: record a decision (P6 spec §9).

The command takes the workspace lock and calls the service, which refuses a recommendation that isn't the current one
or that no clean run made, and plans the recommendation didn't price. It never edits the private config.
"""

from datetime import date
from typing import Annotated

import typer

from energatron.adapters.cli.common import (
    DebugFlag,
    JsonFlag,
    VerboseFlag,
    WorkspaceOption,
    begin,
    open_workspace,
)
from energatron.adapters.cli.output import CommandOutput
from energatron.adapters.workspace import workspace_lock
from energatron.core.errors import UsageError
from energatron.services import decide as decide_service


def decide(
    ctx: typer.Context,
    rec_id: Annotated[str, typer.Argument(help="The recommendation acted on, REC-YYYYMMDD-xxxxxxxx.")],
    switch_date: Annotated[str, typer.Option("--switch-date", help="The day the new plans start, YYYY-MM-DD.")],
    elec: Annotated[str | None, typer.Option("--elec", help="The chosen electricity offer snapshot ID.")] = None,
    gas: Annotated[str | None, typer.Option("--gas", help="The chosen gas offer snapshot ID.")] = None,
    evidence: Annotated[
        list[str] | None, typer.Option("--evidence", help="A landed dataset ID backing the decision; repeatable.")
    ] = None,
    note: Annotated[str | None, typer.Option("--note", help="A short note kept with the decision.")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Validate only; write and commit nothing.")] = False,
    json_output: JsonFlag = False,
    workspace: WorkspaceOption = None,
    verbose: VerboseFlag = False,
    debug: DebugFlag = False,
) -> CommandOutput:
    """Record a decision to switch plans, linked to the recommendation and run it came from."""
    invocation = begin(ctx, verbose=verbose, debug=debug)
    try:
        day = date.fromisoformat(switch_date)
    except ValueError:
        raise UsageError("--switch-date must be YYYY-MM-DD.", field="switch_date") from None
    context = open_workspace(invocation, workspace)
    with workspace_lock(context.paths):
        result = decide_service.decide(
            context,
            rec_id,
            switch_date=day,
            electricity=elec,
            gas=gas,
            evidence=tuple(evidence or ()),
            note=note,
            dry_run=dry_run,
        )
    verb = "Would record" if result.dry_run else "Recorded"
    text = (
        f"{verb} {result.decision_id} for {result.recommendation_id} (run {result.run_id}) in {result.path}.\n"
        f"{decide_service.REMINDER}\n"
    )
    return CommandOutput("decide", result, text)
