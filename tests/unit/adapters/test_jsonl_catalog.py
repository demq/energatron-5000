"""`JsonlCatalog`: one append-only `_catalog.jsonl` per source (D1 §4.1, §13; D3 §3.2, §3.4, §13). Synthetic only."""

import os
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import ClassVar, Literal

import pytest

from energatron.adapters.storage import jsonl_catalog
from energatron.adapters.storage.jsonl_catalog import JsonlCatalog
from energatron.core import persisted
from energatron.core.canonical import JSONValue, canonical_json, sha256_hex
from energatron.core.errors import CatalogCorruptError, IdCollisionError, SchemaTooNewError
from energatron.core.ids import dataset_id
from energatron.core.models import catalog as catalog_module
from energatron.core.models.catalog import CatalogRecord, SourceType, blob_key
from energatron.core.models.workspace import WorkspacePaths
from energatron.core.persisted import migration
from energatron.services import files

SOURCE_TS = datetime(2026, 10, 23, 10, 3, 45)


@pytest.fixture
def paths(tmp_path: Path) -> WorkspacePaths:
    root = tmp_path / "workspace"
    root.mkdir()
    return WorkspacePaths(root=root)


@pytest.fixture
def catalog(paths: WorkspacePaths) -> JsonlCatalog:
    return JsonlCatalog(paths)


def record_doc(
    source: SourceType = SourceType.SAPN_INTERVAL, payload: bytes = b"synthetic", source_ts: datetime = SOURCE_TS
) -> dict[str, JSONValue]:
    digest = sha256_hex(payload)
    ident = dataset_id(source, source_ts, digest)
    return {
        "kind": "catalog_record",
        "schema_version": 1,
        "dataset_id": ident,
        "source": source.value,
        "sha256": digest,
        "bytes": len(payload),
        "stored_path": blob_key(source, ident, ".csv"),
        "original_filename": "synthetic_file.csv",
        "landed_at": "2026-10-23T00:32:10Z",
        "file_mtime": None,
        "source_ts": source_ts.isoformat(),
        "coverage": None,
        "detector": {"name": source.value, "version": 1, "evidence": ["content"]},
        "extra": {},
        "supersedes": None,
        "notes": "",
    }


def make_record(
    source: SourceType = SourceType.SAPN_INTERVAL, payload: bytes = b"synthetic", source_ts: datetime = SOURCE_TS
) -> CatalogRecord:
    return CatalogRecord.from_document(record_doc(source, payload, source_ts))


def line_of(record: CatalogRecord) -> bytes:
    return canonical_json(record.model_dump(mode="json")) + b"\n"


# append and reading back


def test_append_writes_one_canonical_line_per_record(catalog: JsonlCatalog, paths: WorkspacePaths) -> None:
    first, second = make_record(payload=b"one"), make_record(payload=b"two")
    catalog.append(first)
    catalog.append(second)
    assert paths.catalog_path(SourceType.SAPN_INTERVAL).read_bytes() == line_of(first) + line_of(second)


def test_append_creates_a_missing_catalog(catalog: JsonlCatalog, paths: WorkspacePaths) -> None:
    record = make_record(SourceType.RETAILER_TXN)
    catalog.append(record)
    assert paths.catalog_path(SourceType.RETAILER_TXN).read_bytes() == line_of(record)


def test_get_and_find_sha256_search_every_source(catalog: JsonlCatalog) -> None:
    interval = make_record(SourceType.SAPN_INTERVAL, b"one")
    txn = make_record(SourceType.RETAILER_TXN, b"two")
    catalog.append(interval)
    catalog.append(txn)
    assert catalog.get(txn.dataset_id) == txn
    assert catalog.get(interval.dataset_id) == interval
    assert catalog.find_sha256(txn.sha256) == txn
    assert catalog.get(make_record(payload=b"absent").dataset_id) is None
    assert catalog.find_sha256(sha256_hex(b"absent")) is None


