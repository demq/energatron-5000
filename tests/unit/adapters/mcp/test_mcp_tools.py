"""The MCP tools (P9 spec §6): command lines, the options the CLI declares, schemas, the landing path, the runner."""

import json
from pathlib import Path
from typing import Any

import pytest
import typer

from energatron.adapters.cli.app import app
from energatron.adapters.mcp.runner import ToolRunner, check_landing_path, parse_envelope
from energatron.adapters.mcp.tools import CLI_COMMAND, TOOLS, tools_for
from energatron.core.errors import UsageError

REC = "REC-20261101-0123abcd"
PDF = "gas_bill-20261101T000000-0123abcd"
SNAP = "SYN-E-1~20261101T000000Z~0123abcd"
OWNER_ONLY = frozenset({"backup", "schedule", "init", "upgrade", "denylist", "stage", "verify", "mi" + "grate"})

SAMPLES: dict[str, tuple[dict[str, Any], list[str], list[str]]] = {
    "status": ({}, ["status"], []),
    "list_datasets": ({"source": "gas_bill", "limit": 5}, ["datasets", "--source", "gas_bill", "--limit", "5"], []),
    "list_inbox": ({}, ["landing", "list"], []),
    "usage_summary": ({"months": 3}, ["usage", "--months", "3"], []),
    "compare_offers": (
        {"fuel": "gas", "limit": 2, "brand": "x"},
        ["compare", "--fuel", "gas", "--limit", "2", "--brand", "x"],
        [],
    ),
    "explain_recommendation": ({"recommendation_id": REC, "options": 2}, ["explain", "--options", "2"], [REC]),
    "offers_diff": ({"plan_id": "SYN-E-1"}, ["offers", "diff", "--plan", "SYN-E-1"], []),
    "check_gas_bill_record": ({"record": {"kind": "gas_bill_record"}}, ["gas-bill", "check"], ["-"]),
    "land_file": (
        {"path": "data/landing/bill.pdf", "source": "gas_bill", "dry_run": True},
        ["land", "--source", "gas_bill", "--dry-run"],
        ["data/landing/bill.pdf"],
    ),
    "fetch_offers": ({"dry_run": True}, ["fetch-offers", "--dry-run"], []),
    "run_pipeline": ({"draft": True}, ["run", "--draft"], []),
    "record_decision": (
        {"recommendation_id": REC, "switch_date": "2026-12-01", "elec": SNAP, "evidence": [PDF], "note": "switched"},
        ["decide", "--switch-date", "2026-12-01", "--elec", SNAP, "--evidence", PDF, "--note", "switched", "--dry-run"],
        [REC],
    ),
    "new_gas_bill_record": (
        {"pdf_dataset_id": PDF, "extracted_by": "agent:m"},
        ["gas-bill", "new", "--extracted-by", "agent:m"],
        [PDF],
    ),
    "write_gas_bill_record": ({"record": {}, "force": True}, ["gas-bill", "write", "--force"], ["-"]),
    "read_gas_bill_text": ({"pdf_dataset_id": PDF}, ["gas-bill", "text"], [PDF]),
}


def test_every_tool_has_a_sample_and_a_command() -> None:
    names = {tool.name for tool in TOOLS}
    assert names == set(SAMPLES) == set(CLI_COMMAND)
    plan = {"list_datasets", "land_file", "fetch_offers", "run_pipeline", "usage_summary", "compare_offers"}
    assert names >= plan | {"explain_recommendation", "record_decision"}  # the plan's eight (§14.5)


@pytest.mark.parametrize("name", sorted(SAMPLES))
def test_command_lines(name: str) -> None:
    tool = next(tool for tool in TOOLS if tool.name == name)
    arguments, words, values = SAMPLES[name]
    assert tool.argv(tool.model.model_validate(arguments)) == (words, values)
    assert words[0] == CLI_COMMAND[name]


def _command(words: list[str]) -> Any:
    """The click command the words name, walking groups (`gas-bill new`, `offers diff`)."""
    command: Any = typer.main.get_command(app)
    for word in words:
        commands = getattr(command, "commands", None)
        if not commands or word not in commands:
            break
        command = commands[word]
    return command


@pytest.mark.parametrize("name", sorted(SAMPLES))
def test_every_option_a_tool_uses_is_declared_by_its_command(name: str) -> None:
    _, words, _ = SAMPLES[name]
    command = _command(words)
    declared = {option for parameter in command.params for option in (*parameter.opts, *parameter.secondary_opts)}
    assert {word for word in words if word.startswith("--")} <= declared
    assert {"--json", "--workspace"} <= declared


def test_schemas_refuse_extra_properties_and_listings_say_what_they_do() -> None:
    for tool in TOOLS:
        listing = tool.listing()
        assert listing["inputSchema"]["type"] == "object" and listing["inputSchema"]["additionalProperties"] is False
        assert listing["annotations"]["readOnlyHint"] is tool.read_only and listing["description"]
        json.dumps(listing)
    write = {tool.name for tool in TOOLS if not tool.read_only}
    assert write == {
        "land_file", "fetch_offers", "run_pipeline", "record_decision", "new_gas_bill_record", "write_gas_bill_record",
    }  # fmt: skip


def test_the_bill_text_tool_needs_the_opt_in() -> None:
    assert "read_gas_bill_text" not in {tool.name for tool in tools_for(allow_documents=False)}
    assert "read_gas_bill_text" in {tool.name for tool in tools_for(allow_documents=True)}


def test_no_tool_runs_an_owner_only_command() -> None:
    assert not set(CLI_COMMAND.values()) & OWNER_ONLY
    assert all(words[:2] != ["landing", "clear"] for _, words, _ in SAMPLES.values())


