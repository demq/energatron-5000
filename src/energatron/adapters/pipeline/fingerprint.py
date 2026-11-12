"""The code fingerprint: each stage's code entry, the recording fields, and the file (D1 §11).

- `stage_code()` computes one stage's `StageCode` (§11.1): its module set from `closure.py`, plus its declared
  package data; the tree hash of those files; the third-party distributions its code can run; the Python version.
- `install_fields()` maps the distribution's `direct_url.json` (PEP 610) to `install`, `tool_commit` and
  `tool_dirty` (§11.2). Only an editable install runs git, as `git -C <package dir>`.
- `compute_fingerprint()` puts them together for every registered stage; `wiring.code_fingerprint()` calls it.
- `read_fingerprint()`, `write_fingerprint()` and `refresh_fingerprint()` handle `.energatron/code_fingerprint.json`
  for `energatron fingerprint [--check]` (§11.3) and, from P1.3.3, `DvcRunner.repro()`.

Errors name modules, distributions and package-relative paths, never an absolute path (D1 §12).
"""

import importlib.metadata
import json
import logging
import os
import platform
import re
import subprocess
import sys
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import ClassVar, Final, Literal, Protocol

import energatron
from energatron.adapters.pipeline.closure import module_closure
from energatron.core.canonical import JSONValue, content_hash, sha256_hex, tree_hash
from energatron.core.errors import (
    FingerprintStaleError,
    InternalError,
    InvalidInputError,
    ResultWarning,
    SubprocessError,
    WarningCode,
)
from energatron.core.models._common import CommitSha, Sha256Hex, StageName
from energatron.core.models.provenance import CodeFingerprint, Install, StageCode, stale_stages
from energatron.services.context import ServiceResult
from energatron.services.files import dump_json, read_json, write_json
from energatron.services.stages.runner import StageSpec

_log = logging.getLogger(__name__)

DISTRIBUTION: Final = "energatron-5000"
_SKIPPED_NAMES: Final = frozenset({"__pycache__", ".DS_Store"})
_SKIPPED_SUFFIXES: Final = frozenset({".pyc", ".pyo"})
_REQUIREMENT_NAME = re.compile(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)")
_TYPESHED = "_typeshed"
# Variables that would point `git -C <package dir>` at another repository, such as the workspace's in a hook.
_GIT_REPO_VARIABLES: Final = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_COMMON_DIR",
    "GIT_NAMESPACE",
    "GIT_PREFIX",
)
RECORDING_FIELDS: Final = ("energatron_version", "install", "tool_commit", "tool_dirty")


def package_dir() -> Path:
    """The installed package's directory, resolved (D1 §11.1)."""
    return Path(energatron.__file__).resolve().parent


def normalise(distribution: str) -> str:
    """A distribution name in PEP 503 form: `PyYAML` → `pyyaml`, `typing_extensions` → `typing-extensions`."""
    return re.sub(r"[-_.]+", "-", distribution).lower()


# Distributions


class Distributions(Protocol):
    """The installed distributions, as the fingerprint needs them. `InstalledDistributions` is the real one."""

    def top_level(self) -> Mapping[str, Sequence[str]]:
        """Top-level module → the distributions that provide it."""
        ...

    def version(self, distribution: str) -> str | None:
        """The installed version, or None when the distribution isn't installed."""
        ...

    def requires(self, distribution: str) -> Sequence[str]:
        """Its requirement strings, markers included."""
        ...


class InstalledDistributions:
    """`Distributions` from `importlib.metadata`, for the running interpreter."""

    def __init__(self) -> None:
        self._top_level: Mapping[str, Sequence[str]] | None = None

    def top_level(self) -> Mapping[str, Sequence[str]]:
        if self._top_level is None:
            self._top_level = importlib.metadata.packages_distributions()
        return self._top_level

    def version(self, distribution: str) -> str | None:
        try:
            return importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            return None

    def requires(self, distribution: str) -> Sequence[str]:
        try:
            return importlib.metadata.requires(distribution) or ()
        except importlib.metadata.PackageNotFoundError:
            return ()


