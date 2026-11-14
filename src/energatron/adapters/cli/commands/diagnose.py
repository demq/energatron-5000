"""`energatron diagnose [--redacted]`: a support report holding structure, never values (P8 spec §8).

Read-only: no lock; agents may run it. The report is always redacted (plan §14.4's `--redacted` is accepted and changes
nothing). The command gathers what services can't: the live code fingerprint, and the versions of git, uv and DVC.
"""

import platform
import sys
from typing import Annotated

import typer

from energatron.adapters import wiring
from energatron.adapters.cli.common import DebugFlag, JsonFlag, VerboseFlag, WorkspaceOption, begin, open_workspace
from energatron.adapters.cli.output import CommandOutput
from energatron.services import diagnose as diagnose_service
from energatron.services.diagnose import ConfigInfo, DiagnoseResult, ToolInfo

RedactedFlag = Annotated[bool, typer.Option("--redacted", help="Accepted for clarity: the report is always redacted.")]


def diagnose(
    ctx: typer.Context,
    redacted: RedactedFlag = False,
    json_output: JsonFlag = False,
    workspace: WorkspaceOption = None,
    verbose: VerboseFlag = False,
    debug: DebugFlag = False,
) -> CommandOutput:
    """Print a redacted report for a bug report: versions, the workspace's structure and state, no values."""
    invocation = begin(ctx, verbose=verbose, debug=debug)
    context = open_workspace(invocation, workspace)
    live = wiring.code_fingerprint()
    stale, tool_dirty = wiring.code_state(context.paths)
    versions = wiring.program_versions()
    tool = ToolInfo(
        energatron_version=live.energatron_version,
        install=live.install,
        tool_dirty=tool_dirty,
        python=platform.python_version(),
        platform=sys.platform,
        machine=platform.machine(),
        git=versions["git"],
        uv=versions["uv"],
        dvc=versions["dvc"],
    )
    result = diagnose_service.diagnose(
        context, tool=tool, code_stale=stale, tool_unpublished=wiring.tool_unpublished(context.paths)
    )
    return CommandOutput("diagnose", result, text(result))


def text(result: DiagnoseResult) -> str:
    tool, space = result.tool, result.workspace
    lines = [
        f"energatron {tool.energatron_version} ({tool.install}{', dirty' if tool.tool_dirty else ''}), "
        f"Python {tool.python}, {tool.platform} {tool.machine}; git {tool.git}, uv {tool.uv}, dvc {tool.dvc}",
        f"Workspace: region {space.region}, template {space.template_version or 'none (hand-built)'}, "
        f"pipeline {'in sync' if space.pipeline_synced else 'not in sync'}, {len(space.code_stale)} stale stage(s)",
        "Config: " + ", ".join(_config(item) for item in space.configs if item.present),
        f"Runs: {space.runs}, decisions: {space.decisions}, reminders: {', '.join(result.reminders) or 'none'}",
        "Problems: " + (", ".join(f"{problem.check} {problem.code}" for problem in result.problems) or "none"),
        "Paste `energatron diagnose --json` into an issue after reading it; it holds no identifiers or figures.",
    ]
    return "\n".join(lines) + "\n"


def _config(item: ConfigInfo) -> str:
    old = " (old)" if item.current is False else ""
    return f"{item.path} {item.kind or '?'} v{item.schema_version or '?'}{old}"
