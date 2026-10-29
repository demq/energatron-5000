"""`GitDvcVersionControl` against scratch repos, a scratch DVC remote and a scratch bundle folder (D2 §8, §14).

Nothing here touches iCloud or the real workspace: `Backups/` is a folder under the test's `tmp_path`.
"""

import json
import os
import subprocess
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from energatron.adapters.vcs import git_dvc
from energatron.adapters.vcs.git_dvc import GitDvcVersionControl
from energatron.core.errors import (
    BackupNotConfiguredError,
    CommitRejectedError,
    DenylistHitError,
    EnergatronError,
    InternalError,
    StorageError,
    SubprocessError,
    UsageError,
    WarningCode,
)
from energatron.core.ids import compact_utc
from energatron.core.models.vcs import BackupState
from energatron.core.models.workspace import BackupPolicy
from energatron.services import backup as backup_service
from tests.integration._scratch import START, Scratch, dvc, git, make_scratch
from tests.unit.services.test_context import build

pytestmark = pytest.mark.integration

SECRET = "SECRET-HOOK-OUTPUT"


@pytest.fixture
def scratch(tmp_path: Path) -> Scratch:
    return make_scratch(tmp_path)


@contextmanager
def mode(path: Path, bits: int) -> Iterator[None]:
    """Give `path` the permission `bits` for the block, then make it usable again so tmp_path can be cleaned."""
    path.chmod(bits)
    try:
        yield
    finally:
        path.chmod(0o700)


def assert_no_paths(error: EnergatronError, *paths: Path) -> None:
    text = error.message + json.dumps(error.details)
    for path in paths:
        assert str(path) not in text
    assert "/private/" not in text and "/var/folders" not in text and "/Users/" not in text


def hint(error: EnergatronError) -> str:
    value = error.details["hint"]
    assert isinstance(value, str) and value
    return value


def tracked_scratch(scratch: Scratch) -> str:
    """One DVC-tracked folder with one file, committed; returns the head."""
    scratch.write("private/config.toml", "synthetic = true\n")
    scratch.vcs().track(["private"])
    return scratch.commit_all("track private")


# head, dirty_paths


def test_head_is_none_before_the_first_commit(scratch: Scratch) -> None:
    vcs = scratch.vcs()
    assert vcs.head() is None
    sha = scratch.commit_all()
    assert vcs.head() == sha
    assert len(sha) == 40


def test_dirty_paths_lists_modified_and_untracked_files_under_the_paths(scratch: Scratch) -> None:
    scratch.commit_all()
    vcs = scratch.vcs()
    assert vcs.dirty_paths() == []
    scratch.write(".energatron/workspace.toml", "changed = true\n")
    scratch.write("notes/a.txt")
    scratch.write("other/b.txt")
    assert vcs.dirty_paths() == [".energatron/workspace.toml", "notes/a.txt", "other/b.txt"]
    assert vcs.dirty_paths(["notes"]) == ["notes/a.txt"]
    assert vcs.dirty_paths(["notes", ".energatron"]) == [".energatron/workspace.toml", "notes/a.txt"]
    scratch.write(".energatron/tmp/scratch.bin")  # git-ignored
    assert ".energatron/tmp/scratch.bin" not in vcs.dirty_paths()


@pytest.mark.parametrize("bad", ["/abs/path", "../outside", "a/../../b", ""])
def test_paths_must_be_workspace_relative(scratch: Scratch, bad: str) -> None:
    vcs = scratch.vcs()
    with pytest.raises(InternalError):
        vcs.dirty_paths([bad])
    with pytest.raises(InternalError):
        vcs.commit([bad], "m")
    with pytest.raises(InternalError):
        vcs.track([bad])


# commit


def test_commit_stages_exactly_the_given_paths(scratch: Scratch) -> None:
    first = scratch.commit_all()
    scratch.write("a.txt")
    scratch.write("b.txt")
    scratch.write("c.txt")
    git(scratch.root, "add", "c.txt")  # staged by someone else: must not ride along
    vcs = scratch.vcs()
    sha = vcs.commit(["a.txt"], "add a\n\nbody")
    assert sha == vcs.head() != first
    assert git(scratch.root, "show", "--name-only", "--format=", sha).split() == ["a.txt"]
    assert git(scratch.root, "log", "-1", "--format=%B").strip() == "add a\n\nbody"
    assert sorted(vcs.dirty_paths()) == ["b.txt", "c.txt"]


