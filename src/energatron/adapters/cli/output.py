"""What the CLI writes, and how (D1 §8.4-§8.7, §8.9): the `cli_result` envelope, human text, redaction, logs, prompts.

- stdout carries the result only: human text, or exactly one JSON document and a newline.
- stderr carries logs, human-mode warnings and errors, and tracebacks (`--debug`).
- Everything written passes through `core.privacy` first: the envelope is redacted as data (`redact_json`), text
  with `redact()`, and log records by the handler's formatter. The denylist is the invocation's: derived from the
  private config plus `private/denylist.txt`, once the workspace is open.
"""

import logging
import sys
from collections.abc import Collection, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TextIO

import typer

from energatron.adapters import envelope
from energatron.adapters.envelope import CliError, CliResult
from energatron.core.errors import ConfirmationRequiredError, EnergatronError
from energatron.core.privacy import DenyEntry, redact
from energatron.services.context import ServiceResult
from energatron.services.stages.runner import StageResult

__all__ = ["CliError", "CliResult"]  # moved to `adapters/envelope.py` (P10 spec §3.1); still importable from here

JSON_ENV = "ENERGATRON_JSON"
"""`ENERGATRON_JSON=1` does what `--json` does (D1 §8.2)."""

_PREFIX = "energatron: "
_LOGGER = "energatron"


@dataclass
class Invocation:
    """One run of the CLI: its output mode, decided before parsing (D1 §8.5), and the denylist used to redact."""

    json_mode: bool
    debug: bool
    denylist: tuple[DenyEntry, ...] = ()


@dataclass(frozen=True)
class CommandOutput:
    """What a command returns to `main()`: its name, its result, and the result as human text."""

    command: str
    result: ServiceResult | StageResult
    text: str
    error: EnergatronError | None = None  # a failure that still has a result: `E_PARTIAL` (exit 8, D1 §8.5)


def prescan(args: Sequence[str], env: Mapping[str, str]) -> tuple[bool, bool]:
    """`(json_mode, debug)` from the raw arguments, before typer parses them (D1 §8.5).

    JSON mode is on when `--json` appears before any `--` separator, or `ENERGATRON_JSON=1`; debug likewise.
    """
    before = list(args[: list(args).index("--")]) if "--" in args else list(args)
    return "--json" in before or env.get(JSON_ENV) == "1", "--debug" in before


def command_name(args: Sequence[str], commands: Collection[str]) -> str | None:
    """The command named in the raw arguments, for an envelope written before parsing succeeds (D1 §8.5).

    The first argument that doesn't start with `-`, but only when it is one of `commands`; otherwise None, so a
    mistyped filename or value never reaches the envelope (owner decision 2026-10-29, for P1.1.1's D1 revision).
    """
    first = next((arg for arg in args if not arg.startswith("-")), None)
    return first if first in commands else None


_VALUE_OPTIONS = frozenset({"--input", "--output", "--workspace"})
"""The options that take a value as the next argument; the stage pre-scan skips that value."""


def stage_name(args: Sequence[str]) -> str | None:
    """The stage named in `energatron stage <name> …`, from the raw arguments, before typer parses them (D1 §4.5).

    The first two arguments that are neither options nor an option's value must be `stage` and the name. Anything
    after a `--` separator doesn't count. Returns None for any other command, or for `stage` with no name.
    """
    before = list(args[: list(args).index("--")]) if "--" in args else list(args)
    positional: list[str] = []
    skip = False
    for arg in before:
        if skip:
            skip = False
        elif arg in _VALUE_OPTIONS:
            skip = True
        elif not arg.startswith("-"):
            positional.append(arg)
            if len(positional) == 2:
                break
    if len(positional) == 2 and positional[0] == "stage" and positional[1]:
        return positional[1]
    return None


# Writing results


