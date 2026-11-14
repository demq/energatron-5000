"""`energatron offers diff [--plan PLAN_ID]` (P4 spec §11): read-only, takes no lock."""

from typing import Annotated, cast

import typer

from energatron.adapters import reads
from energatron.adapters.cli.common import DebugFlag, JsonFlag, VerboseFlag, WorkspaceOption, begin, open_workspace
from energatron.adapters.cli.output import CommandOutput
from energatron.services.offer_diff import OfferDiffResult

COMMAND = "offers"

PlanOption = Annotated[
    str | None, typer.Option("--plan", metavar="PLAN_ID", help="Compare only this plan.", show_default=False)
]

app = typer.Typer(name="offers", help="Landed retail offers.", add_completion=False, pretty_exceptions_enable=False)


@app.command("diff")
def diff(
    ctx: typer.Context,
    plan: PlanOption = None,
    json_output: JsonFlag = False,
    workspace: WorkspaceOption = None,
    verbose: VerboseFlag = False,
    debug: DebugFlag = False,
) -> CommandOutput:
    """Show what changed between each plan's two newest landed snapshots."""
    invocation = begin(ctx, verbose=verbose, debug=debug)
    context = open_workspace(invocation, workspace)
    result = cast(OfferDiffResult, reads.OFFERS_DIFF.run(context, reads.OffersDiffArgs(plan=plan)))
    return CommandOutput(COMMAND, result, _text(result))


def _text(result: OfferDiffResult) -> str:
    lines = [
        f"{change.plan_id} {change.path} {change.delta}"
        if change.delta is not None
        else f"{change.plan_id} {change.path}: {change.old} -> {change.new}"
        for change in result.changes
    ]
    lines.append(f"{result.plans_compared} plans compared, {len(result.changes)} changes")
    return "\n".join(lines) + "\n"
