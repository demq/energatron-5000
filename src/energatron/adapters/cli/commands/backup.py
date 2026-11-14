"""`energatron backup [--status]`: `dvc push` and a verified git bundle, or whether one is pending (D2 §4, §8.5).

`backup` is owner-run, from a terminal with iCloud access (D2 §4); it takes the workspace lock, `--dry-run`
included. `--status` takes no lock and never touches a backup location: agents may run it.
"""

from typing import Annotated, cast

import typer

from energatron.adapters import reads
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
from energatron.core.errors import UsageError
from energatron.services import backup as backup_service
from energatron.services.backup import BackupResult, BackupStatusResult

StatusFlag = Annotated[
    bool, typer.Option("--status", help="Say whether a backup is pending. No lock; never touches iCloud.")
]

_RUN_IT = "Run `icloud_backup.py backup` in your terminal.\n"


def backup(
    ctx: typer.Context,
    status: StatusFlag = False,
    dry_run: DryRunFlag = False,
    yes: YesFlag = False,
    json_output: JsonFlag = False,
    workspace: WorkspaceOption = None,
    verbose: VerboseFlag = False,
    debug: DebugFlag = False,
) -> CommandOutput:
    """Back the workspace up: `dvc push`, then a verified git bundle. Owner-run, from a terminal with iCloud access."""
    invocation = begin(ctx, verbose=verbose, debug=debug)
    if status and dry_run:
        raise UsageError("--status and --dry-run can't be combined: --status writes nothing anyway.")
    context = open_workspace(invocation, workspace)
    if status:
        result = cast(BackupStatusResult, reads.BACKUP_STATUS.run(context, reads.NoArgs()))
        return CommandOutput("backup", result, _status_text(result))
    with workspace_lock(context.paths):
        done = backup_service.backup(context, dry_run=dry_run)
    return CommandOutput("backup", done, _backup_text(done))


def _status_text(result: BackupStatusResult) -> str:
    """D2 §4's pending-backup lines."""
    if result.refs_backed_up:
        return "Backed up: nothing is pending.\n"
    if result.last_backup_head is None:
        return "Backup pending: nothing is backed up yet.\n" + _RUN_IT
    if result.commits_not_backed_up:
        return f"Backup pending: {result.commits_not_backed_up} commits not in iCloud.\n" + _RUN_IT
    return "Backup pending: refs changed since the last backup (for example a new tag).\n" + _RUN_IT


def _backup_text(result: BackupResult) -> str:
    report = result.report
    if report is None:
        return "Dry run: there is a commit to back up; nothing was touched.\n"
    bundle = "new bundle" if report.bundle_created else "bundle unchanged"
    return (
        f"Backed up: {report.dvc_files_pushed} DVC files pushed; {bundle} {report.bundle_name}; "
        f"bundles kept {report.bundles_kept}, removed {report.bundles_removed}.\n"
    )
