"""The `sensitivity` stage (P6 spec §4): every tariff repriced with the usage scaled and in the previous window.

The conditional-discount scenario is already priced by the cost stages; this stage prices, in scenario
`conditions_met`, three variations per fuel: `usage_up` and `usage_down` (the reference usage scaled by
`1 ± recommend.usage_scale_pct / 100`) and `alt_window` (the window before the reference: the `reference_months` covered
months before the electricity window, and the gas periods before the gas reference holding at least
`gas.reference_bills` bills). A fuel its cost stage couldn't price is unavailable. Outputs: `totals.parquet` (annual
figures and ranks per fuel and variation) and `summary.json`. Workspace data, never written to the tool repo.
"""

import json
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Final, cast

import pandas as pd

from energatron.core.canonical import JSONValue
from energatron.core.engine import Bill, Pricing, money, prepare_usage, price
from energatron.core.errors import InvalidInputError, SchemaTooNewError, WarningCode
from energatron.core.gas_engine import GasPeriod, price_gas
from energatron.core.gas_tariff import GasTariff
from energatron.core.models.gas import (
    GAS_PERIOD_KIND,
    GAS_PERIOD_VERSION,
    GAS_PERIODS_FILENAME,
    GAS_SUMMARY_FILENAME,
    GasProfileSummary,
)
from energatron.core.models.params import GasParams, RecommendParams
from energatron.core.models.recommend import (
    SENSITIVITY_SUMMARY_FILENAME,
    SENSITIVITY_TOTAL_KIND,
    SENSITIVITY_TOTAL_SORT,
    SENSITIVITY_TOTAL_VERSION,
    SENSITIVITY_TOTALS_FILENAME,
    SENSITIVITY_VARIATIONS,
    Fuel,
    FuelSensitivity,
    SensitivitySummary,
    VariationInfo,
)
from energatron.core.models.usage import MONTHLY_KIND, MONTHLY_VERSION
from energatron.core.tariff import Tariff
from energatron.services.files import write_json, write_parquet
from energatron.services.stages import costing
from energatron.services.stages.runner import StageRun, StageSpec
from energatron.services.stages.tables import (
    GAS_PERIOD_SCHEMA,
    SENSITIVITY_TOTAL_SCHEMA,
    USAGE_MONTHLY_SCHEMA,
    load_table,
)

SCENARIO: Final = "conditions_met"
DAYS_PER_YEAR: Final = Decimal(365)
MJ_PLACES: Final = Decimal("0.000001")
MONTHLY_FILENAME: Final = "monthly.parquet"


def _run(run: StageRun) -> None:
    pricing = costing.pricing_of(run.params["pricing"])
    recommend = RecommendParams.model_validate(run.params["recommend"])
    gas = GasParams.model_validate(run.params["gas"])
    reference_months = cast(int, run.params["analysis.reference_months"])
    scale = recommend.usage_scale_pct / 100
    factors = {"usage_up": 1 + scale, "usage_down": 1 - scale}
    totals: list[dict[str, object]] = []
    conserved: dict[str, bool] = {}
    fuels: dict[Fuel, FuelSensitivity] = {
        "electricity": _electricity(run, pricing, factors, reference_months, totals, conserved),
        "gas": _gas(run, pricing, factors, gas.reference_bills, totals, conserved),
    }
    _rank(totals)
    energy_scaled = all(conserved.values())

    output = run.output("sensitivity")
    write_parquet(
        pd.DataFrame(totals, columns=list(SENSITIVITY_TOTAL_SCHEMA.names)),
        output / SENSITIVITY_TOTALS_FILENAME,
        kind=SENSITIVITY_TOTAL_KIND,
        schema_version=SENSITIVITY_TOTAL_VERSION,
        sort_by=SENSITIVITY_TOTAL_SORT,
        schema=SENSITIVITY_TOTAL_SCHEMA,
    )
    summary = SensitivitySummary(
        usage_scale_pct=str(recommend.usage_scale_pct), fuels=fuels, checks={"energy_scaled": energy_scaled}
    )
    write_json(output / SENSITIVITY_SUMMARY_FILENAME, cast(JSONValue, summary.model_dump(mode="json")))
    for fuel, found in fuels.items():
        alt = found.variations["alt_window"]
        if not alt.available and alt.reason not in _NOTHING_PRICED:
            run.warn(
                WarningCode.W_SENSITIVITY_UNAVAILABLE,
                f"The {fuel} ranking can't be checked against an earlier window.",
                fuel=fuel,
                variation="alt_window",
                reason=alt.reason,
            )
    run.check("energy_scaled", energy_scaled, failed=sorted(key for key, ok in conserved.items() if not ok))


