#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.14"
# dependencies = []
# ///
"""Run the workspace's iCloud backup steps by hand, in a terminal that can reach iCloud Drive.

Agent sessions can't read or write iCloud Drive (macOS privacy control on the app that hosts them), and the
owner has chosen not to grant it. So every step that touches iCloud is run by the owner with this script.
Instructions: agent_docs/2026-10/2026-10-23_icloud-backup-runbook.md.

Usage:  uv run energatron-5000/scripts/icloud_backup.py [options] <command>
  check   can this terminal read and write iCloud Drive/Backups? Counts files that are only in the cloud
  step0   one-off safety copy of the workspace's data/landing/ and legacy/, with a verified SHA256SUMS
  backup  `uv run energatron backup` in the workspace (dvc push, then a verified git bundle); --drill also
          runs the restore drill afterwards
  drill   the restore drill (scripts/restore_drill.py); options after `--` are passed on to it. A passed drill
          is recorded in the workspace's .energatron/drill_state.json (git-ignored), which `energatron status` reads

Options (before the command):
  --backups-dir DIR  the Backups folder. Default: the folder that holds `backup.bundle_dir` in the workspace's
                     .energatron/local.toml; failing that, iCloud Drive/Backups. Tests point it at a scratch folder
  --workspace DIR    the workspace (default: demq-elektric next to energatron-5000)

Output is counts only, never file names: the names of the landed files start with the NMI.
Exit codes: 0 done, 1 a check or the backup failed, 2 not set up, no access, or the workspace busy.

Stdlib only, so it runs before any venv exists: uv runs it with a managed Python 3.14 from the inline
metadata above. It never deletes anything.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import tomllib
from datetime import UTC, date, datetime
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1]
PARENT = TOOL.parent
ICLOUD = Path.home() / "Library" / "Mobile Documents" / "com~apple~CloudDocs"
WORKSPACE_NAME = "demq-elektric"
STEP0_SOURCES = ("data/landing", "legacy")
ACCESS_HINT = (
    "System Settings → Privacy & Security → Files and Folders → <this terminal app> → turn on iCloud Drive, "
    "then quit and reopen the terminal"
)


class SetupError(Exception):
    """Not set up, or no access: exit 2."""


def configured_backups_dir(workspace: Path) -> Path | None:
    """The folder that holds `backup.bundle_dir` in `<workspace>/.energatron/local.toml`, or None if it isn't set.

    `energatron backup` writes its bundles and the DVC remote beside each other in that folder (D2 §8), so it is
    the Backups folder. Errors never repeat what the file holds.
    """
    path = workspace / ".energatron" / "local.toml"
    try:
        with path.open("rb") as handle:
            config = tomllib.load(handle)
    except FileNotFoundError:
        return None
    except tomllib.TOMLDecodeError:
        raise SetupError(".energatron/local.toml isn't valid TOML") from None
    except OSError as exc:
        raise SetupError(f".energatron/local.toml can't be read ({oserror(exc)})") from None
    section = config.get("backup")
    if section is None:
        return None
    if not isinstance(section, dict):
        raise SetupError("backup in .energatron/local.toml must be a table")
    value = section.get("bundle_dir")
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise SetupError("backup.bundle_dir in .energatron/local.toml must be a path")
    bundle_dir = Path(value).expanduser()
    if not bundle_dir.is_absolute():
        raise SetupError("backup.bundle_dir in .energatron/local.toml must be an absolute path")
    return Path(os.path.normpath(bundle_dir)).parent


def default_backups_dir(workspace: Path) -> Path:
    """Without `--backups-dir`: the configured folder, else `iCloud Drive/Backups` (e.g. `step0` before set-up)."""
    return configured_backups_dir(workspace) or ICLOUD / "Backups"


def say(status: str, message: str) -> None:
    print(f"{status:<4}  {message}")


def oserror(exc: OSError) -> str:
    # The strerror only; the filename may hold an identifier.
    return exc.strerror or type(exc).__name__


def is_dataless(path: Path) -> bool:
    # st_flags exists only on macOS and the BSDs; elsewhere nothing is ever dataless.
    return bool(getattr(os.lstat(path), "st_flags", 0) & stat.SF_DATALESS)


def files_under(root: Path) -> list[Path]:
    """Every regular file under root, sorted, skipping .DS_Store and symlinks."""
    found = []
    for directory, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(filenames):
            path = Path(directory) / name
            if name != ".DS_Store" and path.is_file() and not path.is_symlink():
                found.append(path)
    return found


def symlinks_under(root: Path) -> int:
    return sum(
        1
        for directory, dirnames, filenames in os.walk(root)
        for name in dirnames + filenames
        if (Path(directory) / name).is_symlink()
    )


def sha256(path: Path) -> str:
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def require_access(backups: Path) -> None:
    """Raise SetupError unless backups/ (or, before it exists, its parent) can be listed and written."""
    target = backups if backups.exists() else backups.parent
    if not target.is_dir():
        raise SetupError("iCloud Drive isn't on this Mac, or the Backups folder's parent is missing")
    try:
        os.listdir(target)
        with tempfile.NamedTemporaryFile(dir=target, prefix=".energatron-write-test-"):
            pass
    except PermissionError as exc:
        raise SetupError(f"this terminal can't use iCloud Drive ({oserror(exc)}): {ACCESS_HINT}") from exc
    except OSError as exc:
        raise SetupError(f"iCloud Drive isn't usable: {oserror(exc)}") from exc


def cmd_check(backups: Path, workspace: Path) -> int:
    require_access(backups)
    say("OK", "this terminal can read and write iCloud Drive")
    if not backups.is_dir():
        say("WARN", "the Backups folder doesn't exist yet; `step0` creates it")
        return 0
    failed = False
    folders = sorted(p for p in backups.iterdir() if p.is_dir() and p.name.startswith(f"{WORKSPACE_NAME}-"))
    if not folders:
        say("WARN", "no backup folders yet; run `step0`")
    for folder in folders:
        files = files_under(folder)
        dataless = sum(1 for f in files if is_dataless(f))
        partial = folder.name.endswith(".partial") or any(f.name.endswith(".partial") for f in files)
        if dataless:
            failed = True
            say("FAIL", f"{folder.name}: {len(files)} files, {dataless} only in the cloud (Keep Downloaded is off)")
        else:
            say("OK", f"{folder.name}: {len(files)} files, all downloaded")
        if partial:
            say("WARN", f"{folder.name}: holds a leftover .partial copy from an interrupted run")
    if not (workspace / ".energatron" / "workspace.toml").is_file():
        say("WARN", "the workspace isn't set up yet (P0 step 6), so `backup` and `drill` can't run")
    return 1 if failed else 0


def cmd_step0(backups: Path, workspace: Path) -> int:
    require_access(backups)
    missing = [source for source in STEP0_SOURCES if not (workspace / source).is_dir()]
    if missing:
        raise SetupError(f"not found in the workspace: {', '.join(missing)}")
    dest = backups / f"{WORKSPACE_NAME}-step0-{date.today().isoformat()}"
    partial = dest.with_name(dest.name + ".partial")
    if dest.exists():
        raise SetupError(f"{dest.name} already exists; nothing was written")
    if partial.exists():
        raise SetupError(f"{partial.name} is left over from an interrupted run; delete it in Finder, then run again")
    links = sum(symlinks_under(workspace / source) for source in STEP0_SOURCES)
    if links:
        raise SetupError(f"{links} symlink(s) found; step0 copies regular files only, so nothing was written")

    manifest = []
    for source in STEP0_SOURCES:
        files = files_under(workspace / source)
        manifest += [(f.relative_to(workspace).as_posix(), sha256(f)) for f in files]
    size = sum((workspace / rel).stat().st_size for rel, _ in manifest)

    backups.mkdir(exist_ok=True)
    ignore = shutil.ignore_patterns(".DS_Store")
    try:
        for source in STEP0_SOURCES:
            shutil.copytree(workspace / source, partial / source, symlinks=True, ignore=ignore)
        say("OK", f"copied {len(manifest)} files ({size / 1e6:.1f} MB)")
        copied = {f.relative_to(partial).as_posix() for f in files_under(partial)}
        bad = sum(1 for rel, digest in manifest if rel not in copied or sha256(partial / rel) != digest)
        extra = len(copied - {rel for rel, _ in manifest})
        if bad or extra:
            say("FAIL", f"copy differs from the source: {bad} files wrong or missing, {extra} unexpected")
            say("", f"left in {partial.name}; delete it in Finder, then run again")
            return 1
        # The format `shasum -a 256 -c SHA256SUMS` reads, so the copy can be checked without this script.
        (partial / "SHA256SUMS").write_text("".join(f"{digest}  {rel}\n" for rel, digest in manifest))
        os.replace(partial, dest)
    except OSError as exc:
        say("FAIL", f"copy failed: {oserror(exc)}")
        if partial.exists():
            say("", f"left in {partial.name}; delete it in Finder, then run again")
        return 1
    say("OK", f"copy verified: {dest.name}")
    print("\nNext: in Finder, right-click iCloud Drive/Backups → Keep Downloaded. Once the upload has finished")
    print("(no cloud badge), run `check`; it must report every folder as downloaded.")
    return 0


def workspace_ready(workspace: Path) -> None:
    if not (workspace / ".energatron" / "workspace.toml").is_file():
        raise SetupError("the workspace isn't set up yet (P0 step 6)")
    if shutil.which("uv") is None:
        raise SetupError("uv isn't on PATH in this terminal")


def cmd_backup(backups: Path, workspace: Path, drill: bool) -> int:
    require_access(backups)
    workspace_ready(workspace)
    proc = subprocess.run(
        ["uv", "run", "--frozen", "energatron", "backup", "--json"],
        cwd=workspace,
        capture_output=True,
        text=True,
    )
    try:
        envelope = json.loads(proc.stdout)
    except json.JSONDecodeError:
        envelope = None
    if not isinstance(envelope, dict):
        say("FAIL", f"`energatron backup` printed no result (exit {proc.returncode}); run it in the workspace")
        return 1
    for warning in envelope.get("warnings") or []:
        code = warning.get("code") if isinstance(warning, dict) else warning
        say("WARN", str(code))
    if not envelope.get("ok"):
        error = envelope.get("error") or {}
        say("FAIL", f"{error.get('code')}: {error.get('message')}")
        # usage and state errors (not configured, workspace locked) mean "not set up or busy"
        return 2 if error.get("category") in {"usage", "state"} else 1
    report = (envelope.get("data") or {}).get("report") or {}
    say("OK", f"DVC files pushed: {report.get('dvc_files_pushed')}")
    bundle = "new bundle written and verified" if report.get("bundle_created") else "no new commits; bundle unchanged"
    say("OK", f"{bundle} ({report.get('bundles_kept')} kept, {report.get('bundles_removed')} removed)")
    if report.get("dataless_files"):
        say("WARN", f"{report['dataless_files']} backup files are only in the cloud; set Keep Downloaded on Backups")
    return cmd_drill(backups, workspace, []) if drill else 0


def cmd_drill(backups: Path, workspace: Path, args: list[str]) -> int:
    require_access(backups)
    workspace_ready(workspace)
    drill = TOOL / "scripts" / "restore_drill.py"
    if not drill.is_file():
        raise SetupError("scripts/restore_drill.py doesn't exist yet (P0 step 2)")
    code = subprocess.run([sys.executable, str(drill), "--workspace", str(workspace), *args], cwd=PARENT).returncode
    if code == 0:
        record_drill(workspace)
    return code


def record_drill(workspace: Path) -> None:
    """After a passed drill: `.energatron/drill_state.json` (`drill_state` v1, P7 spec §9), so `energatron status`
    knows when the next one is due. Git-ignored, like `backup_state.json`. Written atomically; a failure only warns."""
    at = datetime.now(UTC).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
    text = json.dumps({"at": at, "kind": "drill_state", "schema_version": 1}, sort_keys=True, indent=2) + "\n"
    target = workspace / ".energatron" / "drill_state.json"
    try:
        partial = target.with_name(target.name + ".partial")
        partial.write_text(text, encoding="utf-8")
        os.replace(partial, target)
    except OSError as exc:
        say("WARN", f"the drill passed but its record couldn't be written: {oserror(exc)}")
        return
    say("OK", "drill recorded in .energatron/drill_state.json")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--backups-dir", type=Path, default=None)
    parser.add_argument("--workspace", type=Path, default=PARENT / WORKSPACE_NAME)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check")
    commands.add_parser("step0")
    commands.add_parser("backup").add_argument("--drill", action="store_true")
    commands.add_parser("drill").add_argument("drill_args", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    workspace = args.workspace.expanduser().resolve()
    try:
        backups = args.backups_dir.expanduser() if args.backups_dir else default_backups_dir(workspace)
        if args.command == "check":
            return cmd_check(backups, workspace)
        if args.command == "step0":
            return cmd_step0(backups, workspace)
        if args.command == "backup":
            return cmd_backup(backups, workspace, args.drill)
        return cmd_drill(backups, workspace, [a for a in args.drill_args if a != "--"])
    except SetupError as exc:
        say("FAIL", str(exc))
        return 2
    except OSError as exc:
        say("FAIL", f"file error: {oserror(exc)}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
