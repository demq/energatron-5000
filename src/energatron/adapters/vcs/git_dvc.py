"""`GitDvcVersionControl`: the `VersionControl` port over `git` and `dvc` subprocesses (D1 §4.4; D2 §8).

- `git` comes from `PATH`; `dvc` from the running interpreter's venv (`uv run` in the workspace), else `PATH`.
  The package never imports DVC (D1 §12).
- git runs with `GIT_LITERAL_PATHSPECS=1`: a path is a name, never a glob, so `commit()` stages exactly the
  given paths (D1 §4.4).
- Paths in and out are workspace-relative POSIX paths. Errors and logs hold commands, exit codes, counts and
  location names, never a path, a file name from the data or a subprocess's output: hook output may describe
  the content it rejected (D2 §8.6), and absolute paths hold the macOS username (D1 §12).
- `backup()` is owner-run (D2 §4); the CLI holds the workspace lock around it. `backup_status()` reads only the
  local marker and the refs, never the backup locations (D2 §8.5).
"""

import contextlib
import hashlib
import json
import logging
import os
import re
import shutil
import stat
import subprocess
import sys
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath

from energatron.core.canonical import sha256_hex
from energatron.core.errors import (
    BackupNotConfiguredError,
    CommitRejectedError,
    DenylistHitError,
    InternalError,
    InvalidInputError,
    StorageError,
    SubprocessError,
    UsageError,
)
from energatron.core.ids import compact_utc
from energatron.core.models.vcs import BackupReport, BackupState, BackupStatus
from energatron.core.models.workspace import BackupPolicy, WorkspacePaths
from energatron.ports.clock import Clock
from energatron.services.files import fsync_directory, read_json, write_json

_log = logging.getLogger(__name__)

_BUNDLE = re.compile(r"\d{8}T\d{6}Z-[0-9a-f]{7}\.bundle")  # D2 §8.3: the only names retention deletes
_PARTIAL = re.compile(r"\.\d{8}T\d{6}Z-[0-9a-f]{7}\.bundle\.partial")
_URL_SCHEME = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://")
_HOOK_ID = re.compile(r"- hook id: (\S+)")  # pre-commit's failure lines (D2 §1, verified 2026-10-23)
_HOOK_EXIT = re.compile(r"- exit code: (\d+)")
_GUARD_HOOKS = frozenset({"privacy-guard", "privacy-guard-msg"})
_GUARD_HIT = 1  # the guard's exit code for a denylist hit; 2 is a setup error (D2 §7.5, §8.6)
_PARTIAL_MAX_AGE = timedelta(days=1)
_SF_DATALESS = getattr(stat, "SF_DATALESS", 0x40000000)
_LOCATIONS = {"bundle_dir": "the git bundle folder", "dvc_remote": "the DVC remote folder"}
_INTEGRITY_HINT = (
    "the new bundle failed an integrity check and nothing was renamed into place: run the backup again, and if it "
    "fails the same way, run `git fsck` in the workspace"
)
_WORKSPACE_HINT = "check that the workspace's `.energatron/` folder is writable and the disk isn't full"


def _is_dataless(st: os.stat_result) -> bool:
    """An iCloud placeholder whose data isn't on this Mac (D2 §8.1 step 6). Never true where `st_flags` is missing."""
    return bool(getattr(st, "st_flags", 0) & _SF_DATALESS)


def _relative(paths: Sequence[str]) -> list[str]:
    """Check and normalise workspace-relative POSIX paths. The error never repeats a path."""
    normalised: list[str] = []
    for path in paths:
        pure = PurePosixPath(path)
        if not path or "\\" in path or "\0" in path or pure.is_absolute() or ".." in pure.parts or not pure.parts:
            raise InternalError("VersionControl takes workspace-relative POSIX paths only.")
        normalised.append(pure.as_posix())
    return normalised


def _hook_failures(output: str) -> list[tuple[str, int | None]]:
    """The failed hooks in pre-commit's output, in order: each `- hook id:` line and the exit code under it."""
    failures: list[tuple[str, int | None]] = []
    for line in output.splitlines():
        if (hook := _HOOK_ID.fullmatch(line.strip())) is not None:
            failures.append((hook.group(1), None))
        elif (code := _HOOK_EXIT.fullmatch(line.strip())) is not None and failures and failures[-1][1] is None:
            failures[-1] = (failures[-1][0], int(code.group(1)))
    return failures


