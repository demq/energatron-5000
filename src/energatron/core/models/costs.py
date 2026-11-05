"""The P4 marts: what `normalise_offers` writes to `marts/offers/` (P4 spec §8), and later the cost tables.

`offers.parquet` (kind `offer_status`) has one row per considered offer; its pyarrow schema lives in
`services/stages/tables.py` (core holds no pyarrow, D1 §3). `tariffs.json` (`offer_tariffs`) holds the tariffs the
engine prices and `summary.json` (`offers_summary`) the counts and checks. Public offer data only: no identifier and no
household figure.
"""

from datetime import date
from typing import Annotated, ClassVar, Final, Literal

from pydantic import Field

from energatron.core.gas_tariff import GasTariff
from energatron.core.models._common import FrozenModel
from energatron.core.persisted import Persisted
from energatron.core.tariff import Tariff

OFFER_STATUS_KIND: Final = "offer_status"
OFFER_STATUS_VERSION: Final = 1
OFFER_STATUS_COLUMNS: Final = (
    "offer_snapshot_id",
    "plan_id",
    "retailer",
    "brand",
    "display_name",
    "source",
    "pricing_model",
    "status",
    "reasons",
    "approximations",
    "notes",
    "rules",
)
OFFER_STATUS_SORT: Final = ("offer_snapshot_id",)
OFFERS_FILENAME: Final = "offers.parquet"
TARIFFS_FILENAME: Final = "tariffs.json"
OFFERS_SUMMARY_FILENAME: Final = "summary.json"

type OfferStatus = Literal["normalised", "approximate", "excluded"]
_Count = Annotated[int, Field(ge=0)]


class OfferTariffs(Persisted):
    """`offer_tariffs` v1: the tariffs `cost_elec` and `backtest` price, sorted by snapshot ID."""

    KIND: ClassVar[str] = "offer_tariffs"
    SCHEMA_VERSION: ClassVar[int] = 1
    kind: Literal["offer_tariffs"] = "offer_tariffs"
    schema_version: Literal[1] = 1

    as_of: date | None  # the latest fetch log's date; with no fetch log, the newest manual offer's
    tariffs: tuple[Tariff, ...]


class RetailerCounts(FrozenModel):
    normalised: _Count = 0
    approximate: _Count = 0
    excluded: _Count = 0


class OffersSummary(Persisted):
    """`offers_summary` v1: what `normalise_offers` considered and why (P4 spec §8)."""

    KIND: ClassVar[str] = "offers_summary"
    SCHEMA_VERSION: ClassVar[int] = 1
    kind: Literal["offers_summary"] = "offers_summary"
    schema_version: Literal[1] = 1

    as_of: date | None
    statuses: dict[OfferStatus, _Count]
    reasons: dict[str, _Count]  # exclusion reason → offers, each reason of each excluded offer counted once
    retailers: dict[str, RetailerCounts]  # CDR base (or `manual`) → counts
    current_plans: _Count  # plans the latest fetch logs list
    missing_snapshot: _Count  # current plans with no landed snapshot
    manual: _Count
    batches: dict[str, str]  # base → the fetch batch that defined its current plans
    checks: dict[str, bool]


# `cost_elec` (P4 spec §9) and `backtest` (§10)

COST_LINE_KIND: Final = "elec_cost_line"
COST_LINE_VERSION: Final = 1
COST_LINE_COLUMNS: Final = (
    "offer_snapshot_id",
    "scenario",
    "period_start",
    "period_end",
    "line",
    "label",
    "season",
    "block",
    "quantity",
    "unit",
    "rate",
    "amount",
)
COST_LINE_SORT: Final = ("offer_snapshot_id", "scenario", "period_start", "line", "label", "season", "block")
COST_TOTAL_KIND: Final = "elec_cost_total"
COST_TOTAL_VERSION: Final = 1
COST_TOTAL_COLUMNS: Final = (
    "offer_snapshot_id",
    "scenario",
    "status",
    "annual_ongoing",
    "annual_year1",
    "rank",
    "approximations",
)
COST_TOTAL_SORT: Final = ("scenario", "rank")
LINE_ITEMS_FILENAME: Final = "line_items.parquet"
TOTALS_FILENAME: Final = "totals.parquet"
COST_SUMMARY_FILENAME: Final = "summary.json"
SCENARIOS: Final = ("conditions_met", "conditions_not_met")


