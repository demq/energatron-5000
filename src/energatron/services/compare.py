"""`energatron compare` (P9 spec §3.3): the current recommendation's rankings, from `recommendation.json` alone.

Read-only. The options and the priced plans per fuel, cut to `limit` and optionally to brands containing a text, with
the current plan's rank. Without a recommendation every field is null or empty and `missing` names the file.
"""

from datetime import date
from typing import ClassVar, Final, Literal

from energatron.core.errors import UsageError
from energatron.core.models._common import FrozenModel
from energatron.core.models.recommend import CurrentPair, Fuel, PricedTariff, RecommendedOption
from energatron.services.context import ServiceContext, ServiceResult
from energatron.services.status import RECOMMENDATION, read_recommendation

DEFAULT_LIMIT: Final = 10
MAX_LIMIT: Final = 200
FUELS: Final[tuple[Fuel, ...]] = ("electricity", "gas")


class FuelPlans(FrozenModel):
    total: int
    shown: int
    current_rank: int | None
    items: list[PricedTariff]


class CompareResult(ServiceResult):
    """`compare_result` v1 (P9 spec §3.3)."""

    KIND: ClassVar[str] = "compare_result"
    SCHEMA_VERSION: ClassVar[int] = 1
    kind: Literal["compare_result"] = "compare_result"
    schema_version: Literal[1] = 1

    recommendation_id: str | None
    as_of: date | None
    action: str | None
    current: CurrentPair | None
    options: list[RecommendedOption]
    plans: dict[Fuel, FuelPlans]
    missing: list[str]


def compare(
    ctx: ServiceContext, *, fuel: Fuel | None = None, limit: int = DEFAULT_LIMIT, brand: str | None = None
) -> CompareResult:
    """The first `limit` options and plans of each fuel asked for (both by default)."""
    if not 1 <= limit <= MAX_LIMIT:
        raise UsageError(f"--limit must be between 1 and {MAX_LIMIT}.", field="limit")
    rec = read_recommendation(ctx)
    if rec is None:
        return CompareResult(
            recommendation_id=None,
            as_of=None,
            action=None,
            current=None,
            options=[],
            plans={},
            missing=[RECOMMENDATION],
        )
    needle = brand.casefold() if brand else None

    def matches(*brands: str | None) -> bool:
        return needle is None or any(name is not None and needle in name.casefold() for name in brands)

    options = [
        option
        for option in rec.options
        if matches(
            option.brand,
            option.electricity.brand if option.electricity else None,
            option.gas.brand if option.gas else None,
        )
    ]
    plans: dict[Fuel, FuelPlans] = {}
    for name in (fuel,) if fuel else FUELS:
        priced = [plan for plan in rec.priced.get(name, ()) if matches(plan.brand)]
        current = getattr(rec.current, name)
        current_rank = next(
            (
                plan.rank
                for plan in rec.priced.get(name, ())
                if current and plan.offer_snapshot_id == current.offer_snapshot_id
            ),
            None,
        )
        shown = priced[:limit]
        plans[name] = FuelPlans(total=len(priced), shown=len(shown), current_rank=current_rank, items=shown)
    return CompareResult(
        recommendation_id=rec.recommendation_id,
        as_of=rec.as_of,
        action=rec.action,
        current=rec.current,
        options=options[:limit],
        plans=plans,
        missing=[],
    )
