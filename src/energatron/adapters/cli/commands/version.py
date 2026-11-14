"""`energatron version`: works outside a workspace (D1 §8.3; D2 §5.9)."""

from typing import cast

import typer

from energatron.adapters import reads
from energatron.adapters.cli.common import DebugFlag, JsonFlag, VerboseFlag, WorkspaceOption, begin
from energatron.adapters.cli.output import CommandOutput
from energatron.adapters.reads import VersionResult


def version(
    ctx: typer.Context,
    json_output: JsonFlag = False,
    workspace: WorkspaceOption = None,
    verbose: VerboseFlag = False,
    debug: DebugFlag = False,
) -> CommandOutput:
    """Print energatron's version and Python's. Works anywhere."""
    begin(ctx, verbose=verbose, debug=debug)
    result = cast(VersionResult, reads.VERSION.run(None, reads.NoArgs()))
    return CommandOutput("version", result, f"energatron {result.energatron_version} (Python {result.python})\n")
