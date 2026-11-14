"""Every shipped region profile is usable (P8 spec §9): it loads, its TOU windows partition the day, its zone is in
tzdata, its parsers exist, and `init --region` can render a workspace with it. A region contribution must pass these.
"""

import pytest

from energatron.adapters import wiring
from energatron.adapters.sources.registry import PARSERS
from energatron.core.workspace_template import TemplateValues, render, tool_source_line

REGIONS = wiring.region_names()


def test_sa_ships() -> None:
    assert "sa" in REGIONS


def minutes(text: str) -> int:
    hours, mins = text.split(":")
    return int(hours) * 60 + int(mins)


@pytest.mark.parametrize("name", REGIONS)
def test_the_profile_loads_and_its_windows_partition_the_day(name: str) -> None:
    profile = wiring.load_region_profile(name)  # checks the zone against tzdata and every required field
    assert profile.name == name and profile.tou_windows is not None
    covered = [0] * 1440
    for spans in profile.tou_windows.values():
        for span in spans:
            start, end = (minutes(part) for part in span.split("-"))
            for minute in range(1440):
                if (start <= minute < end) if start < end else (minute >= start or minute < end):
                    covered[minute] += 1
    assert set(covered) == {1}


@pytest.mark.parametrize("name", REGIONS)
def test_its_parsers_exist(name: str) -> None:
    profile = wiring.load_region_profile(name)
    assert {source.value for source in profile.parsers} <= {parser.source.value for parser in PARSERS}


@pytest.mark.parametrize("name", REGIONS)
def test_a_workspace_renders_with_it(name: str) -> None:
    values = TemplateValues(name="w", region=name, tool_source=tool_source_line("index"), deny_root="/w")
    files = {item.path: item.data for item in render(values, wiring.template_sources())}
    assert f'region = "{name}"'.encode() in files[".energatron/workspace.toml"]
