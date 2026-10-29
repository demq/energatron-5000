"""The workspace on disk: finding its root (D1 §8.3), reading its config files (D2 §6.1-6.3), and its lock (§8.8).

Imported by `wiring` and by driving adapters (D1 §3). Errors never hold a path (absolute paths hold the macOS
username, D1 §12) or a value from the private config.
"""

import os
import tomllib
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path

from filelock import FileLock, Timeout

from energatron.core.canonical import JSONValue
from energatron.core.errors import (
    InvalidInputError,
    LockedError,
    NotAWorkspaceError,
)
from energatron.core.models.private import PrivateConfig
from energatron.core.models.workspace import LocalConfig, WorkspaceConfig, WorkspacePaths

WORKSPACE_ENV = "ENERGATRON_WORKSPACE"
"""Names the workspace root when `--workspace` isn't given (D1 §8.2, §8.3)."""

_MARKER = Path(".energatron") / "workspace.toml"


def find_workspace(option: str | None, *, cwd: Path, env: Mapping[str, str]) -> WorkspacePaths:
    """The workspace root, from `--workspace`, else `ENERGATRON_WORKSPACE`, else the first folder at or above `cwd`
    holding `.energatron/workspace.toml` (D1 §8.3).

    A folder named by the option or the variable must itself be the root: it isn't searched upwards. A relative
    name is relative to `cwd`, and `~` is expanded. Not found → `E_NOT_A_WORKSPACE`, with `details.source` saying
    where the search started.
    """
    named, source = (option, "option") if option else (env.get(WORKSPACE_ENV) or None, "environment")
    if named is not None:
        root = (cwd / Path(named).expanduser()).resolve()
        if _is_root(root):
            return WorkspacePaths(root=root)
        raise NotAWorkspaceError(
            f"The folder given by the {source} is not an energatron workspace: it has no .energatron/workspace.toml.",
            source=source,
        )
    start = cwd.resolve()
    for folder in (start, *start.parents):
        if _is_root(folder):
            return WorkspacePaths(root=folder)
    raise NotAWorkspaceError(
        "Not in an energatron workspace: no .energatron/workspace.toml here or in any folder above.", source="search"
    )


def read_workspace_config(paths: WorkspacePaths) -> WorkspaceConfig:
    """`.energatron/workspace.toml` (D2 §6.1). Unreadable or invalid → `E_INVALID_INPUT`."""
    document = _read_toml(paths.workspace_config, "workspace_config")
    if document is None:
        raise InvalidInputError("The workspace_config file can't be read.", kind="workspace_config")
    return WorkspaceConfig.from_document(document)


def read_local_config(paths: WorkspacePaths) -> LocalConfig:
    """`.energatron/local.toml` (D2 §6.2); the defaults (no bundle folder) when it's missing."""
    document = _read_toml(paths.local_config, "local_config")
    return LocalConfig() if document is None else LocalConfig.from_document(document)


def backup_bundle_dir(local: LocalConfig) -> Path | None:
    """`backup.bundle_dir` with `~` expanded; None when it's unset (D2 §6.2).

    Not checked here: a relative folder or one inside the workspace fails only the real `backup`, in
    `GitDvcVersionControl.backup()` (D2 §8.1 step 1), so `denylist` and `backup --status` never depend on it (§8.5).
    """
    value = local.backup.bundle_dir
    return None if value is None else Path(os.path.expanduser(value))


def read_private_config(paths: WorkspacePaths) -> PrivateConfig | None:
    """`private/config.toml` (D2 §6.3), or None when it's missing. Invalid → `E_INVALID_INPUT`, naming no value."""
    document = _read_toml(paths.private_config, "private_config")
    return None if document is None else PrivateConfig.from_document(document)


@contextmanager
def workspace_lock(paths: WorkspacePaths) -> Iterator[None]:
    """Hold the exclusive, non-blocking workspace lock, `.energatron/lock` (D1 §8.8). Held elsewhere → `E_LOCKED`."""
    lock = FileLock(paths.lock, timeout=0)
    try:
        lock.acquire()
    except Timeout:
        raise LockedError("Another energatron command holds the workspace lock; try again when it ends.") from None
    try:
        yield
    finally:
        lock.release()


# Helpers


def _is_root(folder: Path) -> bool:
    return (folder / _MARKER).is_file()


def _read_toml(path: Path, kind: str) -> dict[str, JSONValue] | None:
    """A TOML file as a document, or None when it's missing. The parser's message is dropped: it can quote a value."""
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError:
        raise InvalidInputError(f"The {kind} file can't be read.", kind=kind) from None
    try:
        return tomllib.loads(data.decode("utf-8"))
    except UnicodeDecodeError, tomllib.TOMLDecodeError:
        raise InvalidInputError(f"The {kind} file is not valid UTF-8 TOML.", kind=kind) from None
