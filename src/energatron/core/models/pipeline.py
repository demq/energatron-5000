"""What the `PipelineRunner` port reports (D1 §4.5). Created in P0 because the port names these models."""

from energatron.core.models._common import FrozenModel


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
