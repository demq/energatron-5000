"""CDR plan documents built from parameters (P4 spec §14): plan details, list pages and their response bodies.

Everything is made up: plan IDs, brands, distributors, postcodes and prices. The shapes follow the CDR energy standard
(v1.36) as the public API returns them; nothing here is copied from a real response.
"""

import base64
import json
from collections.abc import Mapping, Sequence
from typing import Any, Final

import yaml

ALL_DAYS: Final = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]
DISTRIBUTOR: Final = "Test Networks"
"""Matches `tests/fixtures/params_text.DISTRIBUTOR`."""


def window(start: str, end: str, days: Sequence[str] = ALL_DAYS) -> dict[str, Any]:
    """A `timeOfUse` entry; `end` is the CDR's inclusive end minute (`"22:59"`)."""
    return {"days": list(days), "startTime": start, "endTime": end}


def rate(price: str, volume: float | None = None, unit: str | None = "KWH") -> dict[str, Any]:
    found: dict[str, Any] = {"unitPrice": price}
    if unit is not None:
        found["measureUnit"] = unit
    if volume is not None:
        found["volume"] = volume
    return found


def tou_rate(
    kind: str,
    windows: Sequence[Mapping[str, Any]],
    rates: Sequence[Mapping[str, Any]] = (),
    *,
    price: str = "0.30",
    period: str | None = "P1D",
) -> dict[str, Any]:
    found: dict[str, Any] = {
        "type": kind,
        "rates": [dict(item) for item in rates] or [rate(price)],
        "timeOfUse": [dict(item) for item in windows],
        "displayName": kind.title(),
    }
    if period is not None:
        found["period"] = period
    return found


def standard_tou(peak: str = "0.40", shoulder: str = "0.30", off_peak: str = "0.20") -> list[dict[str, Any]]:
    """Peak 16:00-22:59, off-peak 00:00-06:59, shoulder the rest: a partition of every day."""
    return [
        tou_rate("PEAK", [window("16:00", "22:59")], price=peak),
        tou_rate("OFF_PEAK", [window("00:00", "06:59")], price=off_peak),
        tou_rate("SHOULDER", [window("07:00", "15:59"), window("23:00", "23:59")], price=shoulder),
    ]


