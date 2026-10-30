"""The `parse_nem12` stage (P2 spec §7.1): every landed SAPN interval file as one staged table, plus an index.

It reads the raw directory `data/raw/sapn_interval` whole: the catalog, then each blob the catalog names, in catalog
order (§3.1). Each dataset becomes `<dataset_id>.parquet` and every catalogued dataset a row of `index.parquet`.
Nothing is merged or dropped here, and a superseded dataset is read like any other: `merge_interval` decides what to
use. A dataset that can't be read is left out and named, with a reason, in the `datasets_read` check.

The reader is `core.nem12.read_nem12`, whose result has no identifier field, so no NMI or meter serial can reach a
table. Check details hold counts and dataset IDs, never values.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Final

import pandas as pd

from energatron.core.canonical import sha256_hex
from energatron.core.errors import InvalidInputError
from energatron.core.models.catalog import (
    CATALOG_FILENAME,
    CatalogRecord,
    SourceType,
    parse_catalog,
    precedence_order,
    raw_key,
)
from energatron.core.models.meter import (
    INDEX_COLUMNS,
    INDEX_KIND,
    INDEX_SORT,
    INDEX_VERSION,
    STAGED_COLUMNS,
    STAGED_KIND,
    STAGED_SORT,
    STAGED_VERSION,
)
from energatron.core.nem12 import Nem12Facts, Nem12Read, read_nem12
from energatron.services.files import write_parquet
from energatron.services.stages.runner import StageRun, StageSpec
from energatron.services.stages.tables import INDEX_SCHEMA, STAGED_SCHEMA

SOURCE: Final = SourceType.SAPN_INTERVAL
KNOWN_DETECTOR_VERSIONS: Final = frozenset({1})
"""The `sapn_interval` parser versions whose files this stage reads (P2 spec §3.1)."""
INDEX_FILENAME: Final = "index.parquet"


@dataclass
class _Findings:
    """What the pass over the datasets collects for the checks."""

    mismatched: list[str]  # blob missing, or its hash isn't the record's
    unknown_detector: list[str]
    not_read: dict[str, str]  # dataset → why it wasn't read
    bad_unit: list[str]
    faulty_days: dict[str, int]  # dataset → (suffix, date) groups without 1440 / L rows
    v_faults: dict[str, int]
    negative: dict[str, int]
    disagree: list[str]


def _run(run: StageRun) -> None:
    raw = run.input("raw")
    try:
        catalog = (raw / CATALOG_FILENAME).read_bytes()
    except OSError:
        raise InvalidInputError("The raw directory has no readable catalog.", input="raw") from None
    records = parse_catalog(catalog, SOURCE)
    output = run.output("staged")

    findings = _Findings([], [], {}, [], {}, {}, {}, [])
    facts: dict[str, Nem12Facts] = {}  # only the facts are kept: each table is written, then dropped
    for record in records:
        read = _read(raw, record, findings)
        if read is None:
            continue
        facts[record.dataset_id] = read.facts
        _inspect(record, read, findings)
        write_parquet(
            _staged_frame(read, record.dataset_id),
            output / f"{record.dataset_id}.parquet",
            kind=STAGED_KIND,
            schema_version=STAGED_VERSION,
            sort_by=STAGED_SORT,
            schema=STAGED_SCHEMA,
        )
    write_parquet(
        _index_frame(records, facts),
        output / INDEX_FILENAME,
        kind=INDEX_KIND,
        schema_version=INDEX_VERSION,
        sort_by=INDEX_SORT,
        schema=INDEX_SCHEMA,
    )

    run.check("blobs_match_catalog", not findings.mismatched, mismatched=list(findings.mismatched))
    run.check("detector_known", not findings.unknown_detector, unknown=list(findings.unknown_detector))
    run.check("datasets_read", not findings.not_read, not_read=dict(findings.not_read))
    run.check("values_per_day", not findings.faulty_days, faulty=dict(findings.faulty_days))
    run.check("units", not findings.bad_unit, bad_unit=list(findings.bad_unit))
    run.check("v_days_covered", not findings.v_faults, faults=dict(findings.v_faults))
    run.check("non_negative", not findings.negative, negative=dict(findings.negative))
    run.check("catalog_agrees", not findings.disagree, disagree=list(findings.disagree))


def _read(raw: Path, record: CatalogRecord, findings: _Findings) -> Nem12Read | None:
    """Read one dataset's blob, or note why it wasn't: a blob that isn't the record's, a detector this stage doesn't
    know, or a file the reader refuses. Only a blob that matches its record is read."""
    ident = record.dataset_id
    data = _blob(raw, record)
    blob_ok = data is not None and sha256_hex(data) == record.sha256
    detector_ok = record.detector.name == SOURCE and record.detector.version in KNOWN_DETECTOR_VERSIONS
    if not blob_ok:
        findings.mismatched.append(ident)
    if not detector_ok:
        findings.unknown_detector.append(ident)
    if not blob_ok or data is None:
        findings.not_read[ident] = "blob_mismatch"
        return None
    if not detector_ok:
        findings.not_read[ident] = "detector_unknown"
        return None
    try:
        return read_nem12(data)
    except InvalidInputError as error:
        reason = error.details.get("reason")
        findings.not_read[ident] = reason if isinstance(reason, str) else "invalid"
        if reason == "bad_unit":
            findings.bad_unit.append(ident)
        return None


def _blob(raw: Path, record: CatalogRecord) -> bytes | None:
    """The blob's bytes, found under `raw` whatever the directory is called (so `--input` overrides work)."""
    relative = record.stored_path.removeprefix(raw_key(SOURCE))
    try:
        return raw.joinpath(*relative.split("/")).read_bytes()
    except OSError:
        return None