def _packages(external: Sequence[str], checking_only: Sequence[str], distributions: Distributions) -> dict[str, str]:
    """D1 §11.1 step 4: the distributions the imported third-party modules come from, and their requirements,
    recursively, extras included whenever installed. Names are PEP 503-normalised; versions as installed."""
    top_level = distributions.top_level()
    queue: deque[str] = deque()
    for module in external:
        if module in sys.stdlib_module_names:
            continue
        if module == _TYPESHED and module in checking_only:
            continue  # exists only for type checkers
        provided = [name for name in top_level.get(module, ()) if distributions.version(name) is not None]
        if not provided:
            raise InternalError(f"Module {module} maps to no installed distribution.", module=module)
        queue.extend(provided)
    packages: dict[str, str] = {}
    while queue:
        distribution = queue.popleft()
        key = normalise(distribution)
        if key in packages:
            continue
        version = distributions.version(distribution)
        if version is None:
            continue  # not installed: installing it later makes the stage stale
        packages[key] = version
        for requirement in distributions.requires(distribution):
            match = _REQUIREMENT_NAME.match(requirement)
            if match is not None:
                queue.append(match.group(1))
    return dict(sorted(packages.items()))


# Files


def _check_no_symlink(root: Path, relative: str) -> None:
    """Every path component from `root` down to `relative` must be a real file or directory (D1 §11.1 step 2)."""
    parts = PurePosixPath(relative).parts
    for end in range(1, len(parts) + 1):
        if root.joinpath(*parts[:end]).is_symlink():
            raise InternalError(
                f"A symlink in the package at {'/'.join(parts[:end])}: the fingerprint can't see its target.",
                path="/".join(parts[:end]),
            )


def _skipped(name: str) -> bool:
    return name in _SKIPPED_NAMES or PurePosixPath(name).suffix in _SKIPPED_SUFFIXES


def _package_data_files(root: Path, entry: str) -> list[str]:
    """A declared package-data file, or every file under a declared directory, as package-relative paths."""
    pure = PurePosixPath(entry)
    if pure.is_absolute() or ".." in pure.parts or not pure.parts:
        raise InternalError(f"Package data {entry} isn't a package-relative path.", path=entry)
    _check_no_symlink(root, entry)
    path = root.joinpath(*pure.parts)
    if path.is_file():
        return [pure.as_posix()]
    if not path.is_dir():
        raise InternalError(f"Declared package data {entry} doesn't exist.", path=entry)
    found: list[str] = []
    pending = [path]
    while pending:
        directory = pending.pop()
        for child in sorted(directory.iterdir()):
            relative = child.relative_to(root).as_posix()
            if child.is_symlink():
                raise InternalError(
                    f"A symlink in the package at {relative}: the fingerprint can't see its target.", path=relative
                )
            if _skipped(child.name):
                continue
            if child.is_dir():
                pending.append(child)
            elif child.is_file():
                found.append(relative)
    return found


# A stage's code entry (D1 §11.1)


def stage_code(
    module: str,
    package_data: Sequence[str],
    *,
    package_dir: Path,
    distributions: Distributions,
) -> StageCode:
    """The code entry of the stage whose body is in `module`, reading `package_data` (D1 §11.1)."""
    closure = module_closure(module, package_dir=package_dir)
    files = set(closure.files)
    for entry in package_data:
        files.update(_package_data_files(package_dir, entry))
    ordered = sorted(files)
    for relative in ordered:
        _check_no_symlink(package_dir, relative)
    source_sha256 = tree_hash((relative, sha256_hex((package_dir / relative).read_bytes())) for relative in ordered)
    packages = _packages(closure.external, closure.checking_only, distributions)
    python = platform.python_version()
    stage_sha256 = content_hash({"source_sha256": source_sha256, "packages": dict(packages), "python": python})
    return StageCode(
        stage_sha256=stage_sha256, source_sha256=source_sha256, python=python, packages=packages, files=ordered
    )


