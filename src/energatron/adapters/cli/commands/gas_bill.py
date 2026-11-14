"""`energatron gas-bill new|check|write|text` (P5 spec §3.3; P9 spec §4.2): gas bill records and their validators.

- `new PDF_DATASET_ID [--extracted-by VALUE] [--force]`: scaffold a record; no lock (it only writes the skeleton).
- `check SOURCE`: parse and recompute a record (a file path, or `-` for stdin); read-only, no lock. A failing check →
  `E_CHECK_FAILED` (exit 7) with the failing checks in `details.failed` (D1 §8.5: `data` is null on failure).
- `write SOURCE [--force] [--dry-run]`: the same checks, then the record as canonical YAML in `data/landing/`; takes
  the lock (it writes under `data/`, D1 §8.8).
- `text PDF_DATASET_ID`: a landed gas bill PDF's text, for transcription; read-only.
"""

import sys
from pathlib import Path
from typing import Annotated

import typer

from energatron.adapters import wiring
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
from energatron.core.errors import CheckFailedError, InvalidInputError
from energatron.services import gas_bill as gas_bill_service
from energatron.services.gas_bill import GasBillCheckResult

COMMAND = "gas-bill"

PdfArgument = Annotated[str, typer.Argument(metavar="PDF_DATASET_ID", help="The landed gas bill PDF's dataset ID.")]
SourceArgument = Annotated[
    str, typer.Argument(metavar="SOURCE", help="The record file (YAML or JSON), or - for stdin.")
]
ForceFlag = Annotated[bool, typer.Option("--force", help="Overwrite an existing file in data/landing/.")]
ExtractedByOption = Annotated[
    str | None,
    typer.Option(
        "--extracted-by",
        metavar="VALUE",
        help="manual, or agent:<model id>, filled into the skeleton.",
        show_default=False,
    ),
]

app = typer.Typer(name="gas-bill", help="Gas bill records.", add_completion=False, pretty_exceptions_enable=False)


@app.command("new")
def new(
    ctx: typer.Context,
    pdf_dataset_id: PdfArgument,
    extracted_by: ExtractedByOption = None,
    force: ForceFlag = False,
    json_output: JsonFlag = False,
    workspace: WorkspaceOption = None,
    verbose: VerboseFlag = False,
    debug: DebugFlag = False,
) -> CommandOutput:
    """Write the YAML skeleton for transcribing a landed gas bill PDF into data/landing/."""
    invocation = begin(ctx, verbose=verbose, debug=debug)
    context = open_workspace(invocation, workspace)
    result = gas_bill_service.new_gas_bill_record(context, pdf_dataset_id, force=force, extracted=extracted_by)
    text = f"Wrote {result.path}: fill it from the bill, then check it with `energatron gas-bill check`.\n"
    return CommandOutput(COMMAND, result, text)


@app.command("check")
def check(
    ctx: typer.Context,
    source: SourceArgument,
    json_output: JsonFlag = False,
    workspace: WorkspaceOption = None,
    verbose: VerboseFlag = False,
    debug: DebugFlag = False,
) -> CommandOutput:
    """Check a gas bill record against its own reads and lines, as the gas_bills stage will; writes nothing."""
    invocation = begin(ctx, verbose=verbose, debug=debug)
    data = _read_source(source)
    context = open_workspace(invocation, workspace)
    result = gas_bill_service.check_gas_bill_record(context, data)
    _raise_if_failed(result)
    return CommandOutput(COMMAND, result, _text(result))


@app.command("write")
def write(
    ctx: typer.Context,
    source: SourceArgument,
    force: ForceFlag = False,
    dry_run: DryRunFlag = False,
    json_output: JsonFlag = False,
    workspace: WorkspaceOption = None,
    verbose: VerboseFlag = False,
    debug: DebugFlag = False,
) -> CommandOutput:
    """Check a gas bill record and, if every check passes, write it to data/landing/ ready to land."""
    invocation = begin(ctx, verbose=verbose, debug=debug)
    data = _read_source(source)
    context = open_workspace(invocation, workspace)
    with workspace_lock(context.paths):
        result = gas_bill_service.write_gas_bill_record(context, data, force=force, dry_run=dry_run)
    _raise_if_failed(result)
    return CommandOutput(COMMAND, result, _text(result))


@app.command("text")
def text(
    ctx: typer.Context,
    pdf_dataset_id: PdfArgument,
    json_output: JsonFlag = False,
    workspace: WorkspaceOption = None,
    verbose: VerboseFlag = False,
    debug: DebugFlag = False,
) -> CommandOutput:
    """Print a landed gas bill PDF's text, for transcribing it."""
    invocation = begin(ctx, verbose=verbose, debug=debug)
    context = open_workspace(invocation, workspace)
    result = gas_bill_service.gas_bill_text(context, pdf_dataset_id, wiring.pdf_pages)
    return CommandOutput(COMMAND, result, result.text.rstrip("\n") + "\n")


def _read_source(source: str) -> bytes:
    """The record's bytes: stdin for `-` (data, not a prompt), otherwise the file at that path."""
    if source == "-":
        return sys.stdin.buffer.read()
    try:
        return Path(source).read_bytes()
    except OSError:
        raise InvalidInputError("The record file can't be read.", field="source") from None


def _raise_if_failed(result: GasBillCheckResult) -> None:
    failed = result.failed()
    if failed:
        raise CheckFailedError(
            "The record doesn't recompute: fix the failing checks against the bill.",
            failed=failed,
            pdf_dataset_id=result.pdf_dataset_id,
            recomputed_total=str(result.recomputed_total),
            printed_total=str(result.printed_total),
        )


def _text(result: GasBillCheckResult) -> str:
    verdict = "would write" if result.dry_run and result.path else "wrote" if result.written else "checked"
    where = f" {result.path}" if result.path and (result.written or result.dry_run) else ""
    return f"Every check passed; {verdict}{where} ({result.period_start}..{result.period_end}).\n"