def _inspect(record: CatalogRecord, read: Nem12Read, findings: _Findings) -> None:
    """The per-dataset checks that need the table."""
    ident = record.dataset_id
    table, facts = read.table, read.facts
    expected = 1440 // facts.interval_minutes
    sizes = table.groupby(["suffix", "meter_date"], sort=True).size()
    wrong = int((sizes != expected).sum())
    if wrong:
        findings.faulty_days[ident] = wrong
    if read.v_coverage_faults:
        findings.v_faults[ident] = read.v_coverage_faults
    negative = int((table["kwh"] < 0).sum())
    if negative:
        findings.negative[ident] = negative
    extra = record.extra
    agrees = (
        record.coverage == facts.coverage
        and extra.get("days") == facts.days
        and extra.get("days_v") == facts.days_v
        and extra.get("interval_minutes") == facts.interval_minutes
    )
    if not agrees:
        findings.disagree.append(ident)


def _staged_frame(read: Nem12Read, dataset: str) -> pd.DataFrame:
    return read.table.assign(dataset_id=dataset)[list(STAGED_COLUMNS)]


def _index_frame(records: list[CatalogRecord], found: dict[str, Nem12Facts]) -> pd.DataFrame:
    ranks = {ident: rank for rank, ident in enumerate(precedence_order(records), start=1)}
    superseded = {record.supersedes for record in records if record.supersedes is not None}
    rows: list[dict[str, object]] = []
    for record in records:
        ident = record.dataset_id
        facts = found.get(ident)
        rows.append(
            {
                "dataset_id": ident,
                "precedence": ranks[ident],
                "is_current": ident not in superseded,
                "read_ok": facts is not None,
                "interval_minutes": None if facts is None else facts.interval_minutes,
                "suffixes": [] if facts is None else list(facts.suffixes),
                "days": 0 if facts is None else facts.days,
                "coverage_start": None if facts is None else facts.coverage.start,
                "coverage_end": None if facts is None else facts.coverage.end,
            }
        )
    return pd.DataFrame(rows, columns=list(INDEX_COLUMNS))


SPEC: Final = StageSpec(
    name="parse_nem12",
    inputs={"raw": "data/raw/sapn_interval"},
    params=(),
    outputs={"staged": "data/staged/elec_interval"},
    impl=_run,
)
