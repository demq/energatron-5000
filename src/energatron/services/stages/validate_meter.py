"""The `validate_meter` stage (P2 spec §8): the merged meter intervals against the SAPN SUMMARY totals, the list of
missing dates and the quality shares.

A SUMMARY period is *covered* when the merged table holds every meter date of it for every consumption register
(suffix `E…`). A covered period's meter total is compared with the summary's own total, in kWh, within
`validation.meter.total_tolerance_kwh`: `match` or `mismatch`; any other period is `not_covered`. Only a mismatch, a
blob that isn't its catalog record, an unknown detector or a repeated period fails the stage. A gap is a fact and a
missing SUMMARY is a missing piece of evidence, so each is a warning (plan §10.1).

The SUMMARY CSV is read with `core.sapn_summary.read_summary_csv`, whose result has no identifier field. Check details
hold counts and dataset IDs, never values.
"""

import math
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from itertools import pairwise
from pathlib import Path
from typing import Final

import pandas as pd
import pyarrow as pa

from energatron.core.canonical import sha256_hex
from energatron.core.errors import InternalError, InvalidInputError, WarningCode
from energatron.core.models.catalog import (
    CATALOG_FILENAME,
    CatalogRecord,
    SourceType,
    parse_catalog,
    precedence_order,
    raw_key,
)
from energatron.core.models.meter import (
    COMPARE_COLUMNS,
    COMPARE_KIND,
    COMPARE_SORT,
    COMPARE_VERSION,
    GAPS_COLUMNS,
    GAPS_KIND,
    GAPS_SORT,
    GAPS_VERSION,
    INTERVALS_KIND,
    INTERVALS_VERSION,
    QUALITY_COLUMNS,
    QUALITY_KIND,
    QUALITY_SORT,
    QUALITY_VERSION,
    MeterReport,
    StatusCounts,
)
from energatron.core.sapn_summary import SummaryRow, read_summary_csv
from energatron.services.files import write_json, write_parquet
from energatron.services.stages.runner import StageRun, StageSpec
from energatron.services.stages.tables import STAGED_SCHEMA, load_table

SOURCE: Final = SourceType.SAPN_SUMMARY
KNOWN_DETECTOR_VERSIONS: Final = frozenset({1})
"""The `sapn_summary` parser versions whose files this stage reads (P2 spec §3.1)."""
INTERVALS_FILENAME: Final = "intervals.parquet"
COMPARE_FILENAME: Final = "summary_compare.parquet"
GAPS_FILENAME: Final = "gaps.parquet"
QUALITY_FILENAME: Final = "quality_monthly.parquet"
REPORT_FILENAME: Final = "report.json"
_CONSUMPTION: Final = "E"
"""A consumption register's suffix starts with this letter (P2 spec §3.2)."""
_ONE_DAY: Final = timedelta(days=1)
_DIFF_DECIMALS: Final = 6
"""The difference is rounded to this many decimals before it meets the tolerance. SUMMARY totals have three, so
rounding only strips float noise: a difference of exactly the tolerance is a `match`, deterministically."""

COMPARE_SCHEMA: Final = pa.schema(
    [
        ("dataset_id", pa.string()),
        ("period_start", pa.date32()),
        ("period_end", pa.date32()),
        ("summary_quality", pa.string()),
        ("summary_kwh", pa.float64()),
        ("meter_kwh", pa.float64()),
        ("diff_kwh", pa.float64()),
        ("status", pa.string()),
        ("summary_disagreement", pa.bool_()),
    ]
)
GAPS_SCHEMA: Final = pa.schema(
    [("suffix", pa.string()), ("gap_start", pa.date32()), ("gap_end", pa.date32()), ("days", pa.int32())]
)
QUALITY_SCHEMA: Final = pa.schema(
    [
        ("suffix", pa.string()),
        ("month", pa.string()),
        ("quality_flag", pa.string()),
        ("intervals", pa.int32()),
        ("kwh", pa.float64()),
        ("share", pa.float64()),
    ]
)


@dataclass(frozen=True)
class _Period:
    """One SUMMARY period as used: the row of the highest-precedence dataset that holds it."""

    dataset_id: str
    row: SummaryRow
    disagreement: bool


@dataclass
class _Summary:
    periods: dict[tuple[date, date], _Period]
    mismatched: list[str]  # blob missing, or its hash isn't the record's
    unknown_detector: list[str]
    repeated: dict[str, int]  # dataset → rows repeating a period of the same file


