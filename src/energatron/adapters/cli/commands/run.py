"""`energatron run [--draft]`: run the pipeline, write its manifest and commit (P6 spec §8; plan §9.4).

The command takes the workspace lock, computes the live code fingerprint (services can't, D1 §11.4) and passes the
tool's state to the service, which refuses a dirty tool or dirty tracked config unless `--draft`.
"""

from typing import Annotated

import typer

from energatron.adapters import wiring
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
from energatron.core.models.recommend import ToolState
from energatron.services import run as run_service


def run(
    ctx: typer.Context,
    draft: Annotated[
        bool, typer.Option("--draft", help="Run even with uncommitted config or tool changes; the manifest says so.")
    ] = False,
    json_output: JsonFlag = False,
    workspace: WorkspaceOption = None,
    verbose: VerboseFlag = False,
    debug: DebugFlag = False,
) -> CommandOutput:
    """Run the pipeline, record the run in data/runs/ and commit it."""
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
        result = run_service.run(context, tool=tool, draft=draft)
    lines = [
        f"Run {result.run_id}{' (draft)' if result.draft else ''}: {result.ran} ran, {result.restored} restored, "
        f"{result.cached} up to date.",
        f"Recommendation: {result.recommendation_id or 'none'}"
        + (f" ({result.action}, {'robust' if result.robust else 'not robust'})" if result.action else ""),
        f"Committed {result.commit[:12]}.",
    ]
    return CommandOutput("run", result, "\n".join(lines) + "\n")
