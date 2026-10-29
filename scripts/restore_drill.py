#!/usr/bin/env python3
"""Restore drill (D2 §12): rebuild the workspace from its backups in a scratch folder and check it.

It reads iCloud Drive, so only the owner runs it, through `icloud_backup.py drill` or `backup --drill`, from the
parent folder: at P0 acceptance, then quarterly. Instructions: agent_docs/2026-10/2026-10-23_icloud-backup-runbook.md.

Usage:
  restore_drill.py [--workspace demq-elektric] [--tool-source origin|local] [--keep] [--json]

Steps:
   1 inputs        the bundle folder (`.energatron/local.toml`), the DVC remote (`dvc config --local --list`), the
                   tool source (`origin`: the tool repo's origin URL; `local`: the tool repo itself)
   2 scratch       `$TMPDIR/energatron-restore-<compact UTC>/`
   3 clone         the tool into `energatron-5000/`; the newest bundle (verified) into `demq-elektric/`, without
                   its `origin` remote
   4 tool version  check out `.energatron/code_fingerprint.json`'s `tool_commit` in the tool clone (P1 on)
   5 install       `uv sync --locked` in the workspace clone
   6 data          `dvc pull` (with the remote's `verify` on); `dvc status` and `dvc status --cloud` both `{}`
   7 pipeline      `dvc repro`, then a clean `git status` (only with a `dvc.yaml`, P1 on)
   8 bundle        the clone's HEAD is the live workspace's HEAD
   9 verify        `energatron verify` (P1 on: SKIP only with no `dvc.yaml`, fingerprint file or `data/raw/`)
  10 privacy       `privacy_guard.py --mode workspace --tracked` on the clone, with the clone's `private/`
  11 dataless      no file under the bundle folder or the remote is only in the cloud
  12 clean-up      delete the scratch folder, unless --keep

Output: one PASS / FAIL / SKIP line per step; with --json, one summary document with counts and step results. No
line holds a path or a file name, and no command's own output is shown.
Exit codes: 0 every step passed or was skipped, 1 a step failed, 2 a setup error (steps 1-2, or bad arguments).

Stdlib only, so it runs with a bare `python3`. Keep it runnable on older Pythons (no 3.10+ syntax).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1]
PARENT = TOOL.parent
TOOL_NAME = "energatron-5000"
WORKSPACE_NAME = "demq-elektric"
BUNDLE = re.compile(r"\d{8}T\d{6}Z-[0-9a-f]{7}\.bundle")  # D2 §8.2
COMMIT = re.compile(r"[0-9a-f]{7,64}")
URL_SCHEME = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://")
SF_DATALESS = getattr(stat, "SF_DATALESS", 0x40000000)
STEPS = (
    "inputs",
    "scratch",
    "clone",
    "tool version",
    "install",
    "data",
    "pipeline",
    "bundle",
    "verify",
    "privacy",
    "dataless",
    "clean-up",
)


class SetupError(Exception):
    """Steps 1-2 can't start: exit 2. The message never holds a path."""


