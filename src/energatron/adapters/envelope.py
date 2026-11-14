"""The `cli_result` envelope (D1 §8.5) and its redaction (§8.7), shared by the driving adapters (P10 spec §3.1).

The CLI prints it, the MCP server passes on the CLI's, and the HTTP API answers it: one document per outcome. `data` is
the result model dumped with `model_dump(mode="json")`; null on failure except for exit code 8 (`E_PARTIAL`), where it
holds the per-item results.
"""

import json
from collections.abc import Sequence
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

import energatron
from energatron.core.canonical import JSONValue
from energatron.core.errors import Category, EnergatronError, ResultWarning
from energatron.core.persisted import Persisted
from energatron.core.privacy import DenyEntry, redact_json


class CliError(BaseModel):
    """`error` in the envelope: `{"code", "category", "message", "details"}` (D1 §8.5)."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True, allow_inf_nan=False)

    code: str
    category: Category
    message: str
    details: dict[str, JSONValue] = Field(default_factory=dict)


class CliResult(Persisted):
    """`cli_result` v1, the JSON envelope (D1 §8.5). `data` is the service's result model, dumped; null on failure
    except for exit code 8, where it holds the per-item results. `warnings` are the result's (D1 §7)."""

    KIND: ClassVar[str] = "cli_result"
    SCHEMA_VERSION: ClassVar[int] = 1
    kind: Literal["cli_result"] = "cli_result"
    schema_version: Literal[1] = 1

    command: str | None
    ok: bool
    exit_code: int = Field(ge=0)
    energatron_version: str
    data: dict[str, JSONValue] | None
    warnings: tuple[ResultWarning, ...] = ()
    error: CliError | None


def cli_error(error: EnergatronError) -> CliError:
    return CliError(code=error.code, category=error.category, message=error.message, details=error.details)


def success(
    command: str | None,
    result: Persisted,
    warnings: Sequence[ResultWarning],
    error: EnergatronError | None = None,
) -> CliResult:
    """A result's envelope; with `error` (`E_PARTIAL`), `ok` is false and `data` still holds the result."""
    return CliResult(
        command=command,
        ok=error is None,
        exit_code=0 if error is None else error.exit_code,
        energatron_version=energatron.__version__,
        data=result.model_dump(mode="json"),
        warnings=tuple(warnings),
        error=None if error is None else cli_error(error),
    )


def failure(command: str | None, error: EnergatronError) -> CliResult:
    """An error's envelope: `data` null."""
    return CliResult(
        command=command,
        ok=False,
        exit_code=error.exit_code,
        energatron_version=energatron.__version__,
        data=None,
        error=cli_error(error),
    )


def redacted(envelope: CliResult, denylist: Sequence[DenyEntry]) -> dict[str, Any]:
    """The dumped envelope, redacted as data (D1 §8.7): every string, key and number holding a denylist entry."""
    document = redact_json(envelope.model_dump(mode="json"), tuple(denylist))
    assert isinstance(document, dict)
    return document


def dumps(document: dict[str, Any]) -> str:
    """One line of JSON, as the CLI prints it (no trailing newline)."""
    return json.dumps(document, ensure_ascii=False, allow_nan=False)
