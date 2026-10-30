"""The `catalog_index` stage (D3 §9): every source's catalog as one table, with the catalog's integrity checks.

It reads catalog files only, never blobs. A line that fails to parse is left out of the table and reported by
`records_valid`; a line from a newer energatron fails the stage, because the tool is older than the data. Check
details hold counts and line numbers, never values. `original_filename` and `notes` stay out of the table: marts are
what agents and reports read (plan §14.5).

`id_consistent` recomputes `dataset_id` and `stored_path` as D3 §9 asks, but can't fail on real data:
`CatalogRecord`'s validator already refuses such a line, which then counts against `records_valid`. It stays as a
guard in case that validator is ever loosened.
"""

from collections import Counter
from collections.abc import Iterable
from datetime import UTC
from typing import Final

import pandas as pd
import pyarrow as pa

from energatron.core.canonical import JSONValue, canonical_json
from energatron.core.ids import compact_source
from energatron.core.ids import dataset_id as make_dataset_id
from energatron.core.models.catalog import CATALOG_FILENAME, CatalogRecord, blob_key, catalog_lines
from energatron.services.files import write_parquet
from energatron.services.stages.runner import StageRun, StageSpec, per_source_inputs

TABLE_KIND: Final = "catalog_table"
TABLE_VERSION: Final = 1
TABLE_FILENAME: Final = "datasets.parquet"

SCHEMA: Final = pa.schema(
    [
        ("dataset_id", pa.string()),
        ("source", pa.string()),
        ("sha256", pa.string()),
        ("stored_path", pa.string()),
        ("bytes", pa.int64()),
        ("landed_at", pa.timestamp("us", tz=UTC)),
        ("file_mtime", pa.timestamp("us", tz=UTC)),
        ("source_ts", pa.string()),
        ("source_ts_compact", pa.string()),
        ("coverage_start", pa.date32()),
        ("coverage_end", pa.date32()),
        ("detector_name", pa.string()),
        ("detector_version", pa.int32()),
        ("detector_evidence", pa.list_(pa.string())),
        ("extra_json", pa.string()),
        ("supersedes", pa.string()),
        ("superseded_by", pa.string()),
        ("is_current", pa.bool_()),
    ]
)


def _run(run: StageRun) -> None:
    found: list[tuple[str, CatalogRecord]] = []  # (the file's source, its record), in input order
    invalid: dict[str, list[int]] = {}
    for source, path in run.inputs("catalog").items():
        for line in catalog_lines(path.read_bytes()):
            if line.record is None:
                invalid.setdefault(source, []).append(line.number)
            else:
                found.append((source, line.record))
    records = [record for _, record in found]

    run.check("records_valid", not invalid, invalid=invalid)
    mismatched = Counter(source for source, record in found if record.source.value != source)
    run.check("source_matches_file", not mismatched, mismatched=dict(sorted(mismatched.items())))
    id_duplicates = _duplicates(record.dataset_id for record in records)
    run.check("dataset_id_unique", id_duplicates == 0, duplicates=id_duplicates)
    sha_duplicates = _duplicates(record.sha256 for record in records)
    run.check("sha256_unique", sha_duplicates == 0, duplicates=sha_duplicates)
    inconsistent = sum(1 for record in records if not _id_consistent(record))
    run.check("id_consistent", inconsistent == 0, inconsistent=inconsistent)
    problems = _supersedes_problems(records)
    run.check("supersedes_valid", not any(problems.values()), **problems)

    successors = _successors(records)
    frame = pd.DataFrame([_row(record, successors) for record in records], columns=SCHEMA.names)
    write_parquet(
        frame,
        run.output("catalog") / TABLE_FILENAME,
        kind=TABLE_KIND,
        schema_version=TABLE_VERSION,
        sort_by=["dataset_id"],
        schema=SCHEMA,
    )


def _duplicates(values: Iterable[str]) -> int:
    """How many values appear more than once."""
    return sum(1 for count in Counter(values).values() if count > 1)


def _id_consistent(record: CatalogRecord) -> bool:
    """`dataset_id` and `stored_path` recompute from the record's own fields (D3 §4.1)."""
    if record.dataset_id != make_dataset_id(record.source.value, record.source_ts, record.sha256):
        return False
    extension = "." + record.stored_path.rpartition(".")[2]
    try:
        return record.stored_path == blob_key(record.source, record.dataset_id, extension)
    except ValueError:
        return False


def _successors(records: list[CatalogRecord]) -> dict[str, list[str]]:
    successors: dict[str, set[str]] = {}
    for record in records:
        if record.supersedes is not None:
            successors.setdefault(record.supersedes, set()).add(record.dataset_id)
    return {target: sorted(ids) for target, ids in successors.items()}


def _supersedes_problems(records: list[CatalogRecord]) -> dict[str, JSONValue]:
    """Counts per rule: a target that doesn't exist, one of another source, more than one successor, cycles."""
    by_id = {record.dataset_id: record for record in records}
    missing = other_source = 0
    for record in records:
        if record.supersedes is None:
            continue
        target = by_id.get(record.supersedes)
        if target is None:
            missing += 1
        elif target.source != record.source:
            other_source += 1
    multiple = sum(1 for ids in _successors(records).values() if len(ids) > 1)
    on_cycle: set[str] = set()
    for start in sorted(by_id):
        path: list[str] = []
        current: str | None = start
        while current is not None and current in by_id and current not in path:
            path.append(current)
            current = by_id[current].supersedes
        if current is not None and current in path:
            on_cycle.update(path[path.index(current) :])
    return {
        "missing_target": missing,
        "other_source": other_source,
        "multiple_successors": multiple,
        "cycles": len(on_cycle),
    }


def _row(record: CatalogRecord, successors: dict[str, list[str]]) -> dict[str, object]:
    persisted = record.model_dump(mode="json")
    following = successors.get(record.dataset_id)
    superseded_by = following[0] if following else None
    return {
        "dataset_id": record.dataset_id,
        "source": record.source.value,
        "sha256": record.sha256,
        "stored_path": record.stored_path,
        "bytes": record.bytes,
        "landed_at": record.landed_at,
        "file_mtime": record.file_mtime,
        "source_ts": persisted["source_ts"],
        "source_ts_compact": compact_source(record.source_ts),
        "coverage_start": record.coverage.start if record.coverage is not None else None,
        "coverage_end": record.coverage.end if record.coverage is not None else None,
        "detector_name": record.detector.name.value,
        "detector_version": record.detector.version,
        "detector_evidence": list(record.detector.evidence),
        "extra_json": canonical_json(dict(record.extra)).decode("utf-8"),
        "supersedes": record.supersedes,
        "superseded_by": superseded_by,
        "is_current": superseded_by is None,
    }


SPEC: Final = StageSpec(
    name="catalog_index",
    inputs=per_source_inputs("catalog", CATALOG_FILENAME),
    params=(),
    outputs={"catalog": "data/marts/catalog"},
    impl=_run,
)
