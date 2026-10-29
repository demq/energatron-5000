"""Catalog lines and the raw-zone layout (D1 §4.1, §4.2; D3 §3.2, §4.1). Synthetic values only."""

import json
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from energatron.core.canonical import JSONValue, canonical_json, sha256_hex
from energatron.core.errors import CatalogCorruptError, Category, InternalError, InvalidInputError, SchemaTooNewError
from energatron.core.ids import dataset_id
from energatron.core.models.catalog import CatalogRecord, SourceType, blob_key, catalog_lines, parse_catalog
from energatron.core.models.storage import StoredBlob, check_blob_key
from energatron.core.models.workspace import WorkspacePaths

DIGEST = sha256_hex(b"synthetic")
SOURCE_TS = datetime(2026, 10, 23, 10, 3, 45)
DATASET = dataset_id("sapn_interval", SOURCE_TS, DIGEST)
FILENAME = "synthetic_interval_file.csv"
ROOT = Path("/scratch/workspace")


def record_doc(**changes: JSONValue) -> dict[str, JSONValue]:
    doc: dict[str, JSONValue] = {
        "kind": "catalog_record",
        "schema_version": 1,
        "dataset_id": DATASET,
        "source": "sapn_interval",
        "sha256": DIGEST,
        "bytes": 1234,
        "stored_path": f"data/raw/sapn_interval/2026/{DATASET}.csv",
        "original_filename": FILENAME,
        "landed_at": "2026-10-23T00:32:10Z",
        "file_mtime": None,
        "source_ts": "2026-10-23T10:03:45",
        "coverage": None,
        "detector": {"name": "sapn_interval", "version": 1, "evidence": ["filename", "content"]},
        "extra": {"interval_minutes": 5},
        "supersedes": None,
        "notes": "",
    }
    return doc | changes


def line_of(doc: dict[str, JSONValue]) -> bytes:
    return canonical_json(doc) + b"\n"


# The raw-zone layout


def test_blob_key_files_a_dataset_under_its_source_and_year() -> None:
    assert blob_key(SourceType.SAPN_INTERVAL, DATASET, ".csv") == f"data/raw/sapn_interval/2026/{DATASET}.csv"


def test_blob_key_takes_the_year_from_the_id_so_an_aware_source_ts_is_in_utc() -> None:
    # 00:30 on 1 January at +10:30 is still the previous year in UTC, which is what the ID holds.
    aware = datetime(2027, 1, 1, 0, 30, tzinfo=timezone(timedelta(hours=10, minutes=30)))
    offer = dataset_id("offer_cdr", aware, DIGEST)
    assert blob_key(SourceType.OFFER_CDR, offer, ".json") == f"data/raw/offer_cdr/2026/{offer}.json"


@pytest.mark.parametrize("ext", [".CSV", "csv", ".txt", ""])
def test_blob_key_refuses_other_extensions(ext: str) -> None:
    with pytest.raises(ValueError, match="extension"):
        blob_key(SourceType.SAPN_INTERVAL, DATASET, ext)


def test_blob_key_refuses_an_id_of_another_source_or_no_id() -> None:
    with pytest.raises(ValueError, match="source"):
        blob_key(SourceType.SAPN_SUMMARY, DATASET, ".csv")
    with pytest.raises(ValueError):
        blob_key(SourceType.SAPN_INTERVAL, "not-an-id", ".csv")


def test_workspace_paths_lay_out_the_raw_zone() -> None:
    paths = WorkspacePaths(root=ROOT)
    assert paths.raw_dir(SourceType.RETAILER_TXN) == ROOT / "data" / "raw" / "retailer_txn"
    assert paths.catalog_path(SourceType.RETAILER_TXN) == ROOT / "data" / "raw" / "retailer_txn" / "_catalog.jsonl"
    assert paths.blob_key(SourceType.SAPN_INTERVAL, DATASET, ".csv") == blob_key(
        SourceType.SAPN_INTERVAL, DATASET, ".csv"
    )
    assert paths.relative(paths.raw_dir(SourceType.SAPN_INTERVAL) / "2026" / f"{DATASET}.csv") == blob_key(
        SourceType.SAPN_INTERVAL, DATASET, ".csv"
    )


# Blob keys (D1 §4.2)


@pytest.mark.parametrize("key", ["data/raw/x/2026/y.csv", "data/raw/sapn_interval/_catalog.jsonl"])
def test_valid_blob_keys(key: str) -> None:
    assert check_blob_key(key) == key
    assert StoredBlob(key=key, sha256=DIGEST, bytes=1).key == key


@pytest.mark.parametrize(
    "key",
    [
        "/data/raw/x/y.csv",
        "data/raw/../x/y.csv",
        "data/raw/x\\y.csv",
        "data/staged/x/y.csv",
        "data/raw/",
        "data/raw/x/",
        "data/raw//y.csv",
        "data/raw/./y.csv",
        "",
    ],
)
def test_invalid_blob_keys_are_programming_errors(key: str) -> None:
    with pytest.raises(InternalError):
        check_blob_key(key)
    with pytest.raises(ValueError):
        StoredBlob(key=key, sha256=DIGEST, bytes=1)


