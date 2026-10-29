"""The `BlobStore` port: write-once storage for raw files (D1 §4.2)."""

from typing import Protocol

from energatron.core.models.storage import StoredBlob


class BlobStore(Protocol):
    """Keys are workspace-relative POSIX paths under `data/raw/`; an invalid key is `E_INTERNAL`."""

    def put(self, key: str, data: bytes) -> StoredBlob:
        """Write once: the same bytes again is a no-op returning the existing blob; other bytes → `E_BLOB_EXISTS`."""
        ...

    def get(self, key: str) -> bytes:
        """A missing file → `E_CATALOG_CORRUPT` with `details.reason: "missing_blob"`."""
        ...

    def exists(self, key: str) -> bool:
        """False for a missing file; callers that treat a missing blob as a finding ask this first."""
        ...

    def stat(self, key: str) -> StoredBlob:
        """Hashes the bytes on disk now, on every call. A missing file → `E_CATALOG_CORRUPT` (`missing_blob`)."""
        ...

    def keys(self, prefix: str) -> list[str]:
        """Every stored regular file under `prefix` (a key prefix ending in `/`), recursively, sorted.

        The catalog file is included and `.DS_Store` left out; a symlink → `E_CATALOG_CORRUPT` (`symlink`) (D3 §3.4).
        """
        ...