def _run(run: StageRun) -> None:
    table = load_table(
        run.input("interval") / INTERVALS_FILENAME, STAGED_SCHEMA, INTERVALS_KIND, INTERVALS_VERSION, input="interval"
    ).to_pandas()
    tolerance = _tolerance(run.params)
    summary = _read_summary(run.input("summary"))

    compared = _compare(table, summary.periods, tolerance)
    gap_frame = _gaps(table)
    gap_days = int(gap_frame["days"].sum()) if len(gap_frame) else 0
    report = run.output("report")
    write_parquet(
        compared,
        report / COMPARE_FILENAME,
        kind=COMPARE_KIND,
        schema_version=COMPARE_VERSION,
        sort_by=COMPARE_SORT,
        schema=COMPARE_SCHEMA,
    )
    write_parquet(
        gap_frame,
        report / GAPS_FILENAME,
        kind=GAPS_KIND,
        schema_version=GAPS_VERSION,
        sort_by=GAPS_SORT,
        schema=GAPS_SCHEMA,
    )
    write_parquet(
        _quality(table),
        report / QUALITY_FILENAME,
        kind=QUALITY_KIND,
        schema_version=QUALITY_VERSION,
        sort_by=QUALITY_SORT,
        schema=QUALITY_SCHEMA,
    )
    counts = Counter(compared["status"])
    write_json(
        report / REPORT_FILENAME,
        _report(table, counts, gap_days).model_dump(mode="json"),
    )

    run.check("blobs_match_catalog", not summary.mismatched, mismatched=list(summary.mismatched))
    run.check("detector_known", not summary.unknown_detector, unknown=list(summary.unknown_detector))
    run.check("monthly_totals_match", counts["mismatch"] == 0, mismatched=counts["mismatch"])
    run.check("summary_rows_unique", not summary.repeated, repeated=dict(summary.repeated))
    if gap_days:
        run.warn(
            WarningCode.W_METER_GAPS,
            "Some meter dates are missing between a register's first and last date.",
            gap_days=gap_days,
        )
    if counts["match"] + counts["mismatch"] == 0:
        run.warn(
            WarningCode.W_METER_UNVALIDATED,
            "No SAPN SUMMARY period is covered by the meter data, so the totals are not validated.",
        )


def _tolerance(params: Mapping[str, object]) -> float:
    meter = params.get("validation.meter")
    value = meter.get("total_tolerance_kwh") if isinstance(meter, Mapping) else None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise InternalError("validate_meter got no validation.meter.total_tolerance_kwh.")
    return float(value)


# the SUMMARY periods


def _read_summary(raw: Path) -> _Summary:
    """The periods of the current CSV datasets, highest precedence first (P2 spec §3.1, §8).

    Every dataset's blob and detector are checked, including superseded and PDF ones (§3.1); the others are not read."""
    try:
        catalog = (raw / CATALOG_FILENAME).read_bytes()
    except OSError:
        raise InvalidInputError("The raw directory has no readable catalog.", input="summary") from None
    records = parse_catalog(catalog, SOURCE)
    superseded = {record.supersedes for record in records if record.supersedes is not None}
    by_id = {record.dataset_id: record for record in records}
    found = _Summary({}, [], [], {})
    for ident in precedence_order(records):
        record = by_id[ident]
        data = _blob(raw, record)
        blob_ok = data is not None and sha256_hex(data) == record.sha256
        detector_ok = record.detector.name == SOURCE and record.detector.version in KNOWN_DETECTOR_VERSIONS
        if not blob_ok:
            found.mismatched.append(ident)
        if not detector_ok:
            found.unknown_detector.append(ident)
        # §3.1 checks every blob in the directory; only a current CSV dataset whose checks passed is read
        if data is None or not blob_ok or not detector_ok or ident in superseded or record.extra.get("format") != "csv":
            continue
        _add_periods(found, ident, read_summary_csv(data).rows)
    return found


def _add_periods(found: _Summary, ident: str, rows: tuple[SummaryRow, ...]) -> None:
    """Add one dataset's periods. A period already held by a higher-precedence dataset stays, flagged if the totals
    differ; a period repeated within the file counts once, at its first row, and is noted."""
    first: dict[tuple[date, date], SummaryRow] = {}
    for row in rows:
        key = (row.start, row.end)
        if key in first:
            found.repeated[ident] = found.repeated.get(ident, 0) + 1
        else:
            first[key] = row
    for key, row in first.items():
        held = found.periods.get(key)
        if held is None:
            found.periods[key] = _Period(ident, row, disagreement=False)
        elif held.row.kwh != row.kwh and not held.disagreement:
            found.periods[key] = _Period(held.dataset_id, held.row, disagreement=True)


def _blob(raw: Path, record: CatalogRecord) -> bytes | None:
    """The blob's bytes, found under `raw` whatever the directory is called (so `--input` overrides work)."""
    relative = record.stored_path.removeprefix(raw_key(SOURCE))
    try:
        return raw.joinpath(*relative.split("/")).read_bytes()
    except OSError:
        return None


# the comparison


