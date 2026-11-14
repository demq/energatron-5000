"""Running one tool call (P9 spec §5.3): validate the arguments, run the CLI command as a child, return its envelope.

The child is `python -m energatron <command> … --json --workspace <root> -- <values>`, run in the workspace with
stdout and stderr captured and the tool's timeout, so the agent gets exactly what the command line prints: the same
envelope, redaction, lock and exit code. When the child prints no single valid envelope, or times out (it is killed),
the server builds an `E_SUBPROCESS` envelope itself. The child's stderr is logged at DEBUG only, redacted by the
invocation's formatter.
"""

import json
import logging
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Final

from pydantic import ValidationError

import energatron
from energatron.adapters.cli.output import CliError, CliResult
from energatron.adapters.mcp.tools import CLI_COMMAND, LandFile, Tool
from energatron.core.errors import EnergatronError, SubprocessError, UsageError

_log = logging.getLogger(__name__)

LANDING: Final = "data/landing"

type Run = Callable[[Sequence[str], Path, bytes, float], tuple[int | None, bytes, bytes]]
"""`(argv, cwd, stdin, timeout) -> (exit code or None on timeout, stdout, stderr)`."""


def run_child(argv: Sequence[str], cwd: Path, stdin: bytes, timeout: float) -> tuple[int | None, bytes, bytes]:
    """Run `argv` with captured output; a timeout kills the child and returns `None` as its code."""
    try:
        done = subprocess.run(list(argv), cwd=cwd, input=stdin, capture_output=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        return None, exc.stdout or b"", exc.stderr or b""
    return done.returncode, done.stdout, done.stderr


@dataclass(frozen=True)
class ToolRunner:
    """Runs the tools of one server against one workspace."""

    root: Path
    tools: dict[str, Tool]
    run: Run = run_child
    python: str = sys.executable

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """The MCP tool result for one call: the envelope as text and as structured content."""
        tool = self.tools[name]
        envelope = self._envelope(tool, arguments)
        return {
            "content": [{"type": "text", "text": json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))}],
            "structuredContent": envelope,
            "isError": not envelope.get("ok", False),
        }

    def _envelope(self, tool: Tool, arguments: dict[str, Any]) -> dict[str, Any]:
        command = CLI_COMMAND[tool.name]
        try:
            parsed = tool.model.model_validate(arguments)
            if isinstance(parsed, LandFile):
                check_landing_path(self.root, parsed.path)
        except ValidationError as exc:
            field = ".".join(str(part) for part in exc.errors()[0]["loc"]) or "arguments"
            return envelope_for(command, UsageError(f"Invalid arguments: check {field}.", field=field))
        except EnergatronError as exc:
            return envelope_for(command, exc)
        words, values = tool.argv(parsed)
        argv = [self.python, "-m", "energatron", *words, "--json", "--workspace", str(self.root), "--", *values]
        stdin = tool.stdin(parsed) if tool.stdin else b""
        code, out, err = self.run(argv, self.root, stdin, tool.timeout)
        if err:
            _log.debug("tool %s stderr: %s", tool.name, err.decode("utf-8", errors="replace").strip())
        if code is None:
            return envelope_for(command, SubprocessError("The tool timed out.", tool=tool.name, reason="timeout"))
        found = parse_envelope(out)
        if found is None:
            return envelope_for(
                command, SubprocessError("The tool printed no result.", tool=tool.name, reason="no_envelope")
            )
        return found


def check_landing_path(root: Path, path: str) -> None:
    """`path` must be relative, without `..`, and resolve (symlinks followed) inside `data/landing/` (P9 spec §6.2)."""
    posix = PurePosixPath(path)
    inbox = (root / LANDING).resolve()
    if posix.is_absolute() or ".." in posix.parts or "\\" in path:
        raise UsageError("path must be a workspace-relative path inside data/landing/.", field="path")
    target = (root / posix).resolve()
    if target != inbox and not target.is_relative_to(inbox):
        raise UsageError("path must be a workspace-relative path inside data/landing/.", field="path")


def parse_envelope(out: bytes) -> dict[str, Any] | None:
    """The one `cli_result` envelope on a child's stdout, or None."""
    try:
        text = out.decode("utf-8")
        if text.count("\n") != 1 or not text.endswith("\n"):
            return None
        document = json.loads(text)
        CliResult.from_document(document)
    except ValueError, EnergatronError:
        return None
    return document if isinstance(document, dict) else None


def envelope_for(command: str, error: EnergatronError) -> dict[str, Any]:
    """An error envelope the server builds itself, as the CLI would print it."""
    return CliResult(
        command=command,
        ok=False,
        exit_code=error.exit_code,
        energatron_version=energatron.__version__,
        data=None,
        error=CliError(code=error.code, category=error.category, message=error.message, details=error.details),
    ).model_dump(mode="json")
