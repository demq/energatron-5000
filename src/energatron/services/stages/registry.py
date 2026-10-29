"""Every registered stage, by name (D1 §9.2). Built from each stage module's `SPEC`.

No stage imports this module (D1 §11.1): it imports every stage, so a stage that did would depend on all of them.
"""

from collections.abc import Iterable, Mapping
from types import MappingProxyType
from typing import Final

from energatron.core.errors import InternalError, UsageError
from energatron.services.stages import catalog_index
from energatron.services.stages.runner import StageSpec


def _build(specs: Iterable[StageSpec]) -> Mapping[str, StageSpec]:
    stages: dict[str, StageSpec] = {}
    outputs: set[str] = set()
    for spec in specs:
        if spec.name in stages:
            raise InternalError(f"Stage {spec.name} is registered twice.")
        taken = outputs & set(spec.outputs.values())
        if taken:
            raise InternalError(f"Stage {spec.name} writes an output another stage writes.")
        outputs |= set(spec.outputs.values())
        stages[spec.name] = spec
    return MappingProxyType(stages)


STAGES: Final[Mapping[str, StageSpec]] = _build([catalog_index.SPEC])
"""Every stage, in `dvc.yaml` order."""


def get_stage(name: str) -> StageSpec:
    """The registered stage `name`; an unknown name → `E_USAGE` (D1 §9.3 step 1)."""
    spec = STAGES.get(name)
    if spec is None:
        raise UsageError(f"Unknown stage: {name}. Known stages: {', '.join(STAGES)}.", known=list(STAGES))
    return spec
