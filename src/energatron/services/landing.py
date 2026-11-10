"""`energatron landing list|clear` (P7 spec §5; D3 §16): what the inbox holds, and clearing what is already landed.

An inbox file is a regular file under `data/landing/` (subfolders included), hidden names and symlinks skipped. It is
`landed` when its sha256 is in the catalog and the blob behind that record exists with the same sha256; anything else
is `new`. Names in results are masked component by component (D3 §8.2): SAPN names start with the NMI.

`landing_list` takes no lock and writes nothing. `landing_clear` is called under the workspace lock; it re-hashes each
landed file just before deleting it, never deletes a `new` one, and removes subfolders left empty (never the inbox).
"""

import contextlib
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, ClassVar, Final, Literal

from pydantic import Field

from energatron.core.canonical import sha256_hex
from energatron.core.errors import StorageError
from energatron.core.models._common import FrozenModel
from energatron.core.privacy import DenyEntry, derive_denylist, mask_filename
from energatron.services.context import ServiceContext, ServiceResult

_log = logging.getLogger(__name__)

LANDING_DIR: Final = "data/landing"
_Count = Annotated[int, Field(ge=0)]


class InboxFile(FrozenModel):
    name: str  # workspace-relative below the inbox, each component masked
    status: Literal["landed", "new"]
    dataset_id: str | None
    removed: bool


class LandingResult(ServiceResult):
    """`landing_result` v1 (P7 spec §5)."""

    KIND: ClassVar[str] = "landing_result"
    SCHEMA_VERSION: ClassVar[int] = 1
    kind: Literal["landing_result"] = "landing_result"
    schema_version: Literal[1] = 1

    action: Literal["list", "clear"]
    dry_run: bool
    files: list[InboxFile]
    landed: _Count
    new: _Count
    removed: _Count


@dataclass(frozen=True)
class Inbox:
    """The inbox as `status` needs it: counts only."""

    files: int
    landed: int
    new: int


@dataclass(frozen=True)
class _Found:
    path: Path
    relative: tuple[str, ...]
    sha256: str
    dataset_id: str | None


def landing_list(ctx: ServiceContext) -> LandingResult:
    """Every inbox file and whether it is landed. Read-only; no lock."""
    found = _scan(ctx)
    return _result(ctx, "list", found, removed=set(), dry_run=False)


def landing_clear(ctx: ServiceContext, *, dry_run: bool = False) -> LandingResult:
    """Delete every landed inbox file (`dry_run`: say which), then subfolders left empty. The caller holds the lock."""
    found = _scan(ctx)
    removed: set[Path] = set()
    for item in found:
        if item.dataset_id is None:
            continue
        if dry_run:
            removed.add(item.path)
            continue
        if _hash(item.path) != item.sha256:  # changed since the scan: leave it
            continue
        try:
            item.path.unlink()
        except OSError:
            raise StorageError("An inbox file couldn't be deleted.", reason="unlink_failed") from None
        removed.add(item.path)
    if not dry_run:
        _remove_empty_folders(ctx.paths.root / LANDING_DIR)
    _log.info("landing clear: %d of %d file(s) %s", len(removed), len(found), "would go" if dry_run else "removed")
    return _result(ctx, "clear", found, removed=removed, dry_run=dry_run)


def inbox(ctx: ServiceContext) -> Inbox:
    """The inbox counts, for `status`."""
    found = _scan(ctx)
    landed = sum(1 for item in found if item.dataset_id is not None)
    return Inbox(files=len(found), landed=landed, new=len(found) - landed)


def _scan(ctx: ServiceContext) -> list[_Found]:
    root = ctx.paths.root / LANDING_DIR
    found: list[_Found] = []
    for path in sorted(_files(root)):
        relative = path.relative_to(root).parts
        digest = _hash(path)
        found.append(_Found(path, relative, digest, _landed_id(ctx, digest)))
    return found


def _files(root: Path) -> Iterable[Path]:
    """Regular files below `root`, never following a symlink, hidden names skipped."""
    if root.is_symlink() or not root.is_dir():
        return
    for entry in root.iterdir():
        if entry.name.startswith(".") or entry.is_symlink():
            continue
        if entry.is_dir():
            yield from _files(entry)
        elif entry.is_file():
            yield entry


def _hash(path: Path) -> str:
    try:
        return sha256_hex(path.read_bytes())
    except OSError:
        raise StorageError("An inbox file couldn't be read.", reason="read_failed") from None


def _landed_id(ctx: ServiceContext, digest: str) -> str | None:
    """The dataset ID when the catalog has these bytes and the stored blob still matches them."""
    record = ctx.catalog.find_sha256(digest)
    if record is None or not ctx.blobs.exists(record.stored_path):
        return None
    if ctx.blobs.stat(record.stored_path).sha256 != digest:
        return None
    return record.dataset_id


def _remove_empty_folders(root: Path) -> None:
    if root.is_symlink() or not root.is_dir():
        return
    for entry in root.iterdir():
        if entry.is_dir() and not entry.is_symlink():
            _remove_empty_folders(entry)
            with contextlib.suppress(OSError):  # not empty
                entry.rmdir()


def _result(
    ctx: ServiceContext,
    action: Literal["list", "clear"],
    found: list[_Found],
    *,
    removed: set[Path],
    dry_run: bool,
) -> LandingResult:
    denylist = derive_denylist(ctx.private) if ctx.private is not None else ()
    files = [
        InboxFile(
            name=_mask(item.relative, denylist),
            status="new" if item.dataset_id is None else "landed",
            dataset_id=item.dataset_id,
            removed=item.path in removed,
        )
        for item in found
    ]
    landed = sum(1 for item in files if item.status == "landed")
    return LandingResult(
        action=action,
        dry_run=dry_run,
        files=files,
        landed=landed,
        new=len(files) - landed,
        removed=len(removed),
    )


def _mask(parts: tuple[str, ...], denylist: Iterable[DenyEntry]) -> str:
    entries = tuple(denylist)
    return "/".join(mask_filename(part, entries) for part in parts)
