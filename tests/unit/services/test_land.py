"""`services.land`: landing raw files (D3 §7, §8.2, §8.3, §13).

The workspace is a scratch folder with the real `JsonlCatalog` and `FsBlobStore`, the real `sapn_interval` parser
and a `FixedClock`. `VersionControl` is a fake: a source's pointer file is dirty when its catalog differs from what
was last committed, as `dvc add` would make it. Inputs are generated NEM12 files with synthetic identifiers, kept
in a landing folder outside the workspace.
"""

import json
import logging
import os
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from energatron.adapters.sources.sapn_nem12 import SAPN_INTERVAL
from energatron.adapters.storage.fs_blobs import FsBlobStore
from energatron.adapters.storage.jsonl_catalog import JsonlCatalog
from energatron.core.canonical import canonical_json, sha256_hex
from energatron.core.errors import (
    CommitRejectedError,
    DirtyError,
    EnergatronError,
    ForeignPremisesError,
    IdCollisionError,
    InvalidInputError,
    PartialError,
    PipelineNotSyncedError,
    PrivateConfigMissingError,
    StorageError,
    UnknownSourceError,
    UsageError,
    WarningCode,
)
from energatron.core.ids import dataset_id
from energatron.core.models.catalog import (
    CatalogRecord,
    Detection,
    Detector,
    SourceInfo,
    SourceType,
    blob_key,
)
from energatron.core.models.storage import StoredBlob
from energatron.core.models.vcs import BackupReport, BackupStatus
from energatron.core.models.workspace import WorkspacePaths
from energatron.ports.sources import SourceParser
from energatron.services import land as land_service
from energatron.services.context import ServiceContext
from energatron.services.land import LandPending, LandResult, land, outcome, write_order_key
from energatron.services.stages.dvc_yaml import emit_dvc_yaml
from energatron.services.stages.registry import STAGES
from tests.fixtures.make_nem12 import Nem12Spec, make_nem12, sapn_filename
from tests.fixtures.synthetic import SYNTHETIC_NMI, SYNTHETIC_NMI11, SYNTHETIC_SERIAL, synthetic_private_config
from tests.unit.adapters._workspace import make_workspace, write
from tests.unit.services.test_context import FixedClock, build

SHA = "1a2b3c4" + "d" * 33
ALL_SOURCES = sorted(source.value for source in SourceType)
WINDOW = (date(2025, 1, 1), date(2025, 1, 3))
NOTE = "synthetic private note"
LANDED_AT = "2026-10-23T09:30:00Z"


# Fakes


@dataclass
class LandVcs:
    """A fake `VersionControl` that tracks pointer files the way `dvc add` and `git commit` would."""

    paths: WorkspacePaths
    committed: dict[str, bytes] = field(default_factory=dict)
    pointer_dirty: set[str] = field(default_factory=set)
    other_dirty: dict[str, list[str]] = field(default_factory=dict)
    tracked: list[list[str]] = field(default_factory=list)
    commits: list[tuple[list[str], str]] = field(default_factory=list)
    reject: EnergatronError | None = None
    fail_track: EnergatronError | None = None

    def head(self) -> str | None:
        return SHA if self.commits else None

    def dirty_paths(self, paths: Sequence[str] = ()) -> list[str]:
        found = [entry for path in paths for entry in self.other_dirty.get(path, [])]
        if "data/raw" in paths:
            found.extend(self.pointer_dirty)
        return sorted(set(found))

    def track(self, paths: Sequence[str]) -> list[str]:
        self.tracked.append(list(paths))
        for path in paths:
            if self._catalog(path) != self.committed.get(path, b""):
                self.pointer_dirty.add(f"{path}.dvc")
        if self.fail_track is not None:
            raise self.fail_track
        return [f"{path}.dvc" for path in paths]

    def commit(self, paths: Sequence[str], message: str) -> str:
        if self.reject is not None:
            raise self.reject
        self.commits.append((list(paths), message))
        for path in paths:
            if path.endswith(".dvc"):
                directory = path.removesuffix(".dvc")
                self.committed[directory] = self._catalog(directory)
        self.pointer_dirty -= set(paths)
        return SHA

    def backup(self) -> BackupReport:
        raise AssertionError("land never backs up")

    def backup_status(self) -> BackupStatus:
        return BackupStatus(
            head=self.head(),
            last_backup_head=None,
            last_backup_at=None,
            refs_backed_up=False,
            commits_not_backed_up=len(self.commits),
        )

    def _catalog(self, directory: str) -> bytes:
        path = self.paths.root / directory / "_catalog.jsonl"
        return path.read_bytes() if path.exists() else b""


