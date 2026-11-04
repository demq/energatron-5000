"""`ServiceContext` and the ports (D1 §4, §4.8; D2 §1 change 12).

The fakes here implement every port. Pyright checks that each one satisfies its protocol where the context is
built, so a port signature that drifts from its fake fails the type check; the tests check the context itself.
"""

import dataclasses
import inspect
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

import pytest

from energatron import ports
from energatron.core.models.catalog import CatalogRecord, Detection, SourceInfo, SourceType
from energatron.core.models.offers import Fetched
from energatron.core.models.pipeline import ReproReport, StageStatus
from energatron.core.models.region import RegionProfile
from energatron.core.models.storage import StoredBlob
from energatron.core.models.vcs import BackupReport, BackupStatus
from energatron.core.models.workspace import WorkspaceConfig, WorkspacePaths
from energatron.ports.blobs import BlobStore
from energatron.ports.catalog import Catalog
from energatron.ports.clock import Clock
from energatron.ports.offers import OfferProvider
from energatron.ports.pipeline import PipelineRunner
from energatron.ports.sources import SourceParser
from energatron.ports.vcs import VersionControl
from energatron.services.context import ServiceContext
from tests.architecture._support import import_all_modules


class FakeCatalog:
    def get(self, dataset_id: str) -> CatalogRecord | None:
        return None

    def find_sha256(self, sha256: str) -> CatalogRecord | None:
        return None

    def records(self, source: SourceType | None = None) -> Iterator[CatalogRecord]:
        return iter(())

    def append(self, record: CatalogRecord) -> None:
        return None

    def missing_sources(self) -> list[SourceType]:
        return []

    def initialise(self, source: SourceType) -> bool:
        return False


class FakeBlobs:
    def put(self, key: str, data: bytes) -> StoredBlob:
        raise NotImplementedError

    def get(self, key: str) -> bytes:
        return b""

    def exists(self, key: str) -> bool:
        return False

    def stat(self, key: str) -> StoredBlob:
        raise NotImplementedError

    def keys(self, prefix: str) -> list[str]:
        return []


class FakeParser:
    source: SourceType = SourceType.SAPN_INTERVAL
    version: int = 1

    def detect(self, filename: str, head: bytes) -> Detection | None:
        return None

    def inspect(self, filename: str, data: bytes) -> SourceInfo:
        raise NotImplementedError


class FakeVcs:
    def head(self) -> str | None:
        return None

    def dirty_paths(self, paths: Sequence[str] = ()) -> list[str]:
        return []

    def track(self, paths: Sequence[str]) -> list[str]:
        return []

    def commit(self, paths: Sequence[str], message: str) -> str:
        return "0" * 40

    def backup(self) -> BackupReport:
        raise NotImplementedError

    def backup_status(self) -> BackupStatus:
        return BackupStatus(
            head=None, last_backup_head=None, last_backup_at=None, refs_backed_up=False, commits_not_backed_up=None
        )


class FakeRunner:
    def status(self) -> list[StageStatus]:
        return []

    def repro(self, targets: Sequence[str] = ()) -> ReproReport:
        return ReproReport(ran=[], restored=[], cached=[])


class FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 10, 23, 9, 30, tzinfo=UTC)


class FakeOffers:
    name: str = "cdr"

    def list_plans(self, brand: str) -> list[Fetched]:
        return []

    def get_plan(self, brand: str, plan_id: str) -> Fetched:
        raise NotImplementedError


TEST_REGION = RegionProfile(
    name="sa",
    local_tz="Test/Zone",
    tou_windows={"Alpha": ("08:00-20:00",), "Beta": ("20:00-08:00",)},
    distributor="Test Networks",
    cdr_retailers=("alpha", "beta-energy"),
    parsers=(SourceType.SAPN_INTERVAL,),
)
"""A made-up region profile: a zone, labels, a distributor and retailers that exist nowhere (P2 spec §13.1)."""


def build(**changes: object) -> ServiceContext:
    context = ServiceContext(
        paths=WorkspacePaths(root=Path("/scratch/workspace")),
        config=WorkspaceConfig(region="sa"),
        private=None,
        catalog=FakeCatalog(),
        blobs=FakeBlobs(),
        parsers=(FakeParser(),),
        region=TEST_REGION,
        vcs=FakeVcs(),
        runner=FakeRunner(),
        clock=FixedClock(),
    )
    return dataclasses.replace(context, **changes)  # pyright: ignore[reportArgumentType]


def test_the_context_holds_every_port_and_the_region_and_offers_default_to_empty() -> None:
    context = build()
    assert [field.name for field in dataclasses.fields(context)] == [
        "paths",
        "config",
        "private",
        "catalog",
        "blobs",
        "parsers",
        "region",
        "vcs",
        "runner",
        "clock",
        "offers",
    ]
    assert dict(context.offers) == {}
    assert context.region is TEST_REGION
    assert context.private is None
    assert context.vcs.backup_status().refs_backed_up is False


def test_offers_are_keyed_by_provider_name() -> None:
    provider: OfferProvider = FakeOffers()
    context = build(offers={provider.name: provider})
    assert list(context.offers) == ["cdr"]
    assert build().offers is not build().offers  # no shared mutable default


def test_the_context_is_frozen() -> None:
    context = build()
    with pytest.raises(dataclasses.FrozenInstanceError):
        context.clock = FixedClock()  # pyright: ignore[reportAttributeAccessIssue]


def test_the_fakes_satisfy_their_ports() -> None:
    """Pyright checks these assignments; at run time the protocols' methods are compared by name."""
    pairs: list[tuple[type, object]] = [
        (Catalog, FakeCatalog()),
        (BlobStore, FakeBlobs()),
        (SourceParser, FakeParser()),
        (VersionControl, FakeVcs()),
        (PipelineRunner, FakeRunner()),
        (Clock, FixedClock()),
        (OfferProvider, FakeOffers()),
    ]
    for port, fake in pairs:
        members = {name for name in vars(port) if not name.startswith("_")} - {"__protocol_attrs__"}
        assert members, port
        assert all(hasattr(fake, name) for name in members), port


def test_the_version_control_port_has_six_methods() -> None:
    methods = [name for name, value in vars(VersionControl).items() if inspect.isfunction(value) and name[0] != "_"]
    assert sorted(methods) == ["backup", "backup_status", "commit", "dirty_paths", "head", "track"]


def test_every_port_module_defines_only_protocols() -> None:
    import_all_modules()
    port_modules = sorted(name for name in dir(ports) if not name.startswith("_"))
    assert port_modules == ["blobs", "catalog", "clock", "offers", "pipeline", "sources", "vcs"]
    for module_name in port_modules:
        module = getattr(ports, module_name)
        own = [
            value for value in vars(module).values() if inspect.isclass(value) and value.__module__ == module.__name__
        ]
        assert own, module_name
        assert all(Protocol in value.__mro__ for value in own), module_name