def test_paths_are_literal_not_glob_patterns(scratch: Scratch) -> None:
    """D1 §4.4: exactly the given paths, so a name with glob characters matches only itself (review finding 1)."""
    scratch.commit_all()
    scratch.write("x[1].txt")
    scratch.write("x1.txt")
    scratch.write("y*.txt")
    scratch.write("yz.txt")
    vcs = scratch.vcs()
    assert vcs.dirty_paths(["x[1].txt"]) == ["x[1].txt"]
    assert vcs.dirty_paths(["y*.txt"]) == ["y*.txt"]
    sha = vcs.commit(["x[1].txt", "y*.txt"], "literal names")
    assert sorted(git(scratch.root, "show", "--name-only", "--format=", sha).split()) == ["x[1].txt", "y*.txt"]
    assert vcs.dirty_paths() == ["x1.txt", "yz.txt"]
    assert git(scratch.root, "diff", "--cached", "--name-only") == ""


def test_commit_stages_a_deletion_under_the_path(scratch: Scratch) -> None:
    scratch.write("notes/a.txt")
    scratch.commit_all()
    (scratch.root / "notes" / "a.txt").unlink()
    vcs = scratch.vcs()
    sha = vcs.commit(["notes"], "drop a")
    assert git(scratch.root, "show", "--name-status", "--format=", sha).split() == ["D", "notes/a.txt"]


def test_nothing_to_commit_returns_the_current_head(scratch: Scratch) -> None:
    head = scratch.commit_all()
    vcs = scratch.vcs()
    assert vcs.commit([".energatron"], "no change") == head
    assert vcs.commit([], "no paths") == head


def test_the_first_commit_works_and_nothing_to_commit_without_a_head_is_a_usage_error(scratch: Scratch) -> None:
    vcs = scratch.vcs()
    with pytest.raises(UsageError):
        vcs.commit([], "nothing")
    sha = vcs.commit([".gitignore", ".energatron/workspace.toml"], "first")
    assert vcs.head() == sha


def install_hook(
    scratch: Scratch, hook: str, failures: Sequence[tuple[str, int | None]], *, exit_code: int = 1
) -> None:
    """A git hook that prints pre-commit's failure lines (D2 §1: `- hook id:` then `- exit code:`)."""
    lines = ["#!/bin/sh", f"echo '{SECRET}' >&2"]
    for hook_id, code in failures:
        lines.append(f"echo '{hook_id}.......Failed'")
        lines.append(f"echo '- hook id: {hook_id}'")
        if code is not None:
            lines.append(f"echo '- exit code: {code}'")
        lines.append(f"echo ''; echo '{SECRET}'")
    lines.append(f"exit {exit_code}")
    path = scratch.root / ".git" / "hooks" / hook
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    path.chmod(0o755)


@pytest.mark.parametrize(
    ("hook", "failures", "expected", "hooks"),
    [
        ("pre-commit", [("ruff", 1)], CommitRejectedError, ["ruff"]),
        ("pre-commit", [("nbstripout", None)], CommitRejectedError, ["nbstripout"]),
        ("pre-commit", [("privacy-guard", 1)], DenylistHitError, ["privacy-guard"]),
        ("pre-commit", [("privacy-guard", 2)], CommitRejectedError, ["privacy-guard"]),
        ("commit-msg", [("privacy-guard-msg", 1)], DenylistHitError, ["privacy-guard-msg"]),
        ("commit-msg", [("privacy-guard-msg", 2)], CommitRejectedError, ["privacy-guard-msg"]),
        ("pre-commit", [("ruff", 1), ("privacy-guard", 1)], DenylistHitError, ["ruff", "privacy-guard"]),
    ],
    ids=["ruff", "modified", "guard-hit", "guard-setup", "msg-hit", "msg-setup", "ruff-and-guard"],
)
def test_a_rejecting_hook_maps_to_its_error_without_its_output(
    scratch: Scratch,
    hook: str,
    failures: list[tuple[str, int | None]],
    expected: type[EnergatronError],
    hooks: list[str],
) -> None:
    head = scratch.commit_all()
    install_hook(scratch, hook, failures)
    scratch.write("a.txt")
    vcs = scratch.vcs()
    with pytest.raises(EnergatronError) as caught:
        vcs.commit(["a.txt"], "add a")
    assert type(caught.value) is expected
    assert caught.value.details["hooks"] == hooks
    assert SECRET not in caught.value.message + json.dumps(caught.value.details)
    assert vcs.head() == head