def test_a_key_prefix_ends_with_a_slash() -> None:
    assert check_blob_key("data/raw/sapn_interval/", prefix=True) == "data/raw/sapn_interval/"
    assert check_blob_key("data/raw/", prefix=True) == "data/raw/"
    for bad in ("data/raw/sapn_interval", "data/raw/../", "/data/raw/x/", "data/raw//"):
        with pytest.raises(InternalError):
            check_blob_key(bad, prefix=True)


# stored_path


@pytest.mark.parametrize(
    "stored_path",
    [
        f"data/raw/sapn_interval/2025/{DATASET}.csv",
        f"data/raw/sapn_summary/2026/{DATASET}.csv",
        f"data/raw/sapn_interval/2026/{DATASET}.txt",
        f"data/raw/sapn_interval/2026/{DATASET}.CSV",
        f"data/raw/sapn_interval/{DATASET}.csv",
        "data/raw/sapn_interval/2026/other.csv",
    ],
    ids=["wrong-year", "wrong-source-dir", "other-extension", "upper-case-extension", "no-year", "other-name"],
)
def test_stored_path_must_be_the_blob_key(stored_path: str) -> None:
    with pytest.raises(InvalidInputError):
        CatalogRecord.from_document(record_doc(stored_path=stored_path))


@pytest.mark.parametrize("ext", [".csv", ".pdf", ".json", ".yaml"])
def test_stored_path_takes_any_of_the_four_extensions(ext: str) -> None:
    record = CatalogRecord.from_document(record_doc(stored_path=f"data/raw/sapn_interval/2026/{DATASET}{ext}"))
    assert record.stored_path.endswith(ext)


# from_json_line


def test_a_written_line_reads_back_as_the_same_record() -> None:
    record = CatalogRecord.from_document(record_doc())
    line = canonical_json(record.model_dump(mode="json")) + b"\n"
    assert CatalogRecord.from_json_line(line) == record
    assert CatalogRecord.from_json_line(line.decode()) == record
    assert CatalogRecord.from_json_line(line.rstrip(b"\n")) == record


def test_an_aware_source_ts_keeps_its_offset() -> None:
    aware = datetime(2026, 10, 23, 10, 3, 45, tzinfo=UTC)
    offer = dataset_id("offer_cdr", aware, DIGEST)
    doc = record_doc(
        dataset_id=offer,
        source="offer_cdr",
        stored_path=f"data/raw/offer_cdr/2026/{offer}.json",
        source_ts="2026-10-23T10:03:45Z",
        detector={"name": "offer_cdr", "version": 1, "evidence": ["content"]},
    )
    record = CatalogRecord.from_json_line(line_of(doc))
    assert record.source_ts == aware
    assert record.source_ts.tzinfo is not None


def corrupt(line: bytes | str, number: int | None = 7) -> CatalogCorruptError:
    with pytest.raises(CatalogCorruptError) as caught:
        CatalogRecord.from_json_line(line, number=number)
    return caught.value


@pytest.mark.parametrize(
    "line",
    [
        b"{not json",
        b"",
        b"\n",
        b"[]",
        b'"text"',
        b"\xff\xfe",
        b'{"a": 1}\n{"b": 2}\n',
        line_of(record_doc(kind="workspace_config")),
        line_of(record_doc(bytes=0)),
        line_of(record_doc(schema_version=0)),
        line_of(record_doc(unknown_field=1)),
    ],
    ids=[
        "not-json",
        "empty",
        "blank",
        "array",
        "string",
        "not-utf8",
        "two-lines",
        "other-kind",
        "invalid-field",
        "bad-version",
        "unknown-field",
    ],
)
def test_a_bad_line_is_a_corrupt_catalog_with_its_number(line: bytes) -> None:
    error = corrupt(line)
    assert error.category is Category.STATE
    assert error.details["line"] == 7


def test_nan_and_duplicate_keys_are_refused() -> None:
    nan_line = line_of(record_doc()).replace(b'"bytes":1234', b'"bytes":NaN')
    assert corrupt(nan_line).details["line"] == 7
    duplicate = line_of(record_doc()).replace(b'"bytes":1234', b'"bytes":1234,"bytes":99')
    assert corrupt(duplicate).details["line"] == 7


def test_a_corrupt_line_never_repeats_its_text() -> None:
    doc = record_doc(bytes=0, notes="synthetic private note")
    error = corrupt(line_of(doc))
    shown = f"{error.message} {json.dumps(error.details)} {error!r}"
    assert FILENAME not in shown
    assert "synthetic private note" not in shown
    assert "bytes" in shown  # the field and the rule are named
    bad_json = corrupt(f'{{"original_filename": "{FILENAME}"'.encode())
    assert FILENAME not in f"{bad_json.message} {json.dumps(bad_json.details)}"


def test_the_line_number_is_null_when_unknown() -> None:
    assert corrupt(b"{", number=None).details["line"] is None


def test_a_line_from_a_newer_energatron_says_so() -> None:
    with pytest.raises(SchemaTooNewError):
        CatalogRecord.from_json_line(line_of(record_doc(schema_version=2)))


