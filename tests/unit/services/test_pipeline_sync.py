"""`services.pipeline_sync`: `sync` and `check` (D3 §10.1, §10.2, §13).

The workspace is a scratch folder with the real `JsonlCatalog`; `VersionControl` is a fake that records `track` and,
as `dvc add` does, adds one `/<source>` line per tracked directory to `data/raw/.gitignore`. Nothing commits.
"""

import os
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import ClassVar, Literal

import pytest
import yaml
from pydantic import Field

from energatron.adapters.storage.jsonl_catalog import JsonlCatalog
from energatron.core import persisted
from energatron.core.canonical import JSONValue
from energatron.core.errors import InvalidInputError, PipelineNotSyncedError, SchemaTooNewError
from energatron.core.models.catalog import SourceType
from energatron.core.models.params import Params
from energatron.core.models.vcs import BackupReport, BackupStatus
from energatron.core.models.workspace import WorkspacePaths
from energatron.core.persisted import Persisted
from energatron.services import pipeline_sync
from energatron.services.context import ServiceContext
from energatron.services.pipeline_sync import PipelineSyncResult
from energatron.services.stages.dvc_yaml import emit_dvc_yaml
from energatron.services.stages.registry import STAGES
from tests.unit.adapters._workspace import make_workspace, write
from tests.unit.services.test_context import build

GOLDEN = Path(__file__).resolve().parents[2] / "fixtures" / "golden" / "dvc.yaml"
PARAMS_V1 = "kind: params\nschema_version: 1\n"
ALL_SOURCES = sorted(source.value for source in SourceType)


class TrackingVcs:
    def __init__(self, paths: WorkspacePaths) -> None:
        self.paths = paths
        self.tracked: list[list[str]] = []

    def head(self) -> str | None:
        return None

    def dirty_paths(self, paths: Sequence[str] = ()) -> list[str]:
        return []

    def track(self, paths: Sequence[str]) -> list[str]:
        self.tracked.append(list(paths))
        ignore = self.paths.root / "data" / "raw" / ".gitignore"
        lines = ignore.read_text(encoding="utf-8").splitlines() if ignore.exists() else []
        for path in paths:
            line = f"/{Path(path).name}"
            if line not in lines:
                lines.append(line)
        ignore.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")
        return [f"{path}.dvc" for path in paths]

    def commit(self, paths: Sequence[str], message: str) -> str:
        raise AssertionError("pipeline sync never commits")

    def backup(self) -> BackupReport:
        raise AssertionError("pipeline sync never backs up")

    def backup_status(self) -> BackupStatus:
        raise AssertionError("pipeline sync never asks for the backup status")


@pytest.fixture
def paths(tmp_path: Path) -> WorkspacePaths:
    return make_workspace(tmp_path, private=None)


@pytest.fixture
def vcs(paths: WorkspacePaths) -> TrackingVcs:
    return TrackingVcs(paths)


@pytest.fixture
def ctx(paths: WorkspacePaths, vcs: TrackingVcs) -> ServiceContext:
    return build(paths=paths, catalog=JsonlCatalog(paths), vcs=vcs)


def snapshot(root: Path) -> dict[str, tuple[bytes, int]]:
    """Every file under `root` but the workspace marker, with its bytes and mtime."""
    found: dict[str, tuple[bytes, int]] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            found[path.relative_to(root).as_posix()] = (path.read_bytes(), path.stat().st_mtime_ns)
    return found


def synced(ctx: ServiceContext) -> PipelineSyncResult:
    return pipeline_sync.sync(ctx)


def drift_of(ctx: ServiceContext) -> list[str]:
    with pytest.raises(PipelineNotSyncedError) as caught:
        pipeline_sync.check(ctx)
    drift = caught.value.details["drift"]
    assert isinstance(drift, list)
    return [str(item) for item in drift]


# A fresh workspace


def test_a_fresh_workspace_gets_dvc_yaml_params_and_every_catalog(ctx: ServiceContext, vcs: TrackingVcs) -> None:
    result = synced(ctx)
    root = ctx.paths.root
    assert (root / "dvc.yaml").read_bytes() == GOLDEN.read_bytes()
    assert (root / "params.yaml").read_text(encoding="utf-8") == PARAMS_V1
    for source in SourceType:
        assert ctx.paths.catalog_path(source).read_bytes() == b""
    assert result.dvc_yaml_written is True
    assert result.params_written is True
    assert result.params_rewritten is False
    assert result.catalogs_created == ALL_SOURCES
    assert vcs.tracked == [[f"data/raw/{source}" for source in ALL_SOURCES]]
    assert result.pointer_files == [*(f"data/raw/{source}.dvc" for source in ALL_SOURCES), "data/raw/.gitignore"]
    assert result.dry_run is False
    pipeline_sync.check(ctx)  # in sync now