class SpyBlobs(FsBlobStore):
    """`FsBlobStore` that records the pending marker at each `put`, and can fail the next `put`."""

    def __init__(self, paths: WorkspacePaths) -> None:
        super().__init__(paths)
        self.paths = paths
        self.markers: list[list[str] | None] = []
        self.fail_next: EnergatronError | None = None

    def put(self, key: str, data: bytes) -> StoredBlob:
        marker = self.paths.tmp_dir / "land_pending.json"
        self.markers.append(json.loads(marker.read_text())["dataset_ids"] if marker.exists() else None)
        if self.fail_next is not None:
            error, self.fail_next = self.fail_next, None
            raise error
        return super().put(key, data)


@dataclass
class OfferStandIn:
    """A stand-in `offer_cdr` parser: content `OFFER <snapshot> <n>` gives that snapshot ID."""

    source: SourceType = SourceType.OFFER_CDR
    version: int = 1

    def detect(self, filename: str, head: bytes) -> Detection | None:
        return Detection(source=self.source, evidence=("content",)) if head.startswith(b"OFFER ") else None

    def inspect(self, filename: str, data: bytes) -> SourceInfo:
        snapshot = data.decode().split()[1]
        return SourceInfo(
            source_ts=datetime(2026, 1, 1, tzinfo=UTC),
            coverage=None,
            extra={"offer_snapshot_id": snapshot},
            identifiers={},
            extension=".json",
        )


# Fixtures and helpers


@pytest.fixture
def paths(tmp_path: Path) -> WorkspacePaths:
    paths = make_workspace(tmp_path, private=None)
    write(paths.root, "dvc.yaml", emit_dvc_yaml(STAGES))
    write(paths.root, "params.yaml", "kind: params\nschema_version: 1\n")
    catalog = JsonlCatalog(paths)
    for source in SourceType:
        catalog.initialise(source)
    return paths


@pytest.fixture
def vcs(paths: WorkspacePaths) -> LandVcs:
    return LandVcs(paths)


@pytest.fixture
def blobs(paths: WorkspacePaths) -> SpyBlobs:
    return SpyBlobs(paths)


@pytest.fixture
def ctx(paths: WorkspacePaths, vcs: LandVcs, blobs: SpyBlobs) -> ServiceContext:
    parsers: tuple[SourceParser, ...] = (SAPN_INTERVAL, OfferStandIn())
    return build(
        paths=paths,
        private=synthetic_private_config(),
        catalog=JsonlCatalog(paths),
        blobs=blobs,
        parsers=parsers,
        vcs=vcs,
        clock=FixedClock(),
    )


@pytest.fixture
def landing(tmp_path: Path) -> Path:
    folder = tmp_path / "landing"
    folder.mkdir()
    return folder


def nem12(seed: int = 0, *, nmi: str = SYNTHETIC_NMI) -> bytes:
    return make_nem12(Nem12Spec(start=WINDOW[0], days=3, seed=seed, nmi=nmi))


def sapn_file(
    folder: Path, seed: int = 0, *, hour: int = 10, nmi: str = SYNTHETIC_NMI, name: str | None = None
) -> Path:
    """A SAPN interval file generated at 2025-01-04 `hour`:00:00; `seed` changes its bytes."""
    name = name or sapn_filename("DETAILED", nmi, WINDOW, datetime(2025, 1, 4, hour))
    path = folder / name
    path.write_bytes(nem12(seed, nmi=nmi))
    return path


def expected_id(path: Path, hour: int = 10) -> str:
    return dataset_id("sapn_interval", datetime(2025, 1, 4, hour), sha256_hex(path.read_bytes()))


def catalog_ids(paths: WorkspacePaths, source: SourceType = SourceType.SAPN_INTERVAL) -> list[str]:
    return [record.dataset_id for record in JsonlCatalog(paths).records(source)]


def marker(paths: WorkspacePaths) -> Path:
    return paths.tmp_dir / "land_pending.json"


def snapshot(root: Path) -> dict[str, bytes]:
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()}


def statuses(result: LandResult) -> list[str]:
    return [item.status for item in result.items]


# Statuses and the result


