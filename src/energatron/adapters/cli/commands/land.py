"""`energatron land PATH... [--source S] [--supersedes ID] [--note TEXT] [--dry-run] [--yes]` (D3 §8).

Takes the workspace lock for the whole call, dry runs included (D1 §8.8); `--yes` is accepted and unused, as `land`
never prompts. The exit code follows D3 §8.3: a single rejected input ends with its own error and no `data`;
several inputs with at least one rejected end with `E_PARTIAL`, whose `data` is the result.
"""

from pathlib import Path
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
from energatron.core.errors import PartialError, UsageError
from energatron.core.models.catalog import SourceType
from energatron.services import land as land_service
from energatron.services.land import LandResult

COMMAND = "land"

PathsArgument = Annotated[
    list[Path],
    typer.Argument(
        help="Files to land, or folders whose files (not hidden, not in subfolders) are landed.", show_default=False
    ),
]
SourceOption = Annotated[
    str | None,
    typer.Option("--source", metavar="SOURCE", help="The source type, when detection can't tell.", show_default=False),
]
SupersedesOption = Annotated[
    str | None,
    typer.Option("--supersedes", metavar="DATASET_ID", help="The dataset this one corrects.", show_default=False),
]
NoteOption = Annotated[str, typer.Option("--note", metavar="TEXT", help="A private one-line note for the catalog.")]

_UNCHANGED = frozenset({"already_landed", "duplicate_in_batch", "same_snapshot"})


def land(
    ctx: typer.Context,
    paths: PathsArgument,
    source: SourceOption = None,
    supersedes: SupersedesOption = None,
    note: NoteOption = "",
    dry_run: DryRunFlag = False,
    yes: YesFlag = False,
    json_output: JsonFlag = False,
    workspace: WorkspaceOption = None,
    verbose: VerboseFlag = False,
    debug: DebugFlag = False,
) -> CommandOutput:
    """Copy raw files into the raw zone, catalog them, track them with DVC and commit."""
    invocation = begin(ctx, verbose=verbose, debug=debug)
    source_type = _source_type(source)
    context = open_workspace(invocation, workspace)
    with workspace_lock(context.paths):
        result = land_service.land(
            context, paths, source=source_type, supersedes=supersedes, note=note, dry_run=dry_run
        )
    error = land_service.outcome(result)
    if error is not None and not isinstance(error, PartialError):
        raise error
    return CommandOutput(COMMAND, result, _text(result), error=error)


def _source_type(value: str | None) -> SourceType | None:
    if value is None:
        return None
    try:
        return SourceType(value)
    except ValueError:
        choices = ", ".join(sorted(source.value for source in SourceType))
        raise UsageError(f"--source is one of: {choices}.", parameter="--source") from None


def _text(result: LandResult) -> str:
    """One line per item, then the totals (D3 §8.4)."""
    lines: list[str] = []
    for item in result.items:
        if item.error is not None:
            lines.append(f"{item.position} {item.status} {item.error.code}")
        else:
            lines.append(f"{item.position} {item.status} {item.source} {item.dataset_id}")
    landed = sum(item.status == "landed" for item in result.items)
    unchanged = sum(item.status in _UNCHANGED for item in result.items)
    rejected = sum(item.status == "rejected" for item in result.items)
    commit = result.commit[:7] if result.commit else "none"
    totals = f"landed {landed}, unchanged {unchanged}, rejected {rejected}; commit {commit}"
    if result.dry_run:
        totals = f"dry run: {totals.replace('landed', 'would land', 1)}; nothing was written"
    return "\n".join([*lines, totals]) + "\n"
