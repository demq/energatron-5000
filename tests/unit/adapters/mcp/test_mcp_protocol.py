"""MCP protocol (P9 spec §5.2): negotiation, notifications, the JSON-RPC errors, one line per answer, no echo."""

import io
import json
from typing import Any

import pytest

from energatron.adapters.mcp.protocol import SUPPORTED_VERSIONS, McpServer

SECRET = "QAAAVZZZZZ3"  # the synthetic NMI: never echoed back


def server(calls: list[tuple[str, dict[str, Any]]] | None = None) -> McpServer:
    def call(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if calls is not None:
            calls.append((name, arguments))
        return {"content": [{"type": "text", "text": "{}"}], "isError": False}

    return McpServer(
        name="energatron", version="0.0.0", instructions="Use the tools.", tools=[{"name": "status"}], call=call
    )


def ask(message: object) -> dict[str, Any] | None:
    return server().handle(json.dumps(message))


@pytest.mark.parametrize(
    ("asked", "answered"),
    [
        ("2025-06-18", "2025-06-18"),
        ("2024-11-05", "2024-11-05"),
        ("1999-01-01", SUPPORTED_VERSIONS[0]),
        (None, SUPPORTED_VERSIONS[0]),
    ],
)
def test_initialize_negotiates_the_version(asked: str | None, answered: str) -> None:
    answer = ask({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": asked}})
    assert answer is not None and answer["id"] == 1
    result = answer["result"]
    assert result["protocolVersion"] == answered
    assert result["capabilities"] == {"tools": {"listChanged": False}}
    assert result["serverInfo"] == {"name": "energatron", "version": "0.0.0"} and result["instructions"]


def test_notifications_get_no_answer() -> None:
    for method in ("notifications/initialized", "notifications/cancelled", "anything/else"):
        assert ask({"jsonrpc": "2.0", "method": method}) is None


def test_ping_and_tools_list_keep_the_request_id() -> None:
    assert ask({"jsonrpc": "2.0", "id": "a-1", "method": "ping"}) == {"jsonrpc": "2.0", "id": "a-1", "result": {}}
    answer = ask({"jsonrpc": "2.0", "id": 7, "method": "tools/list"})
    assert answer == {"jsonrpc": "2.0", "id": 7, "result": {"tools": [{"name": "status"}]}}


def test_tools_call_runs_the_tool() -> None:
    calls: list[tuple[str, dict[str, Any]]] = []
    line = json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "status"}})
    answer = server(calls).handle(line)
    assert answer is not None and answer["result"]["isError"] is False and calls == [("status", {})]


def _call(params: object) -> str:
    return json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": params})


@pytest.mark.parametrize(
    ("line", "code"),
    [
        (f"{{not json {SECRET}", -32700),
        ("[1, 2]", -32600),
        (json.dumps({"jsonrpc": "1.0", "id": 1, "method": "ping"}), -32600),
        (json.dumps({"jsonrpc": "2.0", "id": 1}), -32600),
        (json.dumps({"jsonrpc": "2.0", "id": True, "method": "ping"}), -32600),
        (json.dumps({"jsonrpc": "2.0", "id": 1, "method": f"x/{SECRET}"}), -32601),
        (_call({"name": SECRET}), -32602),
        (_call({"name": "status", "arguments": []}), -32602),
        (json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping", "params": [SECRET]}), -32602),
    ],
)
def test_errors_have_fixed_messages(line: str, code: int) -> None:
    answer = server().handle(line)
    assert answer is not None and answer["error"]["code"] == code
    assert SECRET not in json.dumps(answer)


def test_serve_answers_one_line_each_and_skips_blank_lines() -> None:
    out = io.StringIO()
    lines = [
        "\n",
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}) + "\n",
        json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n",
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "ping"}) + "\n",
    ]
    server().serve(lines, out)
    answers = out.getvalue().splitlines()
    assert [json.loads(answer)["id"] for answer in answers] == [1, 2]
    assert out.getvalue().count("\n") == 2
