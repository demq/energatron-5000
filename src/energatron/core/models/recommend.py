"""The P6 kinds (P6 spec §4-§10): sensitivity, bundles, the recommendation, run manifests, decisions and outcomes.

The tables' pyarrow schemas live in `services/stages/tables.py` (core holds no pyarrow, D1 §3). Workspace data: the
documents hold the household's usage-derived costs and are never written to the tool repo.
"""

from datetime import date
from decimal import Decimal
from typing import Annotated, ClassVar, Final, Literal

from pydantic import Field

from energatron.core.canonical import JSONValue
from energatron.core.models._common import FrozenModel
from energatron.core.persisted import Persisted

type Fuel = Literal["electricity", "gas"]
FUELS: Final[tuple[Fuel, ...]] = ("electricity", "gas")
_Count = Annotated[int, Field(ge=0)]

# `sensitivity` (P6 spec §4)

SENSITIVITY_VARIATIONS: Final = ("usage_up", "usage_down", "alt_window")
SENSITIVITY_TOTAL_KIND: Final = "sensitivity_total"
SENSITIVITY_TOTAL_VERSION: Final = 1
SENSITIVITY_TOTAL_SORT: Final = ("fuel", "variation", "rank")
SENSITIVITY_TOTALS_FILENAME: Final = "totals.parquet"
SENSITIVITY_SUMMARY_FILENAME: Final = "summary.json"


class VariationInfo(FrozenModel):
    """Whether a variation was priced for a fuel; why not; the window it priced; the usage factor applied."""

    available: bool
    reason: str | None  # no_usage, no_tariffs, too_little_history, not_contiguous; None when available
    window_start: str | None  # electricity YYYY-MM, gas YYYY-MM-DD
    window_end: str | None
    factor: str | None  # a decimal string for usage_up / usage_down


class FuelSensitivity(FrozenModel):
    tariffs: _Count
    variations: dict[str, VariationInfo]


class SensitivitySummary(Persisted):
    """`sensitivity_summary` v1 (P6 spec §4)."""

    KIND: ClassVar[str] = "sensitivity_summary"
    SCHEMA_VERSION: ClassVar[int] = 1
    kind: Literal["sensitivity_summary"] = "sensitivity_summary"
    schema_version: Literal[1] = 1

    usage_scale_pct: str
    fuels: dict[Fuel, FuelSensitivity]
    checks: dict[str, bool]


# `bundles` (P6 spec §5.3)

BUNDLE_VARIATIONS: Final = ("base", "conditions_not_met", *SENSITIVITY_VARIATIONS)
BUNDLE_OPTION_KIND: Final = "bundle_option"
BUNDLE_OPTION_VERSION: Final = 1
BUNDLE_OPTION_SORT: Final = ("variation", "rank")
BUNDLES_OPTIONS_FILENAME: Final = "options.parquet"
BUNDLES_SUMMARY_FILENAME: Final = "summary.json"


class BundlesSummary(Persisted):
    """`bundles_summary` v1 (P6 spec §5.3)."""

    KIND: ClassVar[str] = "bundles_summary"
    SCHEMA_VERSION: ClassVar[int] = 1
    kind: Literal["bundles_summary"] = "bundles_summary"
    schema_version: Literal[1] = 1

    variations: list[str]  # used, in BUNDLE_VARIATIONS order
    skipped: list[str]
    candidates: dict[Fuel, _Count]
    history_excluded: dict[Fuel, _Count]
    rules: list[str]  # loaded, by id
    rules_applied: list[str]
    options: dict[str, _Count]  # per variation used
    checks: dict[str, bool]


# `recommend` (P6 spec §6, §7)

RECOMMENDATION_FILENAME: Final = "recommendation.json"
RECOMMENDATION_PAGE: Final = "index.html"
type Action = Literal["switch", "stay", "no_current_plan"]
type CurrentSource = Literal["private_config", "backtest"]


class PricedTariff(FrozenModel):
    """A candidate tariff with its base (`conditions_met`) annual figures and rank among the candidates."""

    offer_snapshot_id: str
    plan_id: str
    brand: str
    display_name: str
    source: str
    status: str
    approximations: tuple[str, ...]
    notes: tuple[str, ...]
    annual_ongoing: Decimal
    annual_year1: Decimal
    rank: Annotated[int, Field(ge=1)]


class CurrentPlan(FrozenModel):
    offer_snapshot_id: str
    source: CurrentSource
    annual_ongoing: Decimal
    annual_year1: Decimal


class CurrentPair(FrozenModel):
    electricity: CurrentPlan | None
    gas: CurrentPlan | None
    adjustment: Decimal
    rules: tuple[str, ...]
    annual_ongoing: Decimal | None  # None when neither fuel has a current plan
    annual_year1: Decimal | None


class RecommendedOption(FrozenModel):
    rank: Annotated[int, Field(ge=1)]
    option_key: str
    kind: Literal["independent", "same_brand"]
    brand: str | None
    electricity: PricedTariff | None
    gas: PricedTariff | None
    adjustment: Decimal
    rules: tuple[str, ...]
    annual_ongoing: Decimal
    annual_year1: Decimal
    saving_ongoing: Decimal | None
    saving_year1: Decimal | None


class SensitivityVerdict(FrozenModel):
    variation: str
    top_option_key: str | None
    base_top_rank: int | None
    holds: bool


class ElectricityUsage(FrozenModel):
    window_start: str | None  # YYYY-MM
    window_end: str | None


class GasUsage(FrozenModel):
    reference_start: date | None
    reference_end: date | None
    reference_days: _Count
    partial_year: bool


class RecommendationUsage(FrozenModel):
    electricity: ElectricityUsage | None
    gas: GasUsage | None


class RecommendationInputs(FrozenModel):
    dataset_ids: tuple[str, ...]
    offer_snapshot_ids: tuple[str, ...]
    params_sha256: str
    stage_sha256: str


class Recommendation(Persisted):
    """`recommendation` v1 (P6 spec §7.1). Its ID is `rec_id(as_of, payload)`, the payload being the document without
    `recommendation_id` and `checks` (§7.3)."""

    KIND: ClassVar[str] = "recommendation"
    SCHEMA_VERSION: ClassVar[int] = 1
    kind: Literal["recommendation"] = "recommendation"
    schema_version: Literal[1] = 1

    recommendation_id: str | None  # None only when `as_of` is unknown (the stage then fails `as_of_known`)
    as_of: date | None
    recheck_by: date | None
    action: Action
    robust: bool
    usage: RecommendationUsage
    current: CurrentPair
    options: tuple[RecommendedOption, ...]
    sensitivity: tuple[SensitivityVerdict, ...]
    priced: dict[Fuel, tuple[PricedTariff, ...]]
    inputs: RecommendationInputs
    checks: dict[str, bool]

    def payload(self) -> dict[str, JSONValue]:
        """What the ID hashes: the document without `recommendation_id` and `checks` (§7.3)."""
        dumped: dict[str, JSONValue] = self.model_dump(mode="json")
        return {key: value for key, value in dumped.items() if key not in ("recommendation_id", "checks")}
