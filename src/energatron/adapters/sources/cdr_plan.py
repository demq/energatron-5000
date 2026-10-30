"""`offer_cdr`: a CDR plan-detail response landed by hand (D3 §5.7), version 1.

Public data: no identifiers. `extra.offer_snapshot_id` (D1 §6.3, over `data`) lets `land` treat a re-fetch that
differs only in whitespace, `links` or `meta` as the same snapshot. Pure.
"""

import json
import re
from datetime import datetime
from typing import Final, NoReturn

from energatron.core.canonical import JSONValue
from energatron.core.ids import offer_snapshot_id
from energatron.core.models.catalog import Detection, SourceInfo, SourceType
from energatron.core.text import BOM, fail

_SOURCE: Final = SourceType.OFFER_CDR
_RFC3339: Final = re.compile(r"\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[Zz]|[+-]\d{2}:\d{2})")
_FUEL_TYPES: Final = frozenset({"ELECTRICITY", "GAS", "DUAL"})


def _reject_constant(name: str) -> NoReturn:
    raise ValueError(name)


class CdrPlanParser:
    """The `SourceParser` for `offer_cdr` (D1 §4.3, D3 §5.7)."""

    source: SourceType = _SOURCE
    version: int = 1

    def detect(self, filename: str, head: bytes) -> Detection | None:
        text = head.decode("utf-8", errors="replace").removeprefix(BOM)
        if text.lstrip().startswith("{") and '"planId"' in text and '"lastUpdated"' in text:
            return Detection(source=self.source, evidence=("content",))
        return None

    def inspect(self, filename: str, data: bytes) -> SourceInfo:
        try:
            document = json.loads(data.decode("utf-8").removeprefix(BOM), parse_constant=_reject_constant)
        except UnicodeDecodeError, ValueError, RecursionError:  # RecursionError: nesting too deep to parse
            raise fail(_SOURCE, "The file isn't valid JSON.", "bad_json") from None
        body = document.get("data") if isinstance(document, dict) else None
        if not isinstance(body, dict):
            raise fail(_SOURCE, "The response has no data object.", "no_data")
        plan_id = body.get("planId")
        if not isinstance(plan_id, str) or not plan_id or "~" in plan_id or any(c.isspace() for c in plan_id):
            raise fail(_SOURCE, "data.planId is missing or invalid.", "bad_plan_id")
        last_updated = _rfc3339(body.get("lastUpdated"))
        if last_updated is None:
            raise fail(_SOURCE, "data.lastUpdated is missing or isn't an RFC 3339 time.", "bad_last_updated")
        fuel_type = body.get("fuelType")
        if fuel_type not in _FUEL_TYPES:
            raise fail(_SOURCE, "data.fuelType is missing or invalid.", "bad_fuel_type")
        try:
            snapshot = offer_snapshot_id(plan_id, last_updated, body)
        except ValueError:  # a number canonical JSON can't hold, e.g. an overflowing float
            raise fail(_SOURCE, "The file isn't valid JSON.", "bad_json") from None
        extra: dict[str, JSONValue] = {
            "plan_id": plan_id,
            "fuel_type": fuel_type,
            "offer_snapshot_id": snapshot,
            "source_ts_from": "content",
        }
        return SourceInfo(source_ts=last_updated, coverage=None, extra=extra, identifiers={}, extension=".json")


def _rfc3339(value: object) -> datetime | None:
    if not isinstance(value, str) or _RFC3339.fullmatch(value) is None:
        return None
    try:
        return datetime.fromisoformat(value.upper())  # RFC 3339 allows `t` and `z`; `fromisoformat` doesn't
    except ValueError:
        return None


OFFER_CDR: Final = CdrPlanParser()
"""The registered instance (`registry.PARSERS`)."""
