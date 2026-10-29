"""The `energatron` command line: the typer app and `main()`, its entry point (D1 §8).

`main()` decides the output mode before typer parses anything (D1 §8.5), runs the app with
`standalone_mode=False`, and turns every outcome into exactly one result on stdout: the command's, or an error.
Argument errors become `E_USAGE`, never echoing what was typed; any exception that isn't an `EnergatronError` is a
bug, `E_INTERNAL`, with its traceback on stderr only under `--debug` (D1 §7).
"""

import logging
import os
import sys
from collections.abc import Sequence

import typer

from energatron.adapters.cli.commands import backup, denylist, fingerprint, version
from energatron.adapters.cli.output import (
    CommandOutput,
    Invocation,
    command_name,
    logging_to_stderr,
    prescan,
    write_failure,
    write_success,
)
from energatron.core.errors import EnergatronError, InternalError, UsageError

_log = logging.getLogger(__name__)

app = typer.Typer(
    name="energatron",
    help="Household energy usage analysis and retail offer comparison, local-first.",
    add_completion=False,
    pretty_exceptions_enable=False,
)
app.command("version")(version.version)
app.command("denylist")(denylist.denylist)
app.command("backup")(backup.backup)
app.command("fingerprint")(fingerprint.fingerprint)


def main(argv: Sequence[str] | None = None) -> int:
    """Run one command and return its exit code (D1 §7). `argv` defaults to `sys.argv[1:]`."""
    args = list(sys.argv[1:] if argv is None else argv)
    json_mode, debug = prescan(args, os.environ)
    invocation = Invocation(json_mode=json_mode, debug=debug)
    with logging_to_stderr(invocation):
        try:
            returned = app(args=args, prog_name="energatron", standalone_mode=False, obj=invocation)
        except typer.Abort:
            error: EnergatronError = UsageError("Aborted.")
        except typer.TyperException as exc:  # click's argument errors, raised because standalone_mode is off
            error = usage_error(exc)
        except EnergatronError as exc:
            error = exc
        except Exception as exc:
            if invocation.debug:
                _log.error("internal error", exc_info=True)
            error = InternalError(
                "An internal error occurred; rerun with --debug for the traceback.", exception=type(exc).__name__
            )
        else:
            if isinstance(returned, CommandOutput):
                return write_success(invocation, returned)
            return returned if isinstance(returned, int) else 0  # `--help` returns 0
        return write_failure(invocation, command_name(args, _command_names()), error)


def usage_error(exc: typer.TyperException) -> UsageError:
    """`E_USAGE` for a click argument error, in both output modes.

    click's own text quotes what was typed (an extra argument, a filename, an unknown option, a value), and the
    denylist isn't loaded before parsing, so nothing could redact it (D1 §7, §8.7). The message is therefore
    generic; it names at most an option this CLI declares, also in `details.parameter`.
    """
    candidates = [*getattr(getattr(exc, "param", None), "opts", ()), getattr(exc, "option_name", None)]
    declared = _declared_options()
    name = next((candidate for candidate in candidates if candidate in declared), None)
    if name is None:
        return UsageError("Invalid arguments; see --help.")
    return UsageError(f"Invalid arguments: check {name}; see --help.", parameter=name)


def _command_names() -> frozenset[str]:
    """The names of the app's commands, such as `backup`."""
    return frozenset(getattr(typer.main.get_command(app), "commands", {}))


def _declared_options() -> frozenset[str]:
    """Every option string the app's commands declare, such as `--status` or `-v`."""
    root = typer.main.get_command(app)
    commands = [root, *getattr(root, "commands", {}).values()]
    return frozenset(
        option
        for command in commands
        for parameter in command.params
        for option in (*parameter.opts, *parameter.secondary_opts)
    )
