"""`energatron usage` (P9 spec §3.2): the usage behind the costs, as aggregates an agent can read.

Read-only. Electricity comes from the usage profile's `summary.json`, `monthly.parquet` and `tou_monthly.parquet`; gas
from the gas profile's `summary.json` and `monthly.parquet`. A missing summary leaves its fuel `null` and names the
file in `missing`; a summary or table that doesn't validate → `E_INVALID_INPUT` naming the file, never a value. Never
daily or interval rows, the heatmap or the hourly profile.
"""

import json
import math
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import ClassVar, Final, Literal

import pyarrow as pa

from energatron.core.errors import InvalidInputError, SchemaTooNewError, UsageError
from energatron.core.models import gas as gas_models
from energatron.core.models import usage as usage_models
from energatron.core.models._common import FrozenModel
from energatron.core.models.gas import GasProfileSummary
from energatron.core.models.usage import DstDays, UsageSummary, WindowSetUsed
from energatron.core.persisted import Persisted
from energatron.services.context import ServiceContext, ServiceResult
from energatron.services.stages.tables import (
    GAS_MONTH_SCHEMA,
    SUMMARY_FILENAME,
    USAGE_MONTHLY_SCHEMA,
    USAGE_TOU_MONTHLY_SCHEMA,
    load_table,
)

USAGE_DIR: Final = "data/marts/usage_profile"
GAS_DIR: Final = "data/marts/gas_profile"
DEFAULT_MONTHS: Final = 13
MAX_MONTHS: Final = 60


class MonthUsage(FrozenModel):
    month: str
    kwh: float | None
    avg_daily_kwh: float | None
    coverage: float | None
    tou: dict[str, float | None]


class SuffixUsage(FrozenModel):
    first_date: date
    last_date: date
    covered_months: int
    window_start: str | None
    window_end: str | None
    window_coverage: float | None
    avg_daily_kwh: float | None
    base_load_kw: float | None
    peak_demand_kw: float | None
    load_factor: float | None
    tou_shares: dict[str, float]
    months: list[MonthUsage]


class ElectricityUsage(FrozenModel):
    suffixes: dict[str, SuffixUsage]
    excluded_suffixes: list[str]
    window_sets_used: list[WindowSetUsed]
    dst_days: DstDays
    checks: dict[str, bool]


class GasMonth(FrozenModel):
    month: str
    mj: Decimal


class GasUsage(FrozenModel):
    coverage_start: date | None
    coverage_end: date | None
    gaps: int
    estimated_periods: int
    reference_start: date | None
    reference_end: date | None
    reference_days: int
    reference_bills: int
    reference_mj: Decimal
    partial_year: bool
    checks: dict[str, bool]
    months: list[GasMonth]


class UsageResult(ServiceResult):
    """`usage_result` v1 (P9 spec §3.2)."""

    KIND: ClassVar[str] = "usage_result"
    SCHEMA_VERSION: ClassVar[int] = 1
    kind: Literal["usage_result"] = "usage_result"
    schema_version: Literal[1] = 1

    electricity: ElectricityUsage | None
    gas: GasUsage | None
    missing: list[str]


def usage(ctx: ServiceContext, *, months: int = DEFAULT_MONTHS) -> UsageResult:
    """The electricity and gas aggregates, each monthly list cut to the newest `months` months."""
    if not 1 <= months <= MAX_MONTHS:
        raise UsageError(f"--months must be between 1 and {MAX_MONTHS}.", field="months")
    missing: list[str] = []
    return UsageResult(
        electricity=_electricity(ctx.paths.root, months, missing),
        gas=_gas(ctx.paths.root, months, missing),
        missing=missing,
    )


