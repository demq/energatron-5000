"""`services.pipeline_sync`: `sync` and `check` (D3 §10.1, §10.2, §13).

The workspace is a scratch folder with the real `JsonlCatalog`; `VersionControl` is a fake that records `track` and,
as `dvc add` does, adds one `/<source>` line per tracked directory to `data/raw/.gitignore`. Nothing commits.
"""

import copy
import dataclasses
import os
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

import pytest
import yaml

from energatron.adapters.storage.jsonl_catalog import JsonlCatalog
from energatron.core.errors import InvalidInputError, PipelineNotSyncedError, SchemaTooNewError
from energatron.core.models.catalog import SourceType
from energatron.core.models.params import Params
from energatron.core.models.region import RegionProfile
from energatron.core.models.vcs import BackupReport, BackupStatus
from energatron.core.models.workspace import WorkspacePaths
from energatron.services import pipeline_sync
from energatron.services.context import ServiceContext
from energatron.services.pipeline_sync import PipelineSyncResult
from energatron.services.stages.dvc_yaml import emit_dvc_yaml
from energatron.services.stages.registry import STAGES
from tests.unit.adapters._workspace import make_workspace, write
from tests.unit.services.test_context import TEST_REGION, build

GOLDEN = Path(__file__).resolve().parents[2] / "fixtures" / "golden" / "dvc.yaml"
PARAMS_V1 = "kind: params\nschema_version: 1\n"
DEFAULTS = Params.defaults(TEST_REGION).model_dump(mode="json")
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


def dump(doc: dict[str, Any]) -> str:
    return yaml.safe_dump(doc, sort_keys=False)


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
    text = (root / "params.yaml").read_text(encoding="utf-8")
    assert text.startswith("kind: params\nschema_version: 3\nmeter:\n")
    assert yaml.safe_load(text) == DEFAULTS
    assert DEFAULTS["meter"]["local_tz"] == TEST_REGION.local_tz  # the test region's, not a shipped one
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
    params = write(ctx.paths.root, "params.yaml", "# my comment\n" + dump(DEFAULTS))
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
    write(ctx.paths.root, "params.yaml", "kind: params\nschema_version: 4\n")
    before = snapshot(ctx.paths.root)
    with pytest.raises(SchemaTooNewError):
        pipeline_sync.sync(ctx)
    with pytest.raises(SchemaTooNewError):
        pipeline_sync.check(ctx)
    assert snapshot(ctx.paths.root) == before
    assert vcs.tracked == []


def edited(**sections: Any) -> dict[str, Any]:
    """The defaults with whole `meter` / `validation` leaves replaced: `edited(**{"meter.local_tz": "Other/Zone"})`."""
    doc = copy.deepcopy(DEFAULTS)
    for dotted, value in sections.items():
        *parents, leaf = dotted.split(".")
        target = doc
        for part in parents:
            target = target[part]
        target[leaf] = value
    return doc


def prepare_synced(ctx: ServiceContext, params: dict[str, Any] | str) -> None:
    """A workspace that is in sync but for `params.yaml`, which holds `params`."""
    write(ctx.paths.root, "dvc.yaml", emit_dvc_yaml(STAGES))
    for source in SourceType:
        write(ctx.paths.root, f"data/raw/{source.value}/_catalog.jsonl", "")
    write(ctx.paths.root, "params.yaml", params if isinstance(params, str) else dump(params))


def params_on_disk(ctx: ServiceContext) -> dict[str, Any]:
    return yaml.safe_load((ctx.paths.root / "params.yaml").read_text(encoding="utf-8"))


def test_an_older_params_file_is_drift_then_rewritten_with_the_zone_and_windows_filled(ctx: ServiceContext) -> None:
    prepare_synced(ctx, "# a comment that is lost\n" + PARAMS_V1)
    assert drift_of(ctx) == ["params.yaml"]

    result = synced(ctx)
    assert (result.params_written, result.params_rewritten) == (False, True)
    text = (ctx.paths.root / "params.yaml").read_text(encoding="utf-8")
    assert text.startswith("kind: params\nschema_version: 3\n")
    assert (
        yaml.safe_load(text) == DEFAULTS
    )  # the v1 file held nothing to keep: the zone and windows come from the profile
    pipeline_sync.check(ctx)
    assert synced(ctx).params_rewritten is False