def test_the_result_dumps_as_pipeline_sync_result_v1(ctx: ServiceContext) -> None:
    dumped = synced(ctx).model_dump(mode="json")
    assert dumped["kind"] == "pipeline_sync_result"
    assert dumped["schema_version"] == 1
    assert set(dumped) == {
        "kind",
        "schema_version",
        "dvc_yaml_written",
        "params_written",
        "params_rewritten",
        "catalogs_created",
        "pointer_files",
        "dry_run",
    }


def test_a_second_sync_writes_and_tracks_nothing(ctx: ServiceContext, vcs: TrackingVcs) -> None:
    synced(ctx)
    before = snapshot(ctx.paths.root)
    again = synced(ctx)
    assert snapshot(ctx.paths.root) == before
    assert (again.dvc_yaml_written, again.params_written, again.params_rewritten) == (False, False, False)
    assert again.catalogs_created == []
    assert again.pointer_files == []
    assert len(vcs.tracked) == 1


# dvc.yaml


def test_dvc_yaml_is_rewritten_only_when_its_bytes_differ(ctx: ServiceContext) -> None:
    synced(ctx)
    dvc_yaml = ctx.paths.root / "dvc.yaml"
    dvc_yaml.write_text(emit_dvc_yaml(STAGES) + "# edited by hand\n", encoding="utf-8")
    assert drift_of(ctx) == ["dvc.yaml"]
    result = synced(ctx)
    assert result.dvc_yaml_written is True
    assert dvc_yaml.read_bytes() == GOLDEN.read_bytes()
    assert synced(ctx).dvc_yaml_written is False


def test_a_missing_dvc_yaml_is_drift_and_is_written(ctx: ServiceContext) -> None:
    synced(ctx)
    (ctx.paths.root / "dvc.yaml").unlink()
    assert drift_of(ctx) == ["dvc.yaml"]
    assert synced(ctx).dvc_yaml_written is True


# params.yaml


def test_a_current_params_file_is_validated_and_left_alone(ctx: ServiceContext) -> None:
    params = write(ctx.paths.root, "params.yaml", "# my comment\nkind: params\nschema_version: 1\n")
    result = synced(ctx)
    assert (result.params_written, result.params_rewritten) == (False, False)
    assert params.read_text(encoding="utf-8").startswith("# my comment\n")


def test_a_missing_params_file_is_drift(ctx: ServiceContext) -> None:
    synced(ctx)
    (ctx.paths.root / "params.yaml").unlink()
    assert drift_of(ctx) == ["params.yaml"]


@pytest.mark.parametrize(
    "text",
    [
        "kind: params\nschema_version: 1\nunknown: 1\n",
        "- a list\n",
        "kind: [unclosed\n",
        "kind: other\nschema_version: 1\n",
    ],
)
def test_an_invalid_params_file_is_invalid_input_in_sync_and_check(ctx: ServiceContext, text: str) -> None:
    write(ctx.paths.root, "dvc.yaml", emit_dvc_yaml(STAGES))
    write(ctx.paths.root, "params.yaml", text)
    before = snapshot(ctx.paths.root)
    with pytest.raises(InvalidInputError):
        pipeline_sync.sync(ctx)
    with pytest.raises(InvalidInputError):
        pipeline_sync.check(ctx)
    assert snapshot(ctx.paths.root) == before  # nothing written, not even the catalogs


def test_a_newer_params_file_is_schema_too_new_and_nothing_is_written(ctx: ServiceContext, vcs: TrackingVcs) -> None:
    write(ctx.paths.root, "params.yaml", "kind: params\nschema_version: 2\n")
    before = snapshot(ctx.paths.root)
    with pytest.raises(SchemaTooNewError):
        pipeline_sync.sync(ctx)
    with pytest.raises(SchemaTooNewError):
        pipeline_sync.check(ctx)
    assert snapshot(ctx.paths.root) == before
    assert vcs.tracked == []


@pytest.fixture
def params_v2(monkeypatch: pytest.MonkeyPatch) -> type[Persisted]:
    """Make `params` a test-only v2 kind, adding `meter.timebase` (default 30), with a v1 → v2 migration that inserts
    it. The registries are swapped for copies, so the real `Params` comes back after the test."""
    kinds = {kind: model for kind, model in persisted.registered_kinds().items() if kind != Params.KIND}
    monkeypatch.setattr(persisted, "_KINDS", kinds)
    monkeypatch.setattr(persisted, "_MIGRATIONS", dict(persisted.registered_migrations()))

    class ParamsV2(Persisted):
        KIND: ClassVar[str] = "params"
        SCHEMA_VERSION: ClassVar[int] = 2
        kind: Literal["params"] = "params"
        schema_version: Literal[2] = 2

        meter: dict[str, int] = Field(default_factory=lambda: {"timebase": 30})
        note: str | None = None

    @persisted.migration("params", from_version=1)
    def _v1_to_v2(doc: dict[str, JSONValue]) -> dict[str, JSONValue]:  # pyright: ignore[reportUnusedFunction]
        return {**doc, "schema_version": 2, "meter": {"timebase": 30}}

    return ParamsV2