def test_a_failing_hook_without_hook_lines_is_a_subprocess_error(scratch: Scratch) -> None:
    head = scratch.commit_all()
    install_hook(scratch, "pre-commit", [], exit_code=3)
    scratch.write("a.txt")
    vcs = scratch.vcs()
    with pytest.raises(SubprocessError) as caught:
        vcs.commit(["a.txt"], "add a")
    assert caught.value.details["exit_code"] == 1  # git's own exit code
    assert SECRET not in caught.value.message + json.dumps(caught.value.details)
    assert vcs.head() == head


# track


def test_track_returns_the_pointer_files(scratch: Scratch) -> None:
    scratch.write("private/config.toml")
    scratch.write("data/raw/sapn_interval/x.csv")
    vcs = scratch.vcs()
    assert vcs.track(["private", "data/raw/sapn_interval"]) == ["private.dvc", "data/raw/sapn_interval.dvc"]
    assert (scratch.root / "private.dvc").is_file()
    assert (scratch.root / "data" / "raw" / "sapn_interval.dvc").is_file()
    assert vcs.track([]) == []


def test_track_of_a_missing_path_is_a_subprocess_error(scratch: Scratch) -> None:
    with pytest.raises(SubprocessError):
        scratch.vcs().track(["missing"])


# backup: the happy path


def test_backup_pushes_writes_a_verified_bundle_and_the_marker(scratch: Scratch) -> None:
    head = tracked_scratch(scratch)
    report = scratch.vcs().backup()
    name = f"{compact_utc(START)}-{head[:7]}.bundle"
    assert report.model_dump() == {
        "dvc_files_pushed": 2,  # the file and the folder's `.dir` object
        "bundle_name": name,
        "bundles_kept": 1,
        "bundles_removed": 0,
        "bundle_created": True,
        "dataless_files": 0,
    }
    assert scratch.bundles() == [name]
    git(scratch.root, "bundle", "verify", str(scratch.bundle_dir / name))
    assert scratch.remote_complete()
    state = BackupState.from_document(json.loads(scratch.paths.backup_state.read_text(encoding="utf-8")))
    assert (state.head, state.bundle_name, state.at) == (head, name, START)
    assert not any(scratch.paths.tmp_dir.iterdir())
    assert [p.name for p in scratch.bundle_dir.iterdir()] == [name]  # no partial left


def test_a_second_backup_with_unchanged_refs_writes_no_bundle(scratch: Scratch) -> None:
    tracked_scratch(scratch)
    vcs = scratch.vcs()
    first = vcs.backup()
    scratch.clock.advance(hours=1)
    second = vcs.backup()
    assert second.bundle_created is False
    assert second.bundle_name == first.bundle_name
    assert second.dvc_files_pushed == 0
    assert (second.bundles_kept, second.bundles_removed) == (1, 0)
    assert scratch.bundles() == [first.bundle_name]
    state = BackupState.from_document(json.loads(scratch.paths.backup_state.read_text(encoding="utf-8")))
    assert state.at == START + timedelta(hours=1)


def test_a_new_tag_alone_makes_a_new_bundle(scratch: Scratch) -> None:
    tracked_scratch(scratch)
    vcs = scratch.vcs()
    first = vcs.backup()
    git(scratch.root, "tag", "v1")
    scratch.clock.advance(minutes=5)
    second = vcs.backup()
    assert second.bundle_created is True
    assert second.bundle_name != first.bundle_name
    heads = git(scratch.root, "bundle", "list-heads", str(scratch.bundle_dir / second.bundle_name))
    assert "refs/tags/v1" in heads


