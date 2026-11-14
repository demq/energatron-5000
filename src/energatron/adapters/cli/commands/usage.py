"""`energatron usage [--months N]` (P9 spec §3.2): the usage aggregates behind the costs. Read-only."""

from typing import Annotated, cast

import typer

from energatron.adapters import reads
from energatron.adapters.cli.common import DebugFlag, JsonFlag, VerboseFlag, WorkspaceOption, begin, open_workspace
from energatron.adapters.cli.output import CommandOutput
from energatron.services import usage_summary
from energatron.services.usage_summary import UsageResult

COMMAND = "usage"


def usage(
    ctx: typer.Context,
    months: Annotated[
        int,
        typer.Option("--months", min=1, max=usage_summary.MAX_MONTHS, help="How many of the newest months to list."),
    ] = usage_summary.DEFAULT_MONTHS,
    json_output: JsonFlag = False,
    workspace: WorkspaceOption = None,
    verbose: VerboseFlag = False,
    debug: DebugFlag = False,
) -> CommandOutput:
    """Summarise electricity and gas usage: the profile's figures and monthly totals (no interval rows)."""
    invocation = begin(ctx, verbose=verbose, debug=debug)
    context = open_workspace(invocation, workspace)
    result = cast(UsageResult, reads.USAGE.run(context, reads.UsageArgs(months=months)))
    return CommandOutput(COMMAND, result, text(result))


def _figure(value: float | None, unit: str) -> str:
    return "n/a" if value is None else f"{value:.2f} {unit}"


def text(result: UsageResult) -> str:
    lines: list[str] = []
    if result.electricity is None:
        lines.append("Electricity: no usage profile yet (run the pipeline)")
    for suffix, item in (result.electricity.suffixes if result.electricity else {}).items():
        lines.append(
            f"Electricity {suffix}: {item.first_date.isoformat()}..{item.last_date.isoformat()}, "
            f"{_figure(item.avg_daily_kwh, 'kWh/day')} in {item.window_start}..{item.window_end}"
        )
        lines.extend(f"  {month.month}: {_figure(month.kwh, 'kWh')}" for month in item.months)
    gas = result.gas
    if gas is None:
        lines.append("Gas: no gas profile yet")
    else:
        partial = " (partial year)" if gas.partial_year else ""
        lines.append(f"Gas: reference {gas.reference_start}..{gas.reference_end}, {gas.reference_mj} MJ{partial}")
        lines.extend(f"  {month.month}: {month.mj} MJ" for month in gas.months)
    return "\n".join(lines) + "\n"
