"""`energatron init`: a new workspace from the template (P8 spec §4).

The steps run in §4.3's order. The subprocesses (git, uv, DVC, pre-commit) are behind the `WorkspaceSetup` port; the
denylist, `pipeline sync` and the first commit reuse the services and ports a workspace has once its files exist, so
`open_context` builds that context (with the workspace venv's `dvc`). Nothing is written when a check fails, and
nothing is ever deleted: a failure after the files are written leaves the folder for inspection.
"""

import logging
import re
import secrets
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar, Final, Literal, cast

from energatron.core.canonical import JSONValue
from energatron.core.errors import InvalidInputError, StorageError, SubprocessError, UsageError
from energatron.core.models._common import FrozenModel
from energatron.core.models.private import PrivateConfig
from energatron.core.models.workspace import WorkspacePaths
from energatron.core.workspace_template import (
    TEMPLATE_STATE_PATH,
    TemplateFile,
    TemplateValues,
    project_name,
    render,
    template_state,
)
from energatron.ports.setup import WorkspaceSetup
from energatron.services import denylist as denylist_service
from energatron.services import pipeline_sync
from energatron.services.context import ServiceContext, ServiceResult
from energatron.services.files import atomic_write_bytes, atomic_write_text, make_dirs, write_json

_log = logging.getLogger(__name__)

COMMIT_MESSAGE: Final = "init: energatron workspace"
_UNSAFE_PATH: Final = re.compile(r'["\\\n\r\t]')

type StepName = Literal[
    "write_files",
    "git_init",
    "git_identity",
    "uv_sync",
    "dvc_init",
    "dvc_remote",
    "private_config",
    "denylist",
    "pipeline_sync",
    "pre_commit",
    "commit",
    "demo",
]
STEPS: Final[tuple[StepName, ...]] = (
    "write_files",
    "git_init",
    "git_identity",
    "uv_sync",
    "dvc_init",
    "dvc_remote",
    "private_config",
    "denylist",
    "pipeline_sync",
    "pre_commit",
    "commit",
    "demo",
)

type OpenContext = Callable[[WorkspacePaths, Path | None], ServiceContext]
"""Builds a workspace's context once its files exist, with the given `dvc` executable (None: the default)."""
type RunDemo = Callable[[Path], JSONValue]
"""Fills a new workspace with the demo (P8 spec §5) and returns its `demo_result` as JSON."""


@dataclass(frozen=True)
class PrivateValues:
    """The private config values `init` was given or asked for. Never logged, never in the result."""

    nmi: str
    meter_serials: tuple[str, ...] = ()
    postcode: str | None = None

    def __repr__(self) -> str:
        return "PrivateValues(<redacted>)"


@dataclass(frozen=True)
class InitOptions:
    root: Path  # absolute; must not exist, or be an empty folder
    region: str
    regions: Sequence[str]  # the shipped region profiles
    tool_source: str  # the rendered `[tool.uv.sources]` line, "" for none
    tool_source_kind: Literal["path", "git", "index"]
    home: Path
    sources: Mapping[str, str] = field(default_factory=lambda: dict[str, str]())  # the template texts
    remote: Path | None = None
    private: PrivateValues | None = None
    demo: bool = False
    sync: bool = True
    dry_run: bool = False


class InitStep(FrozenModel):
    name: StepName
    status: Literal["done", "skipped", "planned"]


class InitResult(ServiceResult):
    """`init_result` v1 (P8 spec §4.4). Holds the folder's name, never its path, and no private value."""

    KIND: ClassVar[str] = "init_result"
    SCHEMA_VERSION: ClassVar[int] = 1
    kind: Literal["init_result"] = "init_result"
    schema_version: Literal[1] = 1

    root_name: str
    region: str
    tool_source_kind: Literal["path", "git", "index"]
    files: list[str]
    steps: list[InitStep]
    git_identity_set: bool
    private_config: bool
    demo: dict[str, JSONValue] | None
    dry_run: bool
    next_steps: list[str]


