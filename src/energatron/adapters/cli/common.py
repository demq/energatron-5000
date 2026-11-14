"""What every command shares: the common options (D1 §8.2), the invocation, and opening the workspace (§8.3)."""

import os
from pathlib import Path
from typing import Annotated

import typer

from energatron.adapters import reads
from energatron.adapters.cli.output import Invocation, set_verbosity
from energatron.adapters.workspace import find_workspace
from energatron.core.errors import InternalError
from energatron.core.privacy import DenyEntry
from energatron.services.context import ServiceContext

# Every command accepts these, after the command name (D1 §8.2). `--json` and `--debug` are also pre-scanned by
# `main()`, which decides the output mode before typer parses anything (§8.5).
JsonFlag = Annotated[
    bool, typer.Option("--json", help="Print one JSON envelope (kind cli_result) on stdout. Also ENERGATRON_JSON=1.")
]
WorkspaceOption = Annotated[
    str | None,
    typer.Option(
        "--workspace",
        metavar="PATH",
        help="The workspace root. Also ENERGATRON_WORKSPACE; otherwise the first folder at or above the current one "
        "that holds .energatron/workspace.toml.",
        show_default=False,
    ),
]
VerboseFlag = Annotated[bool, typer.Option("-v", "--verbose", help="Log progress to stderr.")]
DebugFlag = Annotated[bool, typer.Option("--debug", help="Log everything to stderr, with tracebacks.")]

# Mutating commands also accept these.
DryRunFlag = Annotated[bool, typer.Option("--dry-run", help="Run every check and report; write nothing.")]
YesFlag = Annotated[bool, typer.Option("--yes", help="Answer yes to any confirmation.")]


def begin(ctx: typer.Context, *, verbose: bool, debug: bool) -> Invocation:
    """The invocation `main()` passed in, with the log level the options ask for."""
    invocation = ctx.obj
    if not isinstance(invocation, Invocation):
        raise InternalError("The CLI app must be run through energatron.adapters.cli.app.main().")
    set_verbosity(invocation, verbose=verbose, debug=debug)
    return invocation


def open_workspace(invocation: Invocation, option: str | None) -> ServiceContext:
    """Find the workspace, load the redaction denylist as early as possible, then build the context.

    The denylist is `private/denylist.txt`'s entries first, then those derived from the private config once it's
    read (D1 §8.7), so it never depends on the generated file being current.
    """
    paths = find_workspace(option, cwd=Path.cwd(), env=os.environ)

    def hold(manual: tuple[DenyEntry, ...]) -> None:
        invocation.denylist = manual

    context, denylist = reads.open_context(paths, on_manual=hold)
    invocation.denylist = denylist
    return context
