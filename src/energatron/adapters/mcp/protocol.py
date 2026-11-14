"""MCP over stdio, the subset `energatron mcp` serves (P9 spec §5.2): UTF-8 JSON-RPC 2.0, one message per line.

`McpServer` answers `initialize`, `ping`, `tools/list` and `tools/call`, ignores notifications, and turns everything
else into a JSON-RPC error with a fixed message: nothing a client sent is ever echoed. Requests are handled one at a
time, in order. What a tool does is the caller's: `call(tool, arguments)` returns the tool result.
"""

import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any, Final, TextIO

SUPPORTED_VERSIONS: Final = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
"""Newest first; `initialize` answers with the client's version when it is one of these, else the newest."""

PARSE_ERROR: Final = -32700
INVALID_REQUEST: Final = -32600
METHOD_NOT_FOUND: Final = -32601
INVALID_PARAMS: Final = -32602

type Message = dict[str, Any]
type ToolCall = Callable[[str, dict[str, Any]], Message]


class McpServer:
    """One server: its info, its tool listing, and the function that runs a tool."""

    def __init__(
        self, *, name: str, version: str, instructions: str, tools: Sequence[Mapping[str, Any]], call: ToolCall
    ) -> None:
        self._info = {"name": name, "version": version}
        self._instructions = instructions
        self._tools = [dict(tool) for tool in tools]
        self._names = frozenset(str(tool["name"]) for tool in tools)
        self._call = call

    def serve(self, lines: Iterable[str], out: TextIO) -> None:
        """Answer each line of `lines` on `out`, one JSON line per answer, until the input ends."""
        for line in lines:
            if not line.strip():
                continue
            answer = self.handle(line)
            if answer is not None:
                out.write(json.dumps(answer, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n")
                out.flush()

    def handle(self, line: str) -> Message | None:
        """The answer to one line, or None for a notification."""
        try:
            message = json.loads(line)
        except ValueError:
            return _error(None, PARSE_ERROR, "Parse error.")
        if not isinstance(message, dict):
            return _error(None, INVALID_REQUEST, "Invalid request.")
        request: dict[str, Any] = message
        ident = request.get("id")
        if "id" in request and not (isinstance(ident, str | int) and not isinstance(ident, bool)):
            return _error(None, INVALID_REQUEST, "Invalid request.")
        method = request.get("method")
        if request.get("jsonrpc") != "2.0" or not isinstance(method, str):
            return _error(ident if "id" in request else None, INVALID_REQUEST, "Invalid request.")
        if ident is None:
            return None  # a notification: never answered
        params = request.get("params", {})
        if not isinstance(params, dict):
            return _error(ident, INVALID_PARAMS, "Invalid params.")
        return self._dispatch(ident, method, params)

    def _dispatch(self, ident: str | int, method: str, params: dict[str, Any]) -> Message:
        if method == "initialize":
            asked = params.get("protocolVersion")
            version = asked if asked in SUPPORTED_VERSIONS else SUPPORTED_VERSIONS[0]
            return _result(
                ident,
                {
                    "protocolVersion": version,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": self._info,
                    "instructions": self._instructions,
                },
            )
        if method == "ping":
            return _result(ident, {})
        if method == "tools/list":
            return _result(ident, {"tools": self._tools})
        if method == "tools/call":
            name = params.get("name")
            arguments = params.get("arguments", {})
            if not isinstance(name, str) or name not in self._names:
                return _error(ident, INVALID_PARAMS, "Unknown tool.")
            if not isinstance(arguments, dict):
                return _error(ident, INVALID_PARAMS, "Invalid params.")
            return _result(ident, self._call(name, arguments))
        return _error(ident, METHOD_NOT_FOUND, "Method not found.")


def _result(ident: str | int, result: Message) -> Message:
    return {"jsonrpc": "2.0", "id": ident, "result": result}


def _error(ident: str | int | None, code: int, message: str) -> Message:
    return {"jsonrpc": "2.0", "id": ident, "error": {"code": code, "message": message}}
