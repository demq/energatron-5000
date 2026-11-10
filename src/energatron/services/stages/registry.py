"""Every registered stage, by name (D1 §9.2). Built from each stage module's `SPEC`.

No stage imports this module (D1 §11.1): it imports every stage, so a stage that did would depend on all of them.
"""

from collections.abc import Iterable, Mapping
from types import MappingProxyType
from typing import Final

from energatron.core.errors import InternalError, UsageError
from energatron.services.stages import (
    backtest,
    backtest_gas,
    bundles,
    catalog_index,
    cost_elec,
    cost_gas,
    gas_bills,
    gas_profile,
    localise,
    merge_interval,
    normalise_gas_offers,
    normalise_offers,
    parse_nem12,
    sensitivity,
    usage_profile,
    usage_report,
    validate_meter,
    validate_timebase,
)
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


STAGES: Final[Mapping[str, StageSpec]] = _build(
    [
        catalog_index.SPEC,
        parse_nem12.SPEC,
        merge_interval.SPEC,
        validate_meter.SPEC,
        validate_timebase.SPEC,
        localise.SPEC,
        usage_profile.SPEC,
        usage_report.SPEC,
        normalise_offers.SPEC,
        cost_elec.SPEC,
        backtest.SPEC,
        gas_bills.SPEC,
        gas_profile.SPEC,
        normalise_gas_offers.SPEC,
        cost_gas.SPEC,
        backtest_gas.SPEC,
        sensitivity.SPEC,
        bundles.SPEC,
    ]
)
"""Every stage, in `dvc.yaml` order."""


def get_stage(name: str) -> StageSpec:
    """The registered stage `name`; an unknown name → `E_USAGE` (D1 §9.3 step 1)."""
    spec = STAGES.get(name)
    if spec is None:
        raise UsageError(f"Unknown stage: {name}. Known stages: {', '.join(STAGES)}.", known=list(STAGES))
    return spec
