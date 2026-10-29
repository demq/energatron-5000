"""`energatron verify`: rerun every stage into scratch space and re-hash every catalogued blob (D1 §11.4).

The command computes the live code entries and reads the recorded ones (services can't compute them), takes the
workspace lock so the outputs it compares can't change, deletes a scratch directory a killed run left behind, then
calls the service, which writes only under `.energatron/tmp/verify/` and deletes it at the end.
"""

import shutil

import typer

from energatron.adapters import wiring
from energatron.adapters.cli.common import (
    DebugFlag,
    JsonFlag,
    VerboseFlag,
    WorkspaceOption,
    begin,
    open_workspace,
)
from energatron.adapters.cli.output import CommandOutput
from energatron.adapters.workspace import workspace_lock
from energatron.services import verify as verify_service
from energatron.services.stages.dvc_yaml import CODE_FINGERPRINT_PATH


def verify(
    ctx: typer.Context,
    json_output: JsonFlag = False,
    workspace: WorkspaceOption = None,
    verbose: VerboseFlag = False,
    debug: DebugFlag = False,
) -> CommandOutput:
    """Check that every stage reproduces its outputs and every stored raw file matches its catalog record."""
    invocation = begin(ctx, verbose=verbose, debug=debug)
    context = open_workspace(invocation, workspace)
    with workspace_lock(context.paths):
        live = wiring.code_fingerprint()
        recorded = wiring.read_fingerprint(context.paths.root / CODE_FINGERPRINT_PATH)
        scratch = context.paths.tmp_dir / "verify"
        if scratch.exists():
            shutil.rmtree(scratch)
        result = verify_service.verify(context, live=live, recorded=recorded, scratch=scratch)
    text = (
        f"Verified: {len(result.stages)} stage(s) reproduce and {result.datasets} stored file(s) match the catalog.\n"
    )
    return CommandOutput("verify", result, text)