class Drill:
    def __init__(self, workspace: Path, tool_source: str, keep: bool, as_json: bool) -> None:
        self.workspace = workspace
        self.tool_source = tool_source
        self.keep = keep
        self.as_json = as_json
        self.results: list[dict[str, object]] = []
        self.counts: dict[str, int | None] = {
            "bundles": None,
            "dvc_status_entries": None,
            "dvc_cloud_status_entries": None,
            "tracked_files": None,
            "dataless_files": None,
        }
        self.bundle_dir = Path()
        self.bundle = Path()
        self.remote_url = ""
        self.tool_url = ""
        self.scratch: Path | None = None

    # -- output ---------------------------------------------------------------------------------------------------

    def record(self, step: int, status: str, detail: str) -> None:
        self.results.append({"step": step, "name": STEPS[step - 1], "status": status, "detail": detail})
        if not self.as_json:
            print(f"{status:<4}  {step:>2} {STEPS[step - 1]}: {detail}", flush=True)

    def finish(self, code: int, error: str | None = None) -> int:
        if self.as_json:
            summary = {
                "kind": "restore_drill",
                "ok": code == 0,
                "exit_code": code,
                "error": error,
                "counts": self.counts,
                "steps": self.results,
            }
            print(json.dumps(summary, indent=2))
        else:
            failed = sum(1 for r in self.results if r["status"] == "FAIL")
            skipped = sum(1 for r in self.results if r["status"] == "SKIP")
            print(f"\nrestore drill: {len(self.results)} steps, {failed} failed, {skipped} skipped; exit {code}")
        return code

    # -- commands -------------------------------------------------------------------------------------------------

    def run(self, cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
        """Run a command, capturing its output: it may hold paths or file names, so it is never printed."""
        return subprocess.run(list(args), cwd=str(cwd), capture_output=True, text=True)

    # -- step 1 ---------------------------------------------------------------------------------------------------

    def inputs(self) -> None:
        if shutil.which("git") is None or shutil.which("uv") is None:
            raise SetupError("git and uv must be on PATH")
        if not (self.workspace / ".energatron" / "workspace.toml").is_file():
            raise SetupError("the workspace isn't set up yet (no .energatron/workspace.toml)")
        self.bundle_dir = bundle_dir_from(self.workspace / ".energatron" / "local.toml", self.workspace)
        try:
            bundles = sorted(e.name for e in os.scandir(self.bundle_dir) if BUNDLE.fullmatch(e.name) and e.is_file())
        except OSError as exc:
            raise SetupError(f"can't list the bundle folder ({oserror(exc)})") from exc
        self.counts["bundles"] = len(bundles)
        if not bundles:
            raise SetupError("the bundle folder holds no bundle; run `icloud_backup.py backup` first")
        self.bundle = self.bundle_dir / bundles[-1]
        # `--frozen --no-sync`: uv only reads the live workspace (no lock update, no venv sync).
        listed = self.run(self.workspace, "uv", "run", "--frozen", "--no-sync", "dvc", "config", "--local", "--list")
        if listed.returncode != 0:
            raise SetupError(f"`dvc config --local --list` failed in the workspace (exit {listed.returncode})")
        self.remote_url = remote_url_from(listed.stdout, self.workspace)
        if self.tool_source == "origin":
            origin = self.run(TOOL, "git", "remote", "get-url", "origin")
            if origin.returncode != 0 or not origin.stdout.strip():
                raise SetupError("the tool repo has no origin remote; use --tool-source local")
            self.tool_url = origin.stdout.strip()
        else:
            if not (TOOL / ".git").exists():
                raise SetupError("the tool folder isn't a git repo yet")
            self.tool_url = str(TOOL)
        self.record(
            1, "PASS", f"{len(bundles)} bundle(s), the newest used; DVC remote set; tool from {self.tool_source}"
        )

    # -- step 2 ---------------------------------------------------------------------------------------------------

    def make_scratch(self) -> None:
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())  # D1's compact_utc
        scratch = Path(tempfile.gettempdir()) / f"energatron-restore-{stamp}"
        try:
            scratch.mkdir()
        except OSError as exc:
            raise SetupError(f"can't create the scratch folder ({oserror(exc)})") from exc
        self.scratch = scratch
        self.record(2, "PASS", "scratch folder created under $TMPDIR")

    # -- steps 3-12 -----------------------------------------------------------------------------------------------

    def steps(self) -> None:
        assert self.scratch is not None
        tool = self.scratch / TOOL_NAME
        clone = self.scratch / WORKSPACE_NAME
        if not self.clone(tool, clone):
            self.not_run(4, 11)
            return
        self.tool_version(tool, clone)
        installed = self.run(clone, "uv", "sync", "--locked")
        if installed.returncode != 0:
            self.record(5, "FAIL", f"`uv sync --locked` failed (exit {installed.returncode})")
            self.not_run(6, 11)
            return
        self.record(5, "PASS", "`uv sync --locked` done")
        self.data(clone)
        self.pipeline(clone)
        self.bundle_current(clone)
        self.verify(clone)
        self.privacy(tool, clone)
        self.dataless()

    def not_run(self, first: int, last: int) -> None:
        for step in range(first, last + 1):
            self.record(step, "SKIP", "not run: an earlier step failed")

    def clone(self, tool: Path, clone: Path) -> bool:
        cloned = self.run(self.scratch or tool.parent, "git", "clone", "-q", self.tool_url, str(tool))
        if cloned.returncode != 0:
            self.record(3, "FAIL", f"cloning the tool failed (exit {cloned.returncode})")
            return False
        verified = self.run(tool, "git", "bundle", "verify", "-q", str(self.bundle))
        if verified.returncode != 0:
            self.record(3, "FAIL", f"`git bundle verify` failed on the newest bundle (exit {verified.returncode})")
            return False
        restored = self.run(tool.parent, "git", "clone", "-q", str(self.bundle), str(clone))
        if restored.returncode != 0:
            self.record(3, "FAIL", f"cloning the newest bundle failed (exit {restored.returncode})")
            return False
        removed = self.run(clone, "git", "remote", "remove", "origin")
        if removed.returncode != 0:
            self.record(3, "FAIL", f"`git remote remove origin` failed (exit {removed.returncode})")
            return False
        self.record(3, "PASS", "tool cloned; newest bundle verified and cloned")
        return True

    def tool_version(self, tool: Path, clone: Path) -> None:
        fingerprint = clone / ".energatron" / "code_fingerprint.json"
        if not fingerprint.is_file():
            self.record(4, "SKIP", "no code_fingerprint.json (before P1)")
            return
        try:
            recorded = json.loads(fingerprint.read_text(encoding="utf-8"))
        except OSError:
            recorded = None
        except ValueError:
            recorded = None
        if not isinstance(recorded, dict):
            self.record(4, "FAIL", "code_fingerprint.json can't be read")
            return
        commit = recorded.get("tool_commit")
        if commit is None:
            self.record(4, "SKIP", "code_fingerprint.json names no tool_commit")
            return
        if not isinstance(commit, str) or not COMMIT.fullmatch(commit):
            self.record(4, "FAIL", "code_fingerprint.json's tool_commit isn't a commit id")
            return
        checked = self.run(tool, "git", "checkout", "-q", "--detach", commit)
        if checked.returncode != 0:
            self.record(4, "FAIL", f"the recorded tool commit can't be checked out (exit {checked.returncode})")
            return
        self.record(4, "PASS", f"tool at the recorded commit {commit[:7]}")

    def data(self, clone: Path) -> None:
        added = self.run(
            clone, "uv", "run", "--locked", "dvc", "remote", "add", "-d", "--local", "icloud", self.remote_url
        )
        if added.returncode != 0:
            self.record(6, "FAIL", f"`dvc remote add` failed (exit {added.returncode})")
            return
        # `verify`: DVC re-hashes what it downloads. Without it, a changed remote object is checked out and `dvc
        # status` still reports `{}`, because DVC trusts the cache file's name.
        verified = self.run(
            clone, "uv", "run", "--locked", "dvc", "remote", "modify", "--local", "icloud", "verify", "true"
        )
        if verified.returncode != 0:
            self.record(6, "FAIL", f"`dvc remote modify` failed (exit {verified.returncode})")
            return
        pulled = self.run(clone, "uv", "run", "--locked", "dvc", "pull")
        local = self.status_entries(clone, "dvc_status_entries")
        cloud = self.status_entries(clone, "dvc_cloud_status_entries", "--cloud")
        if pulled.returncode != 0:
            self.record(6, "FAIL", f"`dvc pull` failed (exit {pulled.returncode})")
        elif local != 0 or cloud != 0:
            shown = ["unreadable" if n is None else str(n) for n in (local, cloud)]
            self.record(6, "FAIL", f"dvc status lists {shown[0]} entries, dvc status --cloud {shown[1]}")
        else:
            self.record(6, "PASS", "`dvc pull` done; dvc status and dvc status --cloud are both {}")

    def status_entries(self, clone: Path, key: str, *extra: str) -> int | None:
        done = self.run(clone, "uv", "run", "--locked", "dvc", "status", *extra, "--json")
        try:
            status = json.loads(done.stdout)
        except ValueError:
            status = None
        count = len(status) if done.returncode == 0 and isinstance(status, dict) else None
        self.counts[key] = count
        return count

    def pipeline(self, clone: Path) -> None:
        if not (clone / "dvc.yaml").is_file():
            self.record(7, "SKIP", "no dvc.yaml (before P1)")
            return
        repro = self.run(clone, "uv", "run", "--locked", "dvc", "repro")
        if repro.returncode != 0:
            self.record(7, "FAIL", f"`dvc repro` failed (exit {repro.returncode})")
            return
        dirty = self.run(clone, "git", "status", "--porcelain")
        changed = len(dirty.stdout.splitlines())
        if dirty.returncode != 0 or changed:
            self.record(7, "FAIL", f"after `dvc repro`, git status lists {changed} changed path(s)")
            return
        self.record(7, "PASS", "`dvc repro` done; git status clean")

    def bundle_current(self, clone: Path) -> None:
        live = self.run(self.workspace, "git", "rev-parse", "HEAD")
        restored = self.run(clone, "git", "rev-parse", "HEAD")
        if live.returncode != 0 or restored.returncode != 0:
            self.record(8, "FAIL", "a HEAD can't be read")
        elif live.stdout.strip() != restored.stdout.strip():
            self.record(8, "FAIL", "the clone's HEAD isn't the live HEAD: the last commit isn't backed up")
        else:
            self.record(8, "PASS", "the clone's HEAD is the live HEAD")

    def verify(self, clone: Path) -> None:
        p1_files = ("dvc.yaml", ".energatron/code_fingerprint.json", "data/raw")
        if not any(os.path.lexists(os.path.join(str(clone), name)) for name in p1_files):
            self.record(9, "SKIP", "no pipeline files (before P1)")
            return
        done = self.run(clone, "uv", "run", "--locked", "energatron", "verify", "--json")
        try:
            envelope = json.loads(done.stdout)
        except ValueError:
            envelope = None
        if not isinstance(envelope, dict):
            self.record(9, "FAIL", f"`energatron verify` printed no result (exit {done.returncode})")
        elif envelope.get("command") is None and not envelope.get("ok"):
            # D1 §8.5: `command` is null when the word isn't one of the app's commands
            self.record(9, "SKIP", "`energatron verify` doesn't exist yet (P1.3.4)")
        elif envelope.get("ok"):
            self.record(9, "PASS", "`energatron verify` passed")
        else:
            code = (envelope.get("error") or {}).get("code")
            self.record(9, "FAIL", f"`energatron verify` failed ({code})")

    def privacy(self, tool: Path, clone: Path) -> None:
        guard = tool / "scripts" / "privacy_guard.py"
        if not guard.is_file():
            self.record(10, "FAIL", "the tool clone has no scripts/privacy_guard.py")
            return
        args = ["--mode", "workspace", "--tracked", "--denylist-dir", str(clone / "private")]
        done = self.run(clone, sys.executable, str(guard), *args)
        listed = self.run(clone, "git", "ls-files", "-z")
        if listed.returncode == 0:
            self.counts["tracked_files"] = len([p for p in listed.stdout.split("\0") if p])
        if done.returncode == 0:
            self.record(10, "PASS", f"privacy guard clean over {self.counts['tracked_files']} tracked files")
        elif done.returncode == 1:
            self.record(10, "FAIL", "privacy guard found violations; run it in a --keep clone to see them")
        else:
            self.record(10, "FAIL", f"privacy guard setup error (exit {done.returncode})")

    def dataless(self) -> None:
        folders = [self.bundle_dir]
        if not URL_SCHEME.match(self.remote_url):
            folders.append(Path(self.remote_url))
        total = 0
        try:
            for folder in folders:
                total += count_dataless(folder)
        except OSError as exc:
            self.record(11, "FAIL", f"a backup folder can't be read ({oserror(exc)})")
            return
        self.counts["dataless_files"] = total
        if total:
            self.record(11, "FAIL", f"{total} backup file(s) only in the cloud; set Keep Downloaded on Backups")
        elif len(folders) == 1:
            self.record(11, "PASS", "no dataless bundle file (the remote isn't a local folder)")
        else:
            self.record(11, "PASS", "no dataless file in the bundle folder or the remote")

    def clean_up(self) -> None:
        if self.scratch is None:
            return
        if self.keep:
            self.record(12, "SKIP", "--keep: the scratch folder stays under $TMPDIR")
            return
        try:
            shutil.rmtree(self.scratch)
        except OSError as exc:
            self.record(12, "FAIL", f"the scratch folder can't be deleted ({oserror(exc)})")
            return
        self.record(12, "PASS", "scratch folder deleted")