def _compare(table: pd.DataFrame, periods: Mapping[tuple[date, date], _Period], tolerance: float) -> pd.DataFrame:
    days, suffixes = _consumption(table)
    rows: list[dict[str, object]] = []
    for (start, end), period in periods.items():
        summary_kwh = _kwh(period.row.kwh)
        meter_kwh = _meter_total(days, suffixes, start, end)
        if meter_kwh is None:
            diff, status = None, "not_covered"
        else:
            diff = round(meter_kwh - summary_kwh, _DIFF_DECIMALS)
            status = "match" if abs(diff) <= tolerance else "mismatch"
        rows.append(
            {
                "dataset_id": period.dataset_id,
                "period_start": start,
                "period_end": end,
                "summary_quality": period.row.quality,
                "summary_kwh": summary_kwh,
                "meter_kwh": meter_kwh,
                "diff_kwh": diff,
                "status": status,
                "summary_disagreement": period.disagreement,
            }
        )
    return pd.DataFrame(rows, columns=list(COMPARE_COLUMNS))


def _kwh(value: Decimal) -> float:
    """The SUMMARY total as a float, converted once, so the comparison is float against float (P2 spec §8)."""
    return float(value)


def _consumption(table: pd.DataFrame) -> tuple[dict[tuple[str, date], list[float]], list[str]]:
    """Each consumption register's values by `(suffix, meter date)`, and the registers, sorted."""
    days: dict[tuple[str, date], list[float]] = {}
    for suffix, meter_date, kwh in zip(table["suffix"], table["meter_date"], table["kwh"], strict=True):
        if str(suffix).startswith(_CONSUMPTION):
            days.setdefault((str(suffix), meter_date), []).append(float(kwh))
    return days, sorted({suffix for suffix, _ in days})


def _meter_total(
    days: Mapping[tuple[str, date], list[float]], suffixes: list[str], start: date, end: date
) -> float | None:
    """`math.fsum` of the values over `start…end` for every consumption register, or `None` when any register lacks
    any of those meter dates (or there is no consumption register)."""
    if not suffixes:
        return None
    parts: list[float] = []
    for suffix in suffixes:
        day = start
        while day <= end:
            values = days.get((suffix, day))
            if values is None:
                return None
            parts.extend(values)
            day += _ONE_DAY
    return math.fsum(parts)


# gaps and quality


def _gaps(table: pd.DataFrame) -> pd.DataFrame:
    """Per register, each run of missing meter dates between its first and last date."""
    rows: list[dict[str, object]] = []
    dates = table.groupby("suffix", sort=True)["meter_date"].agg(lambda found: sorted(set(found)))
    for suffix, found in dates.items():
        for before, after in pairwise(found):
            missing = (after - before).days - 1
            if missing:
                rows.append(
                    {
                        "suffix": str(suffix),
                        "gap_start": before + _ONE_DAY,
                        "gap_end": after - _ONE_DAY,
                        "days": missing,
                    }
                )
    return pd.DataFrame(rows, columns=list(GAPS_COLUMNS))


def _quality(table: pd.DataFrame) -> pd.DataFrame:
    """Per register, meter month and quality flag: intervals, energy and the share of that month's intervals."""
    if table.empty:
        return pd.DataFrame(columns=list(QUALITY_COLUMNS))
    months = pd.Series([f"{day:%Y-%m}" for day in table["meter_date"]], index=table.index, dtype="object")
    frame = pd.DataFrame(
        {"suffix": table["suffix"], "month": months, "quality_flag": table["quality_flag"], "kwh": table["kwh"]}
    )
    keys = ["suffix", "month", "quality_flag"]
    grouped = frame.groupby(keys, sort=True).agg(intervals=("kwh", "size"), kwh=("kwh", math.fsum)).reset_index()
    month_total = grouped.groupby(["suffix", "month"])["intervals"].transform("sum")
    return grouped.assign(share=grouped["intervals"] / month_total)[list(QUALITY_COLUMNS)]


def _report(table: pd.DataFrame, counts: Counter[str], gap_days: int) -> MeterReport:
    by_suffix = table.groupby("suffix", sort=True)
    shares = len(table)
    return MeterReport(
        summary_rows=StatusCounts(
            match=counts["match"], mismatch=counts["mismatch"], not_covered=counts["not_covered"]
        ),
        gap_days=gap_days,
        intervals={str(suffix): int(count) for suffix, count in by_suffix.size().items()},
        first_date={str(suffix): found for suffix, found in by_suffix["meter_date"].min().items()},
        last_date={str(suffix): found for suffix, found in by_suffix["meter_date"].max().items()},
        a_share=float((table["quality_flag"] == "A").sum() / shares) if shares else None,
    )


SPEC: Final = StageSpec(
    name="validate_meter",
    inputs={"interval": "data/interim/elec_interval_nem", "summary": "data/raw/sapn_summary"},
    params=("validation.meter",),
    outputs={"report": "data/reports/validation/meter"},
    impl=_run,
)