def deny_root(root: Path, home: Path) -> str:
    """The workspace path as the agent deny rules name it: `~/…` under the home folder, else absolute."""
    if root.is_relative_to(home):
        return "~/" + root.relative_to(home).as_posix()
    return root.as_posix()


def private_document(values: PrivateValues, salt: str) -> dict[str, JSONValue]:
    """The `private_config` document for `values` (D2 §6.3)."""
    premises: dict[str, JSONValue] = {"nmi": values.nmi}
    if values.meter_serials:
        premises["meter_serials"] = list(values.meter_serials)
    if values.postcode:
        premises["postcode"] = values.postcode
    return {"kind": "private_config", "schema_version": 1, "salt": salt, "premises": premises}


def private_toml(values: PrivateValues, salt: str) -> str:
    """`private/config.toml` text for `values`, validated through `PrivateConfig` before it is returned."""
    PrivateConfig.from_document(private_document(values, salt))  # E_INVALID_INPUT names the field, never the value
    lines = ['kind = "private_config"', "schema_version = 1", f'salt = "{salt}"', "", "[premises]"]
    lines.append(f"nmi = {_toml_str(values.nmi)}")
    if values.meter_serials:
        lines.append("meter_serials = [" + ", ".join(_toml_str(item) for item in values.meter_serials) + "]")
    if values.postcode:
        lines.append(f"postcode = {_toml_str(values.postcode)}")
    return "\n".join(lines) + "\n"


def _toml_str(value: str) -> str:
    if any(ord(char) < 0x20 or char == "\x7f" for char in value):
        raise InvalidInputError("A private config value holds a control character.", field="premises")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _check(options: InitOptions, setup: WorkspaceSetup) -> None:
    if options.region not in options.regions:
        raise InvalidInputError(
            f"No region profile named that ships with this version; choose one of: {', '.join(options.regions)}.",
            field="region",
        )
    root = options.root
    if _UNSAFE_PATH.search(root.as_posix()):
        raise UsageError("The workspace path holds a quote, backslash or control character.", reason="path_characters")
    if root.exists() and (not root.is_dir() or any(root.iterdir())):
        raise UsageError("The target exists and isn't an empty folder.", reason="not_empty")
    missing = setup.missing_tools()
    if missing:
        raise SubprocessError(f"init needs {' and '.join(missing)} on PATH.", step="check", tools=missing)


def _write_files(root: Path, files: Sequence[TemplateFile]) -> None:
    try:
        make_dirs(root)
        for item in files:
            target = root / item.path
            make_dirs(target.parent)
            if item.symlink_to is not None:
                target.symlink_to(item.symlink_to)
                continue
            atomic_write_bytes(target, item.data)
            target.chmod(item.mode)
        for folder in ("data/landing", "private"):
            make_dirs(root / folder)
    except OSError:
        raise StorageError(
            "The workspace files couldn't be written.", location="workspace", hint="check the folder is writable"
        ) from None


