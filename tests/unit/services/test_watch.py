"""`services.watch` (P7 spec §6): fetch error, run error, no change, behind, a cheaper plan, the dry run.

The fetch and the run are replaced by stand-ins at the service level; the status facts come from the scratch workspace
of `test_status` (with the pipeline in sync). The stand-in run swaps in a recommendation whose top plan differs.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from energatron.adapters.storage.jsonl_catalog import JsonlCatalog
from energatron.core.errors import DirtyError, NetworkError, ResultWarning, WarningCode
from energatron.core.models.recommend import ToolState
from energatron.core.models.workspace import WorkspacePaths
from energatron.services import watch as watch_service
from energatron.services.fetch_offers import FetchOffersResult, RetailerSummary
from energatron.services.run import RunResult
from tests.unit.adapters._workspace import write
from tests.unit.services.test_status import FIXTURES, context, make_status_workspace

CLEAN = ToolState(energatron_version="0.1.0", install="editable", tool_commit="e" * 40, tool_dirty=False)
REC = "data/reports/recommendations/recommendation.json"


@pytest.fixture
def paths(tmp_path: Path) -> WorkspacePaths:
    return make_status_workspace(tmp_path)


@pytest.fixture
def synced(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(watch_service.status_service.pipeline_sync, "check", lambda ctx: None)


def fetched(landed: int, *, failed: bool = False) -> FetchOffersResult:
    retailer = RetailerSummary(
        base="https://cdr.test/alpha", listed=1, selected=1, fetched=landed, unchanged=0, failed=0, left_out={}
    )
    return FetchOffersResult(
        fetch_batch_id="fetch-20261110T000000Z",
        dry_run=False,
        postcode_applied=True,
        retailers=[retailer],
        landed=[f"offer_cdr-20261110T000000-{index:08x}" for index in range(landed)],
        rejected=0,
        commit="c" * 40 if landed else None,
    )


class Stand:
    """The fetch and run stand-ins, and what they were asked."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, root: Path, *, landed: int = 1) -> None:
        self.root = root
        self.landed = landed
        self.fetch_error: Exception | None = None
        self.run_error: Exception | None = None
        self.runs = 0
        self.dry_runs: list[bool] = []
        monkeypatch.setattr(watch_service.fetch_service, "fetch_offers", self.fetch)
        monkeypatch.setattr(watch_service.run_service, "run", self.run)

    def fetch(self, ctx: Any, *, dry_run: bool = False) -> FetchOffersResult:
        self.dry_runs.append(dry_run)
        if self.fetch_error is not None:
            raise self.fetch_error
        found = fetched(0 if dry_run else self.landed)
        pending = ResultWarning(code=WarningCode.W_BACKUP_PENDING, message="Pending.")
        return found.model_copy(update={"warnings": (pending,)})

    def run(self, ctx: Any, *, tool: ToolState, draft: bool = False) -> RunResult:
        assert draft is False
        self.runs += 1
        if self.run_error is not None:
            raise self.run_error
        document = json.loads((FIXTURES / "recommendation" / "v1.json").read_text(encoding="utf-8"))
        document["recommendation_id"] = "REC-20261110-89abcdef"
        document["options"][0]["electricity"]["plan_id"] = "SYN-E-2"
        write(self.root, REC, json.dumps(document))
        return RunResult(
            run_id="run-20261110T000000Z-a1b2c3d",
            draft=False,
            recommendation_id="REC-20261110-89abcdef",
            action="switch",
            robust=True,
            ran=20,
            restored=0,
            cached=0,
            commit="d" * 40,
        )


def all_landed(paths: WorkspacePaths) -> None:
    """A clean run manifest naming every catalogued dataset, so the status isn't behind."""
    ids = sorted(record.dataset_id for record in JsonlCatalog(paths).records())
    document = json.loads((FIXTURES / "run_manifest" / "v1.json").read_text(encoding="utf-8"))
    document |= {"run_id": "run-20261104T000000Z-a1b2c3d", "draft": False, "datasets": ids}
    write(paths.root, "data/runs/run-20261104T000000Z-a1b2c3d.json", json.dumps(document))


def test_a_fetch_that_landed_runs_and_a_new_top_plan_is_notified(
    paths: WorkspacePaths, synced: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    stand = Stand(monkeypatch, paths.root)
    result = watch_service.watch(context(paths), tool=CLEAN, code_stale=[])
    assert (result.ran, result.run_id, result.run_error, result.fetch_error) == (
        True, "run-20261110T000000Z-a1b2c3d", None, None,
    )  # fmt: skip
    assert result.fetch is not None and result.fetch.landed == 1
    assert (result.before, result.after, result.cheaper_plan) == (
        "REC-20261101-0123abcd", "REC-20261110-89abcdef", True,
    )  # fmt: skip
    assert result.notifications[0].title == "energatron: cheaper plan"
    assert "REC-20261110-89abcdef" in result.notifications[0].message
    assert stand.runs == 1
    assert watch_service.outcome(result) is None
    codes = [warning.code.value for warning in result.warnings]
    assert len(codes) == len(set(codes))  # a reminder the fetch also warned about appears once


def test_nothing_new_and_not_behind_means_no_run(
    paths: WorkspacePaths, synced: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    all_landed(paths)
    stand = Stand(monkeypatch, paths.root, landed=0)
    result = watch_service.watch(context(paths), tool=CLEAN, code_stale=[])
    assert (result.ran, result.cheaper_plan, stand.runs) == (False, False, 0)
    assert result.before == result.after
    assert [item.title for item in result.notifications] == ["energatron: reminders"]  # backup pending, drill due


def test_behind_runs_even_when_nothing_landed(
    paths: WorkspacePaths, synced: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    stand = Stand(monkeypatch, paths.root, landed=0)  # the gas bill isn't in the last clean run
    assert watch_service.watch(context(paths), tool=CLEAN, code_stale=[]).ran
    assert stand.runs == 1


def test_a_failed_fetch_skips_the_run_and_is_partial(
    paths: WorkspacePaths, synced: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    stand = Stand(monkeypatch, paths.root)
    stand.fetch_error = NetworkError("No network.")
    result = watch_service.watch(context(paths), tool=CLEAN, code_stale=[])
    assert (result.fetch, result.fetch_error, result.ran, stand.runs) == (None, "E_NETWORK", False, 0)
    assert result.notifications[0].message.startswith("fetch failed with E_NETWORK")
    error = watch_service.outcome(result)
    assert error is not None and error.code == "E_PARTIAL"


def test_a_failed_run_is_recorded_not_retried(
    paths: WorkspacePaths, synced: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    stand = Stand(monkeypatch, paths.root)
    stand.run_error = DirtyError("Dirty.", reason="tool_dirty")
    result = watch_service.watch(context(paths), tool=CLEAN, code_stale=[])
    assert (result.ran, result.run_error, result.cheaper_plan, stand.runs) == (False, "E_DIRTY", False, 1)
    assert any(item.message.startswith("run failed with E_DIRTY") for item in result.notifications)
    assert watch_service.outcome(result) is not None


def test_a_dry_run_fetches_lists_only_and_never_runs(
    paths: WorkspacePaths, synced: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    stand = Stand(monkeypatch, paths.root)
    result = watch_service.watch(context(paths), tool=CLEAN, code_stale=[], dry_run=True)
    assert (result.dry_run, result.ran, stand.runs, stand.dry_runs) == (True, False, 0, [True])
