"""The `Catalog` port: the raw zone's record of every landed dataset (D1 §4.1)."""

from collections.abc import Iterator
from typing import Protocol

from energatron.core.models.catalog import CatalogRecord, SourceType


class Catalog(Protocol):
    """Immutable records, one per landed dataset. Callers hold the workspace lock; the catalog doesn't lock.

    `dataset_id` and `sha256` are each unique across all sources. There's no update or delete: a correction is a
    new record that names what it `supersedes`.
    """

    def get(self, dataset_id: str) -> CatalogRecord | None: ...

    def find_sha256(self, sha256: str) -> CatalogRecord | None: ...

    def records(self, source: SourceType | None = None) -> Iterator[CatalogRecord]:
        """Sources in sorted order, then records in append order within each source."""
        ...

    def append(self, record: CatalogRecord) -> None:
        """Raises `E_ID_COLLISION` if the record's `dataset_id` or `sha256` already exists."""
        ...

    def missing_sources(self) -> list[SourceType]:
        """The sources with no catalog yet, sorted (D3 §3.4)."""
        ...

    def initialise(self, source: SourceType) -> bool:
        """Create the source's empty catalog atomically; False, changing nothing, if one exists (D3 §3.4)."""
        ...