def test_a_new_file_lands_with_its_record_blob_commit_and_result(
    ctx: ServiceContext, vcs: LandVcs, landing: Path
) -> None:
    path = sapn_file(landing)
    os.utime(path, (1_700_000_000, 1_700_000_000))
    result = land(ctx, [path], note=NOTE)
    new = expected_id(path)

    item = result.items[0]
    assert (item.position, item.status, item.source, item.dataset_id) == (1, "landed", "sapn_interval", new)
    assert item.stored_path == f"data/raw/sapn_interval/2025/{new}.csv"
    assert (item.bytes, item.sha256) == (path.stat().st_size, sha256_hex(path.read_bytes()))
    assert item.coverage is not None
    assert (item.coverage.start, item.coverage.end) == WINDOW
    assert item.error is None
    assert item.input == "<outside>/<redacted>_<n>_<n>_<n>_SAPN_DETAILED.csv"
    assert (result.landed, result.commit, result.dry_run, result.skipped_hidden) == ([new], SHA, False, 0)
    assert result.pointer_files == ["data/raw/sapn_interval.dvc"]

    record = JsonlCatalog(ctx.paths).get(new)
    assert record is not None
    assert record.original_filename == path.name
    assert record.notes == NOTE
    assert record.landed_at.isoformat().replace("+00:00", "Z") == LANDED_AT
    assert record.file_mtime == datetime.fromtimestamp(1_700_000_000, UTC)
    assert record.detector == Detector(name=SourceType.SAPN_INTERVAL, version=1, evidence=("filename", "content"))
    assert record.supersedes is None
    assert (ctx.paths.root / record.stored_path).read_bytes() == path.read_bytes()

    assert vcs.tracked == [[f"data/raw/{source}" for source in ALL_SOURCES]]
    assert vcs.commits == [(["data/raw/sapn_interval.dvc"], f"land: {new}")]
    assert not marker(ctx.paths).exists()
    assert path.exists()  # land never moves or deletes its inputs
    assert outcome(result) is None


def test_the_result_dumps_as_land_result_v1(ctx: ServiceContext, landing: Path) -> None:
    dumped = land(ctx, [sapn_file(landing)]).model_dump(mode="json")
    assert set(dumped) == {
        "kind",
        "schema_version",
        "dry_run",
        "items",
        "landed",
        "commit",
        "pointer_files",
        "skipped_hidden",
    }
    assert (dumped["kind"], dumped["schema_version"]) == ("land_result", 1)
    assert set(dumped["items"][0]) == {
        "position",
        "input",
        "bytes",
        "sha256",
        "status",
        "source",
        "dataset_id",
        "stored_path",
        "coverage",
        "error",
    }


def test_landing_again_is_already_landed_with_no_commit(ctx: ServiceContext, vcs: LandVcs, landing: Path) -> None:
    path = sapn_file(landing)
    first = land(ctx, [path])
    again = land(ctx, [path])
    assert statuses(again) == ["already_landed"]
    assert again.items[0].dataset_id == first.landed[0]
    assert again.items[0].stored_path == first.items[0].stored_path
    assert (again.landed, again.commit, again.pointer_files) == ([], None, [])
    assert len(vcs.commits) == 1
    assert len(vcs.tracked) == 2  # every land tracks every source
    assert again.warnings == ()  # no W_BACKUP_PENDING without a commit
    assert outcome(again) is None


def test_a_copy_of_an_accepted_item_is_duplicate_in_batch_naming_its_id(
    ctx: ServiceContext, vcs: LandVcs, landing: Path
) -> None:
    original = sapn_file(landing)
    copy = landing / "copy.csv"
    copy.write_bytes(original.read_bytes())
    result = land(ctx, [original, copy])
    assert statuses(result) == ["landed", "duplicate_in_batch"]
    assert result.items[1].dataset_id == result.items[0].dataset_id
    assert result.landed == [result.items[0].dataset_id]
    assert len(vcs.commits) == 1
    assert outcome(result) is None


def test_a_copy_of_a_rejected_badly_named_item_lands_under_a_sapn_name(ctx: ServiceContext, landing: Path) -> None:
    bad = sapn_file(landing, name=f"{SYNTHETIC_NMI11}_20250101_20250103_20251399000000_SAPN_DETAILED.csv")
    good = sapn_file(landing)
    result = land(ctx, [bad, good])
    assert statuses(result) == ["rejected", "landed"]
    error = result.items[0].error
    assert error is not None
    assert (error.code, error.details["reason"], error.details["position"]) == ("E_INVALID_INPUT", "bad_filename", 1)
    assert result.items[0].sha256 == result.items[1].sha256
    assert result.landed == [expected_id(good)]
    partial = outcome(result)
    assert isinstance(partial, PartialError)


