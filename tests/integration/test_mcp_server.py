"""`energatron mcp` as a real child over pipes (P9 spec §5, §8): the handshake, tool calls that run the real CLI, a
failing tool, the error outside a workspace, and a protocol stream nothing else can write to."""

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest

from tests.unit.services._read_workspace import REC_ID, make_read_workspace

pytestmark = pytest.mark.integration


def request(ident: int, method: str, params: dict[str, Any] | None = None) -> str:
    message: dict[str, Any] = {"jsonrpc": "2.0", "id": ident, "method": method}
    if params is not None:
        message["params"] = params
    return json.dumps(message)


def converse(argv: list[str], lines: list[str], cwd: Path) -> tuple[int, list[dict[str, Any]], str]:
    env = {key: value for key, value in os.environ.items() if not key.startswith("ENERGATRON_")}
    done = subprocess.run(
        argv, input="".join(line + "\n" for line in lines), capture_output=True, text=True, cwd=cwd, env=env,
        timeout=300, check=False,
    )  # fmt: skip
    answers = [json.loads(line) for line in done.stdout.splitlines()]
    return done.returncode, answers, done.stderr


def test_a_session_against_a_workspace(tmp_path: Path) -> None:
    paths, _ = make_read_workspace(tmp_path)
    lines = [
        request(1, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t"}}),
        json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
        request(2, "tools/list"),
        request(3, "tools/call", {"name": "compare_offers", "arguments": {"fuel": "electricity", "limit": 1}}),
        request(4, "tools/call", {"name": "explain_recommendation", "arguments": {"recommendation_id": REC_ID}}),
        request(
            5,
            "tools/call",
            {"name": "explain_recommendation", "arguments": {"recommendation_id": "REC-20261031-0123abcd"}},
        ),
        request(6, "tools/call", {"name": "read_gas_bill_text", "arguments": {}}),
        request(7, "tools/call", {"name": "land_file", "arguments": {"path": "../private"}}),
    ]
    argv = [sys.executable, "-m", "energatron", "mcp", "--workspace", str(paths.root)]
    code, answers, _ = converse(argv, lines, tmp_path)
    assert code == 0
    assert [answer["id"] for answer in answers] == [1, 2, 3, 4, 5, 6, 7]  # the notification got no answer
    assert answers[0]["result"]["protocolVersion"] == "2025-06-18"
    names = {tool["name"] for tool in answers[1]["result"]["tools"]}
    assert "compare_offers" in names and "read_gas_bill_text" not in names
    compared = answers[2]["result"]
    assert compared["isError"] is False and compared["structuredContent"]["data"]["kind"] == "compare_result"
    assert json.loads(compared["content"][0]["text"]) == compared["structuredContent"]
    assert answers[3]["result"]["structuredContent"]["data"]["recommendation_id"] == REC_ID
    refused = answers[4]["result"]
    assert refused["isError"] and refused["structuredContent"]["error"]["details"]["reason"] == "not_current"
    assert answers[5]["error"]["code"] == -32602  # not offered without --allow-documents
    assert answers[6]["result"]["structuredContent"]["error"]["code"] == "E_USAGE"


def test_the_documents_tool_is_listed_on_request(tmp_path: Path) -> None:
    paths, _ = make_read_workspace(tmp_path)
    argv = [sys.executable, "-m", "energatron", "mcp", "--allow-documents", "--workspace", str(paths.root)]
    code, answers, _ = converse(argv, [request(1, "tools/list")], tmp_path)
    assert code == 0 and "read_gas_bill_text" in {tool["name"] for tool in answers[0]["result"]["tools"]}


def test_outside_a_workspace_it_fails_like_any_command(tmp_path: Path) -> None:
    code, answers, _ = converse([sys.executable, "-m", "energatron", "mcp", "--json"], [], tmp_path)
    assert code == 2 and answers[0]["kind"] == "cli_result" and answers[0]["error"]["code"] == "E_NOT_A_WORKSPACE"


def test_nothing_but_the_protocol_reaches_stdout(tmp_path: Path) -> None:
    """A tool run that prints to stdout and writes to file descriptor 1 directly leaves the protocol stream clean."""
    paths, _ = make_read_workspace(tmp_path)
    script = textwrap.dedent(
        f"""
        import json, os, sys
        from pathlib import Path
        from energatron.adapters.mcp import server

        envelope = {{"kind": "cli_result", "schema_version": 1, "command": "status", "ok": True, "exit_code": 0,
                     "energatron_version": "0", "data": {{}}, "warnings": [], "error": None}}

        def noisy(argv, cwd, stdin, timeout):
            print("noise from print")
            os.write(1, b"noise on fd 1\\n")
            subprocess_noise = os.system("echo noise from a child")
            return 0, (json.dumps(envelope) + "\\n").encode(), b""

        server.run_child = noisy
        original = server.build_server
        server.build_server = lambda root, *, allow_documents: original(
            root, allow_documents=allow_documents, run=noisy
        )
        server.serve_stdio(Path({str(paths.root)!r}), allow_documents=False)
        """
    )
    lines = [request(1, "tools/call", {"name": "status", "arguments": {}}), request(2, "ping")]
    code, answers, stderr = converse([sys.executable, "-c", script], lines, tmp_path)
    assert code == 0
    assert [answer["id"] for answer in answers] == [1, 2]
    assert answers[0]["result"]["isError"] is False
    assert "noise from print" in stderr and "noise on fd 1" in stderr and "noise from a child" in stderr