def test_a_first_backup_creates_both_folders_under_backups(tmp_path: Path) -> None:
    scratch = make_scratch(tmp_path, create_dirs=False)
    tracked_scratch(scratch)
    report = scratch.vcs().backup()
    assert scratch.bundle_dir.is_dir() and scratch.remote_dir.is_dir()
    assert report.bundle_created and report.dvc_files_pushed == 2
    assert scratch.bundles() == [report.bundle_name]
    assert scratch.remote_complete()


# backup: retention


def test_retention_keeps_the_latest_and_monthly_bundles_and_never_touches_foreign_files(scratch: Scratch) -> None:
    head = tracked_scratch(scratch)
    old = ["20260701T000000Z-aaaaaaa", "20260715T000000Z-bbbbbbb", "20260803T000000Z-ccccccc"]
    old += ["20260820T000000Z-ddddddd", "20260910T000000Z-eeeeeee"]
    for stem in old:
        (scratch.bundle_dir / f"{stem}.bundle").write_bytes(b"not a bundle")  # the newest is unreadable: a new one
    foreign = ["notes.txt", "20261001T000000Z-abc.bundle", "20260601T000000Z-ABCDEF1.bundle"]
    for name in foreign:
        (scratch.bundle_dir / name).write_bytes(b"foreign")
    stale = scratch.bundle_dir / ".20260101T000000Z-1234567.bundle.partial"
    fresh = scratch.bundle_dir / ".20261023T090000Z-7654321.bundle.partial"
    for path, age in ((stale, timedelta(days=2)), (fresh, timedelta(hours=1))):
        path.write_bytes(b"partial")
        stamp = (START - age).timestamp()
        os.utime(path, (stamp, stamp))

    report = scratch.vcs(keep_latest=3).backup()

    new = f"{compact_utc(START)}-{head[:7]}.bundle"
    kept = ["20260715T000000Z-bbbbbbb.bundle", "20260820T000000Z-ddddddd.bundle", "20260910T000000Z-eeeeeee.bundle"]
    assert report.bundle_created and report.bundle_name == new
    assert (report.bundles_kept, report.bundles_removed) == (4, 2)
    remaining = sorted(path.name for path in scratch.bundle_dir.iterdir())
    assert remaining == sorted([*kept, new, *foreign, fresh.name])


def test_retention_without_monthly_keeps_only_the_latest(scratch: Scratch) -> None:
    tracked_scratch(scratch)
    for stem in ("20260701T000000Z-aaaaaaa", "20260801T000000Z-bbbbbbb", "20260901T000000Z-ccccccc"):
        (scratch.bundle_dir / f"{stem}.bundle").write_bytes(b"not a bundle")
    vcs = GitDvcVersionControl(
        scratch.paths,
        clock=scratch.clock,
        bundle_dir=scratch.bundle_dir,
        policy=BackupPolicy(keep_latest=3, keep_monthly=False),
    )
    report = vcs.backup()
    assert (report.bundles_kept, report.bundles_removed) == (3, 1)
    assert "20260701T000000Z-aaaaaaa.bundle" not in scratch.bundles()


# backup: failures


def test_backup_without_configuration_is_refused(tmp_path: Path) -> None:
    scratch = make_scratch(tmp_path)
    tracked_scratch(scratch)
    with pytest.raises(BackupNotConfiguredError):
        scratch.vcs(configured=False).backup()
    unremoted = make_scratch(tmp_path / "second", remote=False)
    unremoted.commit_all()
    with pytest.raises(BackupNotConfiguredError):
        unremoted.vcs().backup()
    assert not scratch.paths.backup_state.exists() and not unremoted.paths.backup_state.exists()


