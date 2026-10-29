"""`adapters/wiring.py`: `build_context()`, `SystemClock` and the P1 placeholders (D2 §5.9; D1 §4.7, §4.8)."""

from datetime import UTC, timedelta
from pathlib import Path
from typing import Any

import pytest

from energatron.adapters import wiring
from energatron.adapters.vcs.git_dvc import GitDvcVersionControl
from energatron.core.errors import BackupNotConfiguredError, InternalError
from energatron.core.models.workspace import BackupPolicy
from tests.unit.adapters._workspace import local_toml, make_workspace, write


class RecordingVcs:
    """Stands in for `GitDvcVersionControl`, keeping what `build_context()` passed to it."""

    def __init__(self, paths: Any, **kwargs: Any) -> None:
        self.paths = paths
        self.kwargs = kwargs


def test_the_context_holds_the_workspace_and_its_configs(tmp_path: Path) -> None:
    paths = make_workspace(tmp_path)
    ctx = wiring.build_context(paths)
    assert ctx.paths == paths
    assert ctx.config.region == "sa"
    assert ctx.private is not None
    assert tuple(ctx.parsers) == ()
    assert dict(ctx.offers) == {}
    assert isinstance(ctx.vcs, GitDvcVersionControl)
    assert isinstance(ctx.clock, wiring.SystemClock)


def test_no_private_config_leaves_private_unset(tmp_path: Path) -> None:
    assert wiring.build_context(make_workspace(tmp_path, private=None)).private is None


def test_the_vcs_adapter_gets_the_bundle_folder_policy_and_clock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(wiring, "GitDvcVersionControl", RecordingVcs)
    target = tmp_path / "Backups" / "git"
    paths = make_workspace(tmp_path, local=local_toml(str(target)))
    write(
        paths.root,
        ".energatron/workspace.toml",
        'kind = "workspace_config"\nschema_version = 1\nregion = "sa"\n[backup]\nkeep_latest = 5\n',
    )
    clock = wiring.SystemClock()
    vcs = wiring.build_context(paths, clock=clock).vcs
    assert isinstance(vcs, RecordingVcs)
    assert vcs.paths == paths
    assert vcs.kwargs == {"clock": clock, "bundle_dir": target, "policy": BackupPolicy(keep_latest=5)}


def test_no_local_config_means_no_bundle_folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wiring, "GitDvcVersionControl", RecordingVcs)
    vcs = wiring.build_context(make_workspace(tmp_path)).vcs
    assert isinstance(vcs, RecordingVcs)
    assert vcs.kwargs["bundle_dir"] is None


@pytest.mark.parametrize("value", ["Backups/git", "{root}/backups"])
def test_a_bad_bundle_folder_still_builds_the_context(tmp_path: Path, value: str) -> None:
    """Only the real `backup` checks it (D2 §6.2, §8.1 step 1): `denylist` and `backup --status` keep working."""
    paths = make_workspace(tmp_path)
    write(paths.root, ".energatron/local.toml", local_toml(value.format(root=paths.root)))
    ctx = wiring.build_context(paths)
    assert isinstance(ctx.vcs, GitDvcVersionControl)
    with pytest.raises(BackupNotConfiguredError):
        ctx.vcs.backup()


def test_the_p1_ports_are_placeholders_that_raise_internal(tmp_path: Path) -> None:
    ctx = wiring.build_context(make_workspace(tmp_path))
    calls = {
        "Catalog": [
            lambda: ctx.catalog.get("x"),
            lambda: ctx.catalog.find_sha256("0" * 64),
            lambda: ctx.catalog.records(),
            lambda: ctx.catalog.append(None),  # pyright: ignore[reportArgumentType]
        ],
        "BlobStore": [
            lambda: ctx.blobs.put("data/raw/x", b""),
            lambda: ctx.blobs.get("data/raw/x"),
            lambda: ctx.blobs.exists("data/raw/x"),
            lambda: ctx.blobs.stat("data/raw/x"),
        ],
        "PipelineRunner": [lambda: ctx.runner.status(), lambda: ctx.runner.repro()],
    }
    for port, port_calls in calls.items():
        for call in port_calls:
            with pytest.raises(InternalError) as caught:
                call()
            assert caught.value.message == f"{port} arrives in P1"
    assert {type(ctx.catalog).__name__, type(ctx.blobs).__name__, type(ctx.runner).__name__} == {
        "_NotYetCatalog",
        "_NotYetBlobStore",
        "_NotYetPipelineRunner",
    }


def test_the_system_clock_is_aware_utc() -> None:
    now = wiring.SystemClock().now()
    assert now.tzinfo is UTC
    assert now.utcoffset() == timedelta(0)
