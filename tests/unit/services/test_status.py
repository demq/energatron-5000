"""`services.status` (P7 spec §4): each block from a scratch workspace, missing inputs, an invalid drill record.

The workspace holds synthetic run manifests (from the golden `run_manifest` v1), the golden recommendation, a decision
file name, one landed fetch log and one landed gas bill, with the bill's bytes in the inbox. `pipeline_sync.check` is
replaced where a test needs the pipeline in sync; everything else is real.
"""

import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

from energatron.adapters import wiring
from energatron.adapters.storage.fs_blobs import FsBlobStore
from energatron.adapters.storage.jsonl_catalog import JsonlCatalog
from energatron.core.errors import InvalidInputError, WarningCode
from energatron.core.models.catalog import SourceType
from energatron.core.models.vcs import BackupStatus
from energatron.core.models.workspace import WorkspacePaths
from energatron.services import status as status_service
from energatron.services.context import ServiceContext
from tests.fixtures import make_cdr as cdr
from tests.fixtures.make_sources import make_pdf
from tests.fixtures.pipeline_workspace import land_blob
from tests.unit.adapters._workspace import make_workspace, write
from tests.unit.services.test_context import FakeVcs, build

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "schemas"
BILL = make_pdf(creation_date="D:20261005120000+10'30'")
RUN_CLEAN = "run-20261102T000000Z-a1b2c3d"
RUN_DRAFT = "run-20261103T000000Z-a1b2c3d"


class Clock:
    def now(self) -> datetime:
        return datetime(2026, 11, 10, 14, 0, tzinfo=UTC)  # already the 11th in Adelaide


class BackedUp(FakeVcs):
    def backup_status(self) -> BackupStatus:
        return BackupStatus(
            head="a" * 40,
            last_backup_head="a" * 40,
            last_backup_at=datetime(2026, 11, 9, tzinfo=UTC),
            refs_backed_up=True,
            commits_not_backed_up=0,
        )


def manifest(run_id: str, *, draft: bool, datasets: list[str]) -> dict[str, Any]:
    doc = json.loads((FIXTURES / "run_manifest" / "v1.json").read_text(encoding="utf-8"))
    return doc | {"run_id": run_id, "draft": draft, "datasets": datasets}


@pytest.fixture
def paths(tmp_path: Path) -> WorkspacePaths:
    paths = make_workspace(tmp_path, private=None)
    catalog = JsonlCatalog(paths)
    for source in SourceType:
        catalog.initialise(source)
    log = cdr.fetch_log(batch="fetch-20261101T000000Z", fetched_at="2026-11-01T00:00:00Z", retailers=[], selected=[])
    fetch = land_blob(
        paths.root,
        SourceType.OFFER_CDR_INDEX,
        "fetch-log.json",
        cdr.fetch_log_body(log),
        landed_at=datetime(2026, 11, 1, 1, 0, tzinfo=UTC),
    )
    land_blob(paths.root, SourceType.GAS_BILL, "bill.pdf", BILL)
    write(
        paths.root,
        f"data/runs/{RUN_CLEAN}.json",
        json.dumps(manifest(RUN_CLEAN, draft=False, datasets=[fetch.dataset_id])),
    )
    write(paths.root, f"data/runs/{RUN_DRAFT}.json", json.dumps(manifest(RUN_DRAFT, draft=True, datasets=[])))
    write(
        paths.root,
        "data/reports/recommendations/recommendation.json",
        (FIXTURES / "recommendation" / "v1.json").read_text(encoding="utf-8"),
    )
    write(paths.root, "data/decisions/DEC-20261101-01.json", "{}")
    write(paths.root, "data/decisions/DEC-20261105-01.json", "{}")
    (paths.root / "data/landing").mkdir(parents=True)
    (paths.root / "data/landing/bill.pdf").write_bytes(BILL)
    (paths.root / "data/landing/new.csv").write_bytes(b"synthetic\n")
    return paths


def context(paths: WorkspacePaths, **changes: Any) -> ServiceContext:
    arguments: dict[str, Any] = {
        "paths": paths,
        "catalog": JsonlCatalog(paths),
        "blobs": FsBlobStore(paths),
        "clock": Clock(),
        "region": wiring.load_region_profile("sa"),
    } | changes
    return build(**arguments)