@pytest.mark.parametrize(
    ("value", "says"),
    [
        ("Backups/git", "absolute"),
        ("./git", "absolute"),
        ("{root}", "outside the workspace"),
        ("{root}/backups", "outside the workspace"),
        ("{root}/data/../backups", "outside the workspace"),
        ("{tmp}/elsewhere/../ws/backups", "outside the workspace"),
    ],
)
def test_a_relative_or_in_workspace_bundle_folder_is_refused_before_anything_runs(
    scratch: Scratch, monkeypatch: pytest.MonkeyPatch, value: str, says: str
) -> None:
    """D2 §6.2, §8.1 step 1: checked lexically in `backup()` itself, before the DVC remote or any subprocess."""
    tracked_scratch(scratch)
    bundle_dir = Path(value.format(root=scratch.root, tmp=scratch.root.parent))
    vcs = GitDvcVersionControl(scratch.paths, clock=scratch.clock, bundle_dir=bundle_dir, policy=BackupPolicy())

    def no_subprocess(*args: object, **kwargs: object) -> object:
        raise AssertionError("no subprocess may run before the bundle folder is checked")

    monkeypatch.setattr(vcs, "_run", no_subprocess)
    with pytest.raises(BackupNotConfiguredError) as caught:
        vcs.backup()
    assert caught.value.exit_code == 4
    assert says in caught.value.message
    assert_no_paths(caught.value, scratch.root, bundle_dir)
    assert not scratch.paths.backup_state.exists()
    assert not (scratch.root / "backups").exists()


def test_backup_before_the_first_commit_is_a_usage_error(scratch: Scratch) -> None:
    with pytest.raises(UsageError):
        scratch.vcs().backup()


def test_a_missing_backups_folder_is_a_storage_error(tmp_path: Path) -> None:
    scratch = make_scratch(tmp_path, create_dirs=False)
    tracked_scratch(scratch)
    scratch.backups.rename(tmp_path / "gone")
    with pytest.raises(StorageError) as caught:
        scratch.vcs().backup()
    assert "icloud_backup.py backup" in hint(caught.value)
    assert_no_paths(caught.value, scratch.bundle_dir, scratch.remote_dir)
    assert not scratch.backups.exists()
    assert not scratch.paths.backup_state.exists()