def test_same_snapshot_for_an_offer_with_a_known_snapshot_id(ctx: ServiceContext, landing: Path) -> None:
    first, second, third = (landing / f"offer{n}.json" for n in range(3))
    first.write_bytes(b"OFFER PLAN~20260101T000000Z~0a0a0a0a 1\n")
    second.write_bytes(b"OFFER PLAN~20260101T000000Z~0a0a0a0a 2\n")
    landed = land(ctx, [first, second])
    assert statuses(landed) == ["landed", "same_snapshot"]
    assert landed.items[1].dataset_id == landed.items[0].dataset_id
    third.write_bytes(b"OFFER PLAN~20260101T000000Z~0a0a0a0a 3\n")
    later = land(ctx, [third])
    assert statuses(later) == ["same_snapshot"]
    assert later.items[0].dataset_id == landed.items[0].dataset_id
    assert later.warnings == ()  # a public source: no premises warning, no commit


def test_an_unknown_file_alone_is_rejected_with_its_own_error(ctx: ServiceContext, landing: Path) -> None:
    notes = landing / "notes.txt"
    notes.write_text("synthetic\n")
    result = land(ctx, [notes])
    assert statuses(result) == ["rejected"]
    item = result.items[0]
    assert (item.source, item.dataset_id, item.stored_path, item.coverage) == (None, None, None, None)
    assert item.error is not None
    assert (item.error.code, item.error.category) == ("E_UNKNOWN_SOURCE", "input")
    assert item.error.details == {"position": 1}
    error = outcome(result)
    assert isinstance(error, UnknownSourceError)
    assert error.details == {"position": 1}
    assert result.commit is None


def test_mixed_results_are_partial_with_the_accepted_items_committed(
    ctx: ServiceContext, vcs: LandVcs, landing: Path
) -> None:
    notes = landing / "notes.txt"
    notes.write_text("synthetic\n")
    good = sapn_file(landing)
    result = land(ctx, [notes, good])
    assert statuses(result) == ["rejected", "landed"]
    assert result.commit == SHA
    assert vcs.commits[0][1] == f"land: {expected_id(good)}"
    error = outcome(result)
    assert isinstance(error, PartialError)
    assert error.details == {"rejected": 1, "items": 2}


def test_an_id_collision_with_the_catalog_rejects_the_item(ctx: ServiceContext, landing: Path) -> None:
    path = sapn_file(landing)
    real = sha256_hex(path.read_bytes())
    forged_sha = real[:8] + ("0" if real[8] != "0" else "1") * 56
    forged_id = dataset_id("sapn_interval", datetime(2025, 1, 4, 10), forged_sha)
    record = CatalogRecord(
        dataset_id=forged_id,
        source=SourceType.SAPN_INTERVAL,
        sha256=forged_sha,
        bytes=1,
        stored_path=blob_key(SourceType.SAPN_INTERVAL, forged_id, ".csv"),
        original_filename="forged.csv",
        landed_at=datetime(2026, 1, 1, tzinfo=UTC),
        file_mtime=None,
        source_ts=datetime(2025, 1, 4, 10),
        coverage=None,
        detector=Detector(name=SourceType.SAPN_INTERVAL, version=1, evidence=("option",)),
        extra={},
        supersedes=None,
        notes="",
    )
    JsonlCatalog(ctx.paths).append(record)
    result = land(ctx, [path])
    assert statuses(result) == ["rejected"]
    assert isinstance(outcome(result), IdCollisionError)


# Items one at a time: premises, hidden files, symlinks


def test_a_bad_nmi_checksum_digit_rejects_only_that_item(ctx: ServiceContext, landing: Path) -> None:
    wrong = (int(SYNTHETIC_NMI11[-1]) + 1) % 10
    bad = sapn_file(landing, seed=1, name=f"{SYNTHETIC_NMI}{wrong}_20250101_20250103_20250104100000_SAPN_DETAILED.csv")
    good = sapn_file(landing)
    result = land(ctx, [bad, good])
    assert statuses(result) == ["rejected", "landed"]
    error = result.items[0].error
    assert error is not None
    assert error.details == {"reason": "nmi_checksum", "identifier": "nmi", "position": 1}


def test_a_foreign_item_aborts_the_command_with_nothing_written(
    ctx: ServiceContext, vcs: LandVcs, blobs: SpyBlobs, landing: Path
) -> None:
    good = sapn_file(landing)
    foreign = sapn_file(landing, seed=2, nmi="ZZTEST0002")
    before = snapshot(ctx.paths.root)
    with pytest.raises(ForeignPremisesError) as caught:
        land(ctx, [good, foreign])
    assert caught.value.details == {"identifier": "nmi", "position": 2}
    assert snapshot(ctx.paths.root) == before
    assert (vcs.tracked, vcs.commits, blobs.markers) == ([], [], [])


