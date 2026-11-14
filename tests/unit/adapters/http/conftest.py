"""Fixtures for the HTTP tests: `schedule/status` answers the same on any host (CI runs on Linux)."""

import pytest

from tests.unit.adapters._launchd import FakeLaunchctl


@pytest.fixture(autouse=True)
def _macos_launchd(fake_agent: FakeLaunchctl) -> FakeLaunchctl:
    return fake_agent
