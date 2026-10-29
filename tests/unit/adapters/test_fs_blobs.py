"""`FsBlobStore`: write-once raw files under `data/raw/` (D1 §4.2, §13; D3 §3.4, §13). Synthetic bytes only."""

import os
import stat
from pathlib import Path

import pytest

from energatron.adapters.storage import fs_blobs
from energatron.adapters.storage.fs_blobs import FsBlobStore
from energatron.core.canonical import sha256_hex
from energatron.core.errors import BlobExistsError, CatalogCorruptError, InternalError
from energatron.core.models.storage import StoredBlob
from energatron.core.models.workspace import WorkspacePaths
from energatron.services import files

KEY = "data/raw/sapn_interval/2026/synthetic.csv"
DATA = b"synthetic,bytes\n"


@pytest.fixture
def paths(tmp_path: Path) -> WorkspacePaths:
    root = tmp_path / "workspace"
    root.mkdir()
    return WorkspacePaths(root=root)


@pytest.fixture
def store(paths: WorkspacePaths) -> FsBlobStore:
    return FsBlobStore(paths)


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


# put


def test_put_stores_the_bytes_read_only_and_describes_them(store: FsBlobStore, paths: WorkspacePaths) -> None:
    blob = store.put(KEY, DATA)
    assert blob == StoredBlob(key=KEY, sha256=sha256_hex(DATA), bytes=len(DATA))
    target = paths.root / KEY
    assert target.read_bytes() == DATA
    assert mode(target) == 0o444


def test_put_leaves_no_temporary_file_behind(store: FsBlobStore, paths: WorkspacePaths) -> None:
    store.put(KEY, DATA)
    assert sorted(path.name for path in (paths.root / KEY).parent.iterdir()) == ["synthetic.csv"]


def test_put_of_the_same_bytes_again_is_a_no_op_returning_the_blob(store: FsBlobStore, paths: WorkspacePaths) -> None:
    first = store.put(KEY, DATA)
    before = (paths.root / KEY).stat()
    assert store.put(KEY, DATA) == first
    after = (paths.root / KEY).stat()
    assert (after.st_ino, after.st_mtime_ns) == (before.st_ino, before.st_mtime_ns)


def test_put_of_other_bytes_under_a_used_key_is_refused_and_the_file_unchanged(
    store: FsBlobStore, paths: WorkspacePaths
) -> None:
    store.put(KEY, DATA)
    with pytest.raises(BlobExistsError) as caught:
        store.put(KEY, b"other bytes\n")
    assert caught.value.details["key"] == KEY
    assert (paths.root / KEY).read_bytes() == DATA
    assert mode(paths.root / KEY) == 0o444
    assert sorted(path.name for path in (paths.root / KEY).parent.iterdir()) == ["synthetic.csv"]


def test_put_of_the_same_bytes_makes_a_writable_blob_read_only(store: FsBlobStore, paths: WorkspacePaths) -> None:
    """A crash between the link and the chmod leaves a writable blob; adopting it on a re-land fixes the mode."""
    target = paths.root / KEY
    target.parent.mkdir(parents=True)
    target.write_bytes(DATA)
    target.chmod(0o644)
    assert store.put(KEY, DATA) == StoredBlob(key=KEY, sha256=sha256_hex(DATA), bytes=len(DATA))
    assert mode(target) == 0o444


