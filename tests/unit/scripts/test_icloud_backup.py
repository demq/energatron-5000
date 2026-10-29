"""`scripts/icloud_backup.py` (D2 §3, §4, §14) against scratch `--backups-dir` and `--workspace` folders.

Nothing here touches iCloud Drive or the real workspace. Most tests run the script as a subprocess, as the owner
does. The fault-injection tests load it as a module and run `main()` in process. `backup` runs against a fake `uv`
first on `PATH` that prints a canned envelope. `drill` runs a copy of the script inside a scratch tool folder whose
`restore_drill.py` is a stand-in that records its arguments, so the real drill never runs.
"""

import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "icloud_backup.py"
STEP0 = f"demq-elektric-step0-{date.today().isoformat()}"
# Synthetic source file names; no output line may hold one (D2 §4: the real names start with the NMI).
LANDING_NAMES = ("synthname-alpha_reading.csv", "synthname-beta_summary.csv")
LEGACY_NAMES = ("synthname-gamma_bill.pdf", "nested/synthname-delta_usage.json")
ALL_NAMES = ("synthname-alpha", "synthname-beta", "synthname-gamma", "synthname-delta", "nested")


@dataclass
class Ran:
    code: int
    out: str

    @property
    def lines(self) -> list[str]:
        return self.out.splitlines()

    def no_source_names(self) -> bool:
        return not any(name in self.out for name in ALL_NAMES)


@dataclass
class Folders:
    root: Path
    backups: Path
    workspace: Path

    def args(self, *command: str) -> list[str]:
        return ["--backups-dir", str(self.backups), "--workspace", str(self.workspace), *command]


def run(script: Path, *args: str, env: dict[str, str] | None = None, cwd: Path | None = None) -> Ran:
    done = subprocess.run(
        [sys.executable, str(script), *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=cwd,
    )
    return Ran(done.returncode, done.stdout + done.stderr)


def tree(root: Path) -> list[str]:
    """Every path under root, relative and sorted, so a test can show that nothing was written."""
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*"))


@pytest.fixture
def folders(tmp_path: Path) -> Folders:
    """A scratch parent holding `Backups/` (not created yet) and a workspace with landing and legacy files."""
    root = tmp_path / "cloud"
    root.mkdir()
    workspace = tmp_path / "ws"
    for index, name in enumerate(LANDING_NAMES):
        path = workspace / "data" / "landing" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"synthetic landing {index}\n", encoding="utf-8")
    for index, name in enumerate(LEGACY_NAMES):
        path = workspace / "legacy" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"synthetic legacy {index}\n".encode())
    (workspace / "legacy" / ".DS_Store").write_bytes(b"finder\n")
    (workspace / "data" / "landing" / ".DS_Store").write_bytes(b"finder\n")
    return Folders(root, root / "Backups", workspace)


def set_up_workspace(folders: Folders) -> None:
    (folders.workspace / ".energatron").mkdir(exist_ok=True)
    (folders.workspace / ".energatron" / "workspace.toml").write_text(
        'kind = "workspace_config"\nschema_version = 1\nregion = "sa"\n', encoding="utf-8"
    )


@contextmanager
def unreadable(path: Path) -> Iterator[None]:
    mode = path.stat().st_mode
    path.chmod(0)
    try:
        yield
    finally:
        path.chmod(mode)


