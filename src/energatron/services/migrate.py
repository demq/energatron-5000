"""`energatron migrate`: rewrite editable config files written at an older schema version (P8 spec §7; D1 §5).

The targets are D1 §5's editable config: `.energatron/workspace.toml`, `private/config.toml` and the YAML rules in
`config/offer_rules.local/` and `config/bundle_rules.local/`. `params.yaml` stays `pipeline sync`'s. A file is read,
run through its kind's migration chain, validated and written back (TOML through `core/toml_text.py`, YAML with
`kind` and `schema_version` first); comments aren't kept. A rewritten private config is never shown, and the denylist
is refreshed after it. The rewritten paths are committed as `migrate: <n> files`. The caller holds the lock.
"""

import logging
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, Final, Literal, cast

import yaml

from energatron.core.canonical import JSONValue
from energatron.core.errors import InvalidInputError, SchemaTooNewError, StorageError
from energatron.core.models._common import FrozenModel
from energatron.core.persisted import registered_kinds
from energatron.core.toml_text import dumps
from energatron.services import denylist as denylist_service
from energatron.services.context import ServiceContext, ServiceResult
from energatron.services.files import atomic_write_text

_log = logging.getLogger(__name__)

PRIVATE_CONFIG: Final = "private/config.toml"


@dataclass(frozen=True)
class Target:
    """Where editable config of one kind lives: one file, or every `*.yaml` in a folder."""

    path: str
    kind: str
    folder: bool = False

    @property
    def format(self) -> Literal["toml", "yaml"]:
        return "yaml" if self.folder or self.path.endswith((".yaml", ".yml")) else "toml"


TARGETS: Final = (
    Target(".energatron/workspace.toml", "workspace_config"),
    Target(PRIVATE_CONFIG, "private_config"),
    Target("config/offer_rules.local", "offer_rule", folder=True),
    Target("config/bundle_rules.local", "bundle_rule", folder=True),
)


class MigratedFile(FrozenModel):
    path: str
    kind: str
    from_version: int
    to_version: int


class MigrateResult(ServiceResult):
    """`migrate_result` v1 (P8 spec §7)."""

    KIND: ClassVar[str] = "migrate_result"
    SCHEMA_VERSION: ClassVar[int] = 1
    kind: Literal["migrate_result"] = "migrate_result"
    schema_version: Literal[1] = 1

    files: list[MigratedFile]
    commit: str | None
    dry_run: bool


def _files(root: Path, target: Target) -> list[str]:
    if not target.folder:
        return [target.path] if (root / target.path).is_file() else []
    folder = root / target.path
    if not folder.is_dir():
        return []
    return sorted(f"{target.path}/{path.name}" for path in folder.iterdir() if path.suffix in (".yaml", ".yml"))


def _read(path: Path, relative: str, fmt: Literal["toml", "yaml"]) -> dict[str, JSONValue]:
    try:
        text = path.read_text(encoding="utf-8")
        document = tomllib.loads(text) if fmt == "toml" else yaml.safe_load(text)
    except OSError, UnicodeDecodeError, tomllib.TOMLDecodeError, yaml.YAMLError:
        raise InvalidInputError("A config file can't be read or parsed.", path=relative) from None
    if not isinstance(document, dict):
        raise InvalidInputError("A config file isn't a mapping.", path=relative)
    return cast(dict[str, JSONValue], document)


def _text(document: dict[str, JSONValue], fmt: Literal["toml", "yaml"]) -> str:
    if fmt == "toml":
        return dumps(document)
    ordered = {"kind": document["kind"], "schema_version": document["schema_version"]} | document
    return yaml.safe_dump(ordered, sort_keys=False, allow_unicode=True)


def migrate(ctx: ServiceContext, *, dry_run: bool = False, targets: Sequence[Target] = TARGETS) -> MigrateResult:
    """Rewrite every older config file (P8 spec §7). Everything is read and validated before the first write."""
    root = ctx.paths.root
    kinds = registered_kinds()
    planned: list[tuple[str, str, MigratedFile]] = []
    for target in targets:
        model = kinds[target.kind]
        for relative in _files(root, target):
            document = _read(root / relative, relative, target.format)
            if document.get("kind") != target.kind:
                raise InvalidInputError(f"A config file isn't a {target.kind} document.", path=relative)
            version = document.get("schema_version")
            if type(version) is not int:
                raise InvalidInputError("A config file has no valid schema_version.", path=relative)
            if version > model.SCHEMA_VERSION:
                raise SchemaTooNewError(
                    "A config file was written by a newer energatron; upgrade the tool.",
                    path=relative,
                    kind=target.kind,
                    schema_version=version,
                    supported_version=model.SCHEMA_VERSION,
                )
            if version == model.SCHEMA_VERSION:
                continue
            try:
                current = model.from_document(document)
            except InvalidInputError as error:
                raise InvalidInputError("A config file fails validation after migration.", path=relative) from error
            text = _text(cast(dict[str, JSONValue], current.model_dump(mode="json", exclude_none=True)), target.format)
            planned.append(
                (
                    relative,
                    text,
                    MigratedFile(
                        path=relative, kind=target.kind, from_version=version, to_version=model.SCHEMA_VERSION
                    ),
                )
            )
    commit: str | None = None
    if planned and not dry_run:
        try:
            for relative, text, _ in planned:
                atomic_write_text(root / relative, text)
        except OSError:
            raise StorageError("A config file couldn't be written.", location="workspace") from None
        paths = [relative for relative, _, _ in planned if relative != PRIVATE_CONFIG]
        if any(relative == PRIVATE_CONFIG for relative, _, _ in planned):
            paths += list(denylist_service.refresh_denylist(ctx).pointer_files)
        commit = ctx.vcs.commit(paths, f"migrate: {len(planned)} files")
    _log.info("migrate: %d files older than the tool, dry run: %s", len(planned), dry_run)
    return MigrateResult(files=[item for _, _, item in planned], commit=commit, dry_run=dry_run)
