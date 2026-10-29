"""The `catalog_index` stage (D3 §9): the table, its checks and its provenance. Synthetic records only."""

import json
from datetime import date, datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from energatron.core.canonical import JSONValue, canonical_json, content_hash, sha256_hex
from energatron.core.errors import CheckFailedError, SchemaTooNewError
from energatron.core.ids import dataset_id
from energatron.core.models.catalog import SourceType
from energatron.core.models.provenance import StageCode, StageProvenance
from energatron.services.stages import catalog_index
from energatron.services.stages.registry import get_stage
from energatron.services.stages.runner import prepare_stage, run_stage

SOURCE_TS = datetime(2026, 1, 2, 3, 4, 5)
CHECKS = [
    "records_valid",
    "source_matches_file",
    "dataset_id_unique",
    "sha256_unique",
    "id_consistent",
    "supersedes_valid",
]


def _code() -> StageCode:
    source = sha256_hex(b"catalog_index")
    digest = content_hash({"source_sha256": source, "packages": {}, "python": "3.14.0"})
    return StageCode(stage_sha256=digest, source_sha256=source, python="3.14.0", packages={}, files=["__init__.py"])


CODE = _code()


def record(source: str, payload: bytes, **changes: JSONValue) -> dict[str, JSONValue]:
    digest = sha256_hex(payload)
    ident = dataset_id(source, SOURCE_TS, digest)
    doc: dict[str, JSONValue] = {
        "kind": "catalog_record",
        "schema_version": 1,
        "dataset_id": ident,
        "source": source,
        "sha256": digest,
        "bytes": len(payload),
        "stored_path": f"data/raw/{source}/2026/{ident}.csv",
        "original_filename": "synthetic_original_name.csv",
        "landed_at": "2026-01-02T00:00:00Z",
        "file_mtime": None,
        "source_ts": "2026-01-02T03:04:05",
        "coverage": None,
        "detector": {"name": source, "version": 1, "evidence": ["filename", "content"]},
        "extra": {"interval_minutes": 5},
        "supersedes": None,
        "notes": "synthetic note",
    }
    return doc | changes


def lines(*docs: dict[str, JSONValue]) -> bytes:
    return b"".join(canonical_json(doc) + b"\n" for doc in docs)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    for source in SourceType:
        (workspace / "data" / "raw" / source.value).mkdir(parents=True)
        (workspace / "data" / "raw" / source.value / "_catalog.jsonl").write_bytes(b"")
    (workspace / "params.yaml").write_text("kind: params\nschema_version: 1\n")
    return workspace


def write_catalog(root: Path, source: str, data: bytes) -> None:
    (root / "data" / "raw" / source / "_catalog.jsonl").write_bytes(data)


def run_index(root: Path) -> None:
    spec = get_stage("catalog_index")
    prepared = prepare_stage(spec, root=root, params_file=root / "params.yaml")
    run_stage(spec, prepared.inputs, prepared.params, prepared.outputs, code=CODE, recorded_code=CODE.stage_sha256)


def output(root: Path) -> Path:
    return root / "data" / "marts" / "catalog"


def table(root: Path) -> pa.Table:
    return pq.read_table(output(root) / "datasets.parquet")


def checks(root: Path) -> dict[str, tuple[str, dict[str, JSONValue]]]:
    provenance = StageProvenance.from_document(json.loads((output(root) / "_provenance.json").read_text()))
    return {check.name: (check.status, dict(check.details)) for check in provenance.checks}


def failing(root: Path) -> dict[str, tuple[str, dict[str, JSONValue]]]:
    with pytest.raises(CheckFailedError):
        run_index(root)
    return {name: result for name, result in checks(root).items() if result[0] == "fail"}