_NOTHING_PRICED: Final = ("no_usage", "no_tariffs")


def _unavailable(reason: str) -> VariationInfo:
    return VariationInfo(available=False, reason=reason, window_start=None, window_end=None, factor=None)


def _none_available(tariffs: int, reason: str) -> FuelSensitivity:
    return FuelSensitivity(tariffs=tariffs, variations=dict.fromkeys(SENSITIVITY_VARIATIONS, _unavailable(reason)))


def _row(fuel: Fuel, variation: str, snapshot: str, ongoing: Decimal, bill: Bill) -> dict[str, object]:
    return {
        "fuel": fuel,
        "variation": variation,
        "offer_snapshot_id": snapshot,
        "annual_ongoing": ongoing,
        "annual_year1": ongoing - bill.one_off_credit,
        "rank": 0,
    }


def _rank(totals: list[dict[str, object]]) -> None:
    """Rank 1 is the lowest `annual_ongoing` per fuel and variation; ties go to the smaller snapshot ID."""
    groups: dict[tuple[str, str], list[dict[str, object]]] = {}
    for row in totals:
        groups.setdefault((cast(str, row["fuel"]), cast(str, row["variation"])), []).append(row)
    for rows in groups.values():
        ordered = sorted(
            rows, key=lambda row: (cast(Decimal, row["annual_ongoing"]), cast(str, row["offer_snapshot_id"]))
        )
        for position, row in enumerate(ordered, start=1):
            row["rank"] = position


# Electricity


def _electricity(
    run: StageRun,
    pricing: Pricing,
    factors: dict[str, Decimal],
    reference_months: int,
    totals: list[dict[str, object]],
    conserved: dict[str, bool],
) -> FuelSensitivity:
    tariffs = costing.load_tariffs(run.input("offers"), input="offers").tariffs
    local = costing.load_local(run.input("local"), input="local")
    profile = run.input("profile")
    summary = costing.usage_summary(profile)
    suffixes = costing.consumption_suffixes(local)
    suffix = suffixes[0] if len(suffixes) == 1 else None
    window = costing.window(summary, suffix) if suffix is not None else None
    rows = local[local["suffix"] == suffix] if suffix is not None else local.iloc[0:0]
    reference = costing.months(*window) if window is not None else []
    if suffix is None or window is None or not all(costing.has_rows(rows, period) for period in reference):
        return _none_available(len(tariffs), "no_usage")
    if not tariffs:
        return _none_available(0, "no_tariffs")
    variations: dict[str, VariationInfo] = {}
    for variation, factor in factors.items():
        scaled = rows.copy()
        scaled["kwh"] = scaled["kwh"] * float(factor)
        conserved[f"electricity.{variation}"] = _price_elec(variation, tariffs, scaled, reference, pricing, totals)
        variations[variation] = VariationInfo(
            available=True, reason=None, window_start=window[0], window_end=window[1], factor=str(factor)
        )
    alt = _alt_months(profile, suffix, window[0], reference_months)
    if isinstance(alt, str):
        variations["alt_window"] = _unavailable(alt)
    else:
        periods = costing.months(*alt)
        conserved["electricity.alt_window"] = _price_elec("alt_window", tariffs, rows, periods, pricing, totals)
        variations["alt_window"] = VariationInfo(
            available=True, reason=None, window_start=alt[0], window_end=alt[1], factor=None
        )
    return FuelSensitivity(tariffs=len(tariffs), variations=variations)


def _price_elec(
    variation: str,
    tariffs: Sequence[Tariff],
    rows: pd.DataFrame,
    periods: Sequence[tuple[date, date]],
    pricing: Pricing,
    totals: list[dict[str, object]],
) -> bool:
    """Price every tariff over `periods`; whether every bill's usage adds up to the rows' energy."""
    usage = prepare_usage(rows)
    conserved = True
    for tariff in tariffs:
        bill = price(tariff, usage, periods, scenario=SCENARIO, pricing=pricing)
        totals.append(_row("electricity", variation, tariff.offer_snapshot_id, bill.total(), bill))
        conserved = conserved and costing.energy_conserved(bill, rows, periods)
    return conserved


def _alt_months(profile: Path, suffix: str, first: str, count: int) -> tuple[str, str] | str:
    """The `count` covered months of `suffix` before `first` (`YYYY-MM`), as `(first, last)`; or why there are none."""
    table = load_table(
        profile / MONTHLY_FILENAME, USAGE_MONTHLY_SCHEMA, MONTHLY_KIND, MONTHLY_VERSION, input="profile"
    ).to_pylist()
    covered = sorted(
        str(row["month"]) for row in table if row["covered"] and row["suffix"] == suffix and str(row["month"]) < first
    )
    chosen = covered[-count:]
    if len(chosen) < count:
        return "too_little_history"
    if len(costing.months(chosen[0], chosen[-1])) != count:
        return "not_contiguous"
    return chosen[0], chosen[-1]


