"""The `gas_profile` stage (P5 spec §7): the gas bills as usage periods, by month, and the reference year.

Input: `gas_bills`' tables. A bill ending on an estimated read is combined with the bill that follows on, so each period
is metered usage; gaps between bills are listed and warned (`W_GAS_GAP`), an overlap fails. The reference year is the
newest periods holding at least `gas.reference_bills` bills; shorter than `gas.full_year_days` days, it is a partial
year. Outputs: `periods.parquet`, `monthly.parquet` and `summary.json`. Workspace data, never written to the tool repo.
"""

from datetime import date
from decimal import Decimal
from typing import Final, cast

import pandas as pd

from energatron.core.canonical import JSONValue
from energatron.core.errors import WarningCode
from energatron.core.gas_bill import BillUsage, UsagePeriod, combine, monthly, reference
from energatron.core.models.gas import (
    GAS_BILL_KIND,
    GAS_BILL_VERSION,
    GAS_BILLS_FILENAME,
    GAS_MONTH_KIND,
    GAS_MONTH_SORT,
    GAS_MONTH_VERSION,
    GAS_MONTHLY_FILENAME,
    GAS_PERIOD_KIND,
    GAS_PERIOD_SORT,
    GAS_PERIOD_VERSION,
    GAS_PERIODS_FILENAME,
    GAS_SUMMARY_FILENAME,
    GasProfileSummary,
)
from energatron.core.models.params import GasParams
from energatron.services.files import write_json, write_parquet
from energatron.services.stages.runner import StageRun, StageSpec
from energatron.services.stages.tables import GAS_BILL_SCHEMA, GAS_MONTH_SCHEMA, GAS_PERIOD_SCHEMA, load_table


def _run(run: StageRun) -> None:
    settings = GasParams.model_validate(run.params["gas"])
    table = load_table(
        run.input("bills") / GAS_BILLS_FILENAME, GAS_BILL_SCHEMA, GAS_BILL_KIND, GAS_BILL_VERSION, input="bills"
    )
    bills = [
        BillUsage(
            period_start=row["period_start"],
            period_end=row["period_end"],
            mj=cast(Decimal, row["mj"]),
            end_read_type=row["end_read_type"],
        )
        for row in table.to_pylist()
    ]
    found = combine(bills)
    chosen = reference(found.periods, settings.reference_bills)
    in_reference = {(period.start, period.end) for period in chosen}
    reference_days = sum(period.days for period in chosen)

    output = run.output("profile")
    write_parquet(
        pd.DataFrame([_period_row(period, in_reference) for period in found.periods], columns=GAS_PERIOD_SCHEMA.names),
        output / GAS_PERIODS_FILENAME,
        kind=GAS_PERIOD_KIND,
        schema_version=GAS_PERIOD_VERSION,
        sort_by=GAS_PERIOD_SORT,
        schema=GAS_PERIOD_SCHEMA,
    )
    months = monthly(found.periods)
    write_parquet(
        pd.DataFrame(
            [
                {
                    "month": month.month,
                    "days_covered": month.days_covered,
                    "mj": month.mj,
                    "mj_per_day": month.mj_per_day,
                }
                for month in months
            ],
            columns=GAS_MONTH_SCHEMA.names,
        ),
        output / GAS_MONTHLY_FILENAME,
        kind=GAS_MONTH_KIND,
        schema_version=GAS_MONTH_VERSION,
        sort_by=GAS_MONTH_SORT,
        schema=GAS_MONTH_SCHEMA,
    )
    bill_mj = sum((bill.mj for bill in bills), Decimal(0))
    checks = {
        "periods_contiguous_or_listed": not found.overlap,
        "mj_conserved": sum((period.mj for period in found.periods), Decimal(0)) == bill_mj
        and sum((month.mj for month in months), Decimal(0)) == bill_mj,
        "read_types_flagged": _flags_match(found.periods, bills),
    }
    summary = GasProfileSummary(
        periods=len(found.periods),
        coverage_start=found.periods[0].start if found.periods else None,
        coverage_end=found.periods[-1].end if found.periods else None,
        gaps=found.gaps,
        estimated_periods=sum(period.estimated for period in found.periods),
        reference_start=chosen[0].start if chosen else None,
        reference_end=chosen[-1].end if chosen else None,
        reference_days=reference_days,
        reference_bills=sum(period.bills for period in chosen),
        reference_mj=sum((period.mj for period in chosen), Decimal(0)),
        partial_year=reference_days < settings.full_year_days,
        checks=checks,
    )
    write_json(output / GAS_SUMMARY_FILENAME, cast(JSONValue, summary.model_dump(mode="json")))
    if found.gaps:
        run.warn(
            WarningCode.W_GAS_GAP,
            "Some gas bills don't start the day after the one before: a bill may be missing.",
            gaps=[[start.isoformat(), end.isoformat()] for start, end in found.gaps],
        )
    run.check("periods_contiguous_or_listed", checks["periods_contiguous_or_listed"], gaps=len(found.gaps))
    run.check("mj_conserved", checks["mj_conserved"])
    run.check("read_types_flagged", checks["read_types_flagged"], estimated=summary.estimated_periods)


def _flags_match(periods: tuple[UsagePeriod, ...], bills: list[BillUsage]) -> bool:
    """Every period holds bills and is `estimated` exactly when the bill it ends with ends on an estimated read."""
    last_bill = {bill.period_end: bill for bill in bills}
    return sum(period.bills for period in periods) == len(bills) and all(
        period.end in last_bill and period.estimated == (last_bill[period.end].end_read_type == "estimate")
        for period in periods
    )


def _period_row(period: UsagePeriod, in_reference: set[tuple[date, date]]) -> dict[str, object]:
    return {
        "period_start": period.start,
        "period_end": period.end,
        "days": period.days,
        "mj": period.mj,
        "mj_per_day": period.mj_per_day,
        "bills": period.bills,
        "estimated": period.estimated,
        "in_reference": (period.start, period.end) in in_reference,
    }


SPEC: Final = StageSpec(
    name="gas_profile",
    inputs={"bills": "data/interim/gas_bills"},
    params=("gas",),
    outputs={"profile": "data/marts/gas_profile"},
    impl=_run,
)