SCHEMA = pa.schema(
    [
        ("dataset_id", pa.string()),
        ("source", pa.string()),
        ("sha256", pa.string()),
        ("stored_path", pa.string()),
        ("bytes", pa.int64()),
        ("landed_at", pa.timestamp("us", tz="UTC")),
        ("file_mtime", pa.timestamp("us", tz="UTC")),
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


def test_spec_reads_one_catalog_per_source_and_writes_the_catalog_mart() -> None:
    spec = catalog_index.SPEC
    assert spec.name == "catalog_index"
    assert dict(spec.inputs) == {f"catalog.{s}": f"data/raw/{s}/_catalog.jsonl" for s in sorted(SourceType)}
    assert spec.params == ()
    assert dict(spec.outputs) == {"catalog": "data/marts/catalog"}


def test_empty_catalogs_give_a_zero_row_table_and_every_check_passes(root: Path) -> None:
    run_index(root)
    result = table(root)
    assert result.num_rows == 0
    assert result.schema.remove_metadata() == SCHEMA
    assert result.schema.metadata == {b"energatron.kind": b"catalog_table", b"energatron.schema_version": b"1"}
    assert list(checks(root)) == CHECKS
    assert all(status == "pass" for status, _ in checks(root).values())
    assert sorted(path.name for path in output(root).iterdir()) == ["_provenance.json", "datasets.parquet"]


def test_columns_hold_each_record_and_leave_out_private_fields(root: Path) -> None:
    aware = record(
        "offer_cdr",
        b"offer",
        source_ts="2026-01-02T03:04:05+10:30",
        dataset_id=dataset_id("offer_cdr", datetime.fromisoformat("2026-01-02T03:04:05+10:30"), sha256_hex(b"offer")),
    )
    aware["stored_path"] = f"data/raw/offer_cdr/2026/{aware['dataset_id']}.json"
    naive = record(
        "sapn_interval",
        b"interval",
        file_mtime="2026-01-01T12:00:00Z",
        coverage={"start": "2025-01-01", "end": "2025-12-31"},
    )
    write_catalog(root, "offer_cdr", lines(aware))
    write_catalog(root, "sapn_interval", lines(naive))
    run_index(root)
    rows = {row["dataset_id"]: row for row in table(root).to_pylist()}
    assert list(rows) == sorted(rows)
    row = rows[naive["dataset_id"]]  # type: ignore[index]
    assert row["source"] == "sapn_interval"
    assert row["source_ts"] == "2026-01-02T03:04:05"
    assert row["source_ts_compact"] == "20260102T030405"
    assert row["file_mtime"] is not None and row["file_mtime"].isoformat() == "2026-01-01T12:00:00+00:00"
    assert (row["coverage_start"], row["coverage_end"]) == (date(2025, 1, 1), date(2025, 12, 31))
    assert (row["detector_name"], row["detector_version"], row["detector_evidence"]) == (
        "sapn_interval",
        1,
        ["filename", "content"],
    )
    assert row["extra_json"] == canonical_json({"interval_minutes": 5}).decode()
    assert (row["supersedes"], row["superseded_by"], row["is_current"]) == (None, None, True)
    other = rows[aware["dataset_id"]]  # type: ignore[index]
    assert other["source_ts"] == "2026-01-02T03:04:05+10:30"
    assert other["source_ts_compact"] == "20260101T163405"
    assert other["file_mtime"] is None and other["coverage_start"] is None
    assert "original_filename" not in table(root).column_names and "notes" not in table(root).column_names
    data = b"".join(path.read_bytes() for path in output(root).iterdir())
    assert b"synthetic_original_name" not in data and b"synthetic note" not in data


def test_superseded_by_and_is_current(root: Path) -> None:
    old = record("sapn_interval", b"old")
    new = record("sapn_interval", b"new", supersedes=old["dataset_id"])
    write_catalog(root, "sapn_interval", lines(old, new))
    run_index(root)
    rows = {row["dataset_id"]: row for row in table(root).to_pylist()}
    assert (rows[old["dataset_id"]]["superseded_by"], rows[old["dataset_id"]]["is_current"]) == (  # type: ignore[index]
        new["dataset_id"],
        False,
    )
    assert rows[new["dataset_id"]]["is_current"] is True  # type: ignore[index]
    assert all(status == "pass" for status, _ in checks(root).values())


def test_records_valid_fails_naming_line_numbers_and_leaves_bad_lines_out(root: Path) -> None:
    good = record("sapn_interval", b"good")
    write_catalog(root, "sapn_interval", lines(good) + b"not json\n" + lines(good).replace(b"\n", b"\r\n"))
    write_catalog(root, "elec_bill", b"{}\n")
    failed = failing(root)
    assert list(failed) == ["records_valid"]
    assert failed["records_valid"][1] == {"invalid": {"elec_bill": [1], "sapn_interval": [2, 3]}}
    assert table(root).num_rows == 1
    provenance = StageProvenance.from_document(json.loads((output(root) / "_provenance.json").read_text()))
    ids = {item.name: item.dataset_ids for item in provenance.inputs}
    assert ids["catalog.sapn_interval"] == [good["dataset_id"]]
    assert ids["catalog.elec_bill"] == []


def test_source_matches_file_fails_for_a_record_in_another_sources_file(root: Path) -> None:
    write_catalog(root, "sapn_summary", lines(record("sapn_interval", b"x")))
    failed = failing(root)
    assert list(failed) == ["source_matches_file"]
    assert failed["source_matches_file"][1] == {"mismatched": {"sapn_summary": 1}}


def test_a_repeated_record_fails_both_uniqueness_checks(root: Path) -> None:
    doc = record("sapn_interval", b"x")
    write_catalog(root, "sapn_interval", lines(doc, doc))
    failed = failing(root)
    assert list(failed) == ["dataset_id_unique", "sha256_unique"]
    assert failed["dataset_id_unique"][1] == {"duplicates": 1}


def test_sha256_unique_fails_across_sources(root: Path) -> None:
    write_catalog(root, "sapn_interval", lines(record("sapn_interval", b"same")))
    write_catalog(root, "sapn_summary", lines(record("sapn_summary", b"same")))
    failed = failing(root)
    assert list(failed) == ["sha256_unique"]
    assert failed["sha256_unique"][1] == {"duplicates": 1}


def test_id_consistent_fails_when_an_id_does_not_recompute(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write_catalog(root, "sapn_interval", lines(record("sapn_interval", b"x")))
    monkeypatch.setattr(catalog_index, "make_dataset_id", lambda source, ts, digest: "sapn_interval-x-00000000")
    failed = failing(root)
    assert list(failed) == ["id_consistent"]
    assert failed["id_consistent"][1] == {"inconsistent": 1}


@pytest.mark.parametrize("case", ["missing_target", "other_source", "multiple_successors", "cycles"])
def test_supersedes_valid_fails_for_each_rule(root: Path, case: str) -> None:
    base = record("sapn_interval", b"base")
    if case == "missing_target":
        missing = dataset_id("sapn_interval", SOURCE_TS, sha256_hex(b"missing"))
        write_catalog(root, "sapn_interval", lines(record("sapn_interval", b"x", supersedes=missing)))
    elif case == "other_source":
        write_catalog(root, "sapn_interval", lines(base))
        write_catalog(root, "sapn_summary", lines(record("sapn_summary", b"x", supersedes=base["dataset_id"])))
    elif case == "multiple_successors":
        first = record("sapn_interval", b"one", supersedes=base["dataset_id"])
        second = record("sapn_interval", b"two", supersedes=base["dataset_id"])
        write_catalog(root, "sapn_interval", lines(base, first, second))
    else:
        a_id = dataset_id("sapn_interval", SOURCE_TS, sha256_hex(b"a"))
        b_doc = record("sapn_interval", b"b", supersedes=a_id)
        a_doc = record("sapn_interval", b"a", supersedes=b_doc["dataset_id"])
        write_catalog(root, "sapn_interval", lines(a_doc, b_doc))
    failed = failing(root)
    assert list(failed) == ["supersedes_valid"]
    details = failed["supersedes_valid"][1]
    assert details[case] != 0
    assert all(value == 0 for key, value in details.items() if key != case)


def test_a_newer_version_line_fails_the_stage(root: Path) -> None:
    write_catalog(root, "sapn_interval", lines(record("sapn_interval", b"x", schema_version=2)))
    with pytest.raises(SchemaTooNewError):
        run_index(root)
    assert not output(root).exists()
