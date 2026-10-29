"""`adapters/workspace.py`: finding the root (D1 §8.3), reading the config files (D2 §6.1-6.3) and the lock (§8.8)."""

from pathlib import Path

import pytest

from energatron.adapters.workspace import (
    WORKSPACE_ENV,
    backup_bundle_dir,
    find_workspace,
    read_local_config,
    read_private_config,
    read_workspace_config,
    workspace_lock,
)
from energatron.core.errors import (
    InvalidInputError,
    LockedError,
    NotAWorkspaceError,
    SchemaTooNewError,
)
from energatron.core.models.workspace import LocalConfig, WorkspacePaths
from tests.unit.adapters._workspace import MINIMAL_PRIVATE_TOML, local_toml, make_workspace, write
from tests.unit.core._private import NMI, SYNTHETIC_VALUES

# Finding the workspace (D1 §8.3)


def test_the_option_comes_first(tmp_path: Path) -> None:
    chosen = make_workspace(tmp_path, name="chosen")
    other = make_workspace(tmp_path, name="other")
    found = find_workspace(str(chosen.root), cwd=other.root, env={WORKSPACE_ENV: str(other.root)})
    assert found == chosen


def test_the_environment_comes_second(tmp_path: Path) -> None:
    chosen = make_workspace(tmp_path, name="chosen")
    other = make_workspace(tmp_path, name="other")
    assert find_workspace(None, cwd=other.root, env={WORKSPACE_ENV: str(chosen.root)}) == chosen


def test_an_empty_environment_variable_is_unset(tmp_path: Path) -> None:
    paths = make_workspace(tmp_path)
    assert find_workspace(None, cwd=paths.root, env={WORKSPACE_ENV: ""}) == paths


def test_otherwise_the_search_walks_up_from_the_current_directory(tmp_path: Path) -> None:
    paths = make_workspace(tmp_path)
    nested = paths.root / "data" / "landing"
    nested.mkdir(parents=True)
    assert find_workspace(None, cwd=nested, env={}) == paths


def test_a_relative_option_is_relative_to_the_current_directory(tmp_path: Path) -> None:
    paths = make_workspace(tmp_path)
    assert find_workspace("ws", cwd=tmp_path, env={}) == paths


def test_the_found_root_is_absolute(tmp_path: Path) -> None:
    paths = make_workspace(tmp_path)
    found = find_workspace(None, cwd=paths.root, env={})
    assert isinstance(found, WorkspacePaths)
    assert found.root.is_absolute()


def test_no_workspace_anywhere_is_not_a_workspace(tmp_path: Path) -> None:
    with pytest.raises(NotAWorkspaceError) as caught:
        find_workspace(None, cwd=tmp_path, env={})
    assert caught.value.exit_code == 2
    assert str(tmp_path) not in caught.value.message  # no path in an error (D1 §12)


@pytest.mark.parametrize("source", ["option", "environment"])
def test_a_named_folder_must_itself_be_the_root(tmp_path: Path, source: str) -> None:
    """A named folder isn't searched upwards, and the current directory's workspace isn't used instead."""
    paths = make_workspace(tmp_path)
    inside = paths.root / "data"
    inside.mkdir()
    option = str(inside) if source == "option" else None
    env = {WORKSPACE_ENV: str(inside)} if source == "environment" else {}
    with pytest.raises(NotAWorkspaceError) as caught:
        find_workspace(option, cwd=paths.root, env=env)
    assert caught.value.details == {"source": source}


def test_a_workspace_toml_that_is_a_folder_does_not_mark_a_workspace(tmp_path: Path) -> None:
    (tmp_path / ".energatron" / "workspace.toml").mkdir(parents=True)
    with pytest.raises(NotAWorkspaceError):
        find_workspace(None, cwd=tmp_path, env={})


# workspace.toml (D2 §6.1)


def test_the_workspace_config_is_read(tmp_path: Path) -> None:
    paths = make_workspace(tmp_path)
    write(
        paths.root,
        ".energatron/workspace.toml",
        'kind = "workspace_config"\nschema_version = 1\nregion = "sa"\n\n[backup]\nkeep_latest = 4\n',
    )
    config = read_workspace_config(paths)
    assert config.region == "sa"
    assert config.backup.keep_latest == 4
    assert config.backup.keep_monthly is True


@pytest.mark.parametrize(
    "text",
    [
        'kind = "workspace_config"\nschema_version = 1\nregion = ',  # not TOML
        'kind = "local_config"\nschema_version = 1\n',  # another kind
        'kind = "workspace_config"\nschema_version = 1\nregion = "SA"\n',  # not a profile name
        b"\xff\xfe".decode("latin-1"),  # not UTF-8 once written as latin-1 bytes below
    ],
)
def test_a_bad_workspace_config_is_invalid_input(tmp_path: Path, text: str) -> None:
    paths = make_workspace(tmp_path)
    paths.workspace_config.write_bytes(text.encode("latin-1"))
    with pytest.raises(InvalidInputError) as caught:
        read_workspace_config(paths)
    assert caught.value.details.get("kind") == "workspace_config"


