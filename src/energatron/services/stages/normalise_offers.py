"""The `normalise_offers` stage (P4 spec §8): the current electricity offers mapped to tariffs, or excluded with their
reasons. The body is `normalising.normalise` for electricity (P5 spec §8): gas plans are `normalise_gas_offers`'.
"""

from typing import Final

from energatron.services.stages.normalising import ELECTRICITY, RULES_PACKAGE_DATA, normalise
from energatron.services.stages.runner import StageRun, StageSpec


def _run(run: StageRun) -> None:
    normalise(run, ELECTRICITY)


SPEC: Final = StageSpec(
    name="normalise_offers",
    inputs={
        "cdr": "data/raw/offer_cdr",
        "index": "data/raw/offer_cdr_index",
        "manual": "data/raw/offer_manual",
        "rules": "config/offer_rules.local",
    },
    params=("offers", "pricing.gst_rate"),
    outputs={"offers": "data/marts/offers"},
    impl=_run,
    package_data=(RULES_PACKAGE_DATA,),
)