def test_an_empty_raw_zone_has_no_records(catalog: JsonlCatalog) -> None:
    assert list(catalog.records()) == []
    assert catalog.get(make_record().dataset_id) is None
    assert catalog.find_sha256(sha256_hex(b"x")) is None


def test_records_come_by_sorted_source_then_append_order(catalog: JsonlCatalog) -> None:
    later = datetime(2026, 12, 1)
    appended = [
        make_record(SourceType.SAPN_INTERVAL, b"a", later),
        make_record(SourceType.RETAILER_TXN, b"b"),
        make_record(SourceType.SAPN_INTERVAL, b"c", SOURCE_TS),
        make_record(SourceType.ELEC_BILL, b"d"),
        make_record(SourceType.RETAILER_TXN, b"e"),
    ]
    for record in appended:
        catalog.append(record)
    a, b, c, d, e = appended
    assert list(catalog.records()) == [d, b, e, a, c]
    assert list(catalog.records(SourceType.SAPN_INTERVAL)) == [a, c]
    assert list(catalog.records(SourceType.GAS_BILL)) == []


def test_records_is_an_iterator(catalog: JsonlCatalog) -> None:
    catalog.append(make_record())
    assert isinstance(catalog.records(), Iterator)


# Collisions


def test_append_of_an_existing_dataset_id_is_a_collision(catalog: JsonlCatalog, paths: WorkspacePaths) -> None:
    record = make_record()
    catalog.append(record)
    before = paths.catalog_path(SourceType.SAPN_INTERVAL).read_bytes()
    with pytest.raises(IdCollisionError) as caught:
        catalog.append(record)
    assert caught.value.details["dataset_id"] == record.dataset_id
    assert paths.catalog_path(SourceType.SAPN_INTERVAL).read_bytes() == before


def test_append_of_an_existing_sha256_is_a_collision_across_sources(
    catalog: JsonlCatalog, paths: WorkspacePaths
) -> None:
    catalog.append(make_record(SourceType.SAPN_INTERVAL, b"same bytes"))
    other = make_record(SourceType.RETAILER_USAGE, b"same bytes")
    with pytest.raises(IdCollisionError) as caught:
        catalog.append(other)
    assert caught.value.details["dataset_id"] == other.dataset_id
    assert not paths.catalog_path(SourceType.RETAILER_USAGE).exists()


def test_append_of_the_same_sha256_with_another_source_ts_is_a_collision(catalog: JsonlCatalog) -> None:
    catalog.append(make_record(payload=b"same", source_ts=SOURCE_TS))
    with pytest.raises(IdCollisionError):
        catalog.append(make_record(payload=b"same", source_ts=datetime(2027, 1, 1)))


# Corruption


@pytest.mark.parametrize(
    "content",
    [
        b"not json\n",
        b"\n",
        b"[]\n",
        b"{good}\nnot json\n",
        b"{good}\n\n{good2}\n",
        b"{good}",
    ],
    ids=["not-json", "blank-line", "array", "second-line", "blank-middle", "no-final-newline"],
)
def test_a_malformed_catalog_is_corrupt_and_left_untouched(
    catalog: JsonlCatalog, paths: WorkspacePaths, content: bytes
) -> None:
    good, good2 = make_record(payload=b"one"), make_record(payload=b"two")
    content = content.replace(b"{good}\n", line_of(good)).replace(b"{good2}\n", line_of(good2))
    content = content.replace(b"{good}", line_of(good).rstrip(b"\n"))
    path = paths.catalog_path(SourceType.SAPN_INTERVAL)
    path.parent.mkdir(parents=True)
    path.write_bytes(content)
    for call in (
        lambda: list(catalog.records()),
        lambda: list(catalog.records(SourceType.SAPN_INTERVAL)),
        lambda: catalog.get("x"),
        lambda: catalog.find_sha256("0" * 64),
        lambda: catalog.append(make_record(SourceType.RETAILER_TXN, b"new")),
        lambda: catalog.append(make_record(SourceType.SAPN_INTERVAL, b"new")),
    ):
        with pytest.raises(CatalogCorruptError) as caught:
            call()
        assert caught.value.details["source"] == "sapn_interval"
        assert isinstance(caught.value.details["line"], int)
    assert path.read_bytes() == content
    assert not paths.catalog_path(SourceType.RETAILER_TXN).exists()


