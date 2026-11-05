"""Offer documents: what the `OfferProvider` port returns (D1 §4.6), the fetch log and `offer_manual` (P4 spec §3.3,
§5.3).

`Fetched` is unchanged from P0. The fetch log (`offer_fetch_log`, landed as `offer_cdr_index`) keeps every list page
verbatim, base64-encoded, so its text never holds a literal `"planId"` key and the `offer_cdr` parser can't mistake it
for a plan. It holds no postcode and no identifier: only whether the postcode filter applied. An `offer_manual` is a
tariff the owner writes in the CDR contract shape, so one mapper serves both (`core/cdr_tariff.py`).
"""

import base64
import re
from datetime import UTC, date, datetime, time
from typing import Annotated, Any, ClassVar, Final, Literal, Self

from pydantic import AfterValidator, ConfigDict, Field, model_validator

from energatron.core.canonical import JSONValue
from energatron.core.errors import InvalidInputError
from energatron.core.ids import parse_offer_snapshot_id
from energatron.core.models._common import DatasetId, FrozenModel, Sha256Hex, UtcDatetime, retailer_bases, trimmed_text
from energatron.core.persisted import Persisted, migration


class Fetched(FrozenModel):
    """One HTTP response, kept verbatim. The P4 spec §3.1 fixes the response-header allow-list."""

    url: str
    request_headers: dict[str, str]  # includes x-v
    status: int
    fetched_at: UtcDatetime
    response_headers: dict[str, str]  # a fixed allow-list; never cookies
    body: bytes  # verbatim


# The fetch log (P4 spec §3.3)

type FetchAction = Literal["fetched", "unchanged", "failed"]


def _base(value: str) -> str:
    retailer_bases("base")((value,))
    return value


Base = Annotated[str, AfterValidator(_base)]
"""A CDR base URI, e.g. `globird` (lower-case words joined by `-`)."""


def _snapshot(value: str) -> str:
    parse_offer_snapshot_id(value)  # its ValueError never repeats the value
    return value


SnapshotId = Annotated[str, AfterValidator(_snapshot)]


class LoggedPage(FrozenModel):
    """A `Fetched` list page with its body as base64 (`body_b64`), verbatim."""

    url: str
    request_headers: dict[str, str]
    status: int
    fetched_at: UtcDatetime
    response_headers: dict[str, str]
    body_b64: str

    @classmethod
    def of(cls, fetched: Fetched) -> LoggedPage:
        return cls(
            url=fetched.url,
            request_headers=fetched.request_headers,
            status=fetched.status,
            fetched_at=fetched.fetched_at,
            response_headers=fetched.response_headers,
            body_b64=base64.b64encode(fetched.body).decode("ascii"),
        )

    def body(self) -> bytes:
        return base64.b64decode(self.body_b64, validate=True)


class FetchError(FrozenModel):
    """Why a base or a detail couldn't be fetched: the error code, its message, the HTTP status if any."""

    code: str
    message: str
    status: int | None = None


class RetailerFetch(FrozenModel):
    """One base: its list pages, how many plans it listed and left out (by the first rule that failed), any error."""

    base: Base
    pages: tuple[LoggedPage, ...]
    listed: Annotated[int, Field(ge=0)]
    left_out: dict[str, Annotated[int, Field(ge=1)]]
    error: FetchError | None = None


class SelectedPlan(FrozenModel):
    """A plan that passed the local filter, and what happened to its detail."""

    base: Base
    plan_id: str
    fuel_type: Literal["ELECTRICITY", "GAS"]  # the list item's `fuelType` (P5 spec §4.4)
    last_updated: UtcDatetime
    action: FetchAction
    sha256: Sha256Hex | None = None
    offer_snapshot_id: SnapshotId | None = None


class FetchFilter(FrozenModel):
    """The local filter of one batch (P4 spec §3.2 step 3). `postcode_applied` says whether a postcode was known.
    `distributor` is the electricity distributor, `gas_distributor` the gas one (P5 spec §4.4; `null` in a v1 log)."""

    fuel_types: tuple[str, ...]
    customer_type: str
    distributor: str | None
    gas_distributor: str | None
    postcode_applied: bool