# The recording fields (D1 §11.2)

type Git = Callable[..., str]


def install_fields(direct_url: str | None, *, git: Git) -> tuple[Install, str | None, bool]:
    """`(install, tool_commit, tool_dirty)` from `direct_url.json`'s text, or None when the file is missing.

    `git(*args)` runs git in the package directory and returns its stdout; only an editable install calls it.
    """
    if direct_url is None:
        return "index", None, False
    try:
        doc = json.loads(direct_url)
    except ValueError:
        raise InternalError("direct_url.json isn't valid JSON.") from None
    if not isinstance(doc, dict):
        raise InternalError("direct_url.json isn't a JSON object.")
    kinds = [key for key in ("dir_info", "vcs_info", "archive_info") if key in doc]
    if len(kinds) != 1:
        raise InternalError("direct_url.json doesn't name exactly one install kind.", kinds=list(kinds))
    info = doc[kinds[0]]
    if not isinstance(info, dict):
        raise InternalError(f"direct_url.json's {kinds[0]} isn't an object.")
    match kinds[0]:
        case "dir_info" if info.get("editable") is True:
            commit = git("rev-parse", "HEAD").strip()
            dirty = bool(git("status", "--porcelain", "--", ".").strip())
            return "editable", _commit(commit), dirty
        case "dir_info":
            return "local_dir", None, False
        case "vcs_info":
            return "vcs", _commit(info.get("commit_id")), False
        case _:
            return "archive", None, False


def _commit(value: object) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{40}", value) is None:
        raise InternalError("The tool commit isn't a full hex SHA.")
    return value


