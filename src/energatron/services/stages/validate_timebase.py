"""The `validate_timebase` stage (P2 spec §9): which clock the meter data runs on, decided by evidence.

The retailer's usage export reports energy per time-of-use register for periods between midnight reads. For each
period whose meter data is complete and whose total agrees with the meter's (so the read-date alignment is right), the
stage recomputes every register from the merged intervals under each of the three hypotheses of `core.timebase` and
counts the periods each hypothesis explains. The verdict compares the configured `meter.timebase` with the others:
`confirmed`, `unconfirmed` (too little evidence, or a tie with the configured basis) or `contradicted`. Only
`contradicted` fails the stage; `localise` builds local-time data only from a `confirmed` verdict (spec §10), so an
`unconfirmed` basis is never assumed (hand-over decision #11).

The usage export is read with `core.retailer_usage.read_usage_export`, whose result has no identifier field. Check
details hold counts, labels and dataset IDs, never values.
"""

import math
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, tzinfo
from itertools import pairwise
from pathlib import Path
from typing import Final, Literal, cast

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
    INTERVALS_KIND,
    INTERVALS_VERSION,
    PERIODS_COLUMNS,
    PERIODS_KIND,
    PERIODS_SORT,
    PERIODS_VERSION,
    BasisCounts,
    PeriodCounts,
    TimebaseResult,
    TimebaseTolerances,
)
from energatron.core.models.params import TimebaseName
from energatron.core.retailer_usage import UsageRow, read_usage_export
from energatron.core.timebase import Timebase, Window, bucket, local_clock, parse_windows
from energatron.services.files import write_json, write_parquet
from energatron.services.stages.runner import StageRun, StageSpec
from energatron.services.stages.tables import STAGED_SCHEMA, load_table

SOURCE: Final = SourceType.RETAILER_USAGE
KNOWN_DETECTOR_VERSIONS: Final = frozenset({1})
"""The `retailer_usage` parser versions whose files this stage reads (P2 spec §3.1)."""
INTERVALS_FILENAME: Final = "intervals.parquet"
PERIODS_FILENAME: Final = "periods.parquet"
RESULT_FILENAME: Final = "result.json"
_CONSUMPTION: Final = "E"
"""A consumption register's suffix starts with this letter (P2 spec §3.2)."""
_ONE_DAY: Final = timedelta(days=1)
_MIDNIGHT: Final = time(0)
_DIFF_DECIMALS: Final = 6
"""A difference is rounded to this many decimals before it meets a tolerance, so one of exactly the tolerance is a
fit or a match deterministically (the export's figures have four decimals; rounding only strips float noise)."""
_BASES: Final = tuple(Timebase)

PERIODS_SCHEMA: Final = pa.schema(
    [
        ("period_start", pa.date32()),
        ("period_end", pa.date32()),
        ("status", pa.string()),
        ("dataset_id", pa.string()),
        ("label", pa.string()),
        ("reported_kwh", pa.float64()),
        *((f"kwh_{basis.value}", pa.float64()) for basis in _BASES),
        *((f"fit_{basis.value}", pa.bool_()) for basis in _BASES),
    ]
)

type Verdict = Literal["confirmed", "unconfirmed", "contradicted"]


@dataclass(frozen=True)
class _Settings:
    configured: Timebase
    windows: Mapping[str, Sequence[str]]
    register_tolerance_kwh: float
    register_tolerance_pct: float
    total_tolerance_kwh: float
    min_fit_periods: int


@dataclass
class _Usage:
    rows: dict[datetime, tuple[str, UsageRow]] = field(default_factory=dict)  # per read: the dataset and row used
    seen: set[datetime] = field(default_factory=set)  # every read datetime of a dataset that was read
    labels: dict[str, tuple[str, ...]] = field(default_factory=dict)  # per dataset that was read
    mismatched: list[str] = field(default_factory=list)  # blob missing, or its hash isn't the record's
    unknown_detector: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class _Period:
    start: date
    end: date
    dataset_id: str
    row: UsageRow


@dataclass(frozen=True)
class _Meter:
    """The consumption intervals, ready to be summed: by meter date, and by meter date and label under each basis."""

    dates: dict[str, set[date]]  # register → the meter dates it holds
    by_date: dict[date, list[float]]
    by_label: dict[Timebase, dict[tuple[date, str], list[float]]]
    interval_minutes: list[int]


