"""`energatron mcp [--allow-documents]` (P9 spec §5): a local MCP server on stdio for one workspace.

It finds the workspace first: outside one, the usual error (envelope or text) and exit 3. Once serving, stdout carries
only MCP JSON-RPC lines (D1 §8.4's one exception); `--json` is accepted and changes nothing then. It takes no lock and
reads no private config: each tool call runs its CLI command, which does both as it always does.
"""

import os
from pathlib import Path
from typing import Annotated

import typer

from energatron.adapters.cli.common import DebugFlag, JsonFlag, VerboseFlag, WorkspaceOption, begin
from energatron.adapters.mcp.server import serve_stdio
from energatron.adapters.workspace import find_workspace


def mcp(
    ctx: typer.Context,
    allow_documents: Annotated[
        bool,
        typer.Option(
            "--allow-documents",
            help="Also offer read_gas_bill_text: a bill's text, personal details included, goes to the model provider.",
        ),
    ] = False,
    json_output: JsonFlag = False,
    workspace: WorkspaceOption = None,
    verbose: VerboseFlag = False,
    debug: DebugFlag = False,
) -> int:
    """Serve this workspace's tools to an AI agent over MCP (stdio) until stdin ends."""
    begin(ctx, verbose=verbose, debug=debug)
    paths = find_workspace(workspace, cwd=Path.cwd(), env=os.environ)
    serve_stdio(paths.root, allow_documents=allow_documents)
    return 0
