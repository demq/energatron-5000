"""The P6 kinds (P6 spec §4-§10): sensitivity, bundles, the recommendation, run manifests, decisions and outcomes.

The tables' pyarrow schemas live in `services/stages/tables.py` (core holds no pyarrow, D1 §3). Workspace data: the
documents hold the household's usage-derived costs and are never written to the tool repo.
"""

from datetime import date
from decimal import Decimal
from typing import Annotated, ClassVar, Final, Literal

from pydantic import AwareDatetime, Field

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


# `energatron run` (P6 spec §8)


class ToolState(FrozenModel):
    """The tool a run used, from the live code fingerprint (D1 §11.2)."""

    energatron_version: str
    install: str
    tool_commit: str | None
    tool_dirty: bool


class RunStages(FrozenModel):
    """The `ReproReport` of the run, each list in `dvc.yaml` order."""

    ran: list[str]
    restored: list[str]
    cached: list[str]


class RunManifest(Persisted):
    """`run_manifest` v1 (P6 spec §8.2): `data/runs/<run_id>.json`. Immutable: written once, never rewritten."""

    KIND: ClassVar[str] = "run_manifest"
    SCHEMA_VERSION: ClassVar[int] = 1
    kind: Literal["run_manifest"] = "run_manifest"
    schema_version: Literal[1] = 1

    run_id: str
    draft: bool
    started_at: AwareDatetime
    finished_at: AwareDatetime
    workspace_head: str
    tool: ToolState
    stages: RunStages
    datasets: list[str]
    fetch_batches: list[str]
    recommendation_id: str | None
    action: Action | None
    robust: bool | None


# `energatron decide` (P6 spec §9)


class Choice(FrozenModel):
    """A chosen plan and the projection the recommendation priced it at (base scenario, GST-inclusive)."""

    offer_snapshot_id: str
    annual_ongoing: Decimal
    annual_year1: Decimal


class Decision(Persisted):
    """`decision` v1 (P6 spec §9.2): `data/decisions/<DEC-id>.json`. Immutable: written once, never rewritten."""

    KIND: ClassVar[str] = "decision"
    SCHEMA_VERSION: ClassVar[int] = 1
    kind: Literal["decision"] = "decision"
    schema_version: Literal[1] = 1

    decision_id: str
    decided_at: AwareDatetime
    recommendation_id: str
    run_id: str
    switch_date: date
    choices: dict[Fuel, Choice | None]
    evidence: list[str]
    note: str | None


# `outcomes` (P6 spec §10)

OUTCOME_BILL_KIND: Final = "outcome_bill"
OUTCOME_BILL_VERSION: Final = 1
OUTCOME_BILL_SORT: Final = ("decision_id", "fuel", "period_start")
OUTCOMES_FILENAME: Final = "outcomes.parquet"
OUTCOMES_SUMMARY_FILENAME: Final = "summary.json"


class FuelOutcome(FrozenModel):
    bills: _Count
    actual: Decimal
    projected: Decimal
    diff_pct: float | None  # (actual - projected) / projected, in percent; None when nothing was projected


class OutcomesSummary(Persisted):
    """`outcomes_summary` v1 (P6 spec §10): per decision and fuel, the bills after the switch against the projection."""

    KIND: ClassVar[str] = "outcomes_summary"
    SCHEMA_VERSION: ClassVar[int] = 1
    kind: Literal["outcomes_summary"] = "outcomes_summary"
    schema_version: Literal[1] = 1

    decisions: list[str]
    outcomes: dict[str, dict[Fuel, FuelOutcome]]
    checks: dict[str, bool]