def _run(run: StageRun) -> None:
    settings = _settings(run.params)
    windows = parse_windows(settings.windows)
    zone = run.zone(_text(run.params, "meter.local_tz"))
    table = load_table(
        run.input("interval") / INTERVALS_FILENAME, STAGED_SCHEMA, INTERVALS_KIND, INTERVALS_VERSION, input="interval"
    ).to_pandas()
    usage = _read_usage(run.input("usage"))
    meter = _meter(table, windows, zone)

    reads = sorted(usage.rows)
    midnight = [read for read in reads if read.time() == _MIDNIGHT]
    not_midnight = len(reads) - len(midnight)
    periods = [
        _Period(before.date() + _ONE_DAY, after.date(), usage.rows[after][0], usage.rows[after][1])
        for before, after in pairwise(midnight)
    ]

    rows: list[dict[str, object]] = []
    status: Counter[str] = Counter()
    fits: Counter[Timebase] = Counter()
    unexplained = 0
    for period in periods:
        found = _evaluate(period, meter, settings)
        status[found.status] += 1
        rows.extend(found.rows)
        for basis in found.fitting:
            fits[basis] += 1
        if found.status == "used" and not found.fitting:
            unexplained += 1

    counts = {basis: fits[basis] for basis in _BASES}
    verdict, best = _verdict(settings, counts, status["used"])
    write_parquet(
        pd.DataFrame(rows, columns=list(PERIODS_COLUMNS)),
        run.output("report") / PERIODS_FILENAME,
        kind=PERIODS_KIND,
        schema_version=PERIODS_VERSION,
        sort_by=PERIODS_SORT,
        schema=PERIODS_SCHEMA,
    )
    write_json(
        run.output("report") / RESULT_FILENAME,
        _result(settings, verdict, counts, best, status, usage, not_midnight, unexplained).model_dump(mode="json"),
    )

    missing, extra = _label_differences(usage.labels, windows)
    edges = sorted({edge for found in windows.values() for window in found for edge in _edges(window)})
    misaligned = [length for length in meter.interval_minutes if any(edge % length for edge in edges)]
    fit_details = {basis.value: counts[basis] for basis in _BASES}
    run.check("blobs_match_catalog", not usage.mismatched, mismatched=list(usage.mismatched))
    run.check("detector_known", not usage.unknown_detector, unknown=list(usage.unknown_detector))
    run.check("labels_match", not missing and not extra, missing=missing, extra=extra)
    run.check("windows_align", not misaligned, interval_minutes=meter.interval_minutes)
    run.check("configured_is_best", verdict != "contradicted", fits=fit_details, best=[basis.value for basis in best])
    if verdict == "unconfirmed":
        run.warn(
            WarningCode.W_TIMEBASE_UNCONFIRMED,
            "The retailer's export does not settle the meter's time basis, so it is not treated as confirmed.",
            fits=fit_details,
        )
    if unexplained:
        run.warn(
            WarningCode.W_TIMEBASE_UNEXPLAINED,
            "Some periods fit none of the time-basis hypotheses; the retailer's windows may have changed.",
            periods=unexplained,
        )


# parameters


def _text(params: Mapping[str, object], key: str) -> str:
    value = params.get(key)
    if not isinstance(value, str):
        raise InternalError(f"validate_timebase got no {key}.")
    return value


def _number(section: Mapping[str, object], key: str) -> float:
    value = section.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise InternalError(f"validate_timebase got no validation.timebase.{key}.")
    return float(value)


def _settings(params: Mapping[str, object]) -> _Settings:
    section = params.get("validation.timebase")
    if not isinstance(section, Mapping):
        raise InternalError("validate_timebase got no validation.timebase.")
    windows = section.get("windows")
    if not isinstance(windows, Mapping):
        raise InternalError("validate_timebase got no validation.timebase.windows.")
    return _Settings(
        configured=Timebase(_text(params, "meter.timebase")),
        windows=cast("Mapping[str, Sequence[str]]", windows),
        register_tolerance_kwh=_number(section, "register_tolerance_kwh"),
        register_tolerance_pct=_number(section, "register_tolerance_pct"),
        total_tolerance_kwh=_number(section, "total_tolerance_kwh"),
        min_fit_periods=int(_number(section, "min_fit_periods")),
    )


# the usage export


