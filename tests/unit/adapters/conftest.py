"""Fixtures for the adapter and CLI tests: a clean environment, and a fake `VersionControl` behind the wiring."""

from pathlib import Path

import pytest

from energatron.adapters import wiring
from tests.unit.adapters._cli import FakeVcs, FakeVcsState


@pytest.fixture
def fake_vcs(monkeypatch: pytest.MonkeyPatch) -> FakeVcsState:
    """Make the wiring build `FakeVcs`; returns the state its instances share."""
    state = FakeVcsState()
    monkeypatch.setattr(FakeVcs, "state", state)
    monkeypatch.setattr(wiring, "GitDvcVersionControl", FakeVcs)
    return state


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """No inherited ENERGATRON_* settings, and a current directory that is no workspace."""
    for name in ("ENERGATRON_JSON", "ENERGATRON_WORKSPACE"):
        monkeypatch.delenv(name, raising=False)
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.chdir(outside)