class CostSummary(Persisted):
    """`elec_cost_summary` v1: what `cost_elec` priced, over which register and window, and the cheapest offers."""

    KIND: ClassVar[str] = "elec_cost_summary"
    SCHEMA_VERSION: ClassVar[int] = 1
    kind: Literal["elec_cost_summary"] = "elec_cost_summary"
    schema_version: Literal[1] = 1

    suffix: str | None
    window_start: str | None  # YYYY-MM, the first billing month
    window_end: str | None
    periods: _Count
    scenarios: tuple[str, ...]
    tariffs: _Count
    cheapest: dict[str, list[str]]  # scenario → the three lowest `annual_ongoing` snapshot IDs, in rank order
    checks: dict[str, bool]


BILL_KIND: Final = "backtest_bill"
BILL_VERSION: Final = 1
BILL_COLUMNS: Final = (
    "period_start",
    "period_end",
    "offer_snapshot_id",
    "invoice_date",
    "invoice_amount",
    "engine_amount",
    "diff",
    "diff_pct",
    "status",
)
BILL_SORT: Final = ("period_start",)
BILLS_FILENAME: Final = "bills.parquet"
BACKTEST_RESULT_FILENAME: Final = "result.json"
type BillStatus = Literal["within", "outside", "unmatched", "no_tariff", "split_tariff", "not_covered"]


class BacktestResult(Persisted):
    """`backtest_result` v1 (P4 spec §10): counts by status, the largest deviation, the tolerance, the checks."""

    KIND: ClassVar[str] = "backtest_result"
    SCHEMA_VERSION: ClassVar[int] = 1
    kind: Literal["backtest_result"] = "backtest_result"
    schema_version: Literal[1] = 1

    periods: _Count
    statuses: dict[BillStatus, _Count]
    max_abs_diff_pct: float | None  # over matched periods; None when none matched
    tolerance_pct: float
    tariffs_configured: _Count
    checks: dict[str, bool]


class GasOfferTariffs(Persisted):
    """`gas_offer_tariffs` v1 (P5 spec §8): the gas tariffs `cost_gas` and `backtest_gas` price, by snapshot ID."""

    KIND: ClassVar[str] = "gas_offer_tariffs"
    SCHEMA_VERSION: ClassVar[int] = 1
    kind: Literal["gas_offer_tariffs"] = "gas_offer_tariffs"
    schema_version: Literal[1] = 1

    as_of: date | None
    tariffs: tuple[GasTariff, ...]


# `cost_gas` (P5 spec §9.3) and `backtest_gas` (§10)

GAS_COST_LINE_KIND: Final = "gas_cost_line"
GAS_COST_LINE_VERSION: Final = 1
"""The columns, schema and sort of `elec_cost_line`."""
GAS_COST_TOTAL_KIND: Final = "gas_cost_total"
GAS_COST_TOTAL_VERSION: Final = 1
GAS_COST_TOTAL_SORT: Final = ("scenario", "rank")


class GasCostSummary(Persisted):
    """`gas_cost_summary` v1: what `cost_gas` priced over which reference periods, and the cheapest offers."""

    KIND: ClassVar[str] = "gas_cost_summary"
    SCHEMA_VERSION: ClassVar[int] = 1
    kind: Literal["gas_cost_summary"] = "gas_cost_summary"
    schema_version: Literal[1] = 1

    reference_start: date | None
    reference_end: date | None
    reference_days: _Count
    partial_year: bool
    periods: _Count
    scenarios: tuple[str, ...]
    tariffs: _Count
    cheapest: dict[str, list[str]]  # scenario → the three lowest `annual_ongoing` snapshot IDs, in rank order
    checks: dict[str, bool]
