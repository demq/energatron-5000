"""The P0 models and the port models P0.2.2 creates early (D1 §4, §5; D2 §6, §8; D3 §3.1, §4). Synthetic values."""

from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any

import pytest
from pydantic import ValidationError

from energatron.core.canonical import JSONValue, sha256_hex
from energatron.core.errors import InvalidInputError
from energatron.core.ids import dataset_id
from energatron.core.models.catalog import (
    PUBLIC_SOURCES,
    CatalogRecord,
    Coverage,
    Detection,
    Detector,
    SourceInfo,
    SourceType,
)
from energatron.core.models.offers import Fetched
from energatron.core.models.pipeline import ReproReport, StageStatus
from energatron.core.models.storage import StoredBlob
from energatron.core.models.vcs import BackupReport, BackupState, BackupStatus
from energatron.core.models.workspace import LocalConfig, WorkspaceConfig, WorkspacePaths

SHA1 = "1a2b3c4" + "d" * 33
SHA256_COMMIT = "1a2b3c4" + "e" * 57
DIGEST = sha256_hex(b"synthetic")
BUNDLE = "20261023T093000Z-1a2b3c4.bundle"
AT = datetime(2026, 10, 23, 9, 30, tzinfo=UTC)
ROOT = Path("/scratch/workspace")


# WorkspacePaths


def test_workspace_paths_lay_out_the_p0_files_under_the_root() -> None:
    paths = WorkspacePaths(root=ROOT)
    relative = {
        name: PurePosixPath(getattr(paths, name).relative_to(ROOT)).as_posix()
        for name in (
            "energatron_dir",
            "workspace_config",
            "local_config",
            "backup_state",
            "lock",
            "tmp_dir",
            "private_dir",
            "private_config",
            "denylist_generated",
            "denylist_manual",
        )
    }
    assert relative == {
        "energatron_dir": ".energatron",
        "workspace_config": ".energatron/workspace.toml",
        "local_config": ".energatron/local.toml",
        "backup_state": ".energatron/backup_state.json",
        "lock": ".energatron/lock",
        "tmp_dir": ".energatron/tmp",
        "private_dir": "private",
        "private_config": "private/config.toml",
        "denylist_generated": "private/denylist.generated.txt",
        "denylist_manual": "private/denylist.txt",
    }


def test_workspace_paths_need_an_absolute_root_and_are_frozen() -> None:
    with pytest.raises(ValidationError):
        WorkspacePaths(root=Path("relative/workspace"))
    paths = WorkspacePaths(root=ROOT)
    with pytest.raises(ValidationError):
        paths.root = Path("/elsewhere")  # pyright: ignore[reportAttributeAccessIssue]


def test_an_invalid_workspace_root_is_not_shown_in_the_error() -> None:
    for root in (Path("SYNTHUSER/workspace"), "/scratch/SYNTHUSER/../workspace", 7):
        with pytest.raises(ValidationError) as caught:
            WorkspacePaths(root=root)  # pyright: ignore[reportArgumentType]
        assert "SYNTHUSER" not in str(caught.value)
        assert "input_value" not in str(caught.value)
    with pytest.raises(ValidationError) as caught:
        WorkspacePaths(root=ROOT, home="/home/SYNTHUSER")  # pyright: ignore[reportCallIssue]
    assert "SYNTHUSER" not in str(caught.value)


def test_relative_gives_workspace_relative_posix_paths_only() -> None:
    paths = WorkspacePaths(root=ROOT)
    assert paths.relative(ROOT / "data" / "raw" / "x.csv") == "data/raw/x.csv"
    assert paths.relative(ROOT) == "."
    for outside in (Path("/scratch/other/x"), ROOT / ".." / "x", Path("data/x")):
        with pytest.raises(ValueError) as caught:
            paths.relative(outside)
        assert "scratch" not in str(caught.value)


# WorkspaceConfig and LocalConfig (D2 §6.1, §6.2)


def workspace_doc(**backup: JSONValue) -> dict[str, JSONValue]:
    doc: dict[str, JSONValue] = {"kind": "workspace_config", "schema_version": 1, "region": "sa"}
    if backup:
        doc["backup"] = backup
    return doc


def test_workspace_config_backup_defaults() -> None:
    config = WorkspaceConfig.from_document(workspace_doc())
    assert (config.region, config.backup.keep_latest, config.backup.keep_monthly) == ("sa", 10, True)
    config = WorkspaceConfig.from_document(workspace_doc(keep_latest=3, keep_monthly=False))
    assert (config.backup.keep_latest, config.backup.keep_monthly) == (3, False)


