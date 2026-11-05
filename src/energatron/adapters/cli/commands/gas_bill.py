"""`energatron gas-bill new <pdf_dataset_id> [--force]` (P5 spec §3.3): scaffold a gas bill record; takes no lock."""

from typing import Annotated

import typer

from energatron.adapters.cli.common import DebugFlag, JsonFlag, VerboseFlag, WorkspaceOption, begin, open_workspace
from energatron.adapters.cli.output import CommandOutput
from energatron.services.gas_bill import new_gas_bill_record

COMMAND = "gas-bill"

PdfArgument = Annotated[str, typer.Argument(metavar="PDF_DATASET_ID", help="The landed gas bill PDF's dataset ID.")]
ForceFlag = Annotated[bool, typer.Option("--force", help="Overwrite an existing skeleton in data/landing/.")]

app = typer.Typer(name="gas-bill", help="Gas bill records.", add_completion=False, pretty_exceptions_enable=False)


@app.command("new")
def new(
    ctx: typer.Context,
    pdf_dataset_id: PdfArgument,
    force: ForceFlag = False,
    json_output: JsonFlag = False,
    workspace: WorkspaceOption = None,
    verbose: VerboseFlag = False,
    debug: DebugFlag = False,
) -> CommandOutput:
    """Write the YAML skeleton for transcribing a landed gas bill PDF into data/landing/."""
    invocation = begin(ctx, verbose=verbose, debug=debug)
    context = open_workspace(invocation, workspace)
    result = new_gas_bill_record(context, pdf_dataset_id, force=force)
    text = f"Wrote {result.path}: fill it from the bill, then land it (dry-run first).\n"
    return CommandOutput(COMMAND, result, text)