# Gas


def _gas(
    run: StageRun,
    pricing: Pricing,
    factors: dict[str, Decimal],
    reference_bills: int,
    totals: list[dict[str, object]],
    conserved: dict[str, bool],
) -> FuelSensitivity:
    tariffs = costing.load_gas_tariffs(run.input("gas_offers"), input="gas_offers").tariffs
    profile = run.input("gas_profile")
    summary = _gas_summary(profile)
    rows = sorted(
        load_table(
            profile / GAS_PERIODS_FILENAME, GAS_PERIOD_SCHEMA, GAS_PERIOD_KIND, GAS_PERIOD_VERSION, input="gas_profile"
        ).to_pylist(),
        key=lambda row: row["period_start"],
    )
    reference = [_period(row) for row in rows if row["in_reference"]]
    if not reference or summary.reference_days <= 0 or summary.reference_start is None:
        return _none_available(len(tariffs), "no_usage")
    if not tariffs:
        return _none_available(0, "no_tariffs")
    start, end = reference[0].first.isoformat(), reference[-1].last.isoformat()
    variations: dict[str, VariationInfo] = {}
    for variation, factor in factors.items():
        scaled = [
            GasPeriod(period.first, period.last, (period.mj * factor).quantize(MJ_PLACES)) for period in reference
        ]
        conserved[f"gas.{variation}"] = _price_gas(variation, tariffs, scaled, summary.reference_days, pricing, totals)
        variations[variation] = VariationInfo(
            available=True, reason=None, window_start=start, window_end=end, factor=str(factor)
        )
    earlier = [row for row in rows if not row["in_reference"] and row["period_end"] < summary.reference_start]
    chosen: list[dict[str, object]] = []
    for row in reversed(earlier):
        if sum(cast(int, found["bills"]) for found in chosen) >= reference_bills:
            break
        chosen.insert(0, row)
    if sum(cast(int, found["bills"]) for found in chosen) < reference_bills:
        variations["alt_window"] = _unavailable("too_little_history")
    else:
        periods = [_period(row) for row in chosen]
        days = sum(cast(int, row["days"]) for row in chosen)
        conserved["gas.alt_window"] = _price_gas("alt_window", tariffs, periods, days, pricing, totals)
        variations["alt_window"] = VariationInfo(
            available=True,
            reason=None,
            window_start=periods[0].first.isoformat(),
            window_end=periods[-1].last.isoformat(),
            factor=None,
        )
    return FuelSensitivity(tariffs=len(tariffs), variations=variations)


def _period(row: dict[str, object]) -> GasPeriod:
    return GasPeriod(cast(date, row["period_start"]), cast(date, row["period_end"]), cast(Decimal, row["mj"]))


def _price_gas(
    variation: str,
    tariffs: Sequence[GasTariff],
    periods: Sequence[GasPeriod],
    days: int,
    pricing: Pricing,
    totals: list[dict[str, object]],
) -> bool:
    """Price every tariff over `periods`, annualised by `days`; whether every bill's usage MJ equal the periods'."""
    conserved = True
    for tariff in tariffs:
        bill = price_gas(tariff, periods, scenario=SCENARIO, pricing=pricing)
        annual = money(bill.total() * DAYS_PER_YEAR / days)
        totals.append(_row("gas", variation, tariff.offer_snapshot_id, annual, bill))
        for period in periods:
            billed = sum(
                (line.quantity for line in bill.lines if line.line == "usage" and line.period_start == period.first),
                Decimal(0),
            )
            conserved = conserved and billed == period.mj
    return conserved


def _gas_summary(directory: Path) -> GasProfileSummary:
    try:
        return GasProfileSummary.from_document(json.loads((directory / GAS_SUMMARY_FILENAME).read_bytes()))
    except OSError, ValueError, InvalidInputError, SchemaTooNewError:
        raise InvalidInputError("The gas profile has no valid summary.json.", input="gas_profile") from None


SPEC: Final = StageSpec(
    name="sensitivity",
    inputs={
        "offers": "data/marts/offers",
        "gas_offers": "data/marts/offers_gas",
        "local": "data/interim/elec_interval_local",
        "profile": "data/marts/usage_profile",
        "gas_profile": "data/marts/gas_profile",
    },
    params=("pricing", "analysis.reference_months", "gas", "recommend"),
    outputs={"sensitivity": "data/marts/costs/sensitivity"},
    impl=_run,
)
