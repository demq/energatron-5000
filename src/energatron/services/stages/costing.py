"""What `cost_elec`, `backtest` and the gas cost stages share (P4 spec §9, §10; P5 spec §9.3, §10): reading the tariffs
and the local table, the rows of `line_items.parquet`, and ranking. A stage may not import another stage (D1 §11.1), so
this module sits in each of their module sets.
"""

import json
from collections.abc import Iterable
from decimal import Decimal
from pathlib import Path
from typing import cast

import pandas as pd

from energatron.core.engine import LineItem, Pricing
from energatron.core.errors import InvalidInputError, SchemaTooNewError
from energatron.core.models.costs import TARIFFS_FILENAME, GasOfferTariffs, OfferTariffs
from energatron.core.models.meter import LOCAL_KIND, LOCAL_VERSION
from energatron.core.models.params import PricingParams
from energatron.services.stages.tables import LOCAL_SCHEMA, load_table

INTERVALS_FILENAME = "intervals.parquet"


def load_tariffs(directory: Path, *, input: str) -> OfferTariffs:
    """`tariffs.json` of `normalise_offers`; missing or not `offer_tariffs` v1 → `E_INVALID_INPUT`."""
    try:
        document = json.loads((directory / TARIFFS_FILENAME).read_bytes())
        return OfferTariffs.from_document(document)
    except OSError, ValueError, InvalidInputError, SchemaTooNewError:
        raise InvalidInputError(
            "The offers mart has no valid tariffs.json.", input=input, file=TARIFFS_FILENAME
        ) from None


def load_gas_tariffs(directory: Path, *, input: str) -> GasOfferTariffs:
    """`tariffs.json` of `normalise_gas_offers`; missing or not `gas_offer_tariffs` v1 → `E_INVALID_INPUT`."""
    try:
        return GasOfferTariffs.from_document(json.loads((directory / TARIFFS_FILENAME).read_bytes()))
    except OSError, ValueError, InvalidInputError, SchemaTooNewError:
        raise InvalidInputError(
            "The gas offers mart has no valid tariffs.json.", input=input, file=TARIFFS_FILENAME
        ) from None


def load_local(directory: Path, *, input: str) -> pd.DataFrame:
    """`localise`'s intervals, checked against their kind, version and columns."""
    return load_table(directory / INTERVALS_FILENAME, LOCAL_SCHEMA, LOCAL_KIND, LOCAL_VERSION, input=input).to_pandas()


def consumption_suffixes(local: pd.DataFrame) -> list[str]:
    """The consumption registers (`E…`) the table holds, sorted (P3 spec §3)."""
    return sorted(suffix for suffix in local["suffix"].unique().tolist() if str(suffix).startswith("E"))


def pricing_of(value: object) -> Pricing:
    params = PricingParams.model_validate(value)
    return Pricing(
        gst_rate=Decimal(params.gst_rate),
        assume_conditions_met=frozenset(params.assume_conditions_met),
        demand_minutes=params.demand_minutes,
    )


def cost_line_records(snapshot: str, scenario: str, lines: Iterable[LineItem]) -> list[dict[str, object]]:
    """One `elec_cost_line` row per engine line."""
    return [
        {
            "offer_snapshot_id": snapshot,
            "scenario": scenario,
            "period_start": line.period_start,
            "period_end": line.period_end,
            "line": line.line,
            "label": line.label,
            "season": line.season,
            "block": line.block,
            "quantity": line.quantity,
            "unit": line.unit,
            "rate": line.rate,
            "amount": line.amount,
        }
        for line in lines
    ]


def rank(totals: list[dict[str, object]], scenarios: Iterable[str]) -> None:
    """Rank 1 is the lowest `annual_ongoing` in its scenario; ties go to the smaller snapshot ID."""
    for scenario in scenarios:
        ordered = sorted(
            (row for row in totals if row["scenario"] == scenario),
            key=lambda row: (cast(Decimal, row["annual_ongoing"]), cast(str, row["offer_snapshot_id"])),
        )
        for position, row in enumerate(ordered, start=1):
            row["rank"] = position


def cheapest(totals: Iterable[dict[str, object]], scenario: str, count: int = 3) -> list[str]:
    ranked = sorted((row for row in totals if row["scenario"] == scenario), key=lambda row: cast(int, row["rank"]))
    return [cast(str, row["offer_snapshot_id"]) for row in ranked[:count]]
