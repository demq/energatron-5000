"""`energatron explain [REC_ID] [--options N]` (P9 spec §3.4): the current recommendation explained. Read-only.

The command computes the stale stages from the live code fingerprint (services can't, D1 §11.4), as `status` does.
"""

from typing import Annotated

import typer

from energatron.adapters import wiring
from energatron.adapters.cli.common import DebugFlag, JsonFlag, VerboseFlag, WorkspaceOption, begin, open_workspace
from energatron.adapters.cli.output import CommandOutput
from energatron.services import explain as explain_service
from energatron.services.explain import ExplainResult

COMMAND = "explain"


def explain(
    ctx: typer.Context,
    rec_id: Annotated[
        str | None, typer.Argument(metavar="[REC_ID]", help="The current recommendation's ID (optional).")
    ] = None,
    options: Annotated[
        int, typer.Option("--options", min=1, max=explain_service.MAX_OPTIONS, help="How many options to show.")
    ] = explain_service.DEFAULT_OPTIONS,
    json_output: JsonFlag = False,
    workspace: WorkspaceOption = None,
    verbose: VerboseFlag = False,
    debug: DebugFlag = False,
) -> CommandOutput:
    """Explain the current recommendation: what to do, how sure it is, what to check, and where it came from."""
    invocation = begin(ctx, verbose=verbose, debug=debug)
    context = open_workspace(invocation, workspace)
    stale, _ = wiring.code_state(context.paths)
    result = explain_service.explain(context, rec_id, options=options, code_stale=stale)
    return CommandOutput(COMMAND, result, text(result))


def text(result: ExplainResult) -> str:
    if result.recommendation_id is None:
        return "No recommendation yet: run the pipeline.\n"
    lines = [
        f"{result.recommendation_id}: {result.action} ({'robust' if result.robust else 'not robust'}), "
        f"prices as of {result.as_of}" + (f", recheck by {result.recheck_by}" if result.recheck_by else "")
    ]
    for option in result.options:
        names = " + ".join(plan.display_name for plan in (option.electricity, option.gas) if plan)
        saving = f", saves {option.saving_ongoing}/year" if option.saving_ongoing is not None else ""
        lines.append(f"  option {option.rank}: {names}: {option.annual_ongoing}/year{saving}")
    lines.extend(
        f"  sensitivity {verdict.variation}: {'holds' if verdict.holds else 'changes'}"
        for verdict in result.sensitivity
    )
    for fuel, plan in result.chosen.items():
        lines.extend(f"  check ({fuel}): {note}" for note in (*plan.notes, *plan.approximations))
    if result.run:
        lines.append(f"Run: {result.run.run_id}{' (draft)' if result.run.draft else ''}")
    behind = result.behind
    if behind.datasets_not_in_run or behind.code_stale:
        lines.append(
            f"Behind: {behind.datasets_not_in_run} dataset(s) since the run; {len(behind.code_stale)} stage(s) changed"
        )
    return "\n".join(lines) + "\n"
