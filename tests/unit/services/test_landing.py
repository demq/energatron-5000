"""`services.landing` (P7 spec §5): landed against new, masking, the dry run, deleting and empty folders.

A scratch workspace with a real `JsonlCatalog` and `FsBlobStore`: one landed gas bill PDF (catalogued and stored), the
same bytes again in a subfolder, a new file, a catalogued file whose blob is missing, a hidden file and a symlink. The
NMI-like file name comes from the synthetic private config; every byte is synthetic.
"""

from pathlib import Path

import pytest

from energatron.adapters.storage.fs_blobs import FsBlobStore
from energatron.adapters.storage.jsonl_catalog import JsonlCatalog
from energatron.adapters.workspace import read_private_config
from energatron.core.models.catalog import SourceType
from energatron.core.models.workspace import WorkspacePaths
from energatron.services import landing as landing_service
from energatron.services.context import ServiceContext
from tests.fixtures.make_sources import make_pdf
from tests.fixtures.pipeline_workspace import make_record
from tests.unit.adapters._workspace import make_workspace, write
from tests.unit.services.test_context import build

NMI = "QAAAVZZZZZ"  # the synthetic private config's NMI, without its checksum digit
LANDED = make_pdf(creation_date="D:20261005120000+10'30'")
MISSING_BLOB = make_pdf(creation_date="D:20260905120000+09'30'")


@pytest.fixture
def paths(tmp_path: Path) -> WorkspacePaths:
    paths = make_workspace(tmp_path)
    catalog = JsonlCatalog(paths)
    for source in SourceType:
        catalog.initialise(source)
    blobs = FsBlobStore(paths)
    landed = make_record(SourceType.GAS_BILL, "bill.pdf", LANDED)
    catalog.append(landed)
    blobs.put(landed.stored_path, LANDED)
    catalog.append(make_record(SourceType.GAS_BILL, "old.pdf", MISSING_BLOB))  # its blob is never stored
    inbox = paths.root / "data/landing"
    (inbox / "sub").mkdir(parents=True)
    (inbox / "bill.pdf").write_bytes(LANDED)
    (inbox / "sub" / "copy.pdf").write_bytes(LANDED)
    (inbox / f"{NMI}_20261001_detailed.csv").write_bytes(b"synthetic, not landed\n")
    (inbox / "old.pdf").write_bytes(MISSING_BLOB)
    (inbox / ".DS_Store").write_bytes(b"hidden")
    (inbox / "link.pdf").symlink_to(inbox / "bill.pdf")
    return paths


@pytest.fixture
def ctx(paths: WorkspacePaths) -> ServiceContext:
    return build(paths=paths, catalog=JsonlCatalog(paths), blobs=FsBlobStore(paths), private=read_private_config(paths))


def test_list_tells_landed_from_new_and_masks_names(ctx: ServiceContext) -> None:
    result = landing_service.landing_list(ctx)
    statuses = {item.name: item.status for item in result.files}
    assert statuses == {
        "<redacted>_<n>_detailed.csv": "new",
        "bill.pdf": "landed",
        "old.pdf": "new",  # catalogued, but its blob is missing
        "sub/copy.pdf": "landed",
    }
    assert (result.action, result.dry_run, result.landed, result.new, result.removed) == ("list", False, 2, 2, 0)
    assert all(NMI not in item.name for item in result.files)
    dumped = result.model_dump_json()
    assert NMI not in dumped and ".DS_Store" not in dumped and "link.pdf" not in dumped


def test_a_dry_run_clear_deletes_nothing(ctx: ServiceContext, paths: WorkspacePaths) -> None:
    result = landing_service.landing_clear(ctx, dry_run=True)
    assert (result.action, result.dry_run, result.removed) == ("clear", True, 2)
    assert {item.name for item in result.files if item.removed} == {"bill.pdf", "sub/copy.pdf"}
    assert (paths.root / "data/landing/bill.pdf").exists()
    assert (paths.root / "data/landing/sub/copy.pdf").exists()


def test_clear_deletes_only_landed_files_and_empty_folders(ctx: ServiceContext, paths: WorkspacePaths) -> None:
    inbox = paths.root / "data/landing"
    result = landing_service.landing_clear(ctx)
    assert (result.removed, result.new) == (2, 2)
    assert sorted(path.name for path in inbox.iterdir()) == [
        ".DS_Store",
        f"{NMI}_20261001_detailed.csv",
        "link.pdf",
        "old.pdf",
    ]
    assert not (inbox / "sub").exists()
    assert inbox.is_dir()
    assert landing_service.landing_list(ctx).landed == 0


def test_clear_leaves_a_file_that_changed_since_the_scan(
    ctx: ServiceContext, paths: WorkspacePaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = landing_service._hash  # pyright: ignore[reportPrivateUsage]
    calls: list[Path] = []

    def changing(path: Path) -> str:
        calls.append(path)
        return "0" * 64 if calls.count(path) == 2 else real(path)

    monkeypatch.setattr(landing_service, "_hash", changing)
    result = landing_service.landing_clear(ctx)
    assert result.removed == 0
    assert (paths.root / "data/landing/bill.pdf").exists()


def test_inbox_counts_and_a_missing_inbox(ctx: ServiceContext, paths: WorkspacePaths) -> None:
    assert landing_service.inbox(ctx) == landing_service.Inbox(files=4, landed=2, new=2)
    for path in sorted((paths.root / "data/landing").rglob("*"), reverse=True):
        if path.is_dir() and not path.is_symlink():
            path.rmdir()
        else:
            path.unlink()
    (paths.root / "data/landing").rmdir()
    assert landing_service.inbox(ctx) == landing_service.Inbox(files=0, landed=0, new=0)
    assert landing_service.landing_clear(ctx).files == []


def test_without_a_private_config_digit_runs_are_still_masked(paths: WorkspacePaths) -> None:
    context = build(paths=paths, catalog=JsonlCatalog(paths), blobs=FsBlobStore(paths), private=None)
    names = [item.name for item in landing_service.landing_list(context).files]
    assert f"{NMI}_<n>_detailed.csv" in names  # the synthetic NMI has no digits; a real one is all digits
    write(paths.root, "data/landing/x.txt", "x")
    assert "x.txt" in [item.name for item in landing_service.landing_list(context).files]
