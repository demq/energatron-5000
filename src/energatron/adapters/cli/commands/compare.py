"""`energatron compare [--fuel F] [--limit N] [--brand TEXT]` (P9 spec §3.3): the current recommendation's rankings."""

from typing import Annotated, cast

import typer

from energatron.adapters.cli.common import DebugFlag, JsonFlag, VerboseFlag, WorkspaceOption, begin, open_workspace
from energatron.adapters.cli.output import CommandOutput
from energatron.core.errors import UsageError
from energatron.core.models.recommend import Fuel
from energatron.services import compare as compare_service
from energatron.services.compare import CompareResult

COMMAND = "compare"


def compare(
    ctx: typer.Context,
    fuel: Annotated[
        str | None,
        typer.Option("--fuel", metavar="FUEL", help="electricity or gas (default: both).", show_default=False),
    ] = None,
    limit: Annotated[
        int, typer.Option("--limit", min=1, max=compare_service.MAX_LIMIT, help="How many options and plans per list.")
    ] = compare_service.DEFAULT_LIMIT,
    brand: Annotated[
        str | None,
        typer.Option("--brand", metavar="TEXT", help="Only brands containing this text.", show_default=False),
    ] = None,
    json_output: JsonFlag = False,
    workspace: WorkspaceOption = None,
    verbose: VerboseFlag = False,
    debug: DebugFlag = False,
) -> CommandOutput:
    """Rank the options and plans of the current recommendation, with the current plan's rank."""
    invocation = begin(ctx, verbose=verbose, debug=debug)
    if fuel is not None and fuel not in compare_service.FUELS:
        raise UsageError("--fuel is electricity or gas.", parameter="--fuel")
    context = open_workspace(invocation, workspace)
    result = compare_service.compare(context, fuel=cast(Fuel | None, fuel), limit=limit, brand=brand)
    return CommandOutput(COMMAND, result, text(result))


def text(result: CompareResult) -> str:
    if result.recommendation_id is None:
        return "No recommendation yet: run the pipeline.\n"
    lines = [f"{result.recommendation_id} ({result.action}), prices as of {result.as_of}"]
    for option in result.options:
        names = " + ".join(plan.display_name for plan in (option.electricity, option.gas) if plan)
        lines.append(f"  option {option.rank}: {names}: {option.annual_ongoing}/year")
    for fuel, plans in result.plans.items():
        current = f"; current plan ranks {plans.current_rank}" if plans.current_rank else ""
        lines.append(f"{fuel}: {plans.shown} of {plans.total}{current}")
        lines.extend(
            f"  {plan.rank}. {plan.brand} {plan.display_name}: {plan.annual_ongoing}/year" for plan in plans.items
        )
    return "\n".join(lines) + "\n"
