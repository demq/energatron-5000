"""`energatron landing list` and `energatron landing clear [--dry-run] [--yes]` (P7 spec §5).

`list` is read-only and takes no lock. `clear` deletes inbox files whose bytes are already landed, under the workspace
lock; without `--dry-run` it needs `--yes` or a "yes" at a prompt, because it deletes the owner's files.
"""

import typer

from energatron.adapters.cli.common import (
    DebugFlag,
    DryRunFlag,
    JsonFlag,
    VerboseFlag,
    WorkspaceOption,
    YesFlag,
    begin,
    open_workspace,
)
from energatron.adapters.cli.output import CommandOutput, confirm
from energatron.adapters.workspace import workspace_lock
from energatron.services import landing as landing_service
from energatron.services.landing import LandingResult

COMMAND = "landing"

app = typer.Typer(
    name="landing", help="The inbox, data/landing/.", add_completion=False, pretty_exceptions_enable=False
)


@app.command("list")
def list_inbox(
    ctx: typer.Context,
    json_output: JsonFlag = False,
    workspace: WorkspaceOption = None,
    verbose: VerboseFlag = False,
    debug: DebugFlag = False,
) -> CommandOutput:
    """List the inbox files and whether each is already landed."""
    invocation = begin(ctx, verbose=verbose, debug=debug)
    context = open_workspace(invocation, workspace)
    result = landing_service.landing_list(context)
    return CommandOutput(COMMAND, result, _text(result))


@app.command("clear")
def clear(
    ctx: typer.Context,
    dry_run: DryRunFlag = False,
    yes: YesFlag = False,
    json_output: JsonFlag = False,
    workspace: WorkspaceOption = None,
    verbose: VerboseFlag = False,
    debug: DebugFlag = False,
) -> CommandOutput:
    """Delete the inbox files whose bytes are already landed; new files stay."""
    invocation = begin(ctx, verbose=verbose, debug=debug)
    context = open_workspace(invocation, workspace)
    with workspace_lock(context.paths):
        if not dry_run:
            planned = landing_service.landing_list(context)
            if planned.landed == 0:
                return CommandOutput(COMMAND, _cleared_nothing(planned), "Nothing to clear: no inbox file is landed.\n")
            confirm(invocation, f"Delete {planned.landed} landed inbox file(s)?", yes=yes)
        result = landing_service.landing_clear(context, dry_run=dry_run)
    return CommandOutput(COMMAND, result, _text(result))


def _cleared_nothing(listed: LandingResult) -> LandingResult:
    return listed.model_copy(update={"action": "clear"})


def _text(result: LandingResult) -> str:
    lines = [f"{item.status:<6}  {item.name}" + ("  (removed)" if item.removed else "") for item in result.files]
    if result.action == "list":
        lines.append(f"{len(result.files)} file(s): {result.landed} landed, {result.new} new.")
    elif result.dry_run:
        lines.append(f"Dry run: {result.removed} landed file(s) would be deleted; {result.new} new file(s) stay.")
    else:
        lines.append(f"Deleted {result.removed} landed file(s); {result.new} new file(s) stay.")
    return "\n".join(lines) + "\n"
