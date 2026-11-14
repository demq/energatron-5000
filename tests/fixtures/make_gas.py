"""Synthetic gas bill records (P5 spec §3.2, §13): generated from parameters, never derived from a real bill.

`record()` returns a `gas_bill_record` document whose reads, lines and totals agree, computed the way the `gas_bills`
checks recompute them; `record_text()` writes it as YAML with every number and date quoted. Change one value afterwards
to break a rule. Every value is made up.
"""

import re
from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Final

import yaml

PDF_ID: Final = "gas_bill-20261101T000000-0123abcd"
"""A made-up landed PDF dataset ID."""
HEATING_VALUE: Final = Decimal("38.5000")
PRESSURE_FACTOR: Final = Decimal("1.0100")
USAGE_RATE: Final = Decimal("0.04000")
SUPPLY_RATE: Final = Decimal("0.90000")
GST_RATE: Final = Decimal("0.10")
CENT: Final = Decimal("0.01")


def money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def read(read_date: date, start: int, end: int, *, kind: str = "actual", mj_places: int = 2) -> dict[str, str]:
    """One read whose MJ is `(end - start) x heating value x pressure factor`, printed to `mj_places` places."""
    mj = (Decimal(end - start) * HEATING_VALUE * PRESSURE_FACTOR).quantize(
        Decimal(1).scaleb(-mj_places), rounding=ROUND_HALF_UP
    )
    return {
        "date": read_date.isoformat(),
        "type": kind,
        "start": str(start),
        "end": str(end),
        "heating_value": str(HEATING_VALUE),
        "pressure_factor": str(PRESSURE_FACTOR),
        "mj": str(mj),
    }


def record(
    *,
    period_start: date = date(2026, 9, 1),
    days: int = 30,
    start_index: int = 1000,
    volumes: Sequence[int] = (40,),
    read_types: Sequence[str] | None = None,
    pdf_dataset_id: str = PDF_ID,
    extra_lines: Sequence[Mapping[str, Any]] = (),
    mj_places: int = 2,
    extracted_by: str = "agent:test-model",
) -> dict[str, Any]:
    """A consistent record: one read per volume (m³), the last on the period's end, usage at one rate, supply per day,
    GST at 10% of the subtotal; `extra_lines` (each with its `amount`) are added to the subtotal, or after GST when
    `gst_free`."""
    period_end = period_start + timedelta(days=days - 1)
    kinds = list(read_types or ["actual"] * len(volumes))
    reads: list[dict[str, str]] = []
    index = start_index
    for position, volume in enumerate(volumes):
        when = period_end - timedelta(days=(len(volumes) - 1 - position) * 7)
        reads.append(read(when, index, index + volume, kind=kinds[position], mj_places=mj_places))
        index += volume
    mj = sum((Decimal(found["mj"]) for found in reads), Decimal(0))
    usage = {
        "line": "usage",
        "label": "Gas usage",
        "quantity": str(mj),
        "unit": "MJ",
        "rate": str(USAGE_RATE),
        "amount": str(money(mj * USAGE_RATE)),
    }
    supply = {
        "line": "supply",
        "label": "Supply charge",
        "quantity": str(days),
        "unit": "day",
        "rate": str(SUPPLY_RATE),
        "amount": str(money(Decimal(days) * SUPPLY_RATE)),
    }
    extras = [
        {key: value if key == "gst_free" else str(value) for key, value in extra.items()} for extra in extra_lines
    ]
    lines: list[dict[str, Any]] = [usage, supply, *extras]
    taxed = [line for line in lines if not line.get("gst_free", False)]
    after_gst = [line for line in lines if line.get("gst_free", False)]
    subtotal = sum((Decimal(line["amount"]) for line in taxed), Decimal(0))
    gst = money(subtotal * GST_RATE)
    total = subtotal + gst + sum((Decimal(line["amount"]) for line in after_gst), Decimal(0))
    return {
        "kind": "gas_bill_record",
        "schema_version": 2,
        "pdf_dataset_id": pdf_dataset_id,
        "extracted_by": extracted_by,
        "retailer": "Test Gas Co",
        "plan_name": "Test Gas Saver",
        "issue_date": (period_end + timedelta(days=4)).isoformat(),
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "reads": reads,
        "lines": lines,
        "subtotal": str(subtotal),
        "gst": str(gst),
        "total": str(total),
    }


def record_text(document: Mapping[str, Any]) -> str:
    """The document as YAML, `kind` first, with every number and date quoted so they stay strings."""
    return yaml.dump(dict(document), Dumper=_QuotingDumper, sort_keys=False, allow_unicode=True)


class _QuotingDumper(yaml.SafeDumper):
    """Quotes strings that YAML would otherwise read as a number or a date."""


def _represent_text(dumper: yaml.SafeDumper, value: str) -> yaml.ScalarNode:
    style = '"' if _LOOKS_TYPED.fullmatch(value) else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)


_LOOKS_TYPED: Final = re.compile(r"-?[0-9][0-9.:T-]*")
_QuotingDumper.add_representer(str, _represent_text)


def record_bytes(document: Mapping[str, Any]) -> bytes:
    return record_text(document).encode("utf-8")
