"""`energatron fetch-offers [--retailer BASE]... [--all] [--dry-run] [--yes]` (P4 spec §3.2).

Takes the workspace lock for the whole call, dry runs included (D1 §8.8); `--yes` is accepted and unused, as the
command never prompts. Exit 0 when every list and detail was fetched; 8 (`E_PARTIAL`, with the result as `data`) when
some failed but the rest landed; the network error itself when every retailer's list failed.
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
from energatron.core.errors import PartialError
from energatron.services import fetch_offers as fetch_service
from energatron.services.fetch_offers import FetchOffersResult

COMMAND = "fetch-offers"

RetailerOption = Annotated[
    list[str] | None,
    typer.Option(
        "--retailer", metavar="BASE", help="A CDR base URI to fetch (repeatable); default: params offers.retailers."
    ),
]
AllFlag = Annotated[bool, typer.Option("--all", help="Fetch every selected plan's detail, changed or not.")]


def fetch_offers(
    ctx: typer.Context,
    retailer: RetailerOption = None,
    all_details: AllFlag = False,
    dry_run: DryRunFlag = False,
    yes: YesFlag = False,
    json_output: JsonFlag = False,
    workspace: WorkspaceOption = None,
    verbose: VerboseFlag = False,
    debug: DebugFlag = False,
) -> CommandOutput:
    """List retail offers from the CDR, keep the ones for this household and land the changed plans."""
    invocation = begin(ctx, verbose=verbose, debug=debug)
    context = open_workspace(invocation, workspace)
    with workspace_lock(context.paths):
        result = fetch_service.fetch_offers(
            context, retailers=retailer or None, all_details=all_details, dry_run=dry_run
        )
    error = fetch_service.outcome(result)
    if error is not None and not isinstance(error, PartialError):
        raise error
    return CommandOutput(COMMAND, result, _text(result), error=error)


def _text(result: FetchOffersResult) -> str:
    """One line per retailer, then the totals."""
    lines: list[str] = []
    for found in result.retailers:
        if found.error is not None:
            lines.append(f"{found.base}: failed ({found.error.code})")
            continue
        left_out = ", ".join(f"{reason} {count}" for reason, count in found.left_out.items()) or "none"
        lines.append(
            f"{found.base}: listed {found.listed}, selected {found.selected} (fetched {found.fetched}, "
            f"unchanged {found.unchanged}, failed {found.failed}); left out: {left_out}"
        )
    if result.dry_run:
        lines.append("dry run: no detail was fetched and nothing was written")
    else:
        commit = result.commit[:7] if result.commit else "none"
        lines.append(f"landed {len(result.landed)}, rejected {result.rejected}; commit {commit}")
    if not result.postcode_applied:
        lines.append("note: no postcode in private/config.toml, so plans weren't filtered by postcode")
    return "\n".join(lines) + "\n"