def tou_period(
    rates: Sequence[Mapping[str, Any]] | None = None,
    *,
    supply: str = "1.00",
    start: str = "07-01",
    end: str = "06-30",
    demand: Sequence[Mapping[str, Any]] = (),
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    found: dict[str, Any] = {
        "startDate": start,
        "endDate": end,
        "displayName": "Time of Use",
        "rateBlockUType": "timeOfUseRates",
        "timeOfUseRates": [dict(item) for item in (standard_tou() if rates is None else rates)],
        "dailySupplyCharge": supply,
        "dailySupplyChargeType": "SINGLE",
    }
    if demand:
        found["demandCharges"] = [dict(item) for item in demand]
    return found | dict(extra or {})


def single_period(
    rates: Sequence[Mapping[str, Any]] | None = None,
    *,
    supply: str = "1.00",
    period: str | None = "P1D",
    start: str = "01-01",
    end: str = "12-31",
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    single: dict[str, Any] = {"rates": [dict(item) for item in (rates or [rate("0.30")])], "displayName": "Single"}
    if period is not None:
        single["period"] = period
    found: dict[str, Any] = {
        "startDate": start,
        "endDate": end,
        "displayName": "Single Rate",
        "rateBlockUType": "singleRate",
        "singleRate": single,
        "dailySupplyCharge": supply,
        "dailySupplyChargeType": "SINGLE",
    }
    return found | dict(extra or {})


def demand_charge(
    amount: str = "0.50",
    *,
    start: str = "16:00",
    end: str = "20:59",
    days: Sequence[str] = ALL_DAYS,
    measurement: str = "MONTH",
    charge: str = "DAY",
    min_demand: str = "0.00",
    max_demand: str | None = None,
) -> dict[str, Any]:
    found: dict[str, Any] = {
        "amount": amount,
        "startTime": start,
        "endTime": end,
        "days": list(days),
        "measurementPeriod": measurement,
        "chargePeriod": charge,
        "minDemand": min_demand,
        "measureUnit": "KWH",
        "displayName": "Demand",
    }
    if max_demand is not None:
        found["maxDemand"] = max_demand
    return found


def discount(
    kind: str = "CONDITIONAL",
    method: str = "percentOfBill",
    *,
    category: str | None = "PAY_ON_TIME",
    rate_or_amount: str = "0.03",
    end_date: str | None = None,
) -> dict[str, Any]:
    found: dict[str, Any] = {"type": kind, "methodUType": method, "displayName": f"{kind} {method}", "description": ""}
    if category is not None:
        found["category"] = category
    key = "amount" if method == "fixedAmount" else "rate"
    found[method] = (
        {key: rate_or_amount} if method != "percentOverThreshold" else {"rate": rate_or_amount, "usageAmount": "1000"}
    )
    if end_date is not None:
        found["endDate"] = end_date
    return found


def fee(kind: str = "MEMBERSHIP", term: str = "ANNUAL", amount: str = "60.00") -> dict[str, Any]:
    return {"type": kind, "term": term, "amount": amount, "description": "A made-up fee."}


def plan(
    plan_id: str = "TEST1@EME",
    *,
    brand: str = "alpha",
    fuel: str = "ELECTRICITY",
    pricing_model: str = "TIME_OF_USE",
    periods: Sequence[Mapping[str, Any]] | None = None,
    time_zone: str | None = "LOCAL",
    discounts: Sequence[Mapping[str, Any]] = (),
    fees: Sequence[Mapping[str, Any]] = (),
    eligibility: Sequence[str] = (),
    customer_type: str = "RESIDENTIAL",
    distributors: Sequence[str] = (DISTRIBUTOR,),
    postcodes: Sequence[str] | None = None,
    last_updated: str = "2026-10-01T00:00:00Z",
    effective_from: str | None = "2026-07-01T00:00:00Z",
    effective_to: str | None = None,
    contract_extra: Mapping[str, Any] | None = None,
    data_extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """A plan-detail `data` object."""
    if periods is None:
        periods = [tou_period()] if pricing_model.startswith("TIME_OF_USE") else [single_period()]
    contract: dict[str, Any] = {
        "pricingModel": pricing_model,
        "isFixed": False,
        "tariffPeriod": [dict(item) for item in periods],
        "fees": [dict(item) for item in fees],
        "paymentOption": ["DIRECT_DEBIT"],
        "billFrequency": ["P1M"],
        "terms": "Made-up terms.",
    }
    if time_zone is not None:
        contract["timeZone"] = time_zone
    if discounts:
        contract["discounts"] = [dict(item) for item in discounts]
    if eligibility:
        contract["eligibility"] = [{"type": kind, "description": "Made up."} for kind in eligibility]
    contract |= dict(contract_extra or {})
    geography: dict[str, Any] = {"distributors": list(distributors)}
    if postcodes is not None:
        geography["includedPostcodes"] = list(postcodes)
    found: dict[str, Any] = {
        "planId": plan_id,
        "brand": brand,
        "brandName": brand.title(),
        "displayName": f"{brand.title()} Test Plan {plan_id}",
        "type": "MARKET",
        "fuelType": fuel,
        "customerType": customer_type,
        "geography": geography,
        "lastUpdated": last_updated,
        "electricityContract": contract,
    }
    if effective_from is not None:
        found["effectiveFrom"] = effective_from
    if effective_to is not None:
        found["effectiveTo"] = effective_to
    return found | dict(data_extra or {})


def detail_body(data: Mapping[str, Any], *, indent: int | None = None) -> bytes:
    """A plan-detail response body: `{data, links, meta}`, as the API returns it."""
    document = {"data": dict(data), "links": {"self": f"https://cdr.test/plans/{data['planId']}"}, "meta": {}}
    return json.dumps(document, indent=indent).encode("utf-8")


def list_item(data: Mapping[str, Any]) -> dict[str, Any]:
    """The list entry of a plan: its header fields without the contract."""
    keys = (
        "planId",
        "brand",
        "brandName",
        "displayName",
        "type",
        "fuelType",
        "customerType",
        "geography",
        "lastUpdated",
    )
    found = {key: data[key] for key in keys if key in data}
    for key in ("effectiveFrom", "effectiveTo"):
        if key in data:
            found[key] = data[key]
    return found


def list_body(
    plans: Sequence[Mapping[str, Any]], *, next_url: str | None = None, self_url: str = "https://cdr.test/plans"
) -> bytes:
    """A plan-list page: `{data: {plans}, links, meta}`."""
    links: dict[str, Any] = {"self": self_url}
    if next_url is not None:
        links["next"] = next_url
    document = {
        "data": {"plans": [list_item(item) for item in plans]},
        "links": links,
        "meta": {"totalRecords": len(plans), "totalPages": 1},
    }
    return json.dumps(document).encode("utf-8")


# The fetch log (`offer_cdr_index`) and `offer_manual`


def fetch_log(
    *,
    batch: str = "fetch-20261101T000000Z",
    fetched_at: str = "2026-11-01T00:00:00Z",
    retailers: Sequence[tuple[str, Sequence[Mapping[str, Any]]]] = (("alpha", ()),),
    selected: Sequence[Mapping[str, Any]] = (),
    postcode_applied: bool = True,
    gas_distributor: str | None = None,
) -> dict[str, Any]:
    """An `offer_fetch_log` v2 document: each `(base, plans)` gets one list page holding `plans`."""
    entries = []
    for base, plans in retailers:
        body = list_body(plans, self_url=f"https://cdr.test/{base}/plans")
        page = {
            "url": f"https://cdr.test/{base}/plans",
            "request_headers": {"x-v": "1"},
            "status": 200,
            "fetched_at": fetched_at,
            "response_headers": {"content-type": "application/json"},
            "body_b64": base64.b64encode(body).decode("ascii"),
        }
        entries.append({"base": base, "pages": [page], "listed": len(plans), "left_out": {}, "error": None})
    return {
        "kind": "offer_fetch_log",
        "schema_version": 2,
        "fetch_batch_id": batch,
        "fetched_at": fetched_at,
        "filter": {
            "fuel_types": ["ELECTRICITY"],
            "customer_type": "RESIDENTIAL",
            "distributor": DISTRIBUTOR,
            "gas_distributor": gas_distributor,
            "postcode_applied": postcode_applied,
        },
        "retailers": entries,
        "selected": [dict(item) for item in selected],
    }


def selected(
    plan_id: str = "TEST1@EME",
    *,
    base: str = "alpha",
    last_updated: str = "2026-10-01T00:00:00Z",
    action: str = "fetched",
    sha256: str | None = None,
    snapshot: str | None = None,
    fuel_type: str = "ELECTRICITY",
) -> dict[str, Any]:
    return {
        "base": base,
        "plan_id": plan_id,
        "fuel_type": fuel_type,
        "last_updated": last_updated,
        "action": action,
        "sha256": sha256,
        "offer_snapshot_id": snapshot,
    }


def fetch_log_body(document: Mapping[str, Any]) -> bytes:
    """As `fetch-offers` writes it: sorted keys, `indent=2`, a trailing newline."""
    return (json.dumps(document, sort_keys=True, indent=2) + "\n").encode("utf-8")


def offer_manual_text(
    plan_id: str = "MANUAL-TEST-TOU",
    *,
    published: str = "2025-07-01T00:00:00Z",
    prices_include_gst: bool = False,
    effective_from: str | None = "2025-07-01",
    effective_to: str | None = None,
    contract: Mapping[str, Any] | None = None,
) -> str:
    """An `offer_manual` v1 YAML document with every date quoted; `contract` defaults to a TOU plan's."""
    document: dict[str, Any] = {
        "kind": "offer_manual",
        "schema_version": 1,
        "plan_id": plan_id,
        "brand": "manualco",
        "display_name": "A made-up retained offer",
        "published": published,
        "source": "bill",
        "evidence": [],
        "prices_include_gst": prices_include_gst,
        "effective_from": effective_from,
        "effective_to": effective_to,
        "electricityContract": dict(contract or plan()["electricityContract"]),
    }
    return yaml.safe_dump(document, sort_keys=False)  # PyYAML quotes every date-like string
