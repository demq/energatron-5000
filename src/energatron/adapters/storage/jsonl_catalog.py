"""`JsonlCatalog`: the v1 `Catalog`, one append-only `data/raw/<source>/_catalog.jsonl` per source (D1 §4.1; D3 §3.4).

- One compact canonical JSON object per line, each followed by `\\n`. An append opens the file in append mode,
  writes, flushes and `fsync`s it.
- Every read parses the files afresh with core's `parse_catalog`, the reader stage code shares, which upgrades
  older versions in memory and raises `E_CATALOG_CORRUPT` (with `details.source` and `details.line`, never the
  line's text) for a malformed file. The catalog is never repaired automatically.
- New directories are created with `make_dirs`, which `fsync`s the parent of each one.
- `initialise` creates an empty catalog atomically: a temp file, then `os.link`, which leaves an existing file alone.

Callers hold the workspace lock (D1 §8.8); the catalog itself doesn't lock.
"""

import os
import tempfile
from collections.abc import Iterator
from pathlib import Path

from energatron.core.canonical import canonical_json
from energatron.core.errors import IdCollisionError, StorageError
from energatron.core.models.catalog import CatalogRecord, SourceType, parse_catalog
from energatron.core.models.workspace import WorkspacePaths
from energatron.services.files import fsync_directory, make_dirs


class JsonlCatalog:
    """The catalog files of the workspace at `paths.root`."""

    def __init__(self, paths: WorkspacePaths) -> None:
        self._paths = paths

    def get(self, dataset_id: str) -> CatalogRecord | None:
        return next((record for record in self._read_all() if record.dataset_id == dataset_id), None)

    def find_sha256(self, sha256: str) -> CatalogRecord | None:
        return next((record for record in self._read_all() if record.sha256 == sha256), None)

    def records(self, source: SourceType | None = None) -> Iterator[CatalogRecord]:
        """Sources in sorted order, then records in append order. Reads eagerly, so a corrupt file raises here."""
        return iter(self._read(SourceType(source)) if source is not None else self._read_all())

    def append(self, record: CatalogRecord) -> None:
        """Append one line. Raises `E_ID_COLLISION` if the `dataset_id` or `sha256` exists in any source."""
        for existing in self._read_all():
            if existing.dataset_id == record.dataset_id or existing.sha256 == record.sha256:
                raise IdCollisionError(
                    "The catalog already holds this dataset ID or content hash.", dataset_id=record.dataset_id
                )
        line = canonical_json(record.model_dump(mode="json")) + b"\n"
        path = self._paths.catalog_path(record.source)
        try:
            make_dirs(path.parent)
            with path.open("ab") as handle:
                handle.write(line)
                handle.flush()
                os.fsync(handle.fileno())
            fsync_directory(path.parent)
        except OSError:
            raise _storage_error("A catalog line couldn't be written.", record.source) from None

    def missing_sources(self) -> list[SourceType]:
        """The sources with no catalog file yet, sorted."""
        return [source for source in sorted(SourceType) if not _present(self._paths.catalog_path(source))]

    def initialise(self, source: SourceType) -> bool:
        """Create `raw_dir(source)` and an empty catalog; False, changing nothing, when a catalog exists."""
        source = SourceType(source)
        path = self._paths.catalog_path(source)
        if _present(path):
            return False
        try:
            make_dirs(path.parent)
            descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
            try:
                with os.fdopen(descriptor, "wb") as handle:
                    handle.flush()
                    os.fsync(handle.fileno())
                os.chmod(temporary, 0o644)
                try:
                    os.link(temporary, path)
                except FileExistsError:
                    return False
            finally:
                Path(temporary).unlink(missing_ok=True)
            fsync_directory(path.parent)
        except OSError:
            raise _storage_error("An empty catalog couldn't be created.", source) from None
        return True

    # Reading

    def _read_all(self) -> list[CatalogRecord]:
        return [record for source in sorted(SourceType) for record in self._read(source)]

    def _read(self, source: SourceType) -> list[CatalogRecord]:
        path = self._paths.catalog_path(source)
        try:
            data = path.read_bytes()
        except FileNotFoundError:
            return []
        except OSError:
            raise _storage_error("A catalog couldn't be read.", source) from None
        return parse_catalog(data, source)


def _present(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def _storage_error(message: str, source: SourceType) -> StorageError:
    return StorageError(message, location="workspace", source=SourceType(source).value)
