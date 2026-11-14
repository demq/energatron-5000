"""`energatron upgrade`: refresh the template-managed files of a workspace that `init` made (P8 spec §6).

Each managed file is compared with the current template and with the sha256 recorded in `.energatron/template.json`:
`unchanged` (already the new bytes), `updated` (the user hasn't edited it, so it is rewritten), `modified` (edited:
left alone with `W_TEMPLATE_MODIFIED`, unless `force`), `added` (new in the template, or missing) and `removed` (no
longer in the template: left in place, dropped from the record). Seed files are never touched. The written paths and
the record are committed as `upgrade: template v<N>`; nothing written, no commit. The caller holds the workspace lock.
"""

import logging
from collections.abc import Mapping
from pathlib import Path
from typing import ClassVar, Final, Literal, cast

from energatron.core.canonical import JSONValue, sha256_hex
from energatron.core.errors import InvalidInputError, ResultWarning, StorageError, UsageError, WarningCode
from energatron.core.models._common import FrozenModel
from energatron.core.workspace_template import (
    TEMPLATE_STATE_PATH,
    TEMPLATE_VERSION,
    TemplateState,
    TemplateValues,
    project_name,
    render,
    tool_source_line,
)
from energatron.services.context import ServiceContext, ServiceResult
from energatron.services.files import atomic_write_bytes, make_dirs, read_json, write_json
from energatron.services.init import deny_root

_log = logging.getLogger(__name__)

type FileStatus = Literal["unchanged", "updated", "modified", "added", "removed"]
_WRITES: Final = ("updated", "added")


class UpgradedFile(FrozenModel):
    path: str
    status: FileStatus


class UpgradeResult(ServiceResult):
    """`upgrade_result` v1 (P8 spec §6)."""

    KIND: ClassVar[str] = "upgrade_result"
    SCHEMA_VERSION: ClassVar[int] = 1
    kind: Literal["upgrade_result"] = "upgrade_result"
    schema_version: Literal[1] = 1

    from_version: int
    to_version: int
    files: list[UpgradedFile]
    commit: str | None
    dry_run: bool


def read_state(root: Path) -> TemplateState:
    """`.energatron/template.json`; missing → `E_USAGE` (`not_templated`)."""
    path = root / TEMPLATE_STATE_PATH
    if not path.is_file():
        raise UsageError(
            "This workspace wasn't made by `energatron init` (no .energatron/template.json): upgrade it by hand.",
            reason="not_templated",
        )
    try:
        document = read_json(path)
    except OSError, ValueError:
        raise InvalidInputError("The template_state file can't be read.", kind="template_state") from None
    if not isinstance(document, dict):
        raise InvalidInputError("The template_state file isn't a JSON object.", kind="template_state")
    return TemplateState.from_document(document)


def _current(path: Path) -> bytes | None:
    try:
        return path.read_bytes() if path.is_file() else None
    except OSError:
        raise StorageError("A workspace file couldn't be read.", location="workspace") from None


def upgrade(
    ctx: ServiceContext, *, sources: Mapping[str, str], home: Path, force: bool = False, dry_run: bool = False
) -> UpgradeResult:
    """Refresh the managed files (P8 spec §6)."""
    root = ctx.paths.root
    state = read_state(root)
    values = TemplateValues(
        name=project_name(root.name),
        region=ctx.config.region,
        tool_source=tool_source_line("index"),  # pyproject.toml is a seed file: never rewritten
        deny_root=deny_root(root, home),
    )
    managed = [item for item in render(values, sources) if item.managed]
    files: list[UpgradedFile] = []
    recorded = dict(state.files)
    hashes: dict[str, str] = {}
    writes: list[tuple[Path, bytes, int]] = []
    warnings: list[ResultWarning] = []
    for item in managed:
        current = _current(root / item.path)
        new_hash = sha256_hex(item.data)
        status: FileStatus
        if current == item.data:
            status = "unchanged"
        elif current is None:
            status = "added"
        elif item.path not in recorded:
            status = "updated" if force else "modified"
        elif sha256_hex(current) == recorded[item.path] or force:
            status = "updated"
        else:
            status = "modified"
        if status == "modified":
            hashes[item.path] = recorded.get(item.path, sha256_hex(current or b""))
            warnings.append(
                ResultWarning(
                    code=WarningCode.W_TEMPLATE_MODIFIED,
                    message=f"{item.path} was edited here, so upgrade left it alone; rerun with --force to replace it.",
                    details={"path": item.path},
                )
            )
        else:
            hashes[item.path] = new_hash
        if status in _WRITES:
            writes.append((root / item.path, item.data, item.mode))
        files.append(UpgradedFile(path=item.path, status=status))
    files += [
        UpgradedFile(path=path, status="removed") for path in sorted(set(recorded) - {item.path for item in managed})
    ]
    new_state = TemplateState(template_version=TEMPLATE_VERSION, files=hashes)
    commit: str | None = None
    if not dry_run and (writes or new_state != state):
        try:
            for path, data, mode in writes:
                make_dirs(path.parent)
                atomic_write_bytes(path, data)
                path.chmod(mode)
        except OSError:
            raise StorageError("A template file couldn't be written.", location="workspace") from None
        write_json(root / TEMPLATE_STATE_PATH, cast(JSONValue, new_state.model_dump(mode="json")))
        paths = [path.relative_to(root).as_posix() for path, _, _ in writes] + [TEMPLATE_STATE_PATH]
        commit = ctx.vcs.commit(paths, f"upgrade: template v{TEMPLATE_VERSION}")
    _log.info("upgrade: %d files written, dry run: %s", len(writes), dry_run)
    return UpgradeResult(
        from_version=state.template_version,
        to_version=TEMPLATE_VERSION,
        files=files,
        commit=commit,
        dry_run=dry_run,
        warnings=tuple(warnings),
    )