def test_a_missing_params_file_is_written_from_the_defaults_of_the_contexts_profile(ctx: ServiceContext) -> None:
    result = synced(ctx)
    assert (result.params_written, result.params_rewritten) == (True, False)
    assert params_on_disk(ctx) == DEFAULTS
    other = RegionProfile(name="sa", local_tz="Other/Place", tou_windows={"Gamma": ("00:00-00:00",)}, parsers=())
    elsewhere = dataclasses.replace(ctx, region=other)
    (ctx.paths.root / "params.yaml").unlink()
    synced(elsewhere)
    assert params_on_disk(ctx)["meter"]["local_tz"] == "Other/Place"
    assert params_on_disk(ctx)["validation"]["timebase"]["windows"] == {"Gamma": ["00:00-00:00"]}
    assert params_on_disk(ctx)["analysis"]["tou"]["window_sets"] == [
        {"effective_from": None, "windows": {"Gamma": ["00:00-00:00"]}}
    ]


def test_a_hand_edited_zone_is_drift_and_is_rewritten_alone(ctx: ServiceContext) -> None:
    owners = edited(
        **{
            "meter.local_tz": "Owner/Choice",
            "meter.timebase": "local_wall",
            "validation.meter.total_tolerance_kwh": 0.5,
            "validation.timebase.windows": {"Gamma": ["00:00-00:00"]},
            "validation.timebase.min_fit_periods": 7,
        }
    )
    prepare_synced(ctx, owners)
    assert drift_of(ctx) == ["params.yaml"]
    result = synced(ctx)
    assert (result.params_written, result.params_rewritten) == (False, True)
    expected = copy.deepcopy(owners)
    expected["meter"]["local_tz"] = TEST_REGION.local_tz
    assert params_on_disk(ctx) == expected  # only the zone changed; the edited windows and every other value survive
    pipeline_sync.check(ctx)


def test_an_edited_windows_value_and_every_other_edited_value_survive_with_no_drift(ctx: ServiceContext) -> None:
    owners = edited(
        **{
            "meter.timebase": "local_standard",
            "validation.timebase.windows": {"Gamma": ["00:00-12:00"], "Delta": ["12:00-00:00"]},
            "validation.timebase.register_tolerance_pct": 0.2,
        }
    )
    prepare_synced(ctx, "# my comment\n" + dump(owners))
    pipeline_sync.check(ctx)
    result = synced(ctx)
    assert (result.params_written, result.params_rewritten) == (False, False)
    assert (ctx.paths.root / "params.yaml").read_text(encoding="utf-8").startswith("# my comment\n")


@pytest.mark.parametrize(
    ("leaf", "expected"),
    [
        ("meter.local_tz", TEST_REGION.local_tz),
        ("validation.timebase.windows", {"Alpha": ["08:00-20:00"], "Beta": ["20:00-08:00"]}),
    ],
)
def test_a_null_zone_or_windows_value_is_drift_and_is_filled(ctx: ServiceContext, leaf: str, expected: Any) -> None:
    prepare_synced(ctx, edited(**{leaf: None, "meter.timebase": "local_wall"}))
    assert drift_of(ctx) == ["params.yaml"]
    assert synced(ctx).params_rewritten is True
    on_disk = params_on_disk(ctx)
    *parents, last = leaf.split(".")
    target = on_disk
    for part in parents:
        target = target[part]
    assert target[last] == expected
    assert on_disk["meter"]["timebase"] == "local_wall"
    pipeline_sync.check(ctx)


# The first analysis window set (P3 spec §4.3)

FIRST_SET = {"effective_from": None, "windows": {"Alpha": ["08:00-20:00"], "Beta": ["20:00-08:00"]}}
OWN_SET = {"Gamma": ["00:00-12:00"], "Delta": ["12:00-00:00"]}


def v2_defaults() -> dict[str, Any]:
    """The defaults as a version 2 file: no `analysis`."""
    doc = copy.deepcopy(DEFAULTS)
    del doc["analysis"]
    doc["schema_version"] = 2
    return doc


def test_a_v2_params_file_is_rewritten_to_v3_with_the_first_set_filled_from_the_profile(ctx: ServiceContext) -> None:
    prepare_synced(ctx, v2_defaults())
    assert drift_of(ctx) == ["params.yaml"]
    result = synced(ctx)
    assert (result.params_written, result.params_rewritten) == (False, True)
    on_disk = params_on_disk(ctx)
    assert on_disk["schema_version"] == 3
    assert on_disk["analysis"]["tou"]["window_sets"] == [FIRST_SET]
    assert on_disk == DEFAULTS
    pipeline_sync.check(ctx)