def test_put_fsyncs_the_parent_of_every_directory_it_creates(
    store: FsBlobStore, paths: WorkspacePaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    synced: list[Path] = []
    real = fs_blobs.fsync_directory
    monkeypatch.setattr(files, "fsync_directory", lambda path: (synced.append(path), real(path)))
    monkeypatch.setattr(fs_blobs, "fsync_directory", lambda path: (synced.append(path), real(path)))
    store.put(KEY, DATA)
    raw = paths.root / "data/raw"
    for parent in (paths.root, paths.root / "data", raw, raw / "sapn_interval", raw / "sapn_interval/2026"):
        assert parent in synced


@pytest.mark.parametrize(("winner", "error"), [(DATA, None), (b"other bytes\n", BlobExistsError)])
def test_put_that_loses_the_link_race_compares_with_the_winner(
    store: FsBlobStore, paths: WorkspacePaths, monkeypatch: pytest.MonkeyPatch, winner: bytes, error: type | None
) -> None:
    def link_after_another_writer(source: str, target: os.PathLike[str] | str) -> None:
        Path(target).write_bytes(winner)
        Path(target).chmod(0o444)
        raise FileExistsError(target)

    monkeypatch.setattr(fs_blobs.os, "link", link_after_another_writer)
    if error is None:
        assert store.put(KEY, DATA) == StoredBlob(key=KEY, sha256=sha256_hex(DATA), bytes=len(DATA))
    else:
        with pytest.raises(error):
            store.put(KEY, DATA)
    target = paths.root / KEY
    assert target.read_bytes() == winner
    assert sorted(path.name for path in target.parent.iterdir()) == ["synthetic.csv"]


def test_put_that_loses_the_link_race_to_a_directory_is_corrupt(
    store: FsBlobStore, paths: WorkspacePaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    def link_after_a_directory(source: str, target: os.PathLike[str] | str) -> None:
        Path(target).mkdir()
        raise FileExistsError(target)

    monkeypatch.setattr(fs_blobs.os, "link", link_after_a_directory)
    with pytest.raises(CatalogCorruptError) as caught:
        store.put(KEY, DATA)
    assert caught.value.details["reason"] == "not_a_file"
    assert sorted(path.name for path in (paths.root / KEY).parent.iterdir()) == ["synthetic.csv"]


def test_put_of_empty_bytes_works(store: FsBlobStore) -> None:
    assert store.put(KEY, b"").bytes == 0


# Invalid keys


BAD_KEYS = [
    "/data/raw/sapn_interval/x.csv",
    "data/raw/../raw/x.csv",
    "data/raw/sapn_interval/../x.csv",
    "data\\raw\\x.csv",
    "data/raw/sapn_interval\\x.csv",
    "data/marts/x.parquet",
    "x.csv",
    "data/raw/",
    "data/raw/sapn_interval/",
    "data/raw//x.csv",
    "",
]


@pytest.mark.parametrize("key", BAD_KEYS)
def test_every_method_refuses_an_invalid_key_as_internal(store: FsBlobStore, paths: WorkspacePaths, key: str) -> None:
    for call in (
        lambda: store.put(key, DATA),
        lambda: store.get(key),
        lambda: store.exists(key),
        lambda: store.stat(key),
    ):
        with pytest.raises(InternalError):
            call()
    assert not (paths.root / "data").exists()


@pytest.mark.parametrize(
    "prefix", ["data/raw", "data/raw/sapn_interval", "/data/raw/", "data/raw/../", "data/marts/", "data\\raw\\", ""]
)
def test_keys_refuses_an_invalid_prefix_as_internal(store: FsBlobStore, prefix: str) -> None:
    with pytest.raises(InternalError):
        store.keys(prefix)


# get, exists, stat


def test_get_exists_and_stat_read_a_stored_blob(store: FsBlobStore) -> None:
    blob = store.put(KEY, DATA)
    assert store.get(KEY) == DATA
    assert store.exists(KEY) is True
    assert store.stat(KEY) == blob


def test_stat_hashes_what_is_on_disk_now(store: FsBlobStore, paths: WorkspacePaths) -> None:
    store.put(KEY, DATA)
    target = paths.root / KEY
    target.chmod(0o644)
    target.write_bytes(b"changed behind the store's back")
    blob = store.stat(KEY)
    assert blob.sha256 == sha256_hex(b"changed behind the store's back")
    assert blob.bytes == len(b"changed behind the store's back")


@pytest.mark.parametrize("method", ["get", "stat"])
def test_get_and_stat_of_a_missing_blob_are_a_corrupt_catalog(store: FsBlobStore, method: str) -> None:
    with pytest.raises(CatalogCorruptError) as caught:
        getattr(store, method)(KEY)
    assert caught.value.details["reason"] == "missing_blob"
    assert caught.value.details["key"] == KEY


def test_exists_of_a_missing_blob_is_false(store: FsBlobStore) -> None:
    assert store.exists(KEY) is False


def test_a_directory_at_the_key_is_no_blob(store: FsBlobStore, paths: WorkspacePaths) -> None:
    (paths.root / KEY).mkdir(parents=True)
    assert store.exists(KEY) is False
    with pytest.raises(CatalogCorruptError) as caught:
        store.get(KEY)
    assert caught.value.details["reason"] == "missing_blob"
    with pytest.raises(CatalogCorruptError) as caught:
        store.put(KEY, DATA)
    assert caught.value.details["reason"] == "not_a_file"


def test_a_symlink_at_the_key_is_a_corrupt_catalog(store: FsBlobStore, paths: WorkspacePaths) -> None:
    elsewhere = paths.root / "elsewhere.csv"
    elsewhere.write_bytes(DATA)
    (paths.root / KEY).parent.mkdir(parents=True)
    (paths.root / KEY).symlink_to(elsewhere)
    for call in (lambda: store.get(KEY), lambda: store.stat(KEY), lambda: store.exists(KEY)):
        with pytest.raises(CatalogCorruptError) as caught:
            call()
        assert caught.value.details["reason"] == "symlink"
    with pytest.raises(CatalogCorruptError):
        store.put(KEY, DATA)
    assert elsewhere.read_bytes() == DATA


# keys


def test_keys_lists_every_regular_file_under_the_prefix_sorted_and_recursive(
    store: FsBlobStore, paths: WorkspacePaths
) -> None:
    store.put("data/raw/sapn_interval/2026/b.csv", b"b")
    store.put("data/raw/sapn_interval/2025/a.csv", b"a")
    store.put("data/raw/retailer_txn/2026/c.csv", b"c")
    (paths.root / "data/raw/sapn_interval/_catalog.jsonl").write_bytes(b"")
    (paths.root / "data/raw/sapn_interval/.DS_Store").write_bytes(b"finder")
    (paths.root / "data/raw/sapn_interval/2026/.DS_Store").write_bytes(b"finder")
    (paths.root / "data/raw/sapn_interval/empty").mkdir()
    assert store.keys("data/raw/sapn_interval/") == [
        "data/raw/sapn_interval/2025/a.csv",
        "data/raw/sapn_interval/2026/b.csv",
        "data/raw/sapn_interval/_catalog.jsonl",
    ]
    assert store.keys("data/raw/") == [
        "data/raw/retailer_txn/2026/c.csv",
        "data/raw/sapn_interval/2025/a.csv",
        "data/raw/sapn_interval/2026/b.csv",
        "data/raw/sapn_interval/_catalog.jsonl",
    ]


def test_keys_keeps_hidden_files_other_than_ds_store(store: FsBlobStore, paths: WorkspacePaths) -> None:
    directory = paths.root / "data/raw/sapn_interval"
    directory.mkdir(parents=True)
    (directory / ".stray.tmp").write_bytes(b"x")
    assert store.keys("data/raw/sapn_interval/") == ["data/raw/sapn_interval/.stray.tmp"]


def test_keys_of_a_missing_prefix_is_empty(store: FsBlobStore) -> None:
    assert store.keys("data/raw/sapn_interval/") == []
    assert store.keys("data/raw/") == []


@pytest.mark.parametrize("target_kind", ["file", "directory", "dangling"])
def test_a_symlink_under_the_prefix_is_a_corrupt_catalog(
    store: FsBlobStore, paths: WorkspacePaths, target_kind: str
) -> None:
    store.put(KEY, DATA)
    outside = paths.root / "outside"
    if target_kind == "file":
        outside.write_bytes(b"x")
    elif target_kind == "directory":
        outside.mkdir()
        (outside / "inner.csv").write_bytes(b"x")
    (paths.root / "data/raw/sapn_interval/2026/link").symlink_to(outside)
    with pytest.raises(CatalogCorruptError) as caught:
        store.keys("data/raw/sapn_interval/")
    assert caught.value.details["reason"] == "symlink"
    with pytest.raises(CatalogCorruptError):
        store.keys("data/raw/")


def test_a_symlinked_prefix_directory_is_a_corrupt_catalog(store: FsBlobStore, paths: WorkspacePaths) -> None:
    real = paths.root / "real"
    real.mkdir()
    (real / "a.csv").write_bytes(b"a")
    (paths.root / "data/raw").mkdir(parents=True)
    (paths.root / "data/raw/sapn_interval").symlink_to(real, target_is_directory=True)
    with pytest.raises(CatalogCorruptError) as caught:
        store.keys("data/raw/sapn_interval/")
    assert caught.value.details["reason"] == "symlink"


def test_put_creates_missing_directories(store: FsBlobStore, paths: WorkspacePaths) -> None:
    assert not (paths.root / "data").exists()
    store.put(KEY, DATA)
    assert os.path.isfile(paths.root / KEY)
