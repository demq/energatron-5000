"""The `PipelineRunner` port: stage status and repro (D1 §4.5)."""

from collections.abc import Sequence
from typing import Protocol

from energatron.core.models.pipeline import ReproReport, StageStatus


class PipelineRunner(Protocol):
    def status(self) -> list[StageStatus]:
        """Stale stages only, in `dvc.yaml` order. Writes nothing."""
        ...

    def repro(self, targets: Sequence[str] = ()) -> ReproReport: ...