def load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("icloud_backup_under_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_main(module: ModuleType, argv: list[str], capsys: pytest.CaptureFixture[str]) -> Ran:
    code = module.main(argv)
    captured = capsys.readouterr()
    return Ran(code, captured.out + captured.err)


# ---------------------------------------------------------------------------------------------------------- check


def test_check_without_backups_folder(folders: Folders) -> None:
    ran = run(SCRIPT, *folders.args("check"))
    assert ran.code == 0
    assert ran.lines[0].startswith("OK")
    assert any(line.startswith("WARN") and "doesn't exist yet" in line for line in ran.lines)
    assert not folders.backups.exists()
    assert tree(folders.root) == []  # the write test leaves nothing behind


def test_check_with_a_backup_folder(folders: Folders) -> None:
    folder = folders.backups / "demq-elektric-step0-2026-01-01"
    folder.mkdir(parents=True)
    (folder / "a.bin").write_bytes(b"x")
    (folder / "b.bin").write_bytes(b"y")
    ran = run(SCRIPT, *folders.args("check"))
    assert ran.code == 0
    assert "OK    demq-elektric-step0-2026-01-01: 2 files, all downloaded" in ran.lines


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root ignores file modes")
def test_check_with_an_unreadable_parent_exits_2(folders: Folders) -> None:
    with unreadable(folders.root):
        ran = run(SCRIPT, *folders.args("check"))
    assert ran.code == 2
    assert ran.lines[-1].startswith("FAIL")
    assert "can't use iCloud Drive" in ran.out
    assert str(folders.root) not in ran.out


class FakeStat:
    """An `os.lstat` result with `st_flags` replaced, or removed as on Linux."""

    def __init__(self, real: os.stat_result, flags: int | None) -> None:
        self._real = real
        self._flags = flags

    def __getattr__(self, name: str) -> Any:
        if name == "st_flags":
            if self._flags is None:
                raise AttributeError(name)
            return self._flags
        return getattr(self._real, name)


def fake_lstat(target: str | None, flags: int | None) -> Callable[..., Any]:
    real_lstat = os.lstat

    def lstat(path: Any, *args: Any, **kwargs: Any) -> Any:
        result = real_lstat(path, *args, **kwargs)
        if target is None or os.fspath(path).endswith(target):
            return FakeStat(result, flags)
        return result

    return lstat


def test_check_with_an_injected_dataless_file_exits_1(
    folders: Folders, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = folders.backups / "demq-elektric-git"
    folder.mkdir(parents=True)
    (folder / "one.bundle").write_bytes(b"x")
    (folder / "two.bundle").write_bytes(b"y")
    module = load_module()
    monkeypatch.setattr(module.os, "lstat", fake_lstat("two.bundle", stat.SF_DATALESS))
    ran = run_main(module, folders.args("check"), capsys)
    assert ran.code == 1
    assert "FAIL  demq-elektric-git: 2 files, 1 only in the cloud (Keep Downloaded is off)" in ran.lines


def test_check_without_st_flags_counts_nothing_dataless(
    folders: Folders, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = folders.backups / "demq-elektric-dvc"
    folder.mkdir(parents=True)
    (folder / "object").write_bytes(b"x")
    module = load_module()
    monkeypatch.setattr(module.os, "lstat", fake_lstat(None, None))  # Linux: no st_flags at all
    ran = run_main(module, folders.args("check"), capsys)
    assert ran.code == 0
    assert "OK    demq-elektric-dvc: 1 files, all downloaded" in ran.lines


# ---------------------------------------------------------------------------------------------------------- step0


def test_step0_copies_verifies_and_renames(folders: Folders) -> None:
    ran = run(SCRIPT, *folders.args("step0"))
    assert ran.code == 0, ran.out
    dest = folders.backups / STEP0
    assert sorted(p.name for p in folders.backups.iterdir()) == [STEP0]
    assert f"OK    copy verified: {STEP0}" in ran.lines
    assert any(line.startswith("OK    copied 4 files") for line in ran.lines)
    for source, names in (("data/landing", LANDING_NAMES), ("legacy", LEGACY_NAMES)):
        for name in names:
            copied = dest / source / name
            assert copied.read_bytes() == (folders.workspace / source / name).read_bytes()
    assert not any(p.name == ".DS_Store" for p in dest.rglob("*"))
    sums = (dest / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
    assert len(sums) == 4
    assert not any(".DS_Store" in line for line in sums)
    assert ran.no_source_names()


@pytest.mark.skipif(shutil.which("shasum") is None, reason="shasum isn't installed")
def test_step0_manifest_passes_shasum(folders: Folders) -> None:
    assert run(SCRIPT, *folders.args("step0")).code == 0
    checked = subprocess.run(
        ["shasum", "-a", "256", "-c", "--quiet", "SHA256SUMS"],
        cwd=folders.backups / STEP0,
        capture_output=True,
        text=True,
    )
    assert checked.returncode == 0
    assert checked.stdout == ""


def test_step0_second_run_exits_2_and_writes_nothing(folders: Folders) -> None:
    assert run(SCRIPT, *folders.args("step0")).code == 0
    before = tree(folders.root)
    ran = run(SCRIPT, *folders.args("step0"))
    assert ran.code == 2
    assert "already exists; nothing was written" in ran.out
    assert tree(folders.root) == before


def test_step0_leftover_partial_exits_2_and_writes_nothing(folders: Folders) -> None:
    (folders.backups / f"{STEP0}.partial").mkdir(parents=True)
    before = tree(folders.root)
    ran = run(SCRIPT, *folders.args("step0"))
    assert ran.code == 2
    assert "left over from an interrupted run" in ran.out
    assert tree(folders.root) == before


def test_step0_symlink_in_a_source_exits_2_and_writes_nothing(folders: Folders) -> None:
    os.symlink(LANDING_NAMES[0], folders.workspace / "data" / "landing" / "synthname-link")
    ran = run(SCRIPT, *folders.args("step0"))
    assert ran.code == 2
    assert "1 symlink(s) found" in ran.out
    assert tree(folders.root) == []
    assert ran.no_source_names()


def test_step0_copy_fault_exits_1_and_leaves_partial(
    folders: Folders, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    module = load_module()
    real_copy2 = shutil.copy2
    copied: list[str] = []

    def failing_copy(source: str, target: str, **kwargs: Any) -> Any:
        if copied:
            raise OSError(5, "Input/output error", target)  # the filename must not reach the output
        copied.append(source)
        return real_copy2(source, target, **kwargs)

    real_copytree = shutil.copytree

    def copytree(*args: Any, **kwargs: Any) -> Any:
        return real_copytree(*args, **{**kwargs, "copy_function": failing_copy})

    monkeypatch.setattr(module.shutil, "copytree", copytree)
    ran = run_main(module, folders.args("step0"), capsys)
    assert ran.code == 1
    # copytree gathers per-file errors into one `shutil.Error`, which has no strerror: only its class name shows
    assert ran.lines[0] == "FAIL  copy failed: Error"
    assert sorted(p.name for p in folders.backups.iterdir()) == [f"{STEP0}.partial"]
    assert ran.no_source_names()


# --------------------------------------------------------------------------------------------------------- backup


def fake_uv(bin_dir: Path, stdout: str, code: int = 0) -> None:
    bin_dir.mkdir(exist_ok=True)
    uv = bin_dir / "uv"
    uv.write_text(
        f"#!{sys.executable}\nimport sys\nsys.stdout.write({stdout!r})\nsys.exit({code})\n",
        encoding="utf-8",
    )
    uv.chmod(0o755)


def env_with(bin_dir: Path) -> dict[str, str]:
    return {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"}


def envelope(*, ok: bool, report: dict[str, Any] | None = None, error: dict[str, Any] | None = None) -> str:
    return json.dumps(
        {
            "kind": "cli_result",
            "schema_version": 1,
            "command": "backup",
            "ok": ok,
            "exit_code": 0 if ok else 5,
            "data": {"report": report} if ok else None,
            "warnings": [{"code": "W_BACKUP_DATALESS", "message": "", "details": {}}] if ok else [],
            "error": error,
        }
    )


REPORT = {
    "dvc_files_pushed": 3,
    "bundle_created": True,
    "bundle_name": "20261023T093000Z-1a2b3c4.bundle",
    "bundles_kept": 2,
    "bundles_removed": 1,
    "dataless_files": 0,
}


def test_backup_success(folders: Folders, tmp_path: Path) -> None:
    set_up_workspace(folders)
    fake_uv(tmp_path / "bin", envelope(ok=True, report=REPORT))
    ran = run(SCRIPT, *folders.args("backup"), env=env_with(tmp_path / "bin"))
    assert ran.code == 0, ran.out
    assert "OK    DVC files pushed: 3" in ran.lines
    assert "OK    new bundle written and verified (2 kept, 1 removed)" in ran.lines
    assert "WARN  W_BACKUP_DATALESS" in ran.lines
    assert REPORT["bundle_name"] not in ran.out


@pytest.mark.parametrize(
    ("category", "expected"),
    [("state", 2), ("usage", 2), ("external", 1)],
)
def test_backup_error_categories(folders: Folders, tmp_path: Path, category: str, expected: int) -> None:
    set_up_workspace(folders)
    error = {"code": "E_SYNTHETIC", "category": category, "message": "it failed", "details": {}}
    fake_uv(tmp_path / "bin", envelope(ok=False, error=error), code=5)
    ran = run(SCRIPT, *folders.args("backup"), env=env_with(tmp_path / "bin"))
    assert ran.code == expected
    assert "FAIL  E_SYNTHETIC: it failed" in ran.lines


@pytest.mark.parametrize("stdout", ["", "Traceback: not JSON\n", "[1, 2]", '"text"'])
def test_backup_without_an_envelope_exits_1(folders: Folders, tmp_path: Path, stdout: str) -> None:
    set_up_workspace(folders)
    fake_uv(tmp_path / "bin", stdout, code=1)
    ran = run(SCRIPT, *folders.args("backup"), env=env_with(tmp_path / "bin"))
    assert ran.code == 1
    assert ran.lines[-1].startswith("FAIL  `energatron backup` printed no result")


def test_backup_before_the_workspace_is_set_up_exits_2(folders: Folders, tmp_path: Path) -> None:
    fake_uv(tmp_path / "bin", envelope(ok=True, report=REPORT))
    ran = run(SCRIPT, *folders.args("backup"), env=env_with(tmp_path / "bin"))
    assert ran.code == 2
    assert "isn't set up yet" in ran.out


# ---------------------------------------------------------------------------------------------------------- drill

RECORDER = """\
import json, os, sys
with open(os.environ["DRILL_RECORD"], "w", encoding="utf-8") as f:
    json.dump({"argv": sys.argv[1:], "cwd": os.getcwd()}, f)
sys.exit(int(os.environ.get("DRILL_EXIT", "0")))
"""


@pytest.fixture
def tool_copy(tmp_path: Path) -> Path:
    """A scratch parent folder with `tool/scripts/icloud_backup.py`, a copy of the script under test."""
    scripts = tmp_path / "parent" / "tool" / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(SCRIPT, scripts / "icloud_backup.py")
    return scripts / "icloud_backup.py"


def test_drill_before_restore_drill_exists_exits_2(folders: Folders, tool_copy: Path, tmp_path: Path) -> None:
    set_up_workspace(folders)
    fake_uv(tmp_path / "bin", "")
    ran = run(tool_copy, *folders.args("drill"), env=env_with(tmp_path / "bin"))
    assert ran.code == 2
    assert "restore_drill.py doesn't exist yet" in ran.out


def test_drill_passes_its_arguments_on(folders: Folders, tool_copy: Path, tmp_path: Path) -> None:
    set_up_workspace(folders)
    (tool_copy.parent / "restore_drill.py").write_text(RECORDER, encoding="utf-8")
    fake_uv(tmp_path / "bin", "")
    record = tmp_path / "record.json"
    env = {**env_with(tmp_path / "bin"), "DRILL_RECORD": str(record), "DRILL_EXIT": "1"}
    ran = run(tool_copy, *folders.args("drill", "--", "--tool-source", "local", "--json"), env=env)
    assert ran.code == 1  # the drill's own exit code
    recorded = json.loads(record.read_text(encoding="utf-8"))
    assert recorded["argv"] == ["--workspace", str(folders.workspace.resolve()), "--tool-source", "local", "--json"]
    assert Path(recorded["cwd"]).resolve() == (tmp_path / "parent").resolve()


def test_backup_drill_runs_the_drill_after_the_backup(folders: Folders, tool_copy: Path, tmp_path: Path) -> None:
    set_up_workspace(folders)
    (tool_copy.parent / "restore_drill.py").write_text(RECORDER, encoding="utf-8")
    fake_uv(tmp_path / "bin", envelope(ok=True, report={**REPORT, "bundle_created": False}))
    record = tmp_path / "record.json"
    env = {**env_with(tmp_path / "bin"), "DRILL_RECORD": str(record)}
    ran = run(tool_copy, *folders.args("backup", "--drill"), env=env)
    assert ran.code == 0, ran.out
    assert "OK    no new commits; bundle unchanged (2 kept, 1 removed)" in ran.lines
    assert json.loads(record.read_text(encoding="utf-8"))["argv"] == ["--workspace", str(folders.workspace.resolve())]
