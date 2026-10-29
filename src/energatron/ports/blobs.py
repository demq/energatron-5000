"""The `BlobStore` port: write-once storage for raw files (D1 §4.2)."""

from typing import Protocol

from energatron.core.models.storage import StoredBlob


class BlobStore(Protocol):
    """Keys are workspace-relative POSIX paths under `data/raw/`; an invalid key is `E_INTERNAL`."""

    def put(self, key: str, data: bytes) -> StoredBlob:
        """Write once: the same bytes again is a no-op returning the existing blob; other bytes → `E_BLOB_EXISTS`."""
        ...

    def get(self, key: str) -> bytes: ...

    def exists(self, key: str) -> bool: ...

    def stat(self, key: str) -> StoredBlob: ...