def _read_usage(raw: Path) -> _Usage:
    """The reads of the current usage datasets: for each read datetime, the complete row of the highest-precedence
    dataset that has one (P2 spec §3.1, §9.3).

    Every dataset's blob and detector are checked, including superseded ones (§3.1); only a current dataset whose checks
    passed is read."""
    try:
        catalog = (raw / CATALOG_FILENAME).read_bytes()
    except OSError:
        raise InvalidInputError("The raw directory has no readable catalog.", input="usage") from None
    records = parse_catalog(catalog, SOURCE)
    superseded = {record.supersedes for record in records if record.supersedes is not None}
    by_id = {record.dataset_id: record for record in records}
    found = _Usage()
    for ident in precedence_order(records):
        record = by_id[ident]
        data = _blob(raw, record)
        blob_ok = data is not None and sha256_hex(data) == record.sha256
        detector_ok = record.detector.name == SOURCE and record.detector.version in KNOWN_DETECTOR_VERSIONS
        if not blob_ok:
            found.mismatched.append(ident)
        if not detector_ok:
            found.unknown_detector.append(ident)
        if data is None or not blob_ok or not detector_ok or ident in superseded:
            continue
        read = read_usage_export(data)
        found.labels[ident] = read.labels
        for row in read.rows:
            found.seen.add(row.read_dt)
            if row.read_dt not in found.rows and _complete(row):
                found.rows[row.read_dt] = (ident, row)
    return found


def _complete(row: UsageRow) -> bool:
    return row.total is not None and all(value is not None for value in row.values.values())


def _blob(raw: Path, record: CatalogRecord) -> bytes | None:
    """The blob's bytes, found under `raw` whatever the directory is called (so `--input` overrides work)."""
    relative = record.stored_path.removeprefix(raw_key(SOURCE))
    try:
        return raw.joinpath(*relative.split("/")).read_bytes()
    except OSError:
        return None


def _label_differences(
    labels: Mapping[str, tuple[str, ...]], windows: Mapping[str, Sequence[Window]]
) -> tuple[list[str], list[str]]:
    """The window labels some read dataset lacks, and the labels a dataset has that the windows lack (P2 spec §9.3)."""
    missing: set[str] = set()
    extra: set[str] = set()
    for found in labels.values():
        missing |= set(windows) - set(found)
        extra |= set(found) - set(windows)
    return sorted(missing), sorted(extra)


def _edges(window: Window) -> tuple[int, int]:
    return window.start_minute, window.end_minute


# the meter data


def _meter(table: pd.DataFrame, windows: Mapping[str, Sequence[Window]], zone: tzinfo) -> _Meter:
    """The consumption registers' intervals, each placed in a label under every basis."""
    consumption = table["suffix"].astype(str).str.startswith(_CONSUMPTION)
    frame = table.loc[consumption, ["suffix", "meter_date", "meter_start", "interval_minutes", "kwh"]]
    dates = {str(suffix): set(found) for suffix, found in frame.groupby("suffix", sort=True)["meter_date"]}
    by_date: dict[date, list[float]] = {}
    for day, found in frame.groupby("meter_date", sort=True)["kwh"]:
        by_date[cast("date", day)] = [float(value) for value in found]
    starts = frame["meter_start"].drop_duplicates().reset_index(drop=True)
    by_label: dict[Timebase, dict[tuple[date, str], list[float]]] = {}
    for basis in _BASES:
        labels = bucket(local_clock(starts, basis, zone), windows)
        placed = frame["meter_start"].map(pd.Series(labels.to_numpy(), index=pd.Index(starts)))
        grouped = frame.assign(label=placed).groupby(["meter_date", "label"], sort=True)["kwh"]
        by_label[basis] = {}
        for key, found in grouped:
            day, label = cast("tuple[date, str]", key)
            by_label[basis][day, str(label)] = [float(value) for value in found]
    return _Meter(
        dates=dates,
        by_date=by_date,
        by_label=by_label,
        interval_minutes=sorted({int(length) for length in frame["interval_minutes"].unique()}),
    )


# one period


@dataclass(frozen=True)
class _Evaluated:
    status: str
    rows: list[dict[str, object]]
    fitting: list[Timebase]


