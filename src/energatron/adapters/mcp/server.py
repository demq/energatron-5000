"""`energatron mcp`'s loop (P9 spec §5.1): protect the protocol stream, then serve until stdin ends.

File descriptor 1 is duplicated for the protocol and then pointed at stderr, so nothing a library or a child process
writes to stdout can corrupt the protocol. The server holds no state besides the workspace root and the tool set, and
reads no private config itself.
"""

import os
import sys
from pathlib import Path
from typing import Final

import energatron
from energatron.adapters.mcp.protocol import McpServer
from energatron.adapters.mcp.runner import Run, ToolRunner, run_child
from energatron.adapters.mcp.tools import tools_for

INSTRUCTIONS: Final = (
    "Tools for one energatron workspace: see where it stands (status), land new files, refresh offers, run the "
    "pipeline, and read the usage, the offers and the recommendation; every result is the matching `energatron "
    "<command> --json` envelope. Figures stay in this workspace: never copy them elsewhere, and leave backups, the "
    "restore drill and decisions the owner hasn't made to the owner."
)


def build_server(root: Path, *, allow_documents: bool, run: Run = run_child) -> McpServer:
    tools = tools_for(allow_documents=allow_documents)
    runner = ToolRunner(root=root, tools={tool.name: tool for tool in tools}, run=run)
    return McpServer(
        name="energatron",
        version=energatron.__version__,
        instructions=INSTRUCTIONS,
        tools=[tool.listing() for tool in tools],
        call=runner.call,
    )


def serve_stdio(root: Path, *, allow_documents: bool) -> None:
    """Serve on stdin and the original stdout until stdin ends."""
    sys.stdout.flush()
    protocol = os.fdopen(os.dup(1), "w", encoding="utf-8", newline="\n")
    os.dup2(2, 1)
    try:
        build_server(root, allow_documents=allow_documents).serve(iter(sys.stdin.readline, ""), protocol)
    finally:
        protocol.close()