def oserror(exc: OSError) -> str:
    # The strerror only; the filename may hold an identifier.
    return exc.strerror or type(exc).__name__


def bundle_dir_from(local_toml: Path, workspace: Path) -> Path:
    """`[backup] bundle_dir` from `local.toml` (D2 §6.2): `~` expanded, absolute and outside the workspace."""
    try:
        text = local_toml.read_text(encoding="utf-8")
    except OSError as exc:
        raise SetupError("no .energatron/local.toml in the workspace (backups aren't configured)") from exc
    value = read_bundle_dir(text)
    if not value:
        raise SetupError(".energatron/local.toml has no backup.bundle_dir")
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise SetupError("backup.bundle_dir isn't an absolute path")
    try:
        path.resolve().relative_to(workspace.resolve())
    except ValueError:
        return path
    raise SetupError("backup.bundle_dir is inside the workspace")


def read_bundle_dir(text: str) -> str | None:
    try:
        import tomllib  # Python 3.11+
    except ImportError:  # an older python3: read the one string this needs
        match = re.search(r'(?ms)^\[backup\][^\[]*?^bundle_dir\s*=\s*"((?:[^"\\]|\\.)*)"', text)
        return json.loads(f'"{match.group(1)}"') if match else None
    try:
        backup = tomllib.loads(text).get("backup")
    except tomllib.TOMLDecodeError as exc:
        raise SetupError(".energatron/local.toml isn't valid TOML") from exc
    value = backup.get("bundle_dir") if isinstance(backup, dict) else None
    return value if isinstance(value, str) else None