def record_calls(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    calls: list[list[str]] = []
    real = subprocess.run

    def recording(args: list[str], *rest: Any, **kwargs: Any) -> Any:
        calls.append([str(arg) for arg in args])
        return real(args, *rest, **kwargs)

    monkeypatch.setattr(git_dvc.subprocess, "run", recording)
    return calls


def test_an_unreadable_remote_folder_fails_before_dvc_push(scratch: Scratch, monkeypatch: pytest.MonkeyPatch) -> None:
    tracked_scratch(scratch)
    calls = record_calls(monkeypatch)
    with mode(scratch.remote_dir, 0o000), pytest.raises(StorageError) as caught:
        scratch.vcs().backup()
    assert not any("push" in call for call in calls)
    assert caught.value.details["location"] == "dvc_remote"
    assert_no_paths(caught.value, scratch.remote_dir)
    assert scratch.bundles() == []
    assert not scratch.paths.backup_state.exists()


def test_an_unwritable_bundle_folder_is_a_storage_error(scratch: Scratch) -> None:
    tracked_scratch(scratch)
    with mode(scratch.bundle_dir, 0o500), pytest.raises(StorageError) as caught:
        scratch.vcs().backup()
    assert_no_paths(caught.value, scratch.bundle_dir)
    assert scratch.bundles() == []
    assert not scratch.paths.backup_state.exists()
    assert not any(scratch.paths.tmp_dir.iterdir())


def test_a_corrupted_copy_is_a_storage_error_and_nothing_is_renamed_into_place(
    scratch: Scratch, monkeypatch: pytest.MonkeyPatch
) -> None:
    tracked_scratch(scratch)

    def faulty_copy(self: GitDvcVersionControl, source: Path, target: Path) -> None:
        target.write_bytes(source.read_bytes()[:-1] + b"x")

    monkeypatch.setattr(GitDvcVersionControl, "_copy", faulty_copy)
    with pytest.raises(StorageError) as caught:
        scratch.vcs().backup()
    assert_integrity_hint(caught.value)
    assert_no_paths(caught.value, scratch.bundle_dir)
    assert scratch.bundles() == []
    assert not any(path.name.endswith(".bundle") for path in scratch.bundle_dir.iterdir())
    assert not scratch.paths.backup_state.exists()
    assert not any(scratch.paths.tmp_dir.iterdir())


def assert_integrity_hint(error: StorageError) -> None:
    """A bad bundle isn't a missing access: its hint says so and doesn't send the owner to iCloud (finding 4)."""
    assert error.details["location"] == "bundle_dir"
    assert "integrity" in hint(error)
    assert "icloud_backup.py" not in hint(error)


@pytest.mark.parametrize("failing", ["create", "verify"])
def test_a_bundle_git_rejects_is_an_integrity_error(
    scratch: Scratch, monkeypatch: pytest.MonkeyPatch, failing: str
) -> None:
    tracked_scratch(scratch)
    real = GitDvcVersionControl._run

    def failing_run(
        self: GitDvcVersionControl, tool: str, *args: str, stdin: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        if args[:2] == ("bundle", failing):
            return subprocess.CompletedProcess([tool, *args], 1, "", "")
        return real(self, tool, *args, stdin=stdin)

    monkeypatch.setattr(GitDvcVersionControl, "_run", failing_run)
    with pytest.raises(StorageError) as caught:
        scratch.vcs().backup()
    assert_integrity_hint(caught.value)
    assert scratch.bundles() == []
    assert not scratch.paths.backup_state.exists()


def test_a_bundle_whose_heads_differ_is_an_integrity_error(scratch: Scratch, monkeypatch: pytest.MonkeyPatch) -> None:
    tracked_scratch(scratch)
    monkeypatch.setattr(GitDvcVersionControl, "_bundle_heads", lambda self, bundle: [])
    with pytest.raises(StorageError) as caught:
        scratch.vcs().backup()
    assert_integrity_hint(caught.value)
    assert scratch.bundles() == []


# backup: local files (finding 3: D1 §4 allows only `EnergatronError`s)


def assert_workspace_storage_error(error: StorageError, scratch: Scratch) -> None:
    assert error.details["location"] == "workspace"
    assert "icloud_backup.py" not in hint(error)
    assert_no_paths(error, scratch.root, scratch.bundle_dir)


def test_a_scratch_folder_that_cant_be_made_is_a_storage_error(scratch: Scratch) -> None:
    tracked_scratch(scratch)
    scratch.write(".energatron/tmp")  # a file where the scratch folder belongs
    with pytest.raises(StorageError) as caught:
        scratch.vcs().backup()
    assert_workspace_storage_error(caught.value, scratch)
    assert scratch.bundles() == []
    assert not scratch.paths.backup_state.exists()


def test_a_marker_that_cant_be_written_is_a_storage_error(scratch: Scratch) -> None:
    tracked_scratch(scratch)
    scratch.paths.backup_state.mkdir()  # a folder where the marker belongs
    with pytest.raises(StorageError) as caught:
        scratch.vcs().backup()
    assert_workspace_storage_error(caught.value, scratch)
    assert scratch.paths.backup_state.is_dir()


def test_a_local_temp_bundle_that_cant_be_removed_doesnt_fail_the_backup(
    scratch: Scratch, monkeypatch: pytest.MonkeyPatch
) -> None:
    tracked_scratch(scratch)
    real_unlink = Path.unlink

    def stubborn_unlink(self: Path, missing_ok: bool = False) -> None:
        if self.parent == scratch.paths.tmp_dir:
            raise PermissionError("held")
        real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", stubborn_unlink)
    report = scratch.vcs().backup()
    assert scratch.bundles() == [report.bundle_name]
    assert scratch.paths.backup_state.is_file()


def test_dataless_files_are_counted_and_the_service_warns(scratch: Scratch, monkeypatch: pytest.MonkeyPatch) -> None:
    tracked_scratch(scratch)
    monkeypatch.setattr(git_dvc, "_is_dataless", lambda st: True)
    result = backup_service.backup(build(vcs=scratch.vcs()))
    assert result.report is not None
    on_disk = sum(len(files) for folder in (scratch.bundle_dir, scratch.remote_dir) for _, _, files in os.walk(folder))
    assert result.report.dataless_files == on_disk > 0
    assert [warning.code for warning in result.warnings] == [WarningCode.W_BACKUP_DATALESS]
    assert result.warnings[0].details == {"count": on_disk}


def test_the_remote_url_is_read_from_dvc(scratch: Scratch) -> None:
    tracked_scratch(scratch)
    elsewhere = scratch.backups / "dvc-elsewhere"
    dvc(scratch.root, "remote", "modify", "--local", "backup", "url", str(elsewhere))
    scratch.vcs().backup()
    assert elsewhere.is_dir() and any(elsewhere.rglob("*"))
    assert not any(scratch.remote_dir.iterdir())
