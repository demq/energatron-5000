"""The `Progress` port: what a long command is doing, as it does it (D1 §8.4; owner request M.2).

Services announce their phases with `step()`; the `PipelineRunner` reports each stage as it starts and ends. The CLI
prints these on stderr in human mode; everywhere else they go to `NullProgress`. Messages hold fixed text, stage names,
positions and times, never personal values (D1 §8.9).
"""

from typing import Protocol


class Progress(Protocol):
    def step(self, message: str) -> None:
        """A phase begins, such as "Committing the run"."""
        ...

    def stage_started(self, name: str, position: int, total: int) -> None:
        """Stage `name`, the `position`-th of the `total` stages in scope (1-based, `dvc.yaml` order), started."""
        ...

    def stage_finished(self, name: str, position: int, total: int, *, exit_code: int | None, seconds: float) -> None:
        """Stage `name` ended with `exit_code` (None: stopped before it finished) after `seconds`."""
        ...