@pytest.mark.parametrize(
    ("doc", "location"),
    [
        (workspace_doc(keep_latest=2), "backup.keep_latest"),
        (workspace_doc(keep_latest="10"), "backup.keep_latest"),
        (workspace_doc(keep_monthly="yes"), "backup.keep_monthly"),
        (workspace_doc(keep_daily=1), "backup.keep_daily"),
        ({**workspace_doc(), "region": "South Australia"}, "region"),
        ({"kind": "workspace_config", "schema_version": 1}, "region"),
    ],
    ids=["keep-latest-below-3", "keep-latest-string", "keep-monthly-string", "unknown-field", "region", "no-region"],
)
def test_workspace_config_rules(doc: dict[str, JSONValue], location: str) -> None:
    with pytest.raises(InvalidInputError) as caught:
        WorkspaceConfig.from_document(doc)
    errors = caught.value.details["errors"]
    assert isinstance(errors, list)
    assert location in [problem["loc"] for problem in errors if isinstance(problem, dict)]


def test_local_config_keeps_bundle_dir_as_written() -> None:
    written = "~/Library/Mobile Documents/com~apple~CloudDocs/Backups/demq-elektric-git"
    doc: dict[str, JSONValue] = {"kind": "local_config", "schema_version": 1, "backup": {"bundle_dir": written}}
    assert LocalConfig.from_document(doc).backup.bundle_dir == written  # `~` is the adapter's to expand
    empty = LocalConfig.from_document({"kind": "local_config", "schema_version": 1})
    assert empty.backup.bundle_dir is None  # missing: `backup` raises E_BACKUP_NOT_CONFIGURED (D2 §8.1)
    with pytest.raises(InvalidInputError):
        LocalConfig.from_document({"kind": "local_config", "schema_version": 1, "backup": {"bundle_dir": ""}})


# BackupReport, BackupState, BackupStatus (D1 §4.4; D2 §6.5, §8)


def report(**changes: Any) -> BackupReport:
    fields: dict[str, Any] = {
        "dvc_files_pushed": 0,
        "bundle_name": BUNDLE,
        "bundles_kept": 1,
        "bundles_removed": 0,
        "bundle_created": True,
        "dataless_files": 0,
    }
    return BackupReport(**(fields | changes))


def test_backup_report_holds_counts_and_a_bundle_file_name() -> None:
    assert report().model_dump(mode="json") == {
        "dvc_files_pushed": 0,
        "bundle_name": BUNDLE,
        "bundles_kept": 1,
        "bundles_removed": 0,
        "bundle_created": True,
        "dataless_files": 0,
    }


