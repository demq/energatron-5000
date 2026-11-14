"""`energatron diagnose [--redacted]`: a support report holding structure, never values (P8 spec §8).

Read-only: no lock; agents may run it. The report is always redacted (plan §14.4's `--redacted` is accepted and changes
nothing). Its body is `reads.DIAGNOSE` (P10 spec §3.2), which gathers what services can't: the live code fingerprint,
and the versions of git, uv and DVC.
"""

from typing import Annotated, cast

import typer

from energatron.adapters import reads
from energatron.adapters.cli.common import DebugFlag, JsonFlag, VerboseFlag, WorkspaceOption, begin, open_workspace
from energatron.adapters.cli.output import CommandOutput
from energatron.services.diagnose import ConfigInfo, DiagnoseResult

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
    result = cast(DiagnoseResult, reads.DIAGNOSE.run(context, reads.NoArgs()))
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