def test_a_v2_files_own_values_survive_the_rewrite_and_its_validation_windows_are_not_copied(
    ctx: ServiceContext,
) -> None:
    doc = v2_defaults()
    doc["validation"]["timebase"]["windows"] = OWN_SET
    doc["meter"]["timebase"] = "local_wall"
    prepare_synced(ctx, doc)
    synced(ctx)
    on_disk = params_on_disk(ctx)
    assert on_disk["validation"]["timebase"]["windows"] == OWN_SET
    assert on_disk["meter"]["timebase"] == "local_wall"
    assert on_disk["analysis"]["tou"]["window_sets"] == [FIRST_SET]  # the region's, not the owner's validation set


def test_an_edited_first_set_and_every_other_edited_analysis_value_survive_with_no_drift(ctx: ServiceContext) -> None:
    doc = copy.deepcopy(DEFAULTS)
    doc["analysis"]["tou"]["window_sets"] = [
        {"effective_from": None, "windows": OWN_SET},
        {"effective_from": "2025-07-01", "windows": {"Gamma": ["00:00-06:00"], "Delta": ["06:00-00:00"]}},
    ]
    doc["analysis"]["reference_months"] = 6
    doc["analysis"]["weekend"] = ["sunday"]
    doc["analysis"]["base_load"]["percentile"] = 5
    prepare_synced(ctx, "# my comment\n" + dump(doc))
    pipeline_sync.check(ctx)
    result = synced(ctx)
    assert (result.params_written, result.params_rewritten) == (False, False)
    assert (ctx.paths.root / "params.yaml").read_text(encoding="utf-8").startswith("# my comment\n")


def test_a_null_first_set_is_drift_and_is_filled_while_the_other_sets_survive(ctx: ServiceContext) -> None:
    doc = copy.deepcopy(DEFAULTS)
    later = {"effective_from": "2025-07-01", "windows": {"Alpha": ["07:00-21:00"], "Beta": ["21:00-07:00"]}}
    doc["analysis"]["tou"]["window_sets"] = [{"effective_from": None, "windows": None}, later]
    doc["analysis"]["demand_minutes"] = 15
    prepare_synced(ctx, doc)
    assert drift_of(ctx) == ["params.yaml"]
    assert synced(ctx).params_rewritten is True
    on_disk = params_on_disk(ctx)
    assert on_disk["analysis"]["tou"]["window_sets"] == [FIRST_SET, later]
    assert on_disk["analysis"]["demand_minutes"] == 15
    pipeline_sync.check(ctx)


def test_a_dry_run_reports_the_v3_rewrite_and_writes_nothing(ctx: ServiceContext) -> None:
    prepare_synced(ctx, v2_defaults())
    before = snapshot(ctx.paths.root)
    result = pipeline_sync.sync(ctx, dry_run=True)
    assert (result.params_rewritten, result.dry_run) == (True, True)
    assert snapshot(ctx.paths.root) == before


def test_an_invalid_analysis_value_is_invalid_input_in_sync_and_check(ctx: ServiceContext) -> None:
    doc = copy.deepcopy(DEFAULTS)
    doc["analysis"]["demand_minutes"] = 25
    prepare_synced(ctx, doc)
    for call in (pipeline_sync.check, pipeline_sync.sync):
        with pytest.raises(InvalidInputError):
            call(ctx)


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


def test_a_dry_run_of_an_older_params_file_reports_the_rewrite(ctx: ServiceContext) -> None:
    write(ctx.paths.root, "params.yaml", PARAMS_V1)
    before = snapshot(ctx.paths.root)
    result = pipeline_sync.sync(ctx, dry_run=True)
    assert result.params_rewritten is True
    assert snapshot(ctx.paths.root) == before


def test_a_dry_run_of_a_drifted_zone_reports_the_rewrite(ctx: ServiceContext) -> None:
    write(ctx.paths.root, "params.yaml", dump(edited(**{"meter.local_tz": "Owner/Choice"})))
    before = snapshot(ctx.paths.root)
    result = pipeline_sync.sync(ctx, dry_run=True)
    assert (result.params_written, result.params_rewritten) == (False, True)
    assert snapshot(ctx.paths.root) == before


def test_written_files_are_world_readable_and_atomic(ctx: ServiceContext) -> None:
    synced(ctx)
    root = ctx.paths.root
    for name in ("dvc.yaml", "params.yaml"):
        assert os.stat(root / name).st_mode & 0o777 == 0o644
    leftovers: Iterator[Path] = root.glob(".*.tmp")
    assert list(leftovers) == []
