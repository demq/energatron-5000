"""`energatron migrate`: rewrite editable config files written at an older schema version (P8 spec §7). Takes the
lock. Never prints a private value: the result names files, kinds and versions only."""

import typer

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
from energatron.services import migrate as migrate_service
from energatron.services.migrate import MigrateResult


def migrate(
    ctx: typer.Context,
    dry_run: DryRunFlag = False,
    json_output: JsonFlag = False,
    workspace: WorkspaceOption = None,
    verbose: VerboseFlag = False,
    debug: DebugFlag = False,
) -> CommandOutput:
    """Rewrite config files written by an older energatron to the current schema, and commit them."""
    invocation = begin(ctx, verbose=verbose, debug=debug)
    context = open_workspace(invocation, workspace)
    with workspace_lock(context.paths):
        result = migrate_service.migrate(context, dry_run=dry_run)
    return CommandOutput("migrate", result, text(result))


def text(result: MigrateResult) -> str:
    if not result.files:
        return "Every config file is current: nothing to rewrite.\n"
    lines = [f"{item.path}: {item.kind} v{item.from_version} → v{item.to_version}" for item in result.files]
    lines.append("Dry run: nothing was written." if result.dry_run else f"Committed {result.commit or 'nothing'}.")
    return "\n".join(lines) + "\n"
