"""The `normalise_gas_offers` stage (P5 spec §8): the current gas offers mapped to gas tariffs, or excluded with their
reasons. The same inputs, params and checks as `normalise_offers`; the body is `normalising.normalise` for gas.
"""

from typing import Final

from energatron.services.stages.normalising import GAS, RULES_PACKAGE_DATA, normalise
from energatron.services.stages.runner import StageRun, StageSpec


def _run(run: StageRun) -> None:
    normalise(run, GAS)


SPEC: Final = StageSpec(
    name="normalise_gas_offers",
    inputs={
        "cdr": "data/raw/offer_cdr",
        "index": "data/raw/offer_cdr_index",
        "manual": "data/raw/offer_manual",
        "rules": "config/offer_rules.local",
    },
    params=("offers", "pricing.gst_rate"),
    outputs={"offers": "data/marts/offers_gas"},
    impl=_run,
    package_data=(RULES_PACKAGE_DATA,),
)
