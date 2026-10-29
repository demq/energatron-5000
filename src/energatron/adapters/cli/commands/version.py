"""`energatron version`: works outside a workspace (D1 §8.3; D2 §5.9)."""

import platform
from typing import ClassVar, Literal

import typer

import energatron
from energatron.adapters.cli.common import DebugFlag, JsonFlag, VerboseFlag, WorkspaceOption, begin
from energatron.adapters.cli.output import CommandOutput
from energatron.services.context import ServiceResult


class VersionResult(ServiceResult):
    """`version_result` v1 (D2 §6.4)."""

    KIND: ClassVar[str] = "version_result"
    SCHEMA_VERSION: ClassVar[int] = 1
    kind: Literal["version_result"] = "version_result"
    schema_version: Literal[1] = 1

    energatron_version: str
    python: str


def version(
    ctx: typer.Context,
    json_output: JsonFlag = False,
    workspace: WorkspaceOption = None,
    verbose: VerboseFlag = False,
    debug: DebugFlag = False,
) -> CommandOutput:
    """Print energatron's version and Python's. Works anywhere."""
    begin(ctx, verbose=verbose, debug=debug)
    result = VersionResult(energatron_version=energatron.__version__, python=platform.python_version())
    return CommandOutput("version", result, f"energatron {result.energatron_version} (Python {result.python})\n")