def test_a_newer_workspace_config_is_too_new(tmp_path: Path) -> None:
    paths = make_workspace(tmp_path)
    write(paths.root, ".energatron/workspace.toml", 'kind = "workspace_config"\nschema_version = 2\nregion = "sa"\n')
    with pytest.raises(SchemaTooNewError):
        read_workspace_config(paths)


# local.toml (D2 §6.2)


def test_a_missing_local_config_reads_as_the_default(tmp_path: Path) -> None:
    paths = make_workspace(tmp_path)
    local = read_local_config(paths)
    assert local == LocalConfig()
    assert backup_bundle_dir(local) is None


def test_tilde_is_expanded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    paths = make_workspace(tmp_path, local=local_toml("~/Backups/demq-elektric-git"))
    assert backup_bundle_dir(read_local_config(paths)) == home / "Backups" / "demq-elektric-git"


def test_an_absolute_bundle_folder_outside_the_workspace_is_kept(tmp_path: Path) -> None:
    target = tmp_path / "Backups" / "git"
    paths = make_workspace(tmp_path, local=local_toml(str(target)))
    assert backup_bundle_dir(read_local_config(paths)) == target


@pytest.mark.parametrize("value", ["Backups/git", "./git", "{root}", "{root}/backups", "{root}/data/../backups"])
def test_a_relative_or_in_workspace_bundle_folder_is_passed_on_unchecked(tmp_path: Path, value: str) -> None:
    """Reading never rejects it: only the real `backup` does (D2 §6.2, §8.1 step 1), so `--status` still works."""
    paths = make_workspace(tmp_path)
    write(paths.root, ".energatron/local.toml", local_toml(value.format(root=paths.root)))
    assert backup_bundle_dir(read_local_config(paths)) == Path(value.format(root=paths.root))


def test_a_bad_local_config_is_invalid_input(tmp_path: Path) -> None:
    paths = make_workspace(tmp_path, local='kind = "local_config"\nschema_version = 1\nextra = 1\n')
    with pytest.raises(InvalidInputError):
        read_local_config(paths)


# private/config.toml (D2 §6.3)


def test_a_missing_private_config_reads_as_none(tmp_path: Path) -> None:
    paths = make_workspace(tmp_path, private=None)
    assert read_private_config(paths) is None


def test_the_private_config_is_read(tmp_path: Path) -> None:
    paths = make_workspace(tmp_path)
    private = read_private_config(paths)
    assert private is not None
    assert private.premises.nmi[:10] == NMI
    assert private.premises.meter_serials == ("MSYNTHAB", "Q7X")
    minimal = make_workspace(tmp_path, private=MINIMAL_PRIVATE_TOML, name="minimal")
    assert read_private_config(minimal) is not None


@pytest.mark.parametrize(
    "text",
    [
        'kind = "private_config"\nschema_version = 1\nsalt = "QAAAVZZZZZ3\n',  # not TOML; a value in the line
        'kind = "private_config"\nschema_version = 1\nsalt = "0123456789abcdef0123456789abcdef"\n'
        '[premises]\nnmi = "QAAAVZZZZZ4"\n',  # a wrong checksum digit
        'kind = "private_config"\nschema_version = 1\nsalt = "0123456789abcdef0123456789abcdef"\n'
        '[premises]\nnmi = "QAAAVZZZZZ"\npostcode = "Q7X"\n',  # not a postcode
    ],
)
def test_a_bad_private_config_is_invalid_input_without_values(tmp_path: Path, text: str) -> None:
    paths = make_workspace(tmp_path, private=text)
    with pytest.raises(InvalidInputError) as caught:
        read_private_config(paths)
    shown = f"{caught.value.message} {caught.value.details} {caught.value!r}"
    assert not [value for value in (*SYNTHETIC_VALUES, "QAAAVZZZZZ4") if value in shown]


# The lock (D1 §8.8)


def test_the_lock_is_exclusive_and_released(tmp_path: Path) -> None:
    paths = make_workspace(tmp_path)
    with workspace_lock(paths):
        with pytest.raises(LockedError) as caught, workspace_lock(paths):
            pass
        assert caught.value.exit_code == 4
    with workspace_lock(paths):
        pass  # released after the first block
    assert paths.lock.exists()


def test_the_lock_is_released_when_the_block_raises(tmp_path: Path) -> None:
    paths = make_workspace(tmp_path)
    with pytest.raises(RuntimeError), workspace_lock(paths):
        raise RuntimeError("boom")
    with workspace_lock(paths):
        pass