class OfferFetchLog(Persisted):
    """`offer_fetch_log` v2: one `fetch-offers` batch, landed as `offer_cdr_index` (P4 spec §3.3). Version 2 adds
    `filter.gas_distributor` and `selected[].fuel_type` (P5 spec §4.4)."""

    KIND: ClassVar[str] = "offer_fetch_log"
    SCHEMA_VERSION: ClassVar[int] = 2
    kind: Literal["offer_fetch_log"] = "offer_fetch_log"
    schema_version: Literal[2] = 2

    fetch_batch_id: Annotated[str, Field(pattern=r"^fetch-\d{8}T\d{6}Z$")]
    fetched_at: UtcDatetime
    filter: FetchFilter
    retailers: tuple[RetailerFetch, ...]
    selected: tuple[SelectedPlan, ...]


@migration("offer_fetch_log", from_version=1)
def _offer_fetch_log_v1_to_v2(doc: dict[str, JSONValue]) -> dict[str, JSONValue]:
    """Insert `filter.gas_distributor: null` and `fuel_type: ELECTRICITY` in each selected plan: a v1 batch filtered
    and selected electricity only."""
    found: Any = doc.get("filter")
    if isinstance(found, dict):
        found = {**found, "gas_distributor": None}
    selected: Any = doc.get("selected")
    if isinstance(selected, list):
        selected = [{**plan, "fuel_type": "ELECTRICITY"} if isinstance(plan, dict) else plan for plan in selected]
    return {**doc, "schema_version": 2, "filter": found, "selected": selected}


# offer_manual (P4 spec §5.3)

_PLAN_ID: Final = re.compile(r"[A-Za-z0-9@._-]+")


def _plan_id(value: str) -> str:
    if _PLAN_ID.fullmatch(value) is None:
        raise ValueError("plan_id: letters, digits and @._- only")
    return value


class OfferManual(Persisted):
    """`offer_manual` v2: a tariff not in CDR, written in the CDR contract shape: an `electricityContract` or, from
    version 2, a `gasContract` (exactly one; P5 spec §5.3)."""

    model_config = ConfigDict(serialize_by_alias=True, validate_by_name=True)  # merged with Persisted's

    KIND: ClassVar[str] = "offer_manual"
    SCHEMA_VERSION: ClassVar[int] = 2
    kind: Literal["offer_manual"] = "offer_manual"
    schema_version: Literal[2] = 2

    plan_id: Annotated[str, AfterValidator(_plan_id)]
    brand: Annotated[str, AfterValidator(trimmed_text("brand"))]
    display_name: Annotated[str, AfterValidator(trimmed_text("display_name"))]
    published: UtcDatetime  # the quote, publish or bill date; the snapshot's lastUpdated
    source: Literal["bill", "quote", "bpid", "other"]
    evidence: tuple[DatasetId, ...] = ()
    prices_include_gst: Annotated[bool, Field(strict=True)]
    effective_from: date | None = None
    effective_to: date | None = None
    electricity_contract: Annotated[dict[str, JSONValue] | None, Field(alias="electricityContract")] = None
    gas_contract: Annotated[dict[str, JSONValue] | None, Field(alias="gasContract")] = None

    @model_validator(mode="after")
    def _one_contract(self) -> Self:
        if (self.electricity_contract is None) == (self.gas_contract is None):
            raise InvalidInputError(
                "An offer_manual holds exactly one of electricityContract and gasContract.", field="contract"
            )
        return self

    @property
    def fuel(self) -> Literal["ELECTRICITY", "GAS"]:
        return "ELECTRICITY" if self.electricity_contract is not None else "GAS"

    def as_cdr_data(self) -> dict[str, JSONValue]:
        """The document as a CDR plan `data` object, for `map_cdr` or `map_cdr_gas`: residential, no geography."""
        key, contract = (
            ("electricityContract", self.electricity_contract)
            if self.electricity_contract is not None
            else ("gasContract", self.gas_contract)
        )
        data: dict[str, JSONValue] = {
            "planId": self.plan_id,
            "brand": self.brand,
            "displayName": self.display_name,
            "fuelType": self.fuel,
            "customerType": "RESIDENTIAL",
            "lastUpdated": self.published.isoformat(),
            "geography": {"distributors": []},
            key: contract,
        }
        if self.effective_from is not None:
            data["effectiveFrom"] = _midnight_utc(self.effective_from)
        if self.effective_to is not None:
            data["effectiveTo"] = _midnight_utc(self.effective_to)
        return data


def _midnight_utc(day: date) -> str:
    return datetime.combine(day, time(), tzinfo=UTC).isoformat()


@migration("offer_manual", from_version=1)
def _offer_manual_v1_to_v2(doc: dict[str, JSONValue]) -> dict[str, JSONValue]:
    """Only the version changes: every v1 document holds an `electricityContract`."""
    return {**doc, "schema_version": 2}