def _git_in(directory: Path) -> Git:
    def run(*args: str) -> str:
        env = {name: value for name, value in os.environ.items() if name not in _GIT_REPO_VARIABLES}
        try:
            result = subprocess.run(
                ["git", "-C", str(directory), *args],
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
        except OSError:
            raise SubprocessError("git could not be started.", command="git") from None
        if result.returncode != 0:
            command = f"git {args[0]}"
            raise SubprocessError(
                f"`{command}` failed in the energatron checkout.", command=command, exit_code=result.returncode
            )
        return result.stdout

    return run


def commit_published(commit: str, *, root: Path | None = None) -> bool | None:
    """Whether a remote-tracking branch of the tool checkout holds `commit`, as of its last fetch (no network).

    `None` when that can't be told: the checkout has no remote, or git fails (e.g. the commit isn't in it).
    """
    git = _git_in(root if root is not None else package_dir())
    try:
        if not git("remote").strip():
            return None
        return bool(git("branch", "-r", "--contains", commit).strip())
    except SubprocessError:
        return None


def _direct_url() -> str | None:
    try:
        return importlib.metadata.distribution(DISTRIBUTION).read_text("direct_url.json")
    except OSError, UnicodeDecodeError, importlib.metadata.PackageNotFoundError:
        raise InternalError("The energatron-5000 install's direct_url.json can't be read.") from None


# The whole fingerprint


def compute_fingerprint(
    stages: Mapping[str, StageSpec],
    *,
    root: Path | None = None,
    distributions: Distributions | None = None,
    direct_url: Callable[[], str | None] = _direct_url,
) -> CodeFingerprint:
    """The live fingerprint: one code entry per stage in `stages`, in their order, and the recording fields."""
    root = root if root is not None else package_dir()
    distributions = distributions if distributions is not None else InstalledDistributions()
    entries = {
        name: stage_code(spec.impl.__module__, spec.package_data, package_dir=root, distributions=distributions)
        for name, spec in stages.items()
    }
    install, commit, dirty = install_fields(direct_url(), git=_git_in(root))
    return CodeFingerprint(
        energatron_version=importlib.metadata.version(DISTRIBUTION),
        install=install,
        tool_commit=commit,
        tool_dirty=dirty,
        stages=entries,
    )


# The file: `.energatron/code_fingerprint.json`


def read_fingerprint(path: Path) -> CodeFingerprint | None:
    """The recorded fingerprint, or None when the file is missing. Invalid → `E_INVALID_INPUT`."""
    try:
        doc = read_json(path)
    except FileNotFoundError:
        return None
    except ValueError:
        raise InvalidInputError("The code fingerprint file isn't valid JSON.", kind=CodeFingerprint.KIND) from None
    if not isinstance(doc, dict):
        raise InvalidInputError("The code fingerprint file isn't a JSON object.", kind=CodeFingerprint.KIND)
    return CodeFingerprint.from_document(doc)


def write_fingerprint(path: Path, fingerprint: CodeFingerprint) -> bool:
    """Write the file with sorted keys, `indent=2` and a trailing newline, only if its content changed (§11.2).
    Returns whether it was written."""
    value: JSONValue = fingerprint.model_dump(mode="json")
    try:
        if path.read_text(encoding="utf-8") == dump_json(value):
            return False
    except FileNotFoundError, UnicodeDecodeError:
        pass
    write_json(path, value)
    return True


class FingerprintResult(ServiceResult):
    """`fingerprint_result` v1: what `energatron fingerprint [--check]` did (D1 §5, §11.3; fields set in P1.3.2).

    `changed_stages` are the stages whose recorded entry differed from the live one, or was missing or extra;
    `changed_fields` the recording fields that differed. `stages` holds each live `stage_sha256`.
    """

    KIND: ClassVar[str] = "fingerprint_result"
    SCHEMA_VERSION: ClassVar[int] = 1
    kind: Literal["fingerprint_result"] = "fingerprint_result"
    schema_version: Literal[1] = 1

    check: bool
    dry_run: bool
    written: bool
    changed_stages: list[StageName]
    changed_fields: list[str]
    stages: dict[StageName, Sha256Hex]
    install: Install
    tool_commit: CommitSha | None
    tool_dirty: bool


def _changed_fields(live: CodeFingerprint, recorded: CodeFingerprint | None) -> list[str]:
    if recorded is None:
        return []
    return sorted(name for name in RECORDING_FIELDS if getattr(live, name) != getattr(recorded, name))


def refresh_fingerprint(
    path: Path, live: CodeFingerprint, *, check: bool = False, dry_run: bool = False
) -> FingerprintResult:
    """Compare `live` with the recorded file, then write it, unless `check` or `dry_run` (D1 §11.3).

    With `check`: an invalid recorded file → `E_INVALID_INPUT`; a stale, missing or extra stage entry →
    `E_FINGERPRINT_STALE` with `details.stages`; recording fields that differ give `W_FINGERPRINT_METADATA`.
    Writing (and its dry run) treats an invalid recorded file, such as one with git conflict markers, like a missing
    one: every stage changed, and the file is overwritten. Otherwise the command that regenerates it couldn't.
    """
    try:
        recorded = read_fingerprint(path)
    except InvalidInputError:
        if check:
            raise
        _log.warning("The recorded code fingerprint is invalid; it will be replaced.")
        recorded = None
    stale = stale_stages(live, recorded)
    fields = _changed_fields(live, recorded)
    warnings: tuple[ResultWarning, ...] = ()
    if check:
        if stale:
            raise FingerprintStaleError(
                "The recorded code fingerprint is stale: run `energatron fingerprint`.", stages=list(stale)
            )
        if fields:
            warnings = (
                ResultWarning(
                    code=WarningCode.W_FINGERPRINT_METADATA,
                    message="Only the fingerprint's recording fields differ; no stage is stale.",
                    details={"fields": list(fields)},
                ),
            )
    written = False if check or dry_run else write_fingerprint(path, live)
    _log.info("fingerprint: %d stages changed, written %s", len(stale), written)
    return FingerprintResult(
        check=check,
        dry_run=dry_run,
        written=written,
        changed_stages=stale,
        changed_fields=fields,
        stages={name: entry.stage_sha256 for name, entry in live.stages.items()},
        install=live.install,
        tool_commit=live.tool_commit,
        tool_dirty=live.tool_dirty,
        warnings=warnings,
    )