def test_params_v2_is_registered_only_inside_the_fixture(params_v2: type[Persisted]) -> None:
    assert persisted.registered_kinds()["params"] is params_v2


def test_an_older_params_file_is_drift_then_rewritten_keeping_its_values(
    ctx: ServiceContext, params_v2: type[Persisted]
) -> None:
    synced_v1 = PARAMS_V1.replace("schema_version: 1\n", "schema_version: 1\nnote: kept\n")
    write(ctx.paths.root, "dvc.yaml", emit_dvc_yaml(STAGES))
    for source in SourceType:
        write(ctx.paths.root, f"data/raw/{source.value}/_catalog.jsonl", "")
    write(ctx.paths.root, "params.yaml", "# a comment that is lost\n" + synced_v1)
    assert drift_of(ctx) == ["params.yaml"]

    result = synced(ctx)
    assert (result.params_written, result.params_rewritten) == (False, True)
    text = (ctx.paths.root / "params.yaml").read_text(encoding="utf-8")
    assert text.startswith("kind: params\nschema_version: 2\n")
    assert yaml.safe_load(text) == {"kind": "params", "schema_version": 2, "meter": {"timebase": 30}, "note": "kept"}
    pipeline_sync.check(ctx)
    assert synced(ctx).params_rewritten is False


def test_a_missing_params_file_gets_the_current_versions_defaults(
    ctx: ServiceContext, params_v2: type[Persisted]
) -> None:
    assert synced(ctx).params_written is True
    text = (ctx.paths.root / "params.yaml").read_text(encoding="utf-8")
    assert text == "kind: params\nschema_version: 2\nmeter:\n  timebase: 30\nnote: null\n"


# Catalogs


def test_only_missing_catalogs_are_created_and_tracked(ctx: ServiceContext, vcs: TrackingVcs) -> None:
    write(ctx.paths.root, "data/raw/sapn_interval/_catalog.jsonl", "")
    write(ctx.paths.root, "data/raw/.gitignore", "/sapn_interval\n")
    result = synced(ctx)
    expected = [source for source in ALL_SOURCES if source != "sapn_interval"]
    assert result.catalogs_created == expected
    assert vcs.tracked == [[f"data/raw/{source}" for source in expected]]
    assert "data/raw/.gitignore" in result.pointer_files


def test_missing_catalogs_are_drift(ctx: ServiceContext) -> None:
    synced(ctx)
    ctx.paths.catalog_path(SourceType.GAS_BILL).unlink()
    assert drift_of(ctx) == ["catalogs"]


def test_the_gitignore_is_listed_only_when_it_changed(ctx: ServiceContext) -> None:
    write(ctx.paths.root, "data/raw/.gitignore", "".join(f"/{source}\n" for source in ALL_SOURCES))
    result = synced(ctx)
    assert "data/raw/.gitignore" not in result.pointer_files
    assert len(result.pointer_files) == len(ALL_SOURCES)


# --check and --dry-run


def test_check_lists_every_kind_of_drift_in_order(ctx: ServiceContext) -> None:
    before = snapshot(ctx.paths.root)
    assert drift_of(ctx) == ["dvc.yaml", "params.yaml", "catalogs"]
    assert snapshot(ctx.paths.root) == before


def test_check_passes_on_a_synced_workspace_and_writes_nothing(ctx: ServiceContext, vcs: TrackingVcs) -> None:
    synced(ctx)
    before = snapshot(ctx.paths.root)
    pipeline_sync.check(ctx)
    assert snapshot(ctx.paths.root) == before
    assert len(vcs.tracked) == 1


def test_a_dry_run_writes_and_tracks_nothing_but_reports_what_it_would_do(
    ctx: ServiceContext, vcs: TrackingVcs
) -> None:
    before = snapshot(ctx.paths.root)
    result = pipeline_sync.sync(ctx, dry_run=True)
    assert snapshot(ctx.paths.root) == before
    assert vcs.tracked == []
    assert result.dry_run is True
    assert (result.dvc_yaml_written, result.params_written, result.params_rewritten) == (True, True, False)
    assert result.catalogs_created == ALL_SOURCES
    assert result.pointer_files == []


def test_a_dry_run_of_an_older_params_file_reports_the_rewrite(ctx: ServiceContext, params_v2: type[Persisted]) -> None:
    write(ctx.paths.root, "params.yaml", PARAMS_V1)
    before = snapshot(ctx.paths.root)
    result = pipeline_sync.sync(ctx, dry_run=True)
    assert result.params_rewritten is True
    assert snapshot(ctx.paths.root) == before


def test_written_files_are_world_readable_and_atomic(ctx: ServiceContext) -> None:
    synced(ctx)
    root = ctx.paths.root
    for name in ("dvc.yaml", "params.yaml"):
        assert os.stat(root / name).st_mode & 0o777 == 0o644
    leftovers: Iterator[Path] = root.glob(".*.tmp")
    assert list(leftovers) == []