def test_an_unknown_meter_serial_lands_with_a_positioned_warning(ctx: ServiceContext, landing: Path) -> None:
    private = synthetic_private_config(meter_serials=["SYN9999999"])
    result = land(dataclass_replace(ctx, private=private), [sapn_file(landing)])
    codes = [(warning.code, warning.details) for warning in result.warnings]
    assert (WarningCode.W_UNKNOWN_IDENTIFIER, {"identifier": "meter_serial", "position": 1}) in codes
    assert codes[-1][0] == WarningCode.W_BACKUP_PENDING


def test_hidden_files_in_a_directory_are_skipped_and_counted(ctx: ServiceContext, landing: Path) -> None:
    sapn_file(landing)
    hidden = landing / ".hidden.csv"
    hidden.write_bytes(nem12(seed=3))
    (landing / "subfolder").mkdir()
    result = land(ctx, [landing])
    assert (len(result.items), result.skipped_hidden) == (1, 1)
    named = land(ctx, [hidden])  # a hidden file named explicitly is read like any other
    assert statuses(named) == ["landed"]
    assert named.items[0].input == "<outside>/.hidden.csv"


def test_a_directory_expands_to_its_files_sorted_by_name(ctx: ServiceContext, landing: Path) -> None:
    later = sapn_file(landing, seed=1, hour=11)
    earlier = sapn_file(landing, seed=2, hour=9)
    result = land(ctx, [landing])
    assert [item.dataset_id for item in result.items] == [expected_id(earlier, 9), expected_id(later, 11)]
    assert [item.position for item in result.items] == [1, 2]


def test_symlinks_given_or_found_are_rejected_unread(ctx: ServiceContext, landing: Path, tmp_path: Path) -> None:
    target = sapn_file(tmp_path)
    link = landing / "link.csv"
    link.symlink_to(target)
    for argument in (landing, link):
        result = land(ctx, [argument])
        item = result.items[0]
        assert (item.status, item.bytes, item.sha256) == ("rejected", None, None)
        assert item.error is not None
        assert (item.error.code, item.error.details["reason"]) == ("E_INVALID_INPUT", "symlink")
    assert catalog_ids(ctx.paths) == []


def test_an_empty_file_is_rejected(ctx: ServiceContext, landing: Path) -> None:
    empty = landing / "empty.csv"
    empty.write_bytes(b"")
    result = land(ctx, [empty])
    assert result.items[0].error is not None
    assert result.items[0].error.details["reason"] == "empty"


