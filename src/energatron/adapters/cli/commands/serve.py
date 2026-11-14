"""`energatron serve [--host H] [--port N] [--open] [--allow-documents]` (P10 spec §7): the local web UI.

It finds the workspace first (outside one, the usual error and exit 2), listens on a loopback address, prints its one
result (D1 §8.4's second exception: nothing more on stdout while it serves), then serves until Ctrl-C or SIGTERM and
exits 0. Read-only: it takes no lock and writes nothing under the workspace.

The web stack (`adapters/http`, FastAPI, uvicorn) is imported only when `serve` runs, so a workspace whose lock
predates those dependencies still runs every other command (found by P10.3.1).
"""

import os
import webbrowser
from pathlib import Path
from typing import Annotated

import typer

from energatron.adapters.cli.common import DebugFlag, JsonFlag, VerboseFlag, WorkspaceOption, begin
from energatron.adapters.cli.output import CommandOutput, write_success
from energatron.adapters.workspace import find_workspace
from energatron.core.errors import UsageError

COMMAND = "serve"
DEFAULT_PORT = 8765  # `server.DEFAULT_PORT`, repeated so `--help` needs no web stack


def serve(
    ctx: typer.Context,
    host: Annotated[
        str, typer.Option("--host", metavar="HOST", help="127.0.0.1, localhost or ::1: never beyond this machine.")
    ] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port", min=0, max=65535, help="The port; 0 picks a free one.")] = DEFAULT_PORT,
    open_browser: Annotated[bool, typer.Option("--open", help="Open the address in the default browser.")] = False,
    allow_documents: Annotated[
        bool, typer.Option("--allow-documents", help="Also serve a landed gas bill's text, personal details included.")
    ] = False,
    json_output: JsonFlag = False,
    workspace: WorkspaceOption = None,
    verbose: VerboseFlag = False,
    debug: DebugFlag = False,
) -> int:
    """Look at this workspace in a browser: read-only pages and a JSON API on this machine, until Ctrl-C."""
    invocation = begin(ctx, verbose=verbose, debug=debug)
    paths = find_workspace(workspace, cwd=Path.cwd(), env=os.environ)
    try:
        from energatron.adapters.http import server
    except ImportError:
        raise UsageError(
            "serve needs fastapi and uvicorn, which this environment doesn't have.",
            reason="web_dependencies_missing",
            hint="In the workspace, run `uv lock` and `uv sync`, then commit uv.lock (this tool version added them).",
        ) from None
    sock = server.bind(host, port)
    access, result = server.prepare(host, sock, allow_documents=allow_documents)
    text = f"Serving {paths.root.name} at {result.url} (Ctrl-C to stop).\n"
    with server.stop_signals() as stop:
        write_success(invocation, CommandOutput(COMMAND, result, text))
        if open_browser:
            webbrowser.open(result.url)
        server.run(paths, sock, access, allow_documents=allow_documents, verbose=verbose or debug, stop=stop)
    return 0
