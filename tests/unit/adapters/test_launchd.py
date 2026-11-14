"""`adapters/schedule/launchd.py` and `energatron schedule` (P7 spec §8) against a fake `launchctl` and a scratch home.

`FakeLaunchctl` (in `_launchd.py`) and the `fake_agent` fixture (in `conftest.py`) keep everything off the real
`~/Library/LaunchAgents` and launchd.
"""

import plistlib
from pathlib import Path

import pytest

from energatron.adapters.schedule.launchd import Launchd
from energatron.core.errors import SubprocessError, UsageError
from tests.unit.adapters._cli import FakeVcsState, run_cli
from tests.unit.adapters._launchd import FakeLaunchctl
from tests.unit.adapters._workspace import make_workspace


def test_install_writes_the_plist_and_loads_it_and_reinstall_replaces_it(tmp_path: Path) -> None:
    fake = FakeLaunchctl()
    agent = Launchd(home=tmp_path, uid=501, platform="darwin", run=fake)
    agent.install("org.energatron.watch.abcd1234", b"<plist/>")
    state = agent.state("org.energatron.watch.abcd1234")
    assert (state.installed, state.loaded, state.plist) == (True, True, b"<plist/>")
    assert agent.plist_path("x") == tmp_path / "Library" / "LaunchAgents" / "x.plist"
    agent.install("org.energatron.watch.abcd1234", b"<plist2/>")
    assert agent.state("org.energatron.watch.abcd1234").plist == b"<plist2/>"
    assert [call[0] for call in fake.calls if call[0] != "print"] == ["bootout", "bootstrap", "bootout", "bootstrap"]
    assert ["bootstrap", "gui/501", str(agent.plist_path("org.energatron.watch.abcd1234"))] in fake.calls


def test_remove_unloads_and_deletes_and_is_fine_when_nothing_is_installed(tmp_path: Path) -> None:
    agent = Launchd(home=tmp_path, uid=501, platform="darwin", run=FakeLaunchctl())
    agent.remove("l")  # nothing installed
    agent.install("l", b"<plist/>")
    agent.remove("l")
    state = agent.state("l")
    assert (state.installed, state.loaded) == (False, False)


def test_a_failed_bootstrap_is_a_subprocess_error(tmp_path: Path) -> None:
    agent = Launchd(home=tmp_path, uid=501, platform="darwin", run=FakeLaunchctl(fail_bootstrap=True))
    with pytest.raises(SubprocessError):
        agent.install("l", b"<plist/>")


def test_other_platforms_are_refused_and_paths_hide_the_home(tmp_path: Path) -> None:
    with pytest.raises(UsageError):
        Launchd(home=tmp_path, uid=1, platform="linux").check_platform()
    agent = Launchd(home=tmp_path, uid=1, platform="darwin")
    assert agent.shown(agent.plist_path("l")) == "~/Library/LaunchAgents/l.plist"
    assert agent.shown_text(f"<string>{tmp_path}/ws</string>") == "<string>~/ws</string>"


def test_the_cli_installs_reports_and_removes(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, fake_vcs: FakeVcsState, fake_agent: FakeLaunchctl
) -> None:
    paths = make_workspace(tmp_path)
    where = ("--json", "--workspace", str(paths.root))
    dry = run_cli(capsys, "schedule", "install", "--dry-run", *where).envelope()["data"]
    assert (dry["dry_run"], dry["installed"], dry["loaded"]) == (True, False, False)
    assert not paths.logs_dir.exists()
    installed = run_cli(capsys, "schedule", "install", "--weekday", "3", "--hour", "7", *where).envelope()["data"]
    assert (installed["installed"], installed["loaded"], installed["weekday"], installed["hour"]) == (True, True, 3, 7)
    assert installed["plist"].startswith("~/Library/LaunchAgents/org.energatron.watch.")
    assert installed["log"] == ".energatron/logs/watch.log" and paths.logs_dir.is_dir()
    plist = plistlib.loads((tmp_path / "home" / installed["plist"][2:]).read_bytes())
    assert plist["WorkingDirectory"] == str(paths.root)
    status = run_cli(capsys, "schedule", "status", *where).envelope()["data"]
    assert (status["action"], status["loaded"], status["minute"]) == ("status", True, 0)
    removed = run_cli(capsys, "schedule", "remove", *where).envelope()["data"]
    assert (removed["installed"], removed["loaded"], removed["weekday"]) == (False, False, 3)


def test_the_cli_refuses_a_bad_hour(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, fake_vcs: FakeVcsState, fake_agent: FakeLaunchctl
) -> None:
    paths = make_workspace(tmp_path)
    ran = run_cli(capsys, "schedule", "install", "--hour", "24", "--json", "--workspace", str(paths.root))
    assert (ran.code, ran.envelope()["error"]["code"]) == (2, "E_USAGE")
    assert fake_agent.calls == []
