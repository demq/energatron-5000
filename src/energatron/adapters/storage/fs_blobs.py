"""`FsBlobStore`: the v1 `BlobStore`, write-once files under the workspace's `data/raw/` (D1 §4.2; D3 §3.4).

A put writes a temp file in the target directory, `fsync`s it, hard-links it to the final name (which fails if
that name exists, so nothing is overwritten even in a race), unlinks the temp, makes the file read-only and
`fsync`s the directory. New directories come from `make_dirs`, which `fsync`s each one's parent. A put of the
same bytes makes a writable stored file read-only, since a crash between link and chmod leaves one. Reads never
trust a cache, mtime or the catalog: `stat` hashes the bytes on disk.

Keys are checked with `check_blob_key` (an invalid key is a bug: `E_INTERNAL`). Error details name the key, which
holds a dataset ID and never an identifier; an `OSError` becomes `E_STORAGE` without its path.
"""

import os
import stat
import tempfile
from pathlib import Path
from typing import Final

from energatron.core.canonical import sha256_hex
from energatron.core.errors import BlobExistsError, CatalogCorruptError, StorageError
from energatron.core.models.storage import RAW_PREFIX, StoredBlob, check_blob_key
from energatron.core.models.workspace import WorkspacePaths
from energatron.services.files import fsync_directory, make_dirs

_IGNORED: Final = frozenset({".DS_Store"})
_READ_ONLY: Final = 0o444


class FsBlobStore:
    """Raw files as plain files at `<root>/<key>`. Callers hold the workspace lock (D1 §8.8)."""

    def __init__(self, paths: WorkspacePaths) -> None:
        self._root = paths.root

    def put(self, key: str, data: bytes) -> StoredBlob:
        """Store `data` under `key` once. The same bytes again → the existing blob; other bytes → `E_BLOB_EXISTS`."""
        path = self._path(key)
        blob = StoredBlob(key=key, sha256=sha256_hex(data), bytes=len(data))
        if self._is_file(key, path):
            return self._same_or_refuse(key, path, blob)
        if path.exists():
            raise _not_a_file(key)
        try:
            make_dirs(path.parent)
            self._check_no_symlink_dirs(key, path.parent)
            descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
            try:
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
                try:
                    os.link(temporary, path)
                except FileExistsError:
                    linked = False
                else:
                    linked = True
            finally:
                Path(temporary).unlink(missing_ok=True)
            if not linked:  # another writer got there first
                if not self._is_file(key, path):
                    raise _not_a_file(key)
                return self._same_or_refuse(key, path, blob)
            path.chmod(_READ_ONLY)
            fsync_directory(path.parent)
        except OSError:
            raise _storage_error("A raw file couldn't be written.", key) from None
        return blob

    def get(self, key: str) -> bytes:
        """The stored bytes. A missing file → `E_CATALOG_CORRUPT` with `details.reason: "missing_blob"`."""
        path = self._path(key)
        if not self._is_file(key, path):
            raise _missing(key)
        try:
            return path.read_bytes()
        except FileNotFoundError:
            raise _missing(key) from None
        except OSError:
            raise _storage_error("A raw file couldn't be read.", key) from None

    def exists(self, key: str) -> bool:
        """Whether a regular file is stored under `key`. A symlink there → `E_CATALOG_CORRUPT`."""
        return self._is_file(key, self._path(key))

    def stat(self, key: str) -> StoredBlob:
        """The hash and size of the bytes on disk now; never a cached value."""
        data = self.get(key)
        return StoredBlob(key=key, sha256=sha256_hex(data), bytes=len(data))

    def keys(self, prefix: str) -> list[str]:
        """Every regular file under `prefix`, recursively, as sorted keys; `.DS_Store` left out (D3 §3.4).

        A symlink anywhere under the prefix, or as the prefix's directory or one above it below the workspace
        root, → `E_CATALOG_CORRUPT` with `details.reason: "symlink"`.
        """
        check_blob_key(prefix, prefix=True)
        base = self._root / prefix
        self._check_no_symlink_dirs(prefix, base)
        if not base.is_dir():
            return []
        found: list[str] = []
        try:
            for directory, dirnames, filenames in os.walk(base, followlinks=False, onerror=_raise):
                here = Path(directory)
                for name in dirnames:
                    if (here / name).is_symlink():
                        raise _symlink(prefix)
                for name in filenames:
                    entry = here / name
                    mode = entry.lstat().st_mode
                    if stat.S_ISLNK(mode):
                        raise _symlink(prefix)
                    if name in _IGNORED or not stat.S_ISREG(mode):
                        continue
                    found.append(entry.relative_to(self._root).as_posix())
        except OSError:
            raise _storage_error("The raw zone couldn't be listed.", prefix) from None
        return sorted(found)

    # Helpers

    def _path(self, key: str) -> Path:
        return self._root / check_blob_key(key)

    def _is_file(self, key: str, path: Path) -> bool:
        """Whether `path` is a regular file; a symlink at it, or at a directory between it and `data/`, is corrupt."""
        self._check_no_symlink_dirs(key, path.parent)
        try:
            mode = path.lstat().st_mode
        except FileNotFoundError, NotADirectoryError:
            return False
        except OSError:
            raise _storage_error("A raw file couldn't be read.", key) from None
        if stat.S_ISLNK(mode):
            raise _symlink(key)
        return stat.S_ISREG(mode)

    def _check_no_symlink_dirs(self, key: str, directory: Path) -> None:
        """No directory from `data/` down to `directory` is a symlink."""
        top = self._root / RAW_PREFIX.split("/", 1)[0]
        current = directory
        while current.is_relative_to(top):
            if current.is_symlink():
                raise _symlink(key)
            if current == top:
                break
            current = current.parent

    def _same_or_refuse(self, key: str, path: Path, blob: StoredBlob) -> StoredBlob:
        try:
            existing = path.read_bytes()
        except OSError:
            raise _storage_error("A raw file couldn't be read.", key) from None
        if sha256_hex(existing) != blob.sha256:
            raise BlobExistsError("A different file is already stored under this key.", key=key)
        try:  # a crash between the link and the chmod left it writable; adopting it finishes the write
            if stat.S_IMODE(path.lstat().st_mode) != _READ_ONLY:
                path.chmod(_READ_ONLY)
                fsync_directory(path.parent)
        except OSError:
            raise _storage_error("A raw file couldn't be made read-only.", key) from None
        return blob


def _missing(key: str) -> CatalogCorruptError:
    return CatalogCorruptError("A catalogued raw file is missing.", key=key, reason="missing_blob")


def _not_a_file(key: str) -> CatalogCorruptError:
    return CatalogCorruptError("A raw path is taken by something other than a file.", key=key, reason="not_a_file")


def _symlink(key: str) -> CatalogCorruptError:
    return CatalogCorruptError("The raw zone holds a symlink.", key=key, reason="symlink")


def _storage_error(message: str, key: str) -> StorageError:
    return StorageError(message, location="workspace", key=key)


def _raise(error: OSError) -> None:
    raise error
