"""`ServiceContext`: everything a service needs, built by `adapters/wiring.py` (D1 §4.8); and `ServiceResult`.

Services are plain functions that take `ctx: ServiceContext` first. A service that needs the private config raises
`E_PRIVATE_CONFIG_MISSING` when `ctx.private` is None.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from pydantic import Field

from energatron.core.errors import ResultWarning
from energatron.core.models.private import PrivateConfig
from energatron.core.models.workspace import WorkspaceConfig, WorkspacePaths
from energatron.core.persisted import Persisted
from energatron.ports.blobs import BlobStore
from energatron.ports.catalog import Catalog
from energatron.ports.clock import Clock
from energatron.ports.offers import OfferProvider
from energatron.ports.pipeline import PipelineRunner
from energatron.ports.sources import SourceParser
from energatron.ports.vcs import VersionControl


@dataclass(frozen=True)
class ServiceContext:
    paths: WorkspacePaths  # absolute root for this run; never persisted
    config: WorkspaceConfig
    private: PrivateConfig | None  # None if private/config.toml is missing
    catalog: Catalog
    blobs: BlobStore
    parsers: Sequence[SourceParser]
    vcs: VersionControl
    runner: PipelineRunner
    clock: Clock
    offers: Mapping[str, OfferProvider] = field(default_factory=dict)  # P4


class ServiceResult(Persisted):
    """Base of every service's result model (D1 §4.8): a persisted kind, plus the warnings the service returns.

    Warnings are returned in results (D1 §7), but they aren't part of the kind's fields: `warnings` is left out of
    `model_dump()`, so `data` in the CLI envelope holds exactly the fields the spec lists, and the CLI moves the
    warnings to the envelope's own `warnings` (D1 §8.5).
    """

    warnings: tuple[ResultWarning, ...] = Field(default=(), exclude=True)