# Whole catalog files (`parse_catalog`): the rules every reader shares, the adapter and stage code alike


def other_doc(payload: bytes) -> dict[str, JSONValue]:
    digest = sha256_hex(payload)
    ident = dataset_id("sapn_interval", SOURCE_TS, digest)
    return record_doc(dataset_id=ident, sha256=digest, stored_path=f"data/raw/sapn_interval/2026/{ident}.csv")


def test_parse_catalog_reads_every_line_in_order() -> None:
    first, second = record_doc(), other_doc(b"second")
    records = parse_catalog(line_of(first) + line_of(second), SourceType.SAPN_INTERVAL)
    assert [record.dataset_id for record in records] == [first["dataset_id"], second["dataset_id"]]


def test_parse_catalog_of_an_empty_file_is_empty() -> None:
    assert parse_catalog(b"", SourceType.SAPN_INTERVAL) == []


@pytest.mark.parametrize(
    ("content", "line", "reason"),
    [
        (line_of(record_doc()).rstrip(b"\n"), 1, "unterminated"),
        (line_of(record_doc()) + line_of(other_doc(b"x")).rstrip(b"\n"), 2, "unterminated"),
        (line_of(record_doc()).replace(b"\n", b"\r\n"), 1, "crlf"),
        (line_of(record_doc()) + b"\n", 2, "invalid_json"),
        (line_of(record_doc()) + b"not json\n", 2, "invalid_json"),
    ],
    ids=["one-unterminated", "last-unterminated", "crlf", "blank-line", "bad-line"],
)
def test_parse_catalog_refuses_a_malformed_file_naming_source_and_line(content: bytes, line: int, reason: str) -> None:
    with pytest.raises(CatalogCorruptError) as caught:
        parse_catalog(content, SourceType.SAPN_INTERVAL)
    assert caught.value.details["source"] == "sapn_interval"
    assert caught.value.details["line"] == line
    assert caught.value.details["reason"] == reason


def test_parse_catalog_refuses_a_record_of_another_source() -> None:
    with pytest.raises(CatalogCorruptError) as caught:
        parse_catalog(line_of(record_doc()), SourceType.SAPN_SUMMARY)
    assert caught.value.details == {"source": "sapn_summary", "line": 1, "reason": "wrong_source"}


def test_parse_catalog_passes_a_newer_version_on_as_schema_too_new() -> None:
    with pytest.raises(SchemaTooNewError):
        parse_catalog(line_of(record_doc(schema_version=2)), SourceType.SAPN_INTERVAL)


# Line by line (`catalog_lines`): the same rules, but a bad line is reported, not raised (the catalog_index stage)


def test_catalog_lines_reports_each_line_with_its_number() -> None:
    first, second = record_doc(), other_doc(b"second")
    content = line_of(first) + b"not json\n" + line_of(second).replace(b"\n", b"\r\n") + line_of(first).rstrip(b"\n")
    lines = list(catalog_lines(content))
    assert [line.number for line in lines] == [1, 2, 3, 4]
    assert lines[0].record is not None and lines[0].record.dataset_id == first["dataset_id"]
    assert lines[0].error is None
    reasons = [line.error.details["reason"] if line.error is not None else None for line in lines]
    assert reasons == [None, "invalid_json", "crlf", "unterminated"]
    assert all(line.record is None for line in lines[1:])


def test_catalog_lines_keeps_a_record_of_any_source() -> None:
    (line,) = list(catalog_lines(line_of(record_doc())))
    assert line.record is not None and line.record.source == SourceType.SAPN_INTERVAL


def test_catalog_lines_of_an_empty_file_is_empty() -> None:
    assert list(catalog_lines(b"")) == []


def test_catalog_lines_passes_a_newer_version_on_as_schema_too_new() -> None:
    with pytest.raises(SchemaTooNewError):
        list(catalog_lines(line_of(record_doc()) + line_of(record_doc(schema_version=2))))


@pytest.mark.parametrize(
    ("first", "source", "reason"),
    [
        (b"not json\n", SourceType.SAPN_INTERVAL, "invalid_json"),
        (line_of(record_doc()).replace(b"\n", b"\r\n"), SourceType.SAPN_INTERVAL, "crlf"),
        (line_of(record_doc()), SourceType.SAPN_SUMMARY, "wrong_source"),
    ],
    ids=["invalid", "crlf", "wrong-source"],
)
def test_parse_catalog_stops_at_the_first_bad_line_before_a_newer_one(
    first: bytes, source: SourceType, reason: str
) -> None:
    newer = line_of(other_doc(b"newer") | {"schema_version": 2})
    with pytest.raises(CatalogCorruptError) as caught:
        parse_catalog(first + newer, source)
    assert (caught.value.details["line"], caught.value.details["reason"]) == (1, reason)


def test_catalog_lines_is_lazy() -> None:
    lines = catalog_lines(b"not json\n" + line_of(record_doc(schema_version=2)))
    first = next(iter(lines))
    assert first.error is not None and first.number == 1
