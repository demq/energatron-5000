"""The README is the runbook (P7 spec §10): every `uv run energatron …` line in its fenced blocks must parse.

Each line's command path must exist in the typer app, and every `--option` it uses must be declared for that command
(`--json`, `--workspace`, `-v` and `--debug` are declared by every command). Arguments in `<…>` are placeholders.
"""

import re
import shlex
from pathlib import Path
from typing import Any

import typer

from energatron.adapters.cli.app import app

README = Path(__file__).resolve().parents[2] / "README.md"
_FENCE = re.compile(r"```[a-z]*\n(.*?)```", re.DOTALL)
_COMMAND = re.compile(r"(?:uv run|uvx --from \S+) energatron ")
"""A command line: `uv run energatron …` in a workspace, or `uvx --from <source> energatron …` (the quickstart's)."""


def readme_commands() -> list[list[str]]:
    found: list[list[str]] = []
    for block in _FENCE.findall(README.read_text(encoding="utf-8")):
        for line in block.splitlines():
            line = line.split("#", 1)[0].strip()
            match = _COMMAND.match(line)
            if match:
                found.append(shlex.split(line[match.end() :].replace("<", "'<").replace(">", ">'")))
    return found


def resolve(words: list[str]) -> tuple[Any, list[str]]:
    """The command the words name, and the words after its path (typer's own click types: duck-typed)."""
    command: Any = typer.main.get_command(app)
    rest = list(words)
    while getattr(command, "commands", None) and rest and rest[0] in command.commands:
        command = command.commands[rest.pop(0)]
    assert not getattr(command, "commands", None), f"incomplete command: {' '.join(words)}"
    return command, rest


def test_the_readme_shows_the_p7_commands() -> None:
    paths = {" ".join(words[: 2 if words[0] in {"landing", "schedule", "pipeline", "offers", "gas-bill"} else 1])
             for words in readme_commands()}  # fmt: skip
    assert {"status", "watch", "landing list", "landing clear", "schedule install", "schedule status", "run", "land",
            "fetch-offers", "decide", "gas-bill new", "pipeline sync", "verify"} <= paths  # fmt: skip


def test_the_readme_shows_the_p8_commands() -> None:
    paths = {words[0] for words in readme_commands()}
    assert {"init", "upgrade", "migrate", "diagnose"} <= paths
    assert ["init", "--demo", "~/energatron-demo"] in readme_commands()


def test_the_readme_shows_the_p9_commands() -> None:
    paths = {" ".join(words[:2] if words[0] == "gas-bill" else words[:1]) for words in readme_commands()}
    assert {"mcp", "datasets", "usage", "compare", "explain", "gas-bill check"} <= paths


def test_every_readme_command_parses() -> None:
    commands = readme_commands()
    assert commands
    for words in commands:
        command, rest = resolve(words)
        declared = {option for parameter in command.params for option in parameter.opts}
        options = [word for word in rest if word.startswith("-")]
        unknown = [option for option in options if option not in declared]
        assert not unknown, f"{' '.join(words)}: {unknown}"
        arguments = [parameter for parameter in command.params if parameter.param_type_name == "argument"]
        if arguments and arguments[0].required:
            assert any(not word.startswith("-") for word in rest), f"{' '.join(words)}: missing an argument"