def init(
    options: InitOptions,
    *,
    setup: WorkspaceSetup,
    open_context: OpenContext,
    run_demo: RunDemo | None = None,
) -> InitResult:
    """Create the workspace (P8 spec §4.3). `run_demo` fills it with the demo when `options.demo` is set."""
    _check(options, setup)
    root = options.root
    values = TemplateValues(
        name=project_name(root.name),
        region=options.region,
        tool_source=options.tool_source,
        deny_root=deny_root(root, options.home),
    )
    files = render(values, options.sources)
    written = sorted([*(item.path for item in files), TEMPLATE_STATE_PATH])
    done: dict[StepName, Literal["done", "skipped", "planned"]] = {}

    def result(*, identity: bool, private: bool, demo: dict[str, JSONValue] | None) -> InitResult:
        return InitResult(
            root_name=root.name,
            region=options.region,
            tool_source_kind=options.tool_source_kind,
            files=written,
            steps=[InitStep(name=name, status=done.get(name, "skipped")) for name in STEPS],
            git_identity_set=identity,
            private_config=private,
            demo=demo,
            dry_run=options.dry_run,
            next_steps=_next_steps(options, private=private),
        )

    has_private = options.private is not None
    if options.dry_run:
        for name in STEPS:
            if _planned(name, options):
                done[name] = "planned"
        return result(identity=False, private=has_private, demo=None)

    _write_files(root, files)
    done["write_files"] = "done"
    setup.git_init(root)
    done["git_init"] = "done"
    identity = setup.ensure_git_identity(root)
    if identity:
        done["git_identity"] = "done"
    if options.sync:
        setup.uv_sync(root)
        done["uv_sync"] = "done"
    setup.dvc_init(root)
    done["dvc_init"] = "done"
    if options.remote is not None:
        _remote(root, options.remote, values.name, setup)
        done["dvc_remote"] = "done"
    paths = WorkspacePaths(root=root)
    dvc = setup.dvc_executable(root)
    if options.private is not None:
        _write_private(paths, options.private)
        done["private_config"] = "done"
        denylist_service.refresh_denylist(open_context(paths, dvc))
        done["denylist"] = "done"
    context = open_context(paths, dvc)
    pipeline_sync.sync(context)
    done["pipeline_sync"] = "done"
    if options.sync:
        setup.pre_commit_install(root)
        done["pre_commit"] = "done"
    write_json(root / TEMPLATE_STATE_PATH, cast(JSONValue, template_state(files).model_dump(mode="json")))
    context.vcs.commit(list(setup.unstaged_paths(root)), COMMIT_MESSAGE)
    done["commit"] = "done"
    _log.info("init: workspace committed")
    demo: dict[str, JSONValue] | None = None
    if options.demo and run_demo is not None:
        found = run_demo(root)
        demo = cast(dict[str, JSONValue], found) if isinstance(found, dict) else None
        done["demo"] = "done"
    return result(identity=identity, private=has_private, demo=demo)


def _planned(name: StepName, options: InitOptions) -> bool:
    match name:
        case "git_identity":
            return False  # known only once the repo exists
        case "uv_sync" | "pre_commit":
            return options.sync
        case "dvc_remote":
            return options.remote is not None
        case "private_config" | "denylist":
            return options.private is not None
        case "demo":
            return options.demo
        case _:
            return True


def _remote(root: Path, remote: Path, name: str, setup: WorkspaceSetup) -> None:
    """The DVC remote and, beside it, the bundle folder in `local.toml` (D2 §6.2)."""
    try:
        make_dirs(remote)
    except OSError:
        raise StorageError("The DVC remote folder couldn't be created.", location="dvc_remote") from None
    setup.dvc_remote(root, remote)
    bundle_dir = (remote.parent / f"{name}-git").as_posix()
    atomic_write_text(
        root / ".energatron" / "local.toml",
        f'kind = "local_config"\nschema_version = 1\n\n[backup]\nbundle_dir = {_toml_str(bundle_dir)}\n',
    )


def _write_private(paths: WorkspacePaths, values: PrivateValues) -> None:
    text = private_toml(values, secrets.token_hex(16))
    try:
        atomic_write_text(paths.root / "private" / "config.toml", text)
    except OSError:
        raise StorageError("private/config.toml couldn't be written.", location="workspace") from None


def _next_steps(options: InitOptions, *, private: bool) -> list[str]:
    found: list[str] = []
    if not options.sync:
        found.append("Run `uv sync` and `uv run pre-commit install` in the workspace.")
    if not private:
        found.append(
            "Copy config/private.example.toml to private/config.toml, fill in your values, then run "
            "`uv run energatron denylist` and commit private.dvc."
        )
    if options.remote is None:
        found.append("Add a backup remote: `uv run dvc remote add --local -d backup <folder>` (see the README).")
    if options.demo:
        found.append("Open data/reports/recommendations/index.html and data/reports/usage/index.html.")
    else:
        found.append("Download your meter data into data/landing/, then `uv run energatron land data/landing/`.")
    return found