def test_an_oversized_file_is_rejected_before_it_is_read(
    ctx: ServiceContext, landing: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(land_service, "MAX_BYTES", 10)
    result = land(ctx, [sapn_file(landing)])
    item = result.items[0]
    assert item.error is not None
    assert item.error.details["reason"] == "too_large"
    assert item.sha256 is None


# Preconditions (D3 §7.1): nothing is read or written


def test_a_missing_private_config_fails_first(ctx: ServiceContext, landing: Path) -> None:
    with pytest.raises(PrivateConfigMissingError):
        land(dataclass_replace(ctx, private=None), [landing / "missing.csv"])


def test_an_unsynced_pipeline_fails(ctx: ServiceContext, landing: Path) -> None:
    (ctx.paths.root / "dvc.yaml").write_text("# edited\n")
    with pytest.raises(PipelineNotSyncedError):
        land(ctx, [sapn_file(landing)])


def test_a_dirty_raw_zone_beyond_pointer_files_fails(ctx: ServiceContext, vcs: LandVcs, landing: Path) -> None:
    vcs.other_dirty["data/raw"] = ["data/raw/stray.txt"]
    with pytest.raises(DirtyError) as caught:
        land(ctx, [sapn_file(landing)])
    assert caught.value.details["paths"] == ["data/raw/stray.txt"]
    assert vcs.tracked == []


def test_dirty_pointer_files_are_allowed_and_committed(ctx: ServiceContext, vcs: LandVcs, landing: Path) -> None:
    vcs.pointer_dirty = {"data/raw/.gitignore", "data/raw/sapn_interval.dvc"}
    path = sapn_file(landing)
    result = land(ctx, [path])
    assert result.pointer_files == ["data/raw/.gitignore", "data/raw/sapn_interval.dvc"]
    assert vcs.commits[0][1] == f"land: {expected_id(path)}"


@pytest.mark.parametrize("name", ["dvc.yaml", "params.yaml", ".pre-commit-config.yaml"])
def test_an_uncommitted_pipeline_file_fails_with_a_hint(
    ctx: ServiceContext, vcs: LandVcs, landing: Path, name: str
) -> None:
    vcs.other_dirty[name] = [name]
    with pytest.raises(DirtyError) as caught:
        land(ctx, [sapn_file(landing)])
    assert caught.value.details["paths"] == [name]
    assert caught.value.details["hint"] == "commit these first"


def test_a_missing_path_or_one_under_data_raw_is_a_usage_error(ctx: ServiceContext, landing: Path) -> None:
    good = sapn_file(landing)
    with pytest.raises(UsageError) as caught:
        land(ctx, [good, landing / "missing.csv"])
    assert caught.value.details["position"] == 2
    inside = write(ctx.paths.root, "data/raw/sapn_interval/x.csv", "synthetic\n")
    with pytest.raises(UsageError) as caught:
        land(ctx, [inside])
    assert caught.value.details["position"] == 1


def test_an_unknown_supersedes_target_is_a_usage_error(ctx: ServiceContext, landing: Path) -> None:
    with pytest.raises(UsageError):
        land(ctx, [sapn_file(landing)], supersedes="sapn_interval-20250101T000000-0a0a0a0a")


@pytest.mark.parametrize("note", ["x" * 501, "two\nlines", "carriage\rreturn"])
def test_a_long_or_multi_line_note_is_a_usage_error(ctx: ServiceContext, landing: Path, note: str) -> None:
    with pytest.raises(UsageError):
        land(ctx, [sapn_file(landing)], note=note)


def test_no_input_files_is_a_usage_error(ctx: ServiceContext, landing: Path) -> None:
    (landing / ".only-hidden").write_text("x")
    with pytest.raises(UsageError):
        land(ctx, [landing])


def test_source_option_without_a_parser_is_a_usage_error(ctx: ServiceContext, landing: Path) -> None:
    with pytest.raises(UsageError):
        land(ctx, [sapn_file(landing)], source=SourceType.ELEC_BILL)


def test_source_option_records_option_evidence(ctx: ServiceContext, landing: Path) -> None:
    result = land(ctx, [sapn_file(landing)], source=SourceType.SAPN_INTERVAL)
    record = JsonlCatalog(ctx.paths).get(result.landed[0])
    assert record is not None
    assert record.detector.evidence == ("option",)


# Supersedes


def test_supersedes_rules(ctx: ServiceContext, landing: Path) -> None:
    first = land(ctx, [sapn_file(landing, seed=1, hour=9)]).landed[0]
    second = land(ctx, [sapn_file(landing, seed=2, hour=10)], supersedes=first).landed[0]
    record = JsonlCatalog(ctx.paths).get(second)
    assert record is not None
    assert record.supersedes == first

    with pytest.raises(UsageError):  # already superseded
        land(ctx, [sapn_file(landing, seed=3, hour=11)], supersedes=first)
    with pytest.raises(UsageError):  # more than one item
        land(ctx, [sapn_file(landing, seed=4, hour=12), sapn_file(landing, seed=5, hour=13)], supersedes=second)
    offer = landing / "offer.json"
    offer.write_bytes(b"OFFER PLAN~20260101T000000Z~0b0b0b0b 1\n")
    with pytest.raises(UsageError):  # another source
        land(ctx, [offer], supersedes=second)
    assert len(catalog_ids(ctx.paths)) == 2


# Planning: stray files and orphan blobs; dry runs


def test_an_uncatalogued_raw_file_fails_dry_run_and_real_run_alike(
    ctx: ServiceContext, vcs: LandVcs, landing: Path
) -> None:
    write(ctx.paths.root, "data/raw/sapn_interval/2025/stray.csv", "synthetic\n")
    path = sapn_file(landing)
    for dry_run in (True, False):
        with pytest.raises(EnergatronError) as caught:
            land(ctx, [path], dry_run=dry_run)
        assert caught.value.code == "E_CATALOG_CORRUPT"
        assert caught.value.details == {"reason": "uncatalogued_file", "source": "sapn_interval", "count": 1}
    assert catalog_ids(ctx.paths) == []
    assert vcs.tracked == []


def test_an_orphan_blob_matching_an_accepted_item_is_adopted(ctx: ServiceContext, landing: Path) -> None:
    path = sapn_file(landing)
    new = expected_id(path)
    FsBlobStore(ctx.paths).put(blob_key(SourceType.SAPN_INTERVAL, new, ".csv"), path.read_bytes())
    result = land(ctx, [path])
    assert result.landed == [new]


def test_a_dry_run_writes_nothing_and_reports_the_real_statuses(
    ctx: ServiceContext, vcs: LandVcs, landing: Path
) -> None:
    notes = landing / "notes.txt"
    notes.write_text("synthetic\n")
    path = sapn_file(landing)
    before = snapshot(ctx.paths.root)
    dry = land(ctx, [path, notes], dry_run=True)
    assert snapshot(ctx.paths.root) == before
    assert (vcs.tracked, vcs.commits) == ([], [])
    assert dry.dry_run is True
    assert (dry.commit, dry.pointer_files) == (None, [])
    real = land(ctx, [path, notes])
    assert statuses(dry) == statuses(real) == ["landed", "rejected"]
    assert dry.landed == real.landed
    assert dry.model_dump(mode="json")["items"] == real.model_dump(mode="json")["items"]


# Writing: the marker, the order, tracking and the commit


def test_the_marker_holds_the_new_ids_before_the_first_put_and_is_gone_after_the_commit(
    ctx: ServiceContext, blobs: SpyBlobs, landing: Path
) -> None:
    one, two = sapn_file(landing, seed=1, hour=9), sapn_file(landing, seed=2, hour=10)
    result = land(ctx, [one, two])
    ids = sorted([expected_id(one, 9), expected_id(two, 10)])
    assert blobs.markers == [ids, ids]
    assert result.landed == ids
    assert not marker(ctx.paths).exists()


def test_the_commit_message_names_several_ids_sorted(ctx: ServiceContext, vcs: LandVcs, landing: Path) -> None:
    one, two = sapn_file(landing, seed=1, hour=11), sapn_file(landing, seed=2, hour=9)
    land(ctx, [one, two])
    ids = sorted([expected_id(one, 11), expected_id(two, 9)])
    assert vcs.commits[0][1] == f"land: 2 datasets\n\n{ids[0]}\n{ids[1]}"


def test_items_are_written_in_data_age_order(ctx: ServiceContext, landing: Path) -> None:
    newer = sapn_file(landing, seed=1, hour=11)
    older = sapn_file(landing, seed=2, hour=9)
    land(ctx, [newer, older])
    assert catalog_ids(ctx.paths) == [expected_id(older, 9), expected_id(newer, 11)]


def test_the_write_order_key_puts_a_null_mtime_after_every_other() -> None:
    ts = datetime(2025, 1, 1)
    keys = [
        write_order_key(SourceType.SAPN_INTERVAL, ts, None, 1),
        write_order_key(SourceType.SAPN_INTERVAL, ts, datetime(2026, 1, 1, tzinfo=UTC), 2),
        write_order_key(SourceType.SAPN_INTERVAL, ts, datetime(2025, 1, 1, tzinfo=UTC), 3),
        write_order_key(SourceType.ELEC_BILL, datetime(2026, 1, 1), None, 4),
        write_order_key(SourceType.SAPN_INTERVAL, datetime(2024, 1, 1), None, 5),
    ]
    assert [key[-1] for key in sorted(keys)] == [4, 5, 3, 2, 1]


def test_a_failed_put_leaves_the_marker_and_the_rerun_heals(
    ctx: ServiceContext, vcs: LandVcs, blobs: SpyBlobs, landing: Path
) -> None:
    path = sapn_file(landing)
    blobs.fail_next = StorageError("synthetic failure")
    with pytest.raises(StorageError):
        land(ctx, [path])
    pending = LandPending.from_document(json.loads(marker(ctx.paths).read_text()))
    assert pending.dataset_ids == [expected_id(path)]
    result = land(ctx, [path])
    assert result.landed == [expected_id(path)]
    assert vcs.commits[0][1] == f"land: {expected_id(path)}"
    assert not marker(ctx.paths).exists()


def test_an_interrupted_run_after_append_is_committed_naming_the_marker_ids(
    ctx: ServiceContext, vcs: LandVcs, landing: Path
) -> None:
    path = sapn_file(landing)
    vcs.fail_track = StorageError("synthetic failure")
    with pytest.raises(StorageError):
        land(ctx, [path])
    assert marker(ctx.paths).exists()
    vcs.fail_track = None
    result = land(ctx, [path])
    assert statuses(result) == ["already_landed"]
    assert result.landed == []
    assert vcs.commits == [(["data/raw/sapn_interval.dvc"], f"land: {expected_id(path)}")]
    assert result.commit == SHA
    assert not marker(ctx.paths).exists()


def test_dirty_pointers_without_ids_commit_as_a_resync(ctx: ServiceContext, vcs: LandVcs, landing: Path) -> None:
    path = sapn_file(landing)
    land(ctx, [path])
    vcs.pointer_dirty = {"data/raw/sapn_interval.dvc"}
    result = land(ctx, [path])
    assert vcs.commits[-1][1] == "land: resync raw pointers"
    assert result.commit == SHA


def test_a_rejected_commit_keeps_the_marker_and_says_to_rerun(ctx: ServiceContext, vcs: LandVcs, landing: Path) -> None:
    vcs.reject = CommitRejectedError("A git hook rejected the commit.", hooks=["synthetic"])
    path = sapn_file(landing)
    with pytest.raises(CommitRejectedError) as caught:
        land(ctx, [path])
    assert "re-run" in str(caught.value.details["hint"])
    assert marker(ctx.paths).exists()
    vcs.reject = None
    assert land(ctx, [path]).commit == SHA
    assert vcs.commits[0][1] == f"land: {expected_id(path)}"


def test_backup_pending_is_warned_only_after_a_commit(ctx: ServiceContext, landing: Path) -> None:
    result = land(ctx, [sapn_file(landing)])
    pending = [warning for warning in result.warnings if warning.code == WarningCode.W_BACKUP_PENDING]
    assert len(pending) == 1
    assert pending[0].details == {"commits_not_backed_up": 1}


# Privacy


def test_no_file_name_identifier_or_note_reaches_the_result_or_logs(
    ctx: ServiceContext, landing: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG, logger="energatron")
    good = sapn_file(landing)
    bad = sapn_file(landing, seed=1, name=f"{SYNTHETIC_NMI11}_20250101_20250103_20251399000000_SAPN_DETAILED.csv")
    result = land(ctx, [good, bad], note=NOTE)
    dumped = canonical_json(result.model_dump(mode="json")).decode()
    dumped += json.dumps([warning.model_dump(mode="json") for warning in result.warnings])
    for text in (dumped, caplog.text):
        for secret in (good.name, bad.name, SYNTHETIC_NMI, SYNTHETIC_SERIAL, NOTE, str(landing)):
            assert secret not in text


def test_input_inside_the_workspace_is_shown_relative_and_masked(ctx: ServiceContext) -> None:
    inbox = ctx.paths.root / "data" / "landing"
    inbox.mkdir(parents=True)
    path = sapn_file(inbox)
    result = land(ctx, [path])
    assert result.items[0].input == "data/landing/<redacted>_<n>_<n>_<n>_SAPN_DETAILED.csv"


def test_land_pending_is_a_sorted_id_list() -> None:
    ids = ["sapn_interval-20250101T000000-0a0a0a0a", "elec_bill-20250101T000000-0b0b0b0b"]
    with pytest.raises(InvalidInputError):
        LandPending.from_document({"kind": "land_pending", "schema_version": 1, "dataset_ids": ids})
    assert LandPending.from_document(
        {"kind": "land_pending", "schema_version": 1, "dataset_ids": sorted(ids)}
    ).dataset_ids == sorted(ids)


def dataclass_replace(ctx: ServiceContext, **changes: object) -> ServiceContext:
    import dataclasses

    return dataclasses.replace(ctx, **changes)  # pyright: ignore[reportArgumentType]


# Review fixes (P1.4.2 /examine)


def test_an_unreadable_folder_argument_is_a_usage_error_naming_its_position(
    ctx: ServiceContext, landing: Path, tmp_path: Path
) -> None:
    good = sapn_file(landing)
    locked = tmp_path / "locked-synthetic-folder"
    locked.mkdir()
    locked.chmod(0)
    try:
        with pytest.raises(UsageError) as caught:
            land(ctx, [good, locked])
    finally:
        locked.chmod(0o755)
    assert caught.value.details == {"position": 2}
    assert "locked-synthetic-folder" not in caught.value.message
    assert catalog_ids(ctx.paths) == []


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        "[1]",
        '{"kind": "land_pending", "schema_version": 1, "dataset_ids": ["b-x", "a"]}',
        '{"kind": "other", "schema_version": 1}',
    ],
)
def test_a_corrupt_marker_is_invalid_input_with_a_hint(ctx: ServiceContext, landing: Path, text: str) -> None:
    marker(ctx.paths).parent.mkdir(parents=True, exist_ok=True)
    marker(ctx.paths).write_text(text)
    with pytest.raises(InvalidInputError) as caught:
        land(ctx, [sapn_file(landing)])
    hint = str(caught.value.details["hint"])
    assert ".energatron/tmp/land_pending.json" in hint
    assert "resync raw pointers" in hint
    assert str(ctx.paths.root) not in hint
    assert catalog_ids(ctx.paths) == []
