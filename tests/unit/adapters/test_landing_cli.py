"""`energatron landing list|clear` (P7 spec §5): one JSON document, the confirmation, and no lock for `list`."""

from pathlib import Path

import pytest

from energatron.adapters.storage.fs_blobs import FsBlobStore
from energatron.adapters.storage.jsonl_catalog import JsonlCatalog
from energatron.core.models.catalog import SourceType
from energatron.core.models.workspace import WorkspacePaths
from tests.fixtures.make_sources import make_pdf
from tests.fixtures.pipeline_workspace import make_record
from tests.unit.adapters._cli import FakeVcsState, run_cli
from tests.unit.adapters._workspace import make_workspace
from tests.unit.adapters.test_locking import held_by_another_process

LANDED = make_pdf(creation_date="D:20261005120000+10'30'")


@pytest.fixture
def paths(tmp_path: Path, fake_vcs: FakeVcsState) -> WorkspacePaths:
    paths = make_workspace(tmp_path)
    catalog = JsonlCatalog(paths)
    for source in SourceType:
        catalog.initialise(source)
    record = make_record(SourceType.GAS_BILL, "bill.pdf", LANDED)
    catalog.append(record)
    FsBlobStore(paths).put(record.stored_path, LANDED)
    inbox = paths.root / "data/landing"
    inbox.mkdir(parents=True)
    (inbox / "bill.pdf").write_bytes(LANDED)
    (inbox / "new.csv").write_bytes(b"synthetic\n")
    return paths


def test_list_prints_one_document_and_runs_while_the_lock_is_held(
    capsys: pytest.CaptureFixture[str], paths: WorkspacePaths
) -> None:
    with held_by_another_process(paths):
        ran = run_cli(capsys, "landing", "list", "--json", "--workspace", str(paths.root))
    assert ran.code == 0
    data = ran.envelope()["data"]
    assert (data["kind"], data["action"], data["landed"], data["new"]) == ("landing_result", "list", 1, 1)


def test_clear_needs_yes_in_json_mode(capsys: pytest.CaptureFixture[str], paths: WorkspacePaths) -> None:
    ran = run_cli(capsys, "landing", "clear", "--json", "--workspace", str(paths.root))
    assert ran.code == 2
    assert ran.envelope()["error"]["code"] == "E_CONFIRMATION_REQUIRED"
    assert (paths.root / "data/landing/bill.pdf").exists()


def test_clear_with_yes_deletes_the_landed_file(capsys: pytest.CaptureFixture[str], paths: WorkspacePaths) -> None:
    ran = run_cli(capsys, "landing", "clear", "--yes", "--json", "--workspace", str(paths.root))
    assert ran.code == 0
    data = ran.envelope()["data"]
    assert (data["action"], data["removed"], data["new"]) == ("clear", 1, 1)
    assert not (paths.root / "data/landing/bill.pdf").exists()
    assert (paths.root / "data/landing/new.csv").exists()
    again = run_cli(capsys, "landing", "clear", "--json", "--workspace", str(paths.root))
    assert again.code == 0  # nothing landed is left: no confirmation needed
    assert again.envelope()["data"]["removed"] == 0


def test_clear_dry_run_needs_no_confirmation(capsys: pytest.CaptureFixture[str], paths: WorkspacePaths) -> None:
    ran = run_cli(capsys, "landing", "clear", "--dry-run", "--json", "--workspace", str(paths.root))
    assert ran.code == 0
    assert (ran.envelope()["data"]["dry_run"], ran.envelope()["data"]["removed"]) == (True, 1)
    assert (paths.root / "data/landing/bill.pdf").exists()