@pytest.mark.parametrize(
    "changes",
    [
        {"bundle_name": "/abs/" + BUNDLE},
        {"bundle_name": "20261023T093000Z-1a2b3c.bundle"},
        {"bundle_name": "x.bundle"},
        {"dvc_files_pushed": -1},
        {"bundles_kept": -1},
        {"bundles_removed": -1},
        {"dataless_files": -1},
    ],
    ids=["directory", "short-head", "free-name", "pushed", "kept", "removed", "dataless"],
)
def test_backup_report_rules(changes: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        report(**changes)


def state_doc(**changes: JSONValue) -> dict[str, JSONValue]:
    doc: dict[str, JSONValue] = {
        "kind": "backup_state",
        "schema_version": 1,
        "head": SHA1,
        "refs_sha256": DIGEST,
        "bundle_name": BUNDLE,
        "at": "2026-10-23T09:30:00Z",
    }
    return doc | changes


def test_backup_state_round_trips() -> None:
    state = BackupState.from_document(state_doc())
    assert state.at == AT
    assert state.model_dump(mode="json") == state_doc()
    assert BackupState.from_document(state_doc(head=SHA256_COMMIT)).head == SHA256_COMMIT


@pytest.mark.parametrize(
    "changes",
    [
        {"head": SHA1[:39]},
        {"head": SHA1.upper()},
        {"refs_sha256": DIGEST[:63]},
        {"bundle_name": "latest.bundle"},
        {"at": "2026-10-23T09:30:00"},
        {"at": "2026-10-23T19:00:00+09:30"},
    ],
    ids=["short-head", "upper-head", "short-refs", "bundle-name", "naive-at", "non-utc-at"],
)
def test_backup_state_rules(changes: dict[str, JSONValue]) -> None:
    with pytest.raises(InvalidInputError):
        BackupState.from_document(state_doc(**changes))


def test_backup_status_fields() -> None:
    status = BackupStatus(
        head=SHA1,
        last_backup_head=SHA1,
        last_backup_at=AT,
        refs_backed_up=True,
        commits_not_backed_up=0,
    )
    assert status.model_dump(mode="json") == {
        "head": SHA1,
        "last_backup_head": SHA1,
        "last_backup_at": "2026-10-23T09:30:00Z",
        "refs_backed_up": True,
        "commits_not_backed_up": 0,
    }
    never = BackupStatus(
        head=None, last_backup_head=None, last_backup_at=None, refs_backed_up=False, commits_not_backed_up=None
    )
    assert never.refs_backed_up is False
    with pytest.raises(ValidationError):
        BackupStatus(
            head=SHA1, last_backup_head=None, last_backup_at=None, refs_backed_up=False, commits_not_backed_up=-1
        )
    with pytest.raises(ValidationError):
        BackupStatus(
            head=SHA1,
            last_backup_head=SHA1,
            last_backup_at=AT.astimezone(timezone(timedelta(hours=9, minutes=30))),
            refs_backed_up=False,
            commits_not_backed_up=1,
        )


# SourceType and the catalog models (D3 §3.1, §4)


def test_source_types_and_public_sources() -> None:
    assert sorted(SourceType) == [
        "elec_bill",
        "gas_bill",
        "gas_bill_record",
        "offer_cdr",
        "offer_cdr_index",
        "offer_manual",
        "retailer_txn",
        "retailer_usage",
        "sapn_interval",
        "sapn_summary",
    ]
    assert frozenset({SourceType.OFFER_CDR, SourceType.OFFER_CDR_INDEX}) == PUBLIC_SOURCES


def test_coverage_is_ordered() -> None:
    assert Coverage(start=date(2026, 1, 1), end=date(2026, 1, 1)).end == date(2026, 1, 1)
    with pytest.raises(ValidationError):
        Coverage(start=date(2026, 1, 2), end=date(2026, 1, 1))


@pytest.mark.parametrize(
    ("evidence", "valid"),
    [
        (("filename",), True),
        (("content",), True),
        (("filename", "content"), True),
        ((), False),
        (("content", "filename"), False),
        (("filename", "filename"), False),
        (("option",), False),
    ],
)
def test_detection_evidence_is_ordered_and_never_empty(evidence: tuple[str, ...], valid: bool) -> None:
    if valid:
        assert Detection(source=SourceType.SAPN_INTERVAL, evidence=evidence).evidence == evidence  # pyright: ignore[reportArgumentType]
    else:
        with pytest.raises(ValidationError):
            Detection(source=SourceType.SAPN_INTERVAL, evidence=evidence)  # pyright: ignore[reportArgumentType]


def source_info(**changes: Any) -> SourceInfo:
    fields: dict[str, Any] = {
        "source_ts": datetime(2026, 10, 23, 10, 3, 45),
        "coverage": None,
        "extra": {"interval_minutes": 5},
        "identifiers": {"nmi": ["SYNTHNMI01"], "meter_serial": ["SYNTHSERIAL"]},
        "extension": ".csv",
    }
    return SourceInfo(**(fields | changes))


def test_source_info_never_shows_its_identifiers() -> None:
    info = source_info()
    assert info.identifiers["nmi"] == ["SYNTHNMI01"]
    assert "SYNTHNMI01" not in repr(info)
    assert "SYNTHSERIAL" not in str(info)
    with pytest.raises(ValidationError) as caught:
        source_info(identifiers={"nmi": ["SYNTHNMI01"], "address": ["SYNTHADDRESS"]})
    assert "SYNTHADDRESS" not in str(caught.value)


def test_source_info_never_dumps_its_identifiers() -> None:
    info = source_info()
    assert "identifiers" not in info.model_dump()
    assert "identifiers" not in info.model_dump(mode="json")
    dumped = info.model_dump_json()
    assert "identifiers" not in dumped
    assert "SYNTHNMI01" not in dumped
    assert "SYNTHSERIAL" not in dumped


@pytest.mark.parametrize("extension", [".CSV", ".txt", "csv", ""])
def test_source_info_extension_is_one_of_four(extension: str) -> None:
    with pytest.raises(ValidationError):
        source_info(extension=extension)


def test_source_info_extra_refuses_nan() -> None:
    with pytest.raises(ValidationError):
        source_info(extra={"ratio": float("nan")})


SOURCE_TS = datetime(2026, 10, 23, 10, 3, 45)
DATASET = dataset_id("sapn_interval", SOURCE_TS, DIGEST)


def record_doc(**changes: JSONValue) -> dict[str, JSONValue]:
    doc: dict[str, JSONValue] = {
        "kind": "catalog_record",
        "schema_version": 1,
        "dataset_id": DATASET,
        "source": "sapn_interval",
        "sha256": DIGEST,
        "bytes": 1234,
        "stored_path": f"data/raw/sapn_interval/2026/{DATASET}.csv",
        "original_filename": "synthetic_interval_file.csv",
        "landed_at": "2026-10-23T00:32:10Z",
        "file_mtime": "2026-10-23T00:05:41Z",
        "source_ts": "2026-10-23T10:03:45",
        "coverage": {"start": "2024-10-23", "end": "2026-10-22"},
        "detector": {"name": "sapn_interval", "version": 1, "evidence": ["filename", "content"]},
        "extra": {"interval_minutes": 5, "days": 731},
        "supersedes": None,
        "notes": "",
    }
    return doc | changes


def test_a_catalog_record_round_trips() -> None:
    record = CatalogRecord.from_document(record_doc())
    assert record.source is SourceType.SAPN_INTERVAL
    assert record.source_ts == SOURCE_TS
    assert record.detector == Detector(name=SourceType.SAPN_INTERVAL, version=1, evidence=("filename", "content"))
    assert record.model_dump(mode="json") == record_doc()
    option = CatalogRecord.from_document(
        record_doc(detector={"name": "sapn_interval", "version": 2, "evidence": ["option"]})
    )
    assert option.detector.evidence == ("option",)


def test_a_catalog_record_never_shows_its_filename_or_notes() -> None:
    record = CatalogRecord.from_document(record_doc(notes="synthetic private note"))
    assert "synthetic_interval_file" not in repr(record)
    assert "synthetic private note" not in repr(record)


@pytest.mark.parametrize(
    "changes",
    [
        {"dataset_id": dataset_id("sapn_interval", SOURCE_TS, sha256_hex(b"other"))},
        {"source": "sapn_summary"},
        {"sha256": DIGEST.upper()},
        {"bytes": 0},
        {"original_filename": ""},
        {"original_filename": "x" * 256},
        {"original_filename": "dir/synthetic.csv"},
        {"landed_at": "2026-10-23T00:32:10"},
        {"landed_at": "2026-10-23T10:02:10+09:30"},
        {"file_mtime": "2026-10-23T00:05:41"},
        {"coverage": {"start": "2026-10-23", "end": "2026-10-22"}},
        {"detector": {"name": "sapn_summary", "version": 1, "evidence": ["filename"]}},
        {"detector": {"name": "sapn_interval", "version": 0, "evidence": ["filename"]}},
        {"detector": {"name": "sapn_interval", "version": 1, "evidence": []}},
        {"detector": {"name": "sapn_interval", "version": 1, "evidence": ["option", "content"]}},
        {"supersedes": "not-an-id"},
        {"notes": "two\nlines"},
        {"notes": "x" * 501},
        {"source": "sapn_meter"},
    ],
    ids=[
        "dataset-id-mismatch",
        "source-mismatch",
        "sha256-upper",
        "zero-bytes",
        "empty-filename",
        "long-filename",
        "filename-with-directory",
        "naive-landed-at",
        "non-utc-landed-at",
        "naive-file-mtime",
        "coverage-reversed",
        "detector-other-source",
        "detector-version-zero",
        "detector-no-evidence",
        "detector-option-and-content",
        "supersedes-not-an-id",
        "notes-two-lines",
        "notes-too-long",
        "unknown-source",
    ],
)
def test_catalog_record_rules(changes: dict[str, JSONValue]) -> None:
    with pytest.raises(InvalidInputError):
        CatalogRecord.from_document(record_doc(**changes))


# StoredBlob, StageStatus, ReproReport, Fetched (D1 §4.2, §4.5, §4.6)


def test_stored_blob() -> None:
    blob = StoredBlob(key="data/raw/x/2026/y.csv", sha256=DIGEST, bytes=3)
    assert blob.model_dump() == {"key": "data/raw/x/2026/y.csv", "sha256": DIGEST, "bytes": 3}
    with pytest.raises(ValidationError):
        StoredBlob(key="k", sha256="nothex", bytes=3)
    with pytest.raises(ValidationError):
        StoredBlob(key="k", sha256=DIGEST, bytes=-1)


def test_pipeline_models() -> None:
    status = StageStatus(
        name="catalog_index", changed_deps=["a"], changed_outs=[], changed_params=[], changed_cmd=False
    )
    assert status.changed_deps == ["a"]
    assert ReproReport(ran=["a"], restored=[], cached=["b"]).cached == ["b"]
    with pytest.raises(ValidationError):
        status.name = "other"  # pyright: ignore[reportAttributeAccessIssue]


def test_fetched_is_utc_and_keeps_bytes_verbatim() -> None:
    fetched = Fetched(
        url="https://example.invalid/plans",
        request_headers={"x-v": "1"},
        status=200,
        fetched_at=AT,
        response_headers={},
        body=b"\x00{}",
    )
    assert fetched.body == b"\x00{}"
    with pytest.raises(ValidationError):
        Fetched(
            url="https://example.invalid/plans",
            request_headers={},
            status=200,
            fetched_at=datetime(2026, 10, 23, 9, 30),
            response_headers={},
            body=b"",
        )
