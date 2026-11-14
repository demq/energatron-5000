"""`energatron datasets [--source SOURCE] [--limit N]` (P9 spec §3.1): the landed datasets, newest first. Read-only."""

from typing import Annotated

import typer

from energatron.adapters.cli.commands.land import source_type
from energatron.adapters.cli.common import DebugFlag, JsonFlag, VerboseFlag, WorkspaceOption, begin, open_workspace
from energatron.adapters.cli.output import CommandOutput
from energatron.services import datasets as datasets_service
from energatron.services.datasets import DatasetsResult

COMMAND = "datasets"


def datasets(
    ctx: typer.Context,
    source: Annotated[
        str | None, typer.Option("--source", metavar="SOURCE", help="Only this source type.", show_default=False)
    ] = None,
    limit: Annotated[
        int,
        typer.Option("--limit", min=1, max=datasets_service.MAX_LIMIT, help="How many datasets to list, newest first."),
    ] = datasets_service.DEFAULT_LIMIT,
    json_output: JsonFlag = False,
    workspace: WorkspaceOption = None,
    verbose: VerboseFlag = False,
    debug: DebugFlag = False,
) -> CommandOutput:
    """List the landed datasets (IDs, source types, dates), newest first, with a count per source type."""
    invocation = begin(ctx, verbose=verbose, debug=debug)
    kind = source_type(source)
    context = open_workspace(invocation, workspace)
    result = datasets_service.datasets(context, source=kind, limit=limit)
    return CommandOutput(COMMAND, result, text(result))


def text(result: DatasetsResult) -> str:
    lines = [
        f"{entry.dataset_id}  {entry.landed_at.date().isoformat()}"
        + (f"  {entry.coverage.start.isoformat()}..{entry.coverage.end.isoformat()}" if entry.coverage else "")
        + (f"  extracted by {entry.extracted_by}" if entry.extracted_by else "")
        for entry in result.datasets
    ]
    counts = ", ".join(f"{source} {count}" for source, count in result.counts.items() if count)
    lines.append(f"Showing {result.shown} of {result.total}. Catalogs: {counts or 'empty'}")
    return "\n".join(lines) + "\n"
