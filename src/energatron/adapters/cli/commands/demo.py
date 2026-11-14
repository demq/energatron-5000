"""`energatron demo`: fill a demo workspace with synthetic data, land it, fetch the frozen offers and run (P8 spec §5).

Takes the workspace lock. Like `run`, it computes the live code fingerprint for the run's tool state; the frozen offer
provider comes from `wiring`. `init --demo` runs it in the new workspace's venv, or in-process with `--no-sync`.
"""

from typing import cast

import typer

from energatron.adapters import wiring
from energatron.adapters.cli.common import DebugFlag, JsonFlag, VerboseFlag, WorkspaceOption, begin, open_workspace
from energatron.adapters.cli.output import CommandOutput
from energatron.adapters.workspace import workspace_lock
from energatron.core.canonical import JSONValue
from energatron.core.models.recommend import ToolState
from energatron.core.models.workspace import WorkspacePaths
from energatron.services import demo as demo_service
from energatron.services.context import ServiceContext
from energatron.services.demo import DemoResult

COMMAND = "demo"


def demo(
    ctx: typer.Context,
    json_output: JsonFlag = False,
    workspace: WorkspaceOption = None,
    verbose: VerboseFlag = False,
    debug: DebugFlag = False,
) -> CommandOutput:
    """Fill a workspace made by `init --demo` with synthetic data and run it to a recommendation."""
    invocation = begin(ctx, verbose=verbose, debug=debug)
    context = open_workspace(invocation, workspace)
    result = _build(context)
    return CommandOutput(COMMAND, result, text(result))


def _build(context: ServiceContext) -> DemoResult:
    with workspace_lock(context.paths):
        live = wiring.code_fingerprint()
        tool = ToolState(
            energatron_version=live.energatron_version,
            install=live.install,
            tool_commit=live.tool_commit,
            tool_dirty=live.tool_dirty,
        )
        provider = wiring.demo_provider(context.clock, context.region)
        return demo_service.build(context, tool=tool, provider=provider)


def run_demo(paths: WorkspacePaths) -> JSONValue:
    """The demo in this process, for `init --demo --no-sync`: its result as JSON."""
    return cast(JSONValue, _build(wiring.build_context(paths)).model_dump(mode="json"))


def text(result: DemoResult) -> str:
    return (
        f"Demo: {result.landed} datasets and {result.offers} offer files landed; run {result.run_id}, "
        f"recommendation {result.recommendation_id or 'none'}.\nOpen {' and '.join(result.reports)}.\n"
    )
