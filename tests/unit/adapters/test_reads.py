"""The shared read paths (P10 spec §3.2): every CLI command is classified, and each read path matches its command."""

from typing import Any

import pytest
import typer

from energatron.adapters import reads
from energatron.adapters.cli.app import app
from energatron.core.errors import UsageError

# Commands that write, take the lock, serve, or set the machine up: not read paths. A new command must be added to
# `reads.READS` or here (P10 spec §3.2).
NOT_READ = frozenset({
    "init", "demo", "upgrade", "migrate", "denylist", "backup", "fingerprint", "stage", "pipeline sync", "verify",
    "land", "fetch-offers", "gas-bill new", "gas-bill write", "run", "decide", "landing clear", "mcp", "watch",
    "schedule install", "schedule remove",
})  # fmt: skip


def _commands(command: Any, prefix: str = "") -> dict[str, Any]:
    subcommands: dict[str, Any] | None = getattr(command, "commands", None)  # typer's group, whichever click it uses
    if subcommands is not None:
        found: dict[str, Any] = {}
        for name, sub in subcommands.items():
            found |= _commands(sub, f"{prefix} {name}".strip())
        return found
    return {prefix: command}


COMMANDS = _commands(typer.main.get_command(app))


def _words(path: reads.ReadPath) -> tuple[str, list[str]]:
    words = [word for word in path.argv if not word.startswith("-")]
    return " ".join(words), [word for word in path.argv if word.startswith("-")]


def test_every_command_is_a_read_path_or_listed_as_not_one() -> None:
    read = {_words(path)[0] for path in reads.READS}
    assert read <= set(COMMANDS), read - set(COMMANDS)
    unclassified = set(COMMANDS) - read - NOT_READ
    assert not unclassified, f"classify these commands in reads.READS or NOT_READ: {sorted(unclassified)}"
    assert set(COMMANDS) >= NOT_READ, NOT_READ - set(COMMANDS)


def test_names_are_unique_and_in_the_spec_order() -> None:
    assert [path.name for path in reads.READS] == [
        "status", "datasets", "usage", "compare", "explain", "offers/diff", "landing", "backup/status", "diagnose",
        "fingerprint/check", "pipeline/check", "schedule/status", "version", "gas-bill/check", "gas-bill/text",
    ]  # fmt: skip
    assert [path.document for path in reads.READS].count(True) == 1 and reads.GAS_BILL_TEXT.document


@pytest.mark.parametrize("path", reads.READS, ids=lambda path: path.name)
def test_argv_flags_are_declared_by_the_command(path: reads.ReadPath) -> None:
    name, flags = _words(path)
    declared = {option for parameter in COMMANDS[name].params for option in parameter.opts}
    assert set(flags) <= declared
    assert path.command == name.split()[0]


# The CLI parameter each argument field stands for: an option `--<field>` unless named here.
_ARGUMENTS = {"rec_id": "rec_id", "record": "source", "pdf_dataset_id": "pdf_dataset_id"}


def _parameter(command: Any, field: str) -> Any:
    if field in _ARGUMENTS:
        return next(parameter for parameter in command.params if parameter.name == _ARGUMENTS[field])
    return next(parameter for parameter in command.params if f"--{field}" in parameter.opts)


@pytest.mark.parametrize("path", reads.READS, ids=lambda path: path.name)
def test_each_argument_matches_the_cli_parameter(path: reads.ReadPath) -> None:
    command = COMMANDS[_words(path)[0]]
    for field, info in path.args.model_fields.items():
        parameter = _parameter(command, field)
        if type(parameter.type).__name__ == "IntRange":
            bounds: dict[str, Any] = {}
            for item in info.metadata:
                bounds |= {key: getattr(item, key) for key in ("ge", "le") if getattr(item, key, None) is not None}
            assert (bounds["ge"], bounds["le"]) == (parameter.type.min, parameter.type.max), field
            assert info.default == parameter.default, field
        elif not info.is_required():
            assert info.default is None and parameter.default is None, field


@pytest.mark.parametrize(
    ("path", "values", "name"),
    [
        (reads.DATASETS, {"limit": "0"}, "limit"),
        (reads.DATASETS, {"source": "nope-<script>"}, "source"),
        (reads.USAGE, {"months": "lots"}, "months"),
        (reads.COMPARE, {"fuel": "water"}, "fuel"),
        (reads.EXPLAIN, {"options": "99"}, "options"),
        (reads.STATUS, {"extra": "probe-value"}, "extra"),
    ],
)
def test_parse_errors_name_the_field_never_the_value(path: reads.ReadPath, values: dict[str, str], name: str) -> None:
    with pytest.raises(UsageError) as raised:
        path.parse(values)
    assert raised.value.details["parameter"] == name
    for value in values.values():
        assert value not in raised.value.message and value not in str(raised.value.details)


def test_parse_accepts_strings_as_a_query_gives_them() -> None:
    args = reads.COMPARE.parse({"fuel": "gas", "limit": "3", "brand": "x"})
    assert args == reads.CompareArgs(fuel="gas", limit=3, brand="x")
    assert reads.STATUS.parse({}) == reads.NoArgs()


def test_run_refuses_the_wrong_arguments() -> None:
    with pytest.raises(TypeError):
        reads.USAGE.run(None, reads.NoArgs())


def test_version_needs_no_workspace() -> None:
    result = reads.VERSION.run(None, reads.NoArgs())
    assert result.model_dump(mode="json")["kind"] == "version_result"
    assert not reads.VERSION.needs_workspace