def test_a_corrupt_line_names_its_number_and_never_its_text(catalog: JsonlCatalog, paths: WorkspacePaths) -> None:
    path = paths.catalog_path(SourceType.SAPN_INTERVAL)
    path.parent.mkdir(parents=True)
    path.write_bytes(line_of(make_record()) + b'{"secret-looking": "SYNTHETIC-TEXT"}\n')
    with pytest.raises(CatalogCorruptError) as caught:
        list(catalog.records())
    assert caught.value.details["line"] == 2
    assert "SYNTHETIC-TEXT" not in str(caught.value)
    assert "SYNTHETIC-TEXT" not in caught.value.message
    assert "SYNTHETIC-TEXT" not in repr(caught.value.details)


def test_a_record_of_another_source_in_a_catalog_is_corrupt(catalog: JsonlCatalog, paths: WorkspacePaths) -> None:
    path = paths.catalog_path(SourceType.SAPN_INTERVAL)
    path.parent.mkdir(parents=True)
    path.write_bytes(line_of(make_record(SourceType.RETAILER_TXN)))
    with pytest.raises(CatalogCorruptError) as caught:
        list(catalog.records())
    assert caught.value.details["line"] == 1
    assert caught.value.details["reason"] == "wrong_source"


def test_a_line_from_a_newer_energatron_is_schema_too_new(catalog: JsonlCatalog, paths: WorkspacePaths) -> None:
    path = paths.catalog_path(SourceType.SAPN_INTERVAL)
    path.parent.mkdir(parents=True)
    path.write_bytes(canonical_json(record_doc() | {"schema_version": 2}) + b"\n")
    with pytest.raises(SchemaTooNewError):
        list(catalog.records())


def test_crlf_line_endings_are_corrupt(catalog: JsonlCatalog, paths: WorkspacePaths) -> None:
    """Lines are LF only (D1 §12); a CRLF file was edited by hand."""
    path = paths.catalog_path(SourceType.SAPN_INTERVAL)
    path.parent.mkdir(parents=True)
    path.write_bytes(line_of(make_record()).replace(b"\n", b"\r\n"))
    with pytest.raises(CatalogCorruptError):
        list(catalog.records())


# Old versions are upgraded on read


@pytest.fixture
def catalog_record_v2(monkeypatch: pytest.MonkeyPatch) -> type[CatalogRecord]:
    """A test-only `catalog_record` v2 with a migration from v1, in place of the real model for one test."""
    kinds = {kind: model for kind, model in persisted.registered_kinds().items() if kind != "catalog_record"}
    monkeypatch.setattr(persisted, "_KINDS", kinds)
    monkeypatch.setattr(persisted, "_MIGRATIONS", dict(persisted.registered_migrations()))

    class CatalogRecordV2(CatalogRecord):
        KIND: ClassVar[str] = "catalog_record"
        SCHEMA_VERSION: ClassVar[int] = 2
        kind: Literal["catalog_record"] = "catalog_record"
        schema_version: Literal[2] = 2  # pyright: ignore[reportIncompatibleVariableOverride]
        label: str

    @migration("catalog_record", from_version=1)
    def _v1_to_v2(doc: dict[str, JSONValue]) -> dict[str, JSONValue]:  # pyright: ignore[reportUnusedFunction]
        return {**doc, "schema_version": 2, "label": "upgraded"}

    monkeypatch.setattr(catalog_module, "CatalogRecord", CatalogRecordV2)
    return CatalogRecordV2


def test_an_old_version_line_is_upgraded_on_read_and_the_file_untouched(
    paths: WorkspacePaths, catalog_record_v2: type[CatalogRecord]
) -> None:
    path = paths.catalog_path(SourceType.SAPN_INTERVAL)
    path.parent.mkdir(parents=True)
    content = canonical_json(record_doc()) + b"\n"
    path.write_bytes(content)
    (record,) = JsonlCatalog(paths).records()
    assert isinstance(record, catalog_record_v2)
    assert record.schema_version == 2
    assert record.model_dump()["label"] == "upgraded"
    assert path.read_bytes() == content