@pytest.mark.parametrize(
    "path",
    [
        "/etc/hosts",
        "../x",
        "data/landing/../../private/config.toml",
        "data/raw/x",
        "private",
        "data\\landing\\x",
        "data/landing/link",
    ],
)
def test_land_file_is_confined_to_the_inbox(tmp_path: Path, path: str) -> None:
    (tmp_path / "data/landing").mkdir(parents=True)
    (tmp_path / "private").mkdir()
    (tmp_path / "data/landing/link").symlink_to(tmp_path / "private")
    with pytest.raises(UsageError):
        check_landing_path(tmp_path, path)


@pytest.mark.parametrize("path", ["data/landing", "data/landing/bill.pdf", "data/landing/sub/a.csv"])
def test_land_file_accepts_the_inbox(tmp_path: Path, path: str) -> None:
    (tmp_path / "data/landing").mkdir(parents=True)
    check_landing_path(tmp_path, path)


ENVELOPE: dict[str, Any] = {
    "kind": "cli_result", "schema_version": 1, "command": "status", "ok": True, "exit_code": 0,
    "energatron_version": "0", "data": {}, "warnings": [], "error": None,
}  # fmt: skip


class Recorder:
    def __init__(self, code: int | None, out: bytes) -> None:
        self.code, self.out = code, out
        self.calls: list[tuple[list[str], Path, bytes, float]] = []

    def __call__(self, argv: Any, cwd: Path, stdin: bytes, timeout: float) -> tuple[int | None, bytes, bytes]:
        self.calls.append((list(argv), cwd, stdin, timeout))
        return self.code, self.out, b"child stderr"


def runner(tmp_path: Path, recorder: Recorder) -> ToolRunner:
    return ToolRunner(root=tmp_path, tools={tool.name: tool for tool in TOOLS}, run=recorder, python="py")


def line(document: dict[str, Any]) -> bytes:
    return (json.dumps(document) + "\n").encode()


def test_a_call_runs_the_cli_with_json_and_the_workspace_before_the_values(tmp_path: Path) -> None:
    recorder = Recorder(0, line(ENVELOPE))
    result = runner(tmp_path, recorder).call("write_gas_bill_record", {"record": {"kind": "x"}})
    argv, cwd, stdin, timeout = recorder.calls[0]
    assert argv == ["py", "-m", "energatron", "gas-bill", "write", "--json", "--workspace", str(tmp_path), "--", "-"]
    assert (cwd, json.loads(stdin), timeout) == (tmp_path, {"kind": "x"}, 120.0)
    assert result["isError"] is False and result["structuredContent"] == ENVELOPE
    assert json.loads(result["content"][0]["text"]) == ENVELOPE


def test_a_failed_command_is_an_error_result(tmp_path: Path) -> None:
    error = {"code": "E_DIRTY", "category": "state", "message": "m", "details": {}}
    failed = ENVELOPE | {"ok": False, "exit_code": 4, "data": None, "error": error}
    result = runner(tmp_path, Recorder(4, line(failed))).call("run_pipeline", {})
    assert result["isError"] is True and result["structuredContent"]["error"]["code"] == "E_DIRTY"


@pytest.mark.parametrize(
    ("code", "out", "reason"),
    [
        (None, b"", "timeout"),
        (1, b"Traceback\n", "no_envelope"),
        (0, b'{"kind": "cli_result"}\n', "no_envelope"),
        (0, line(ENVELOPE) * 2, "no_envelope"),
    ],
)
def test_a_crash_or_timeout_becomes_e_subprocess(tmp_path: Path, code: int | None, out: bytes, reason: str) -> None:
    result = runner(tmp_path, Recorder(code, out)).call("status", {})
    error = result["structuredContent"]["error"]
    assert result["isError"] and error["code"] == "E_SUBPROCESS"
    assert error["details"] == {"tool": "status", "reason": reason}
    assert result["structuredContent"]["command"] == "status"


@pytest.mark.parametrize(
    ("name", "arguments", "field"),
    [
        ("list_datasets", {"limit": 0}, "limit"),
        ("list_datasets", {"surprise": 1}, "surprise"),
        ("record_decision", {"recommendation_id": "QAAAVZZZZZ3", "switch_date": "2026-12-01"}, "recommendation_id"),
        ("land_file", {"path": "../private/config.toml"}, "path"),
    ],
)
def test_bad_arguments_never_run_and_never_echo(
    tmp_path: Path, name: str, arguments: dict[str, Any], field: str
) -> None:
    recorder = Recorder(0, b"")
    result = runner(tmp_path, recorder).call(name, arguments)
    error = result["structuredContent"]["error"]
    assert result["isError"] and error["code"] == "E_USAGE" and error["details"] == {"field": field}
    assert recorder.calls == [] and "QAAAVZZZZZ3" not in json.dumps(result)


def test_parse_envelope_wants_exactly_one_valid_document() -> None:
    assert parse_envelope(line(ENVELOPE)) == ENVELOPE
    assert parse_envelope(b"\xff\n") is None and parse_envelope(json.dumps(ENVELOPE).encode()) is None


def test_land_file_names_a_source_only_for_an_ambiguous_file() -> None:
    """Bill PDF spec §3: a bill PDF lands by detection; `source` is for `E_AMBIGUOUS_SOURCE`."""
    description = next(tool for tool in TOOLS if tool.name == "land_file").listing()["description"]
    assert "needs source" not in description and "E_AMBIGUOUS_SOURCE" in description
