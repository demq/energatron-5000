"""The P4 marts: what `normalise_offers` writes to `marts/offers/` (P4 spec §8), and later the cost tables.

`offers.parquet` (kind `offer_status`) has one row per considered offer; its pyarrow schema lives in
`services/stages/tables.py` (core holds no pyarrow, D1 §3). `tariffs.json` (`offer_tariffs`) holds the tariffs the
engine prices and `summary.json` (`offers_summary`) the counts and checks. Public offer data only: no identifier and no
household figure.
"""

from datetime import date
from typing import Annotated, ClassVar, Final, Literal

from pydantic import Field

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
