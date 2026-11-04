"""A fixture workspace's raw zone for the P2 stages (P2 spec §13.2). Every value is made up.

`build_raw_zone(root)` writes the blobs and catalogs of a raw zone, as `land` would have left them, from generated
files only:
- two overlapping `sapn_interval` datasets (A, then B, in catalog order) with one conflict day, whose values differ
  and whose newer `UpdateDateTime` belongs to B; A also holds a day of quality `V` and B a day of quality `E52`
- one `sapn_summary` CSV, computed from the merged "truth" file (A and B with the conflict day resolved to B)
- two `retailer_usage` exports generated under `nem_aest`, the newer with an empty newest row
- an empty catalog for every other source type

The span covers both daylight-saving changes of `year`. Each P2 stage sub-task grows this module with what its stage
needs. The catalog records come from the real parsers' `inspect`, so a stage reads them as it would read landed ones.
`land_blob` lands one more file, for stage tests that need a tampered or odd dataset.
"""

import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Final

from energatron.adapters.sources.registry import PARSERS
from energatron.core.canonical import canonical_json, sha256_hex
from energatron.core.ids import dataset_id
from energatron.core.models.catalog import CATALOG_FILENAME, CatalogRecord, Detector, SourceType, blob_key
from tests.fixtures import oracle
from tests.fixtures.make_nem12 import Nem12Spec, QualityRange, make_nem12, sapn_filename
from tests.fixtures.make_sources import make_retailer_usage_from_nem12, make_summary_from_nem12
from tests.fixtures.synthetic import SYNTHETIC_NMI

YEAR: Final = 2025
INTERVAL_MINUTES: Final = 30
SEED: Final = 7
WINDOWS: Final[Mapping[str, tuple[str, ...]]] = {
    "Peak": ("06:00-10:00", "15:00-01:00"),
    "Off Peak": ("01:00-06:00",),
    "Shoulder": ("10:00-15:00",),
}
"""Made-up TOU windows that partition the day; the usage exports are generated with them."""
LABELS: Final = ("Off Peak", "Peak", "Shoulder")
_LANDED: Final = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
_TAIL_DAYS: Final = 3  # `dst_spec`: the span ends three days after the October change
_A_DAYS: Final = 120
_B_FROM: Final = 100  # B starts on the 101st day, so the two overlap on 20 days
_CONFLICT: Final = 110


@dataclass(frozen=True)
class Dataset:
    """One landed dataset: what a test needs to find it again."""

    source: SourceType
    dataset_id: str
    filename: str
    stored_path: str
    sha256: str


@dataclass(frozen=True)
class IntervalSpecs:
    """The generator specs behind the interval datasets."""

    a: Nem12Spec
    b: Nem12Spec
    truth: Nem12Spec  # the merged result: every day once, the conflict day as B has it
    conflict_day: date
    v_day: date  # a day of quality V in A, outside the overlap
    e_day: date  # a day of quality E52 in B, outside the overlap
    a_only: tuple[date, date]
    overlap: tuple[date, date]
    b_only: tuple[date, date]


@dataclass(frozen=True)
class RawZone:
    root: Path
    specs: IntervalSpecs
    interval: tuple[Dataset, Dataset]  # A then B, in catalog order
    summary: Dataset
    usage: tuple[Dataset, Dataset]
    truth: bytes  # the NEM12 text the merged table must equal in energy
    periods: tuple[tuple[date, date], ...]  # the SUMMARY CSV's periods


def _days(spec: Nem12Spec) -> list[date]:
    return [spec.start + timedelta(days=offset) for offset in range(spec.days)]


def day_values(nem12: bytes, days: Sequence[date], *, suffix: str = "E1") -> dict[date, list[str]]:
    """Each day's values as the file writes them (three decimals), from the oracle."""
    found: dict[date, list[str]] = defaultdict(list)
    for item in oracle.intervals(nem12):
        if item.suffix == suffix and item.meter_start.date() in days:
            found[item.meter_start.date()].append(f"{item.kwh:.3f}")
    return dict(found)


def interval_specs(year: int = YEAR) -> IntervalSpecs:
    """The specs for A, B and the truth, spanning both daylight-saving changes of `year`."""
    april, october = oracle.sa_dst_dates(year)
    start = april - timedelta(days=3)
    total = (october - start).days + 1 + _TAIL_DAYS
    base = Nem12Spec(start=start, days=total, interval_minutes=INTERVAL_MINUTES, seed=SEED)
    all_days = _days(base)
    conflict_day = all_days[_CONFLICT]
    original = day_values(make_nem12(base), [conflict_day])[conflict_day]
    altered = [f"{float(value) + 0.1:.3f}" for value in original]
    truth = replace(base, day_values={conflict_day: altered})
    truth_values = day_values(make_nem12(truth), all_days)
    v_day, e_day = all_days[30], all_days[150]
    intervals = 1440 // INTERVAL_MINUTES
    spec_a = replace(
        base,
        days=_A_DAYS,
        v_days={v_day: (QualityRange(1, intervals, "S14", "79"),)},
    )
    b_days = all_days[_B_FROM:]
    spec_b = replace(
        base,
        start=b_days[0],
        days=len(b_days),
        day_values={day: truth_values[day] for day in b_days},
        day_quality={e_day: "E52"},
        update_dt=lambda day: (
            datetime.combine(day + timedelta(days=11 if day == conflict_day else 1), datetime.min.time())
            + timedelta(hours=5)
        ),
    )
    return IntervalSpecs(
        a=spec_a,
        b=spec_b,
        truth=truth,
        conflict_day=conflict_day,
        v_day=v_day,
        e_day=e_day,
        a_only=(all_days[0], all_days[_B_FROM - 1]),
        overlap=(all_days[_B_FROM], all_days[_A_DAYS - 1]),
        b_only=(all_days[_A_DAYS], all_days[-1]),
    )


