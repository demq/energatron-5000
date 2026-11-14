"""Fixtures for the adapter and CLI tests: a clean environment, and fake `VersionControl` and launchd wiring."""

from pathlib import Path

import pytest

from energatron.adapters import wiring
from energatron.adapters.schedule.launchd import Launchd
from tests.unit.adapters._cli import FakeVcs, FakeVcsState
from tests.unit.adapters._launchd import FakeLaunchctl


@pytest.fixture
def fake_vcs(monkeypatch: pytest.MonkeyPatch) -> FakeVcsState:
    """Make the wiring build `FakeVcs`; returns the state its instances share."""
    state = FakeVcsState()
    monkeypatch.setattr(FakeVcs, "state", state)
    monkeypatch.setattr(wiring, "GitDvcVersionControl", FakeVcs)
    return state


@pytest.fixture
def fake_agent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeLaunchctl:
    """Make the wiring build a macOS `Launchd` on a scratch home and a fake `launchctl`, whatever the host."""
    fake = FakeLaunchctl()
    home = tmp_path / "home"
    monkeypatch.setattr(wiring, "launchd", lambda: Launchd(home=home, uid=501, platform="darwin", run=fake))
    return fake


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """No inherited ENERGATRON_* settings, and a current directory that is no workspace."""
    for name in ("ENERGATRON_JSON", "ENERGATRON_WORKSPACE"):
        monkeypatch.delenv(name, raising=False)
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.chdir(outside)
