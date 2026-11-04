"""`offers diff`: what changed between each plan's two newest landed snapshots (P4 spec §11). Read-only; no lock.

Both snapshots are mapped with `map_cdr` (no rules, a household that meets every eligibility rule and has a
controlled-load register, so as much as possible maps) at the newer snapshot's `lastUpdated` date, then compared field
by field. An excluded side compares as its status and reasons. Public offer data only.
"""

import json
from collections.abc import Mapping
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import ClassVar, Literal, cast

from energatron.core.canonical import JSONValue
from energatron.core.cdr_tariff import Excluded, Household, Plan, map_cdr
from energatron.core.errors import UsageError
from energatron.core.models._common import FrozenModel
from energatron.core.models.catalog import CatalogRecord, SourceType
from energatron.services.context import ServiceContext, ServiceResult

_NEUTRAL = Household(unmet_eligibility=frozenset(), controlled_load=True)
_SKIPPED = frozenset({"offer_snapshot_id"})


class Change(FrozenModel):
    plan_id: str
    path: str
    old: JSONValue
    new: JSONValue
    delta: str | None = None  # the signed difference, when both sides are numbers


class OfferDiffResult(ServiceResult):
    """`offer_diff_result` v1: the plans compared and every field that changed, by plan ID then path."""

    KIND: ClassVar[str] = "offer_diff_result"
    SCHEMA_VERSION: ClassVar[int] = 1
    kind: Literal["offer_diff_result"] = "offer_diff_result"
    schema_version: Literal[1] = 1

    plans_compared: int
    changes: list[Change]


def offer_diff(ctx: ServiceContext, *, plan: str | None = None) -> OfferDiffResult:
    by_plan: dict[str, list[CatalogRecord]] = {}
    for record in ctx.catalog.records(SourceType.OFFER_CDR):
        plan_id = record.extra.get("plan_id")
        if isinstance(plan_id, str):
            by_plan.setdefault(plan_id, []).append(record)
    if plan is not None and plan not in by_plan:
        raise UsageError("No landed offer has that plan ID.", plan=plan)
    changes: list[Change] = []
    compared = 0
    for plan_id in sorted(by_plan) if plan is None else [plan]:
        records = sorted(by_plan[plan_id], key=lambda record: (record.source_ts, record.landed_at))
        if len(records) < 2:
            continue
        older, newer = records[-2:]
        as_of = newer.source_ts.date()
        compared += 1
        old, new = _flat(_mapped(ctx, older, as_of)), _flat(_mapped(ctx, newer, as_of))
        for path in sorted(set(old) | set(new)):
            if old.get(path) != new.get(path):
                changes.append(
                    Change(
                        plan_id=plan_id,
                        path=path,
                        old=old.get(path),
                        new=new.get(path),
                        delta=_delta(old.get(path), new.get(path)),
                    )
                )
    return OfferDiffResult(plans_compared=compared, changes=changes)


def _mapped(ctx: ServiceContext, record: CatalogRecord, as_of: date) -> Mapping[str, JSONValue]:
    data = cast(dict[str, JSONValue], json.loads(ctx.blobs.get(record.stored_path))["data"])
    snapshot = cast(str, record.extra.get("offer_snapshot_id", ""))
    found = map_cdr(data, Plan(offer_snapshot_id=snapshot, retailer="cdr"), _NEUTRAL, as_of=as_of)
    if isinstance(found, Excluded):
        return {"status": "excluded", "reasons": list(found.reasons)}
    return cast(Mapping[str, JSONValue], found.model_dump(mode="json"))


def _flat(value: JSONValue, prefix: str = "") -> dict[str, JSONValue]:
    if isinstance(value, dict):
        out: dict[str, JSONValue] = {}
        for key, item in value.items():
            if prefix == "" and key in _SKIPPED:
                continue
            out |= _flat(item, f"{prefix}.{key}" if prefix else key)
        return out
    if isinstance(value, list) and value and all(isinstance(item, dict) for item in value):
        out = {}
        for index, item in enumerate(value):
            out |= _flat(item, f"{prefix}[{index}]")
        return out
    return {prefix: value}


def _delta(old: JSONValue, new: JSONValue) -> str | None:
    if not isinstance(old, str) or not isinstance(new, str):
        return None
    try:
        difference = Decimal(new) - Decimal(old)
    except InvalidOperation:
        return None
    return f"{difference:+f}"