def _refs_sha256(refs: Sequence[str]) -> str:
    """D2 §6.5: the sha256 of the sorted `<objectname> <refname>` lines, `HEAD` included, each ending in `\\n`."""
    return sha256_hex("".join(f"{line}\n" for line in sorted(refs)).encode("utf-8"))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _storage_error(message: str, location: str, **details: int) -> StorageError:
    """A backup location that can't be reached: the hint names the access that's missing (D2 §8.1)."""
    hint = f"{_LOCATIONS[location]} can't be reached from here: run `icloud_backup.py backup` in a terminal with "
    hint += "iCloud access"
    return StorageError(message, location=location, hint=hint, **details)


def _integrity_error(message: str) -> StorageError:
    """A new bundle that git rejects or whose copy differs: the folder was reached, so no access hint."""
    return StorageError(message, location="bundle_dir", hint=_INTEGRITY_HINT)


def _workspace_error(message: str) -> StorageError:
    """A local file under `.energatron/` (the scratch folder, the marker) that can't be written."""
    return StorageError(message, location="workspace", hint=_WORKSPACE_HINT)


class GitDvcVersionControl:
    """git and DVC in the workspace at `paths.root`, with backups to a bundle folder and the default DVC remote.

    `bundle_dir` is `local.toml`'s `backup.bundle_dir` with `~` expanded (None when unset). Only `backup()` checks
    it (D2 §6.2, §8.1 step 1); `backup_status()` never looks at it (§8.5). `policy` is `workspace.toml`'s
    `[backup]`; `clock` names the bundles (D2 §8.2).
    """

    def __init__(
        self,
        paths: WorkspacePaths,
        *,
        clock: Clock,
        bundle_dir: Path | None,
        policy: BackupPolicy,
        dvc: Path | None = None,
    ) -> None:
        self._paths = paths
        self._clock = clock
        self._bundle_dir = bundle_dir
        self._policy = policy
        beside = Path(sys.executable).parent / "dvc"
        self._dvc = str(dvc) if dvc is not None else str(beside) if beside.is_file() else "dvc"

    # Subprocesses

    def _run(self, tool: str, *args: str, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
        executable = self._dvc if tool == "dvc" else tool
        env = {**os.environ, "GIT_LITERAL_PATHSPECS": "1"} if tool == "git" else None
        try:
            result = subprocess.run(
                [executable, *args],
                cwd=self._paths.root,
                env=env,
                input=stdin,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
        except OSError:
            raise SubprocessError(f"{tool} could not be started.", command=tool) from None
        _log.debug("%s %s exited %d", tool, args[0], result.returncode)
        return result

    def _check(self, tool: str, *args: str, stdin: str | None = None) -> str:
        result = self._run(tool, *args, stdin=stdin)
        if result.returncode != 0:
            command = f"{tool} {args[0]}"
            raise SubprocessError(f"`{command}` failed.", command=command, exit_code=result.returncode)
        return result.stdout

    # The port

    def head(self) -> str | None:
        result = self._run("git", "rev-parse", "--verify", "--quiet", "HEAD^{commit}")
        if result.returncode == 1 and not result.stdout.strip():
            return None  # an unborn branch: no commit yet
        if result.returncode != 0:
            raise SubprocessError("`git rev-parse` failed.", command="git rev-parse", exit_code=result.returncode)
        return result.stdout.strip()

    def dirty_paths(self, paths: Sequence[str] = ()) -> list[str]:
        relative = _relative(paths)
        output = self._check(
            "git", "status", "--porcelain=v1", "-z", "--untracked-files=all", "--no-renames", "--", *relative
        )
        return sorted({entry[3:] for entry in output.split("\0") if entry})

    def track(self, paths: Sequence[str]) -> list[str]:
        relative = _relative(paths)
        if not relative:
            return []
        self._check("dvc", "add", "--quiet", *relative)
        pointers = [f"{path}.dvc" for path in relative]
        if not all((self._paths.root / pointer).is_file() for pointer in pointers):
            raise InternalError("`dvc add` succeeded but wrote no pointer file.")
        return pointers

    def commit(self, paths: Sequence[str], message: str) -> str:
        relative = _relative(paths)
        if relative:
            self._check("git", "add", "--", *relative)
            staged = self._run("git", "diff", "--cached", "--quiet", "--", *relative)
            if staged.returncode not in (0, 1):
                raise SubprocessError("`git diff` failed.", command="git diff", exit_code=staged.returncode)
            if staged.returncode == 1:
                return self._commit(relative, message)
        head = self.head()
        if head is None:
            raise UsageError("Nothing to commit.")
        return head

    def _commit(self, relative: list[str], message: str) -> str:
        """Commit exactly `relative` (`git commit --only`), letting the hooks run; map a rejection (D2 §8.6)."""
        result = self._run("git", "commit", "--quiet", "--file=-", "--only", "--", *relative, stdin=message)
        if result.returncode == 0:
            head = self.head()
            if head is None:
                raise InternalError("git commit succeeded but HEAD has no commit.")
            return head
        failures = _hook_failures(result.stdout + "\n" + result.stderr)  # never logged or returned
        if not failures:
            raise SubprocessError("`git commit` failed.", command="git commit", exit_code=result.returncode)
        hooks = list(dict.fromkeys(hook for hook, _ in failures))
        if any(hook in _GUARD_HOOKS and code == _GUARD_HIT for hook, code in failures):
            raise DenylistHitError("The privacy guard rejected the commit.", hooks=hooks)
        raise CommitRejectedError("A git hook rejected the commit.", hooks=hooks)

    def backup(self) -> BackupReport:
        """D2 §8.1, steps 1 to 7. Any `OSError` on a backup location → `E_STORAGE`, with no path shown."""
        now = self._clock.now().astimezone(UTC).replace(microsecond=0)
        bundle_dir = self._configured_bundle_dir()
        remote_dir = self._remote_dir()
        head = self.head()
        if head is None:
            raise UsageError("Nothing to back up yet: the workspace has no commit.")
        self._reach(bundle_dir, "bundle_dir")
        self._reach(remote_dir, "dvc_remote")

        pushed = self._push()

        refs = self._refs(head)
        existing = self._bundles(bundle_dir)
        if existing and self._bundle_heads(bundle_dir / existing[-1]) == refs:
            name, created = existing[-1], False
        else:
            name, created = f"{compact_utc(now)}-{head[:7]}.bundle", True
            self._write_bundle(bundle_dir, name, refs)

        kept, removed = self._retain(bundle_dir, now)
        dataless = self._count_dataless(bundle_dir, "bundle_dir") + self._count_dataless(remote_dir, "dvc_remote")
        state = BackupState(head=head, refs_sha256=_refs_sha256(refs), bundle_name=name, at=now)
        try:
            write_json(self._paths.backup_state, state.model_dump(mode="json"))
        except OSError:
            raise _workspace_error("The backup_state marker couldn't be written.") from None
        _log.info(
            "backup: %d DVC files pushed, bundle created: %s, kept %d, removed %d", pushed, created, kept, removed
        )
        return BackupReport(
            dvc_files_pushed=pushed,
            bundle_name=name,
            bundles_kept=kept,
            bundles_removed=removed,
            bundle_created=created,
            dataless_files=dataless,
        )

    def backup_status(self) -> BackupStatus:
        head = self.head()
        marker = self._read_marker()
        if marker is None:
            return BackupStatus(
                head=head, last_backup_head=None, last_backup_at=None, refs_backed_up=False, commits_not_backed_up=None
            )
        backed_up = marker.refs_sha256 == _refs_sha256(self._refs(head))
        count: int | None = None
        if head is not None and self._run("git", "cat-file", "-e", f"{marker.head}^{{commit}}").returncode == 0:
            count = int(self._check("git", "rev-list", "--count", f"{marker.head}..HEAD").strip())
        return BackupStatus(
            head=head,
            last_backup_head=marker.head,
            last_backup_at=marker.at,
            refs_backed_up=backed_up,
            commits_not_backed_up=count,
        )

    # Backup steps

    def _configured_bundle_dir(self) -> Path:
        """`bundle_dir`, normalised: set, absolute and outside the workspace, or → `E_BACKUP_NOT_CONFIGURED` (D2 §6.2).

        The check is lexical (`normpath`): it never looks at the folder, so it runs before anything else.
        """
        if self._bundle_dir is None:
            raise BackupNotConfiguredError("No bundle folder is set: add backup.bundle_dir to .energatron/local.toml.")
        if not self._bundle_dir.is_absolute():
            raise BackupNotConfiguredError("backup.bundle_dir in .energatron/local.toml must be an absolute path.")
        normalised = Path(os.path.normpath(self._bundle_dir))
        if normalised.is_relative_to(self._paths.root):
            raise BackupNotConfiguredError("backup.bundle_dir in .energatron/local.toml must be outside the workspace.")
        return normalised

    def _remote_dir(self) -> Path:
        """The default DVC remote's folder (D2 §8.1 step 1). No default, or one that isn't a local folder → not set."""
        default = self._run("dvc", "remote", "default")
        name = default.stdout.strip()
        if default.returncode != 0 or not name:
            raise BackupNotConfiguredError("No default DVC remote is set: add one with `dvc remote add --local -d`.")
        url = self._check("dvc", "config", f"remote.{name}.url").strip()
        if not url or _URL_SCHEME.match(url) is not None:
            raise BackupNotConfiguredError("The default DVC remote must be a local folder.")
        path = Path(url).expanduser()
        return path if path.is_absolute() else (self._paths.root / ".dvc" / path).resolve()

    def _reach(self, directory: Path, location: str) -> None:
        """List `directory`; create it if only it is missing and its parent can be listed (D2 §8.1 step 1)."""
        try:
            try:
                os.listdir(directory)
            except FileNotFoundError:
                os.listdir(directory.parent)
                directory.mkdir()
                os.listdir(directory)
        except OSError:
            raise _storage_error("A backup location can't be reached.", location) from None

    def _cloud_missing(self) -> int:
        output = self._check("dvc", "status", "--cloud", "--json")
        try:
            missing = json.loads(output)
        except ValueError:
            raise SubprocessError("`dvc status` printed no JSON.", command="dvc status") from None
        if not isinstance(missing, dict):
            raise SubprocessError("`dvc status` printed no JSON object.", command="dvc status")
        return len(missing)

    def _push(self) -> int:
        """D2 §8.1 step 2: count what the remote lacks, push, and check that it now lacks nothing."""
        pushed = self._cloud_missing()
        result = self._run("dvc", "push", "--quiet")
        if result.returncode != 0:
            raise _storage_error("`dvc push` failed.", "dvc_remote", exit_code=result.returncode)
        missing = self._cloud_missing()
        if missing:
            raise _storage_error("The DVC remote still lacks objects after `dvc push`.", "dvc_remote", missing=missing)
        return pushed

    def _refs(self, head: str | None) -> list[str]:
        """Every ref as `<objectname> <refname>`, plus `<head> HEAD`, sorted (D2 §8.1 step 3)."""
        output = self._check("git", "for-each-ref", "--format=%(objectname) %(refname)")
        refs = [line for line in output.splitlines() if line]
        if head is not None:
            refs.append(f"{head} HEAD")
        return sorted(refs)

    def _bundle_heads(self, bundle: Path) -> list[str] | None:
        """A bundle's heads, sorted; None when git can't read it (then a new bundle is written)."""
        result = self._run("git", "bundle", "list-heads", str(bundle))
        if result.returncode != 0:
            return None
        return sorted(line for line in result.stdout.splitlines() if line)

    def _bundles(self, bundle_dir: Path) -> list[str]:
        """The bundle files in the folder, oldest first: names sort chronologically (D2 §8.2)."""
        try:
            with os.scandir(bundle_dir) as entries:
                return sorted(e.name for e in entries if _BUNDLE.fullmatch(e.name) and e.is_file(follow_symlinks=False))
        except OSError:
            raise _storage_error("The bundle folder can't be listed.", "bundle_dir") from None

    def _copy(self, source: Path, target: Path) -> None:
        """Copy `source` to `target` and `fsync` it."""
        with source.open("rb") as reader, target.open("wb") as writer:
            shutil.copyfileobj(reader, writer, 1 << 20)
            writer.flush()
            os.fsync(writer.fileno())

    def _write_bundle(self, bundle_dir: Path, name: str, refs: list[str]) -> None:
        """D2 §8.1 step 4: create, verify, copy, check and rename into place; nothing is renamed on any failure."""
        try:
            self._paths.tmp_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            raise _workspace_error("The workspace's scratch folder couldn't be made.") from None
        local = self._paths.tmp_dir / name
        partial = bundle_dir / f".{name}.partial"
        try:
            if self._run("git", "bundle", "create", "--quiet", str(local), "--all").returncode != 0:
                raise _integrity_error("`git bundle create` failed.")
            if self._run("git", "bundle", "verify", "--quiet", str(local)).returncode != 0:
                raise _integrity_error("The new bundle failed `git bundle verify`.")
            if self._bundle_heads(local) != refs:
                raise _integrity_error("The new bundle's heads differ from the refs.")
            try:
                digest = _sha256_file(local)
            except OSError:
                raise _workspace_error("The new bundle couldn't be read back.") from None
            try:
                self._copy(local, partial)
                if _sha256_file(partial) != digest:
                    raise _integrity_error("The bundle's copy differs from the local bundle.")
                os.replace(partial, bundle_dir / name)
                fsync_directory(bundle_dir)
            except OSError:
                raise _storage_error("The bundle couldn't be copied into the bundle folder.", "bundle_dir") from None
            finally:
                with contextlib.suppress(OSError):  # else retention removes it once it's a day old
                    partial.unlink(missing_ok=True)
        finally:
            try:
                local.unlink(missing_ok=True)
            except OSError:  # git-ignored scratch space; never mask the step's own result
                _log.warning("backup: the local bundle copy couldn't be removed from the scratch folder")

    def _retain(self, bundle_dir: Path, now: datetime) -> tuple[int, int]:
        """D2 §8.3: keep the newest `keep_latest` and each month's newest; delete old partials. (kept, removed)."""
        names = self._bundles(bundle_dir)
        keep = set(names[-self._policy.keep_latest :])
        if self._policy.keep_monthly:
            newest_of_month = {name[:6]: name for name in names}  # sorted oldest first, so the newest wins
            keep |= set(newest_of_month.values())
        try:
            for name in names:
                if name not in keep:
                    (bundle_dir / name).unlink()
            with os.scandir(bundle_dir) as entries:
                partials = [e for e in entries if _PARTIAL.fullmatch(e.name) and e.is_file(follow_symlinks=False)]
            for entry in partials:
                modified = datetime.fromtimestamp(entry.stat(follow_symlinks=False).st_mtime, UTC)
                if now - modified > _PARTIAL_MAX_AGE:
                    os.unlink(entry.path)
        except OSError:
            raise _storage_error("Old bundles couldn't be removed.", "bundle_dir") from None
        return len(keep), len(names) - len(keep)

    def _count_dataless(self, directory: Path, location: str) -> int:
        """D2 §8.1 step 6: files under `directory` that are iCloud placeholders."""

        def fail(error: OSError) -> None:
            raise error

        count = 0
        try:
            for base, _, files in os.walk(directory, onerror=fail):
                count += sum(_is_dataless(os.lstat(os.path.join(base, name))) for name in files)
        except OSError:
            raise _storage_error("A backup location can't be read.", location) from None
        return count

    def _read_marker(self) -> BackupState | None:
        """`backup_state`, or None when it's missing. One that can't be read or isn't valid → `E_INVALID_INPUT`."""
        try:
            document = read_json(self._paths.backup_state)
        except FileNotFoundError:
            return None
        except ValueError:
            raise InvalidInputError("The backup_state document is not valid JSON.", kind="backup_state") from None
        except OSError:
            raise InvalidInputError("The backup_state document can't be read.", kind="backup_state") from None
        if not isinstance(document, dict):
            raise InvalidInputError("The backup_state document is not a JSON object.", kind="backup_state")
        return BackupState.from_document(document)