def remote_url_from(listing: str, workspace: Path) -> str:
    """The default remote's URL from `dvc config --local --list` (`key=value` lines).

    Not `dvc remote list --local`, as D2 §12 says: it prints a table that wraps a long path at the terminal width.
    """
    values = {}
    for line in listing.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            values[key.strip()] = value.strip()
    remotes = sorted(k[len("remote.") : -len(".url")] for k in values if k.startswith("remote.") and k.endswith(".url"))
    name = values.get("core.remote") or (remotes[0] if len(remotes) == 1 else None)
    url = values.get(f"remote.{name}.url") if name else None
    if not url:
        raise SetupError("the workspace has no default local DVC remote")
    if not URL_SCHEME.match(url) and not Path(url).expanduser().is_absolute():
        url = str((workspace / ".dvc" / url).resolve())  # DVC stores a relative path relative to `.dvc/`
    return url


def count_dataless(folder: Path) -> int:
    total = 0

    def fail(error: OSError) -> None:
        raise error

    for directory, _dirnames, filenames in os.walk(folder, onerror=fail):
        for name in filenames:
            flags = getattr(os.lstat(os.path.join(directory, name)), "st_flags", 0)
            if flags & SF_DATALESS:
                total += 1
    return total


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # type: ignore[override]  # exit 2 without echoing what was typed
        self.print_usage(sys.stderr)
        print("restore_drill: invalid arguments; see --help", file=sys.stderr)
        sys.exit(2)


def main(argv: list[str] | None = None) -> int:
    parser = _Parser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--workspace", type=Path, default=PARENT / WORKSPACE_NAME)
    parser.add_argument("--tool-source", choices=("origin", "local"), default="origin")
    parser.add_argument("--keep", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    drill = Drill(args.workspace.expanduser().resolve(), args.tool_source, args.keep, args.json)
    step = 1
    try:
        drill.inputs()
        step = 2
        drill.make_scratch()
    except SetupError as exc:
        drill.record(step, "FAIL", str(exc))
        return drill.finish(2, str(exc))
    try:
        drill.steps()
    except Exception as exc:  # a traceback could show paths: report the step and the type only
        done = {result["step"] for result in drill.results}
        step = min(n for n in range(3, 13) if n not in done)
        drill.record(step, "FAIL", f"unexpected {type(exc).__name__}")
        drill.not_run(step + 1, 11)  # still one line per step; step 12 (clean-up) follows
    finally:
        drill.clean_up()
    failed = any(result["status"] == "FAIL" for result in drill.results)
    return drill.finish(1 if failed else 0)


if __name__ == "__main__":
    sys.exit(main())