# missing_sources and initialise


def test_missing_sources_lists_every_source_without_a_catalog_sorted(
    catalog: JsonlCatalog, paths: WorkspacePaths
) -> None:
    assert catalog.missing_sources() == sorted(SourceType)
    assert catalog.missing_sources() == sorted(SourceType, key=lambda source: source.value)
    catalog.initialise(SourceType.SAPN_INTERVAL)
    catalog.append(make_record(SourceType.ELEC_BILL))
    paths.raw_dir(SourceType.GAS_BILL).mkdir(parents=True)  # a directory alone is no catalog
    expected = [s for s in sorted(SourceType) if s not in {SourceType.SAPN_INTERVAL, SourceType.ELEC_BILL}]
    assert catalog.missing_sources() == expected


def test_initialise_creates_an_empty_catalog_and_its_directory(catalog: JsonlCatalog, paths: WorkspacePaths) -> None:
    assert catalog.initialise(SourceType.OFFER_CDR) is True
    path = paths.catalog_path(SourceType.OFFER_CDR)
    assert path.read_bytes() == b""
    assert sorted(entry.name for entry in path.parent.iterdir()) == ["_catalog.jsonl"]
    assert os.access(path, os.W_OK)
    assert list(catalog.records(SourceType.OFFER_CDR)) == []


@pytest.mark.parametrize("content", [b"", b"anything at all, even corrupt\n"])
def test_initialise_leaves_an_existing_catalog_alone(
    catalog: JsonlCatalog, paths: WorkspacePaths, content: bytes
) -> None:
    path = paths.catalog_path(SourceType.OFFER_CDR)
    path.parent.mkdir(parents=True)
    path.write_bytes(content)
    before = path.stat()
    assert catalog.initialise(SourceType.OFFER_CDR) is False
    after = path.stat()
    assert path.read_bytes() == content
    assert (after.st_ino, after.st_mtime_ns) == (before.st_ino, before.st_mtime_ns)
    assert sorted(entry.name for entry in path.parent.iterdir()) == ["_catalog.jsonl"]


def test_initialise_then_append(catalog: JsonlCatalog, paths: WorkspacePaths) -> None:
    catalog.initialise(SourceType.SAPN_INTERVAL)
    record = make_record()
    catalog.append(record)
    assert paths.catalog_path(SourceType.SAPN_INTERVAL).read_bytes() == line_of(record)
    assert list(catalog.records()) == [record]


def test_initialise_that_loses_the_link_race_returns_false_and_leaves_no_temp(
    catalog: JsonlCatalog, paths: WorkspacePaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    def link_after_another_writer(source: str, target: os.PathLike[str] | str) -> None:
        Path(target).write_bytes(b"the other writer's\n")
        raise FileExistsError(target)

    monkeypatch.setattr(jsonl_catalog.os, "link", link_after_another_writer)
    assert catalog.initialise(SourceType.OFFER_CDR) is False
    path = paths.catalog_path(SourceType.OFFER_CDR)
    assert path.read_bytes() == b"the other writer's\n"
    assert sorted(entry.name for entry in path.parent.iterdir()) == ["_catalog.jsonl"]


def test_initialise_fsyncs_the_parent_of_every_directory_it_creates(
    catalog: JsonlCatalog, paths: WorkspacePaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    synced: list[Path] = []
    real = files.fsync_directory
    monkeypatch.setattr(files, "fsync_directory", lambda path: (synced.append(path), real(path)))
    monkeypatch.setattr(jsonl_catalog, "fsync_directory", lambda path: (synced.append(path), real(path)))
    catalog.initialise(SourceType.OFFER_CDR)
    raw = paths.root / "data/raw"
    for parent in (paths.root, paths.root / "data", raw, raw / "offer_cdr"):
        assert parent in synced
