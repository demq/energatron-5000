"""`energatron datasets` (P9 spec §3.1): the landed datasets, newest first, for an agent or a person.

Read-only: no lock, no writes. The result holds what identifies a dataset inside the workspace (its ID, source type,
dates and the detector that read it) and never what came with the file: no original file name, note, stored path,
size, modification time or parser `extra` besides `extracted_by` (P9 spec §4.1).
"""

from datetime import datetime
from typing import ClassVar, Final, Literal

from energatron.core.errors import UsageError
from energatron.core.models._common import FrozenModel
from energatron.core.models.catalog import CatalogRecord, Coverage, SourceType
from energatron.services.context import ServiceContext, ServiceResult

DEFAULT_LIMIT: Final = 50
MAX_LIMIT: Final = 1000


class DatasetEntry(FrozenModel):
    dataset_id: str
    source: SourceType
    landed_at: datetime
    source_ts: datetime
    coverage: Coverage | None
    detector: str  # "<name> v<N>"
    extracted_by: str | None
    supersedes: str | None


class DatasetsResult(ServiceResult):
    """`datasets_result` v1 (P9 spec §3.1)."""

    KIND: ClassVar[str] = "datasets_result"
    SCHEMA_VERSION: ClassVar[int] = 1
    kind: Literal["datasets_result"] = "datasets_result"
    schema_version: Literal[1] = 1

    counts: dict[str, int]
    total: int
    shown: int
    datasets: list[DatasetEntry]


def datasets(ctx: ServiceContext, *, source: SourceType | None = None, limit: int = DEFAULT_LIMIT) -> DatasetsResult:
    """Every catalog's record count, and the newest `limit` datasets (of `source`, when given)."""
    if not 1 <= limit <= MAX_LIMIT:
        raise UsageError(f"--limit must be between 1 and {MAX_LIMIT}.", field="limit")
    records = list(ctx.catalog.records())
    counts = {kind.value: 0 for kind in SourceType}
    for record in records:
        counts[record.source.value] += 1
    chosen = [record for record in records if source is None or record.source is source]
    chosen.sort(key=lambda record: (record.landed_at, record.dataset_id), reverse=True)
    shown = [entry(record) for record in chosen[:limit]]
    return DatasetsResult(counts=counts, total=len(chosen), shown=len(shown), datasets=shown)


def entry(record: CatalogRecord) -> DatasetEntry:
    extracted = record.extra.get("extracted_by")
    return DatasetEntry(
        dataset_id=record.dataset_id,
        source=record.source,
        landed_at=record.landed_at,
        source_ts=record.source_ts,
        coverage=record.coverage,
        detector=f"{record.detector.name.value} v{record.detector.version}",
        extracted_by=extracted if isinstance(extracted, str) else None,
        supersedes=record.supersedes,
    )
