"""What the `PipelineRunner` port reports, and the repro log's lines (D1 §4.5).

`StageStatus` and `ReproReport` were created in P0 because the port names them; P1 adds `ReproLogEntry`.
"""

from typing import ClassVar, Literal, Self

from pydantic import Field, model_validator

from energatron.core.models._common import FrozenModel
from energatron.core.persisted import Persisted


class StageStatus(FrozenModel):
    """A stale stage and what changed."""

    name: str
    changed_deps: list[str]  # workspace-relative paths, sorted
    changed_outs: list[str]  # workspace-relative paths, sorted
    changed_params: list[str]  # dotted params keys, sorted
    changed_cmd: bool


class ReproReport(FrozenModel):
    """What a repro did. Each list is in `dvc.yaml` order."""

    ran: list[str]  # stages whose command executed
    restored: list[str]  # stages DVC restored from its run cache without executing
    cached: list[str]  # every other stage in scope: up to date, nothing done


class ReproLogEntry(Persisted):
    """One line `energatron stage` appends to the file `ENERGATRON_REPRO_LOG` names (D1 §4.5).

    `start` has no exit code; `end` has the one `main()` is about to return. The stage name is as the argv pre-scan
    found it, before anything checks it, so it is any non-empty text.
    """

    KIND: ClassVar[str] = "repro_log_entry"
    SCHEMA_VERSION: ClassVar[int] = 1
    kind: Literal["repro_log_entry"] = "repro_log_entry"
    schema_version: Literal[1] = 1

    stage: str = Field(min_length=1)
    event: Literal["start", "end"]
    exit_code: int | None = Field(ge=0, le=255)

    @model_validator(mode="after")
    def _exit_code_on_end_only(self) -> Self:
        if (self.event == "end") != (self.exit_code is not None):
            raise ValueError("an end record has an exit code and a start record has none")
        return self