def make_record(
    source: SourceType,
    filename: str,
    data: bytes,
    *,
    landed_at: datetime = _LANDED,
    file_mtime: datetime | None = None,
    supersedes: str | None = None,
) -> CatalogRecord:
    """The catalog record `land` would write for `data`, built from the real parser's `inspect`."""
    parser = next(item for item in PARSERS if item.source == source)
    info = parser.inspect(filename, data)
    digest = sha256_hex(data)
    ident = dataset_id(source.value, info.source_ts, digest)
    return CatalogRecord(
        dataset_id=ident,
        source=source,
        sha256=digest,
        bytes=len(data),
        stored_path=blob_key(source, ident, info.extension),
        original_filename=filename,
        landed_at=landed_at,
        file_mtime=file_mtime,
        source_ts=info.source_ts,
        coverage=info.coverage,
        detector=Detector(name=source, version=parser.version, evidence=("filename", "content")),
        extra=info.extra,
        supersedes=supersedes,
        notes="synthetic",
    )


def land_blob(
    root: Path,
    source: SourceType,
    filename: str,
    data: bytes,
    *,
    landed_at: datetime = _LANDED,
    file_mtime: datetime | None = None,
    supersedes: str | None = None,
) -> Dataset:
    """Write the blob and append its record to the source's catalog."""
    record = make_record(source, filename, data, landed_at=landed_at, file_mtime=file_mtime, supersedes=supersedes)
    blob = root / record.stored_path
    blob.parent.mkdir(parents=True, exist_ok=True)
    blob.write_bytes(data)
    catalog = root / "data" / "raw" / source.value / CATALOG_FILENAME
    catalog.parent.mkdir(parents=True, exist_ok=True)
    existing = catalog.read_bytes() if catalog.exists() else b""
    catalog.write_bytes(existing + canonical_json(record.model_dump(mode="json")) + b"\n")
    return Dataset(source, record.dataset_id, filename, record.stored_path, record.sha256)


def empty_raw_zone(root: Path) -> None:
    """A raw directory with an empty catalog for every source type, and the local offer-rules folder `pipeline sync`
    creates (a `normalise_offers` dependency, P4 spec §4.3)."""
    for source in SourceType:
        directory = root / "data" / "raw" / source.value
        directory.mkdir(parents=True, exist_ok=True)
        (directory / CATALOG_FILENAME).write_bytes(b"")
    rules = root / "config" / "offer_rules.local"
    rules.mkdir(parents=True, exist_ok=True)
    (rules / "README.md").write_text("Local offer rules (none).\n", encoding="utf-8")


def build_raw_zone(root: Path, *, year: int = YEAR) -> RawZone:
    """Write the whole fixture raw zone under `root`; see the module doc."""
    empty_raw_zone(root)
    specs = interval_specs(year)
    a_text, b_text, truth = make_nem12(specs.a), make_nem12(specs.b), make_nem12(specs.truth)
    first = specs.a_only[0]
    last = specs.b_only[1]
    interval: list[Dataset] = []
    for spec, text, generated in (
        (specs.a, a_text, datetime(year, 8, 5, 9, 0, 0)),
        (specs.b, b_text, datetime(year, 10, 12, 9, 0, 0)),
    ):
        window = (spec.start, spec.start + timedelta(days=spec.days - 1))
        name = sapn_filename("DETAILED", SYNTHETIC_NMI, window, generated)
        interval.append(land_blob(root, SourceType.SAPN_INTERVAL, name, text))

    periods: list[tuple[date, date]] = []
    cursor = first
    while cursor + timedelta(days=29) < last:
        periods.append((cursor, cursor + timedelta(days=29)))
        cursor += timedelta(days=30)
    periods.append((cursor, last))
    summary_name = sapn_filename("SUMMARY", SYNTHETIC_NMI, (first, last), datetime(year, 10, 12, 9, 5, 0))
    summary = land_blob(
        root,
        SourceType.SAPN_SUMMARY,
        summary_name,
        make_summary_from_nem12(truth, periods=periods),
    )

    def read(month: int, day: int = 1) -> datetime:
        return datetime(year, month, day)

    usage: list[Dataset] = []
    exports = (
        ([read(5), read(6), read(7), read(8)], False),
        ([read(9), read(10), read(10, 9)], True),
    )
    for position, (reads, blank_last) in enumerate(exports, start=1):
        data = make_retailer_usage_from_nem12(
            truth,
            basis="nem_aest",
            windows=WINDOWS,
            reads=reads,
            labels=LABELS,
            blank_last=blank_last,
        )
        usage.append(land_blob(root, SourceType.RETAILER_USAGE, f"synthetic_usage_{position}.csv", data))

    return RawZone(
        root=root,
        specs=specs,
        interval=(interval[0], interval[1]),
        summary=summary,
        usage=(usage[0], usage[1]),
        truth=truth,
        periods=tuple(periods),
    )


def total_kwh(nem12: bytes, *, suffix: str = "E1") -> float:
    """The sum of every value of one register in a NEM12 text."""
    return math.fsum(item.kwh for item in oracle.intervals(nem12) if item.suffix == suffix)
