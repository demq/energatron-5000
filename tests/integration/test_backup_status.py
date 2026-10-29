"""`GitDvcVersionControl.backup_status()` against scratch repos (D2 §6.5, §8.5, §14).

The status reads the local marker and the refs only: the backup folders are made unreadable while it runs.
"""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from energatron.core.errors import InvalidInputError
from energatron.core.models.vcs import BackupStatus
from tests.integration._scratch import START, Scratch, git, make_scratch

pytestmark = pytest.mark.integration


@pytest.fixture
def scratch(tmp_path: Path) -> Scratch:
    scratch = make_scratch(tmp_path)
    scratch.write("private/config.toml")
    scratch.vcs().track(["private"])
    return scratch


@contextmanager
def backups_unreadable(scratch: Scratch) -> Iterator[None]:
    """The status must never touch the backup folders: any access in the block fails."""
    scratch.backups.chmod(0o000)
    try:
        yield
    finally:
        scratch.backups.chmod(0o700)


def status(scratch: Scratch) -> BackupStatus:
    with backups_unreadable(scratch):
        return scratch.vcs().backup_status()


def test_no_marker_means_never_backed_up(scratch: Scratch) -> None:
    head = scratch.commit_all()
    assert status(scratch) == BackupStatus(
        head=head, last_backup_head=None, last_backup_at=None, refs_backed_up=False, commits_not_backed_up=None
    )


def test_before_the_first_commit_there_is_no_head(scratch: Scratch) -> None:
    assert status(scratch).head is None
    assert status(scratch).refs_backed_up is False


def test_after_a_backup_the_refs_are_backed_up(scratch: Scratch) -> None:
    head = scratch.commit_all()
    scratch.vcs().backup()
    assert status(scratch) == BackupStatus(
        head=head, last_backup_head=head, last_backup_at=START, refs_backed_up=True, commits_not_backed_up=0
    )


def test_a_new_commit_is_pending(scratch: Scratch) -> None:
    first = scratch.commit_all()
    scratch.vcs().backup()
    scratch.write("notes.txt")
    second = scratch.commit_all("second")
    result = status(scratch)
    assert (result.head, result.last_backup_head) == (second, first)
    assert result.refs_backed_up is False
    assert result.commits_not_backed_up == 1


def test_a_new_tag_alone_is_pending_with_no_new_commits(scratch: Scratch) -> None:
    scratch.commit_all()
    scratch.vcs().backup()
    git(scratch.root, "tag", "v1")
    result = status(scratch)
    assert result.refs_backed_up is False
    assert result.commits_not_backed_up == 0


def test_a_marker_whose_head_no_longer_exists_has_no_count(scratch: Scratch) -> None:
    scratch.commit_all()
    scratch.vcs().backup()
    marker = scratch.paths.backup_state
    doc = json.loads(marker.read_text(encoding="utf-8"))
    doc["head"] = "0" * 40
    marker.write_text(json.dumps(doc), encoding="utf-8")
    result = status(scratch)
    assert result.last_backup_head == "0" * 40
    assert result.commits_not_backed_up is None
    assert result.refs_backed_up is True  # the refs hash still matches: the head is only a hint


@pytest.mark.parametrize(
    "text",
    ["{not json", "[]", '{"kind": "backup_state", "schema_version": 1}', '{"kind": "other", "schema_version": 1}'],
    ids=["syntax", "not-an-object", "missing-fields", "other-kind"],
)
def test_a_corrupt_marker_is_invalid_input(scratch: Scratch, text: str) -> None:
    scratch.commit_all()
    scratch.paths.backup_state.write_text(text, encoding="utf-8")
    with pytest.raises(InvalidInputError) as caught:
        status(scratch)
    assert str(scratch.root) not in caught.value.message + json.dumps(caught.value.details)


@pytest.mark.parametrize("kind", ["folder", "unreadable"])
def test_a_marker_that_cant_be_read_is_invalid_input(scratch: Scratch, kind: str) -> None:
    """D1 §4: only `EnergatronError`s leave the adapter; a raw `OSError` used to escape (review finding 3)."""
    scratch.commit_all()
    marker = scratch.paths.backup_state
    if kind == "folder":
        marker.mkdir()
    else:
        marker.write_text("{}", encoding="utf-8")
        marker.chmod(0o000)
    try:
        with pytest.raises(InvalidInputError) as caught:
            status(scratch)
    finally:
        if kind == "unreadable":
            marker.chmod(0o600)
    assert caught.value.details["kind"] == "backup_state"
    assert str(scratch.root) not in caught.value.message + json.dumps(caught.value.details)
