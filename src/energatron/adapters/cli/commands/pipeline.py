"""`energatron pipeline sync [--check] [--dry-run]`: the workspace's generated pipeline files (D3 §10.1).

`sync` takes the workspace lock, `--dry-run` included; `--check` writes nothing and takes no lock, so it runs inside
`land`'s commit hook while `land` holds it (D1 §8.8). Never commits: the result lists what to commit.
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
from energatron.core.errors import UsageError
from energatron.services import pipeline_sync
from energatron.services.pipeline_sync import PipelineSyncResult

COMMAND = "pipeline"
"""The envelope's `command`: the group's name, as `main()` names it on a failure (D1 §8.5)."""

CheckFlag = Annotated[
    bool,
    typer.Option("--check", help="Write nothing; fail with E_PIPELINE_NOT_SYNCED if the pipeline files have drifted."),
]

app = typer.Typer(
    name="pipeline",
    help="The workspace's generated pipeline files.",
    add_completion=False,
    pretty_exceptions_enable=False,
)


@app.command("sync")
def sync(
    ctx: typer.Context,
    check: CheckFlag = False,
    dry_run: DryRunFlag = False,
    yes: YesFlag = False,
    json_output: JsonFlag = False,
    workspace: WorkspaceOption = None,
    verbose: VerboseFlag = False,
    debug: DebugFlag = False,
) -> CommandOutput:
    """Write dvc.yaml and params.yaml and create missing catalogs, in line with the installed tool."""
    invocation = begin(ctx, verbose=verbose, debug=debug)
    if check and dry_run:
        raise UsageError("--check and --dry-run can't be combined: --check writes nothing anyway.")
    context = open_workspace(invocation, workspace)
    if check:
        pipeline_sync.check(context)
        result = PipelineSyncResult(
            dvc_yaml_written=False,
            params_written=False,
            params_rewritten=False,
            catalogs_created=[],
            pointer_files=[],
            dry_run=False,
        )
        return CommandOutput(COMMAND, result, "The pipeline files are in sync with the tool.\n")
    with workspace_lock(context.paths):
        result = pipeline_sync.sync(context, dry_run=dry_run)
    return CommandOutput(COMMAND, result, _text(result))


def _text(result: PipelineSyncResult) -> str:
    changes = [
        name
        for name, changed in (
            ("dvc.yaml", result.dvc_yaml_written),
            ("params.yaml (created)", result.params_written),
            ("params.yaml (upgraded; comments are lost)", result.params_rewritten),
        )
        if changed
    ]
    if result.catalogs_created:
        changes.append(f"{len(result.catalogs_created)} empty catalogs")
    if result.dry_run:
        return f"Dry run: would write {', '.join(changes) or 'nothing'}; nothing was written.\n"
    if not changes:
        return "The pipeline files are already in sync with the tool.\n"
    pointers = ", ".join(result.pointer_files) or "none"
    return f"Pipeline synced: wrote {', '.join(changes)}. DVC pointer files: {pointers}. Commit them.\n"