def _evaluate(period: _Period, meter: _Meter, settings: _Settings) -> _Evaluated:
    reported = {label: _kwh(value) for label, value in sorted(period.row.values.items())}
    days = _period_days(period)
    status = "used"
    meter_total = _covered_total(meter, days)
    if meter_total is None:
        status = "not_covered"
    else:
        diff = round(meter_total - _kwh(period.row.total), _DIFF_DECIMALS)
        if abs(diff) > settings.total_tolerance_kwh:
            status = "total_mismatch"
    rows: list[dict[str, object]] = []
    fit_by_label: dict[Timebase, list[bool]] = {basis: [] for basis in _BASES}
    for label, value in reported.items():
        row: dict[str, object] = {
            "period_start": period.start,
            "period_end": period.end,
            "status": status,
            "dataset_id": period.dataset_id,
            "label": label,
            "reported_kwh": value,
        }
        for basis in _BASES:
            if status == "used":
                recomputed = math.fsum(kwh for day in days for kwh in meter.by_label[basis].get((day, label), ()))
                fits = _register_fits(recomputed, value, settings)
                fit_by_label[basis].append(fits)
                row[f"kwh_{basis.value}"], row[f"fit_{basis.value}"] = recomputed, fits
            else:
                row[f"kwh_{basis.value}"], row[f"fit_{basis.value}"] = None, None
        rows.append(row)
    fitting = [basis for basis in _BASES if status == "used" and all(fit_by_label[basis])]
    return _Evaluated(status, rows, fitting)


def _kwh(value: object) -> float:
    """An export figure as a float, converted once, so every comparison is float against float."""
    if value is None:
        raise InternalError("validate_timebase met an incomplete row.")
    return float(cast("float", value))


def _period_days(period: _Period) -> list[date]:
    days: list[date] = []
    day = period.start
    while day <= period.end:
        days.append(day)
        day += _ONE_DAY
    return days


def _covered_total(meter: _Meter, days: Sequence[date]) -> float | None:
    """`math.fsum` of the consumption over `days`, or `None` when any register lacks any of those meter dates (or there
    is no consumption register)."""
    if not meter.dates:
        return None
    if any(day not in held for held in meter.dates.values() for day in days):
        return None
    return math.fsum(value for day in days for value in meter.by_date.get(day, ()))


def _register_fits(recomputed: float, reported: float, settings: _Settings) -> bool:
    allowed = max(settings.register_tolerance_kwh, settings.register_tolerance_pct / 100 * reported)
    return abs(round(recomputed - reported, _DIFF_DECIMALS)) <= allowed


# the verdict


def _verdict(settings: _Settings, fits: Mapping[Timebase, int], used: int) -> tuple[Verdict, list[Timebase]]:
    """The verdict and the bases with the most fits (P2 spec §9.4)."""
    best_count = max(fits.values())
    best = [basis for basis in sorted(_BASES) if best_count and fits[basis] == best_count]
    configured = fits[settings.configured]
    others = [count for basis, count in fits.items() if basis is not settings.configured]
    if used == 0 or best_count < settings.min_fit_periods:
        return "unconfirmed", best
    if configured == best_count and any(count == best_count for count in others):
        return "unconfirmed", best
    if all(configured > count for count in others):
        return "confirmed", best
    return "contradicted", best


def _result(
    settings: _Settings,
    verdict: Verdict,
    fits: Mapping[Timebase, int],
    best: Sequence[Timebase],
    status: Mapping[str, int],
    usage: _Usage,
    not_midnight: int,
    unexplained: int,
) -> TimebaseResult:
    return TimebaseResult(
        configured=cast("TimebaseName", settings.configured.value),
        verdict=verdict,
        fits=BasisCounts(**{basis.value: fits[basis] for basis in _BASES}),
        best=[cast("TimebaseName", basis.value) for basis in best],
        periods=PeriodCounts(
            used=status["used"], not_covered=status["not_covered"], total_mismatch=status["total_mismatch"]
        ),
        incomplete=len(usage.seen - set(usage.rows)),
        not_midnight=not_midnight,
        unexplained=unexplained,
        tolerances=TimebaseTolerances(
            register_tolerance_kwh=settings.register_tolerance_kwh,
            register_tolerance_pct=settings.register_tolerance_pct,
            total_tolerance_kwh=settings.total_tolerance_kwh,
            min_fit_periods=settings.min_fit_periods,
        ),
    )


SPEC: Final = StageSpec(
    name="validate_timebase",
    inputs={"interval": "data/interim/elec_interval_nem", "usage": "data/raw/retailer_usage"},
    params=("meter.local_tz", "meter.timebase", "validation.timebase"),
    outputs={"report": "data/reports/validation/timebase"},
    impl=_run,
)