def write_success(invocation: Invocation, output: CommandOutput) -> int:
    """Print a command's result: the envelope, or its text on stdout and its warnings on stderr. Returns 0.

    A result that comes with an error (`E_PARTIAL`) is printed the same way, with the error added: in the envelope
    `ok` is false and `data` still holds the result; in human mode the error follows the warnings on stderr. Returns
    the error's exit code then.
    """
    result, warnings, error = output.result, output.result.warnings, output.error
    if invocation.json_mode:
        _write_envelope(invocation, envelope.success(output.command, result, warnings, error))
    else:
        _write(invocation, sys.stdout, output.text)
        for warning in warnings:
            _write(invocation, sys.stderr, f"{_PREFIX}warning {warning.code}: {warning.message}\n")
        if error is not None:
            _write_error_text(invocation, error)
    return 0 if error is None else error.exit_code


def write_failure(invocation: Invocation, command: str | None, error: EnergatronError) -> int:
    """Print an error: the envelope, or `energatron: error <code>: <message>` (and its hint) on stderr."""
    if invocation.json_mode:
        _write_envelope(invocation, envelope.failure(command, error))
    else:
        _write_error_text(invocation, error)
    return error.exit_code


def _write_error_text(invocation: Invocation, error: EnergatronError) -> None:
    _write(invocation, sys.stderr, f"{_PREFIX}error {error.code}: {error.message}\n")
    hint = error.details.get("hint")
    if isinstance(hint, str):
        _write(invocation, sys.stderr, f"{_PREFIX}hint: {hint}\n")


def _write_envelope(invocation: Invocation, document: CliResult) -> None:
    """Redact the dumped envelope as data, then serialise it: one line (D1 §8.7)."""
    sys.stdout.write(envelope.dumps(envelope.redacted(document, invocation.denylist)) + "\n")
    sys.stdout.flush()


def _write(invocation: Invocation, stream: TextIO, text: str) -> None:
    stream.write(redact(text, invocation.denylist))
    stream.flush()


# Logging (D1 §8.9)


class RedactingFormatter(logging.Formatter):
    """Formats a record, traceback included, then redacts it with the invocation's current denylist (D1 §8.7)."""

    def __init__(self, invocation: Invocation) -> None:
        super().__init__(f"{_PREFIX}%(levelname)s %(name)s: %(message)s")
        self._invocation = invocation

    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record), self._invocation.denylist)


class _StderrHandler(logging.Handler):
    """Writes to whatever `sys.stderr` is when the record is emitted."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            sys.stderr.write(self.format(record) + "\n")
            sys.stderr.flush()
        except Exception:
            self.handleError(record)


@contextmanager
def logging_to_stderr(invocation: Invocation) -> Iterator[None]:
    """Send the `energatron` loggers to stderr through the redacting formatter, for one invocation."""
    logger = logging.getLogger(_LOGGER)
    handler = _StderrHandler()
    handler.setFormatter(RedactingFormatter(invocation))
    saved = logger.level, logger.propagate
    logger.addHandler(handler)
    logger.propagate = False
    set_verbosity(invocation, verbose=False)
    try:
        yield
    finally:
        logger.removeHandler(handler)
        logger.setLevel(saved[0])
        logger.propagate = saved[1]


def set_verbosity(invocation: Invocation, *, verbose: bool, debug: bool = False) -> None:
    """WARNING by default, INFO with `-v`, DEBUG with `--debug` (D1 §8.9)."""
    level = logging.DEBUG if debug or invocation.debug else logging.INFO if verbose else logging.WARNING
    logging.getLogger(_LOGGER).setLevel(level)


# Confirmation (D1 §8.6)


def ask(question: str) -> bool:
    """Prompt on stderr; the answer is read from the terminal."""
    return typer.confirm(question, default=False, err=True)


def confirm(invocation: Invocation, question: str, *, yes: bool) -> None:
    """Go on only with `--yes`, or a "yes" typed at a prompt.

    A prompt happens only when stdin and stderr are both terminals and JSON mode is off; otherwise →
    `E_CONFIRMATION_REQUIRED`. A "no" → `E_CONFIRMATION_REQUIRED` too, and nothing is done.
    """
    if yes:
        return
    if invocation.json_mode or not (sys.stdin.isatty() and sys.stderr.isatty()):
        raise ConfirmationRequiredError("This needs confirmation and can't prompt here: rerun with --yes.")
    if not ask(question):
        raise ConfirmationRequiredError("Not confirmed; nothing was done.")