def _electricity(root: Path, months: int, missing: list[str]) -> ElectricityUsage | None:
    summary_path = f"{USAGE_DIR}/{SUMMARY_FILENAME}"
    if not (root / summary_path).exists():
        missing.append(summary_path)
        return None
    summary = read_document(root, summary_path, UsageSummary)
    monthly = _rows(
        root,
        f"{USAGE_DIR}/monthly.parquet",
        USAGE_MONTHLY_SCHEMA,
        usage_models.MONTHLY_KIND,
        usage_models.MONTHLY_VERSION,
    )
    tou = _rows(
        root,
        f"{USAGE_DIR}/tou_monthly.parquet",
        USAGE_TOU_MONTHLY_SCHEMA,
        usage_models.TOU_MONTHLY_KIND,
        usage_models.TOU_MONTHLY_VERSION,
    )
    suffixes: dict[str, SuffixUsage] = {}
    for suffix, item in summary.suffixes.items():
        labels: dict[str, dict[str, float | None]] = {}
        for row in tou:
            if row["suffix"] == suffix:
                labels.setdefault(str(row["month"]), {})[str(row["label"])] = _number(row["kwh"])
        rows = [row for row in monthly if row["suffix"] == suffix][-months:]
        suffixes[suffix] = SuffixUsage(
            first_date=item.first_date,
            last_date=item.last_date,
            covered_months=item.covered_months,
            window_start=item.window_start,
            window_end=item.window_end,
            window_coverage=item.window_coverage,
            avg_daily_kwh=item.avg_daily_kwh,
            base_load_kw=item.base_load_kw,
            peak_demand_kw=item.peak_demand_kw,
            load_factor=item.load_factor,
            tou_shares=item.tou_shares,
            months=[
                MonthUsage(
                    month=str(row["month"]),
                    kwh=_number(row["kwh"]),
                    avg_daily_kwh=_number(row["avg_daily_kwh"]),
                    coverage=_number(row["coverage"]),
                    tou=labels.get(str(row["month"]), {}),
                )
                for row in rows
            ],
        )
    return ElectricityUsage(
        suffixes=suffixes,
        excluded_suffixes=summary.excluded_suffixes,
        window_sets_used=summary.window_sets_used,
        dst_days=summary.dst_days,
        checks=summary.checks,
    )


def _gas(root: Path, months: int, missing: list[str]) -> GasUsage | None:
    summary_path = f"{GAS_DIR}/{gas_models.GAS_SUMMARY_FILENAME}"
    if not (root / summary_path).exists():
        missing.append(summary_path)
        return None
    summary = read_document(root, summary_path, GasProfileSummary)
    rows = _rows(
        root,
        f"{GAS_DIR}/{gas_models.GAS_MONTHLY_FILENAME}",
        GAS_MONTH_SCHEMA,
        gas_models.GAS_MONTH_KIND,
        gas_models.GAS_MONTH_VERSION,
    )
    return GasUsage(
        coverage_start=summary.coverage_start,
        coverage_end=summary.coverage_end,
        gaps=len(summary.gaps),
        estimated_periods=summary.estimated_periods,
        reference_start=summary.reference_start,
        reference_end=summary.reference_end,
        reference_days=summary.reference_days,
        reference_bills=summary.reference_bills,
        reference_mj=summary.reference_mj,
        partial_year=summary.partial_year,
        checks=summary.checks,
        months=[GasMonth(month=str(row["month"]), mj=Decimal(str(row["mj"]))) for row in rows[-months:]],
    )


def read_document[T: Persisted](root: Path, relative: str, model: type[T]) -> T:
    """A workspace JSON document of `model`'s kind; one that doesn't validate → `E_INVALID_INPUT` naming the file."""
    try:
        return model.from_document(json.loads((root / relative).read_bytes()))
    except OSError, ValueError, InvalidInputError, SchemaTooNewError:
        raise InvalidInputError("A workspace document isn't valid.", file=relative) from None


def _rows(root: Path, relative: str, schema: pa.Schema, kind: str, version: int) -> list[dict[str, object]]:
    """A mart table's rows, in file order, checked against its kind, version and columns."""
    try:
        table = load_table(root / relative, schema, kind, version, input=relative)
    except InvalidInputError:
        raise InvalidInputError("A workspace table isn't valid.", file=relative) from None
    return table.to_pylist()


def _number(value: object) -> float | None:
    """A table's float, with null and NaN as None."""
    if value is None:
        return None
    number = float(value)  # type: ignore[arg-type]
    return None if math.isnan(number) else number
