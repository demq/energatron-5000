"""`energatron upgrade`: refresh the template-managed files of a workspace `init` made (P8 spec §6). Takes the lock."""

from pathlib import Path
from typing import Annotated

import typer

from energatron.adapters import wiring
from energatron.adapters.cli.common import (
    DebugFlag,
    DryRunFlag,
    JsonFlag,
    VerboseFlag,
    WorkspaceOption,
    begin,
    open_workspace,
)
from energatron.adapters.cli.output import CommandOutput
from energatron.adapters.workspace import workspace_lock
from energatron.services import upgrade as upgrade_service
from energatron.services.upgrade import UpgradeResult

ForceFlag = Annotated[bool, typer.Option("--force", help="Also replace managed files you edited.")]


def upgrade(
    ctx: typer.Context,
    force: ForceFlag = False,
    dry_run: DryRunFlag = False,
    json_output: JsonFlag = False,
    workspace: WorkspaceOption = None,
    verbose: VerboseFlag = False,
    debug: DebugFlag = False,
) -> CommandOutput:
    """Refresh the workspace's template-managed files (hooks, scripts, ignore files) and commit them."""
    invocation = begin(ctx, verbose=verbose, debug=debug)
    context = open_workspace(invocation, workspace)
    with workspace_lock(context.paths):
        result = upgrade_service.upgrade(
            context, sources=wiring.template_sources(), home=Path.home().resolve(), force=force, dry_run=dry_run
        )
    return CommandOutput("upgrade", result, text(result))


def text(result: UpgradeResult) -> str:
    counts: dict[str, int] = {}
    for item in result.files:
        counts[item.status] = counts.get(item.status, 0) + 1
    summary = ", ".join(f"{count} {status}" for status, count in sorted(counts.items()))
    if result.dry_run:
        return f"Dry run: template v{result.from_version} → v{result.to_version}: {summary}. Nothing was written.\n"
    tail = f"Committed {result.commit[:12]}." if result.commit else "Nothing to commit."
    return f"Template v{result.from_version} → v{result.to_version}: {summary}. {tail}\n"