@pytest.fixture
def synced(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(status_service.pipeline_sync, "check", lambda ctx: None)


def test_every_block(paths: WorkspacePaths, synced: None) -> None:
    result = status_service.status(context(paths), code_stale=["usage_profile"], tool_dirty=True)
    assert result.today == date(2026, 11, 11)  # the region's local date
    assert result.pipeline.model_dump() == {
        "synced": True, "drift": [], "code_stale": ["usage_profile"], "tool_dirty": True,
    }  # fmt: skip
    assert result.last_run is not None and (result.last_run.run_id, result.last_run.draft) == (RUN_DRAFT, True)
    assert result.last_clean_run_id == RUN_CLEAN
    assert result.datasets_not_in_last_clean_run == 1  # the gas bill
    rec = result.recommendation
    assert rec is not None and rec.recommendation_id == "REC-20261101-0123abcd" and rec.action == "switch"
    assert rec.top is not None and rec.top.gas is None and rec.top.electricity
    assert (result.offers.days_since, result.offers.last_fetch_at) == (10, datetime(2026, 11, 1, 1, 0, tzinfo=UTC))
    assert result.landing.model_dump() == {"files": 2, "landed": 1, "new": 1}
    assert result.decisions.model_dump() == {"count": 2, "latest": "DEC-20261105-01"}
    assert result.backup.refs_backed_up is False
    assert result.drill.model_dump() == {"last_drill_at": None, "days_since": None, "due": True}
    assert result.reminders == [
        "W_LANDING_NEW", "W_RUN_BEHIND", "W_BACKUP_PENDING", "W_DRILL_DUE", "W_LANDING_LANDED",
    ]  # fmt: skip
    assert [warning.code.value for warning in result.warnings] == result.reminders


def test_a_recorded_drill_and_a_backup_quiet_their_reminders(paths: WorkspacePaths, synced: None) -> None:
    write(paths.root, ".energatron/drill_state.json", (FIXTURES / "drill_state" / "v1.json").read_text("utf-8"))
    result = status_service.status(context(paths, vcs=BackedUp()), code_stale=[], tool_dirty=False)
    assert result.drill.model_dump() == {
        "last_drill_at": datetime(2026, 11, 1, tzinfo=UTC), "days_since": 10, "due": False,
    }  # fmt: skip
    assert WarningCode.W_DRILL_DUE.value not in result.reminders
    assert WarningCode.W_BACKUP_PENDING.value not in result.reminders


def test_an_invalid_drill_record_counts_as_missing(paths: WorkspacePaths, synced: None) -> None:
    write(paths.root, ".energatron/drill_state.json", '{"kind": "drill_state"}')
    result = status_service.status(context(paths), code_stale=[], tool_dirty=False)
    assert result.drill.due and result.drill.last_drill_at is None


def test_drift_is_reported_not_raised(paths: WorkspacePaths) -> None:
    result = status_service.status(context(paths), code_stale=[], tool_dirty=False)
    assert result.pipeline.synced is False and result.pipeline.drift
    assert WarningCode.W_RUN_BEHIND.value in result.reminders


def test_an_empty_workspace_gives_nulls_and_zeros(tmp_path: Path, synced: None) -> None:
    paths = make_workspace(tmp_path, private=None)
    result = status_service.status(context(paths), code_stale=[], tool_dirty=False)
    assert (result.last_run, result.last_clean_run_id, result.recommendation) == (None, None, None)
    assert (result.offers.days_since, result.landing.files, result.decisions.count) == (None, 0, 0)
    assert "W_OFFERS_STALE" in result.reminders and "W_RUN_BEHIND" in result.reminders


def test_an_invalid_manifest_is_invalid_input(paths: WorkspacePaths, synced: None) -> None:
    write(paths.root, "data/runs/run-20261104T000000Z-a1b2c3d.json", "{}")
    with pytest.raises(InvalidInputError) as caught:
        status_service.status(context(paths), code_stale=[], tool_dirty=False)
    assert caught.value.details == {"file": "data/runs/run-20261104T000000Z-a1b2c3d.json"}


def test_status_writes_nothing(paths: WorkspacePaths, synced: None) -> None:
    before = sorted(str(path) for path in paths.root.rglob("*"))
    status_service.status(context(paths), code_stale=[], tool_dirty=False)
    assert sorted(str(path) for path in paths.root.rglob("*")) == before
