"""`energatron denylist [--check]`: write `private/denylist.generated.txt`, or check it's current (D2 §7.2).

Takes the workspace lock, `--check` and `--dry-run` included (D2 §5.9). Never commits: the caller commits
`private.dvc`.
"""

from typing import Annotated

import typer

from energatron.adapters.cli.common import (
    DebugFlag,
    DryRunFlag,
    JsonFlag,
    VerboseFlag,
    WorkspaceOption,
    YesFlag,
    begin,
    open_workspace,
)
from energatron.adapters.cli.output import CommandOutput
from energatron.adapters.workspace import workspace_lock
from energatron.services import denylist as denylist_service
from energatron.services.denylist import DenylistResult

CheckFlag = Annotated[
    bool, typer.Option("--check", help="Write nothing; fail with E_DENYLIST_STALE if the file isn't current.")
]


def denylist(
    ctx: typer.Context,
    check: CheckFlag = False,
    dry_run: DryRunFlag = False,
    yes: YesFlag = False,
    json_output: JsonFlag = False,
    workspace: WorkspaceOption = None,
    verbose: VerboseFlag = False,
    debug: DebugFlag = False,
) -> CommandOutput:
    """Write the privacy guard's denylist from private/config.toml, then `dvc add private`."""
    invocation = begin(ctx, verbose=verbose, debug=debug)
    context = open_workspace(invocation, workspace)
    with workspace_lock(context.paths):
        result = denylist_service.refresh_denylist(context, check=check, dry_run=dry_run)
    return CommandOutput("denylist", result, _text(result, check=check))


def _text(result: DenylistResult, *, check: bool) -> str:
    counts = f"{result.entries} entries, {result.manual_entries} manual"
    if check:
        return f"Denylist is current: {counts}.\n"
    if result.dry_run:
        return f"Dry run: the denylist would have {counts}; nothing was written.\n"
    state = "written" if result.written else "unchanged"
    pointers = ", ".join(result.pointer_files) or "none"
    return f"Denylist {state}: {counts}. DVC pointer files: {pointers}; commit them.\n"
