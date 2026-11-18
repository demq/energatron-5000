"""`services.run` (P6 spec §8): the refusals, the manifest, the commit and the backup warning.

The workspace is a synced scratch folder with the real `JsonlCatalog`; `VersionControl` and `PipelineRunner` are fakes
that record what they're asked. The fake repro writes a recommendation document, as the `recommend` stage would.
"""

import json
import shutil
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from energatron.adapters.storage.jsonl_catalog import JsonlCatalog
from energatron.core.errors import DirtyError, PipelineNotSyncedError, StageFailedError, WarningCode
from energatron.core.ids import parse_run_id
from energatron.core.models.catalog import SourceType
from energatron.core.models.params import Params
from energatron.core.models.pipeline import ReproReport
from energatron.core.models.recommend import RunManifest, ToolState
from energatron.core.models.vcs import BackupStatus
from energatron.core.models.workspace import WorkspacePaths
from energatron.services import run as run_service
from energatron.services.context import ServiceContext
from energatron.services.stages.dvc_yaml import emit_dvc_yaml
from energatron.services.stages.registry import STAGES
from tests.fixtures.progress import RecordingProgress
from tests.unit.adapters._workspace import make_workspace, write
from tests.unit.services.test_context import TEST_REGION, FakeRunner, FakeVcs, build

HEAD = "a1b2c3d" + "0" * 33
COMMIT = "f" * 40
REC = Path(__file__).resolve().parents[2] / "fixtures" / "schemas" / "recommendation" / "v1.json"
CLEAN = ToolState(energatron_version="0.1.0", install="editable", tool_commit="e" * 40, tool_dirty=False)


class RunVcs(FakeVcs):
    def __init__(self, dirty: Sequence[str] = ()) -> None:
        self.dirty = list(dirty)
        self.tracked: list[list[str]] = []
        self.commits: list[tuple[list[str], str]] = []

    def head(self) -> str | None:
        return HEAD

    def dirty_paths(self, paths: Sequence[str] = ()) -> list[str]:
        return [path for path in self.dirty if any(path == p or path.startswith(f"{p}/") for p in paths)]

    def track(self, paths: Sequence[str]) -> list[str]:
        self.tracked.append(list(paths))
        return [f"{path}.dvc" for path in paths]

    def commit(self, paths: Sequence[str], message: str) -> str:
        self.commits.append((list(paths), message))
        return COMMIT

    def backup_status(self) -> BackupStatus:
        return BackupStatus(
            head=HEAD, last_backup_head=None, last_backup_at=None, refs_backed_up=False, commits_not_backed_up=3
        )


class RecRunner(FakeRunner):
    def __init__(self, root: Path, *, recommend: bool = True, fail: bool = False) -> None:
        self.root, self.recommend, self.fail, self.calls = root, recommend, fail, 0

    def repro(self, targets: Sequence[str] = ()) -> ReproReport:
        self.calls += 1
        if self.fail:
            raise StageFailedError("A stage failed.", stage="bundles", exit_code=1, ran=[], restored=[])
        if self.recommend:
            target = self.root / "data/reports/recommendations/recommendation.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(REC, target)
        return ReproReport(ran=["catalog_index", "recommend"], restored=["parse_nem12"], cached=["localise"])


class StepClock:
    def __init__(self) -> None:
        self.calls = 0

    def now(self) -> datetime:
        self.calls += 1
        return datetime(2026, 11, 10, 9, 0, self.calls, tzinfo=UTC)


@pytest.fixture
def paths(tmp_path: Path) -> WorkspacePaths:
    paths = make_workspace(tmp_path, private=None)
    write(paths.root, "dvc.yaml", emit_dvc_yaml(STAGES))
    defaults = Params.defaults(TEST_REGION).model_dump(mode="json")
    write(paths.root, "params.yaml", yaml.safe_dump(defaults, sort_keys=False))
    for folder in ("config/offer_rules.local", "config/bundle_rules.local", "data/runs", "data/decisions"):
        write(paths.root, f"{folder}/README.md", "readme\n")
    catalog = JsonlCatalog(paths)
    for source in SourceType:
        catalog.initialise(source)
    return paths


def context(
    paths: WorkspacePaths, vcs: RunVcs, runner: RecRunner, progress: RecordingProgress | None = None
) -> ServiceContext:
    return build(
        paths=paths,
        catalog=JsonlCatalog(paths),
        vcs=vcs,
        runner=runner,
        clock=StepClock(),
        progress=progress or RecordingProgress(),
    )


def manifest_of(paths: WorkspacePaths, run_id: str) -> RunManifest:
    return RunManifest.from_document(json.loads((paths.root / "data/runs" / f"{run_id}.json").read_bytes()))


