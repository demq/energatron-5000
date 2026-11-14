"""`energatron fingerprint [--check]`: write `.energatron/code_fingerprint.json`, or check it's current (D1 §11.3).

The live entries come from `wiring.code_fingerprint()`. Writing takes the workspace lock, `--dry-run` included;
`--check` writes nothing and takes no lock (D1 §8.8).
"""

from typing import Annotated, cast

import typer

from energatron.adapters import reads, wiring
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
from energatron.services.stages.dvc_yaml import CODE_FINGERPRINT_PATH

CheckFlag = Annotated[
    bool,
    typer.Option("--check", help="Write nothing; fail with E_FINGERPRINT_STALE if a stage's recorded entry is stale."),
]


def fingerprint(
    ctx: typer.Context,
    check: CheckFlag = False,
    dry_run: DryRunFlag = False,
    yes: YesFlag = False,
    json_output: JsonFlag = False,
    workspace: WorkspaceOption = None,
    verbose: VerboseFlag = False,
    debug: DebugFlag = False,
) -> CommandOutput:
    """Record each stage's code entry in .energatron/code_fingerprint.json. Run it before `dvc repro`."""
    invocation = begin(ctx, verbose=verbose, debug=debug)
    if check and dry_run:
        raise UsageError("--check and --dry-run can't be combined: --check writes nothing anyway.")
    context = open_workspace(invocation, workspace)
    path = context.paths.root / CODE_FINGERPRINT_PATH
    if check:
        result = cast(wiring.FingerprintResult, reads.FINGERPRINT_CHECK.run(context, reads.NoArgs()))
    else:
        with workspace_lock(context.paths):
            result = wiring.refresh_fingerprint(path, wiring.code_fingerprint(), dry_run=dry_run)
    stages = len(result.stages)
    if check:
        text = f"Code fingerprint is current: {stages} stages.\n"
    elif result.dry_run:
        text = f"Dry run: {len(result.changed_stages)} of {stages} stage entries would change; nothing was written.\n"
    elif result.written:
        text = f"Code fingerprint written: {len(result.changed_stages)} of {stages} stage entries changed.\n"
    else:
        text = f"Code fingerprint unchanged: {stages} stages.\n"
    return CommandOutput("fingerprint", result, text)