def test_a_clean_run_writes_the_manifest_and_commits_exactly_the_run_files(paths: WorkspacePaths) -> None:
    vcs = RunVcs(dirty=["data/reports/.gitignore", "data/landing.txt"])
    result = run_service.run(context(paths, vcs, RecRunner(paths.root)), tool=CLEAN)
    assert parse_run_id(result.run_id)
    assert result.run_id == "run-20261110T090001Z-a1b2c3d"
    assert (result.draft, result.recommendation_id, result.action, result.robust) == (
        False,
        "REC-20261101-0123abcd",
        "switch",
        True,
    )
    assert (result.ran, result.restored, result.cached, result.commit) == (2, 1, 1, COMMIT)
    assert vcs.tracked == [["data/runs"]]
    assert vcs.commits == [
        (
            [".energatron/code_fingerprint.json", "data/reports/.gitignore", "data/runs.dvc", "dvc.lock"],
            f"run: {result.run_id} REC-20261101-0123abcd",
        )
    ]
    manifest = manifest_of(paths, result.run_id)
    assert (manifest.draft, manifest.workspace_head, manifest.tool) == (False, HEAD, CLEAN)
    assert manifest.started_at < manifest.finished_at
    assert (manifest.stages.ran, manifest.stages.restored, manifest.stages.cached) == (
        ["catalog_index", "recommend"],
        ["parse_nem12"],
        ["localise"],
    )
    assert (manifest.datasets, manifest.fetch_batches) == ([], [])
    assert [(warning.code, warning.details) for warning in result.warnings] == [
        (WarningCode.W_BACKUP_PENDING, {"commits_not_backed_up": 3})
    ]


def test_dirty_tracked_config_is_refused_unless_draft(paths: WorkspacePaths) -> None:
    vcs = RunVcs(dirty=["params.yaml", "config/bundle_rules.local/new.yaml", "data/raw/gas_bill.dvc", "data/runs.dvc"])
    runner = RecRunner(paths.root)
    with pytest.raises(DirtyError) as caught:
        run_service.run(context(paths, vcs, runner), tool=CLEAN)
    assert caught.value.details["paths"] == [
        "config/bundle_rules.local/new.yaml",
        "data/raw/gas_bill.dvc",
        "params.yaml",
    ]
    assert runner.calls == 0
    result = run_service.run(context(paths, vcs, runner), tool=CLEAN, draft=True)
    assert result.draft is True
    assert manifest_of(paths, result.run_id).draft is True


def test_a_dirty_tool_is_refused_unless_draft(paths: WorkspacePaths) -> None:
    dirty = CLEAN.model_copy(update={"tool_dirty": True})
    with pytest.raises(DirtyError) as caught:
        run_service.run(context(paths, RunVcs(), RecRunner(paths.root)), tool=dirty)
    assert caught.value.details["reason"] == "tool_dirty"
    assert run_service.run(context(paths, RunVcs(), RecRunner(paths.root)), tool=dirty, draft=True).draft


def test_an_unsynced_workspace_is_refused(paths: WorkspacePaths) -> None:
    (paths.root / "data/runs/README.md").unlink()
    (paths.root / "data/runs").rmdir()
    with pytest.raises(PipelineNotSyncedError):
        run_service.run(context(paths, RunVcs(), RecRunner(paths.root)), tool=CLEAN)


def test_a_failed_repro_writes_and_commits_nothing(paths: WorkspacePaths) -> None:
    vcs = RunVcs()
    with pytest.raises(StageFailedError):
        run_service.run(context(paths, vcs, RecRunner(paths.root, fail=True)), tool=CLEAN)
    assert (vcs.tracked, vcs.commits) == ([], [])
    assert [path.name for path in (paths.root / "data/runs").iterdir()] == ["README.md"]


def test_without_a_recommendation_the_message_names_only_the_run(paths: WorkspacePaths) -> None:
    vcs = RunVcs()
    result = run_service.run(context(paths, vcs, RecRunner(paths.root, recommend=False)), tool=CLEAN)
    assert result.recommendation_id is None
    assert vcs.commits[0][1] == f"run: {result.run_id}"


def test_the_result_dumps_as_run_result_v1(paths: WorkspacePaths) -> None:
    dumped = run_service.run(context(paths, RunVcs(), RecRunner(paths.root)), tool=CLEAN).model_dump(mode="json")
    assert (dumped["kind"], dumped["schema_version"]) == ("run_result", 1)
    assert "warnings" not in dumped


def test_a_run_announces_each_phase_in_order(paths: WorkspacePaths) -> None:
    progress = RecordingProgress()
    run_service.run(context(paths, RunVcs(), RecRunner(paths.root), progress), tool=CLEAN)
    assert progress.steps() == [
        "Checking that the pipeline files match this tool",
        "Checking for uncommitted changes",
        "Recording the run",
        "Committing the run",
    ]


def test_a_draft_run_skips_the_uncommitted_changes_check(paths: WorkspacePaths) -> None:
    progress = RecordingProgress()
    run_service.run(context(paths, RunVcs(), RecRunner(paths.root), progress), tool=CLEAN, draft=True)
    assert "Checking for uncommitted changes" not in progress.steps()
