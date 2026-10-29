"""The parser registry and the region profile's filter (D1 §4.3; D3 §10.3, §13)."""

from pathlib import Path

import pytest

from energatron.adapters import wiring
from energatron.adapters.sources.registry import PARSERS, parsers_for
from energatron.adapters.sources.sapn_nem12 import SAPN_INTERVAL
from energatron.core.errors import InternalError
from energatron.core.models.catalog import SourceInfo, SourceType
from energatron.core.models.region import RegionProfile
from tests.unit.adapters._workspace import make_workspace


def profile(*sources: SourceType) -> RegionProfile:
    return RegionProfile(name="sa", parsers=sources)


def test_one_parser_per_source_and_each_source_is_a_source_type() -> None:
    sources = [parser.source for parser in PARSERS]
    assert len(sources) == len(set(sources))
    assert all(isinstance(source, SourceType) for source in sources)
    assert all(isinstance(parser.version, int) and parser.version >= 1 for parser in PARSERS)


def test_sapn_interval_is_registered() -> None:
    assert SAPN_INTERVAL in PARSERS


def test_the_profile_keeps_the_listed_parsers_in_registry_order() -> None:
    assert parsers_for(profile(SourceType.SAPN_INTERVAL)) == (SAPN_INTERVAL,)
    assert parsers_for(profile()) == ()


class StandIn:
    """A stand-in parser: only `source` and `version` matter to the registry."""

    def __init__(self, source: SourceType) -> None:
        self.source = source
        self.version = 1

    def detect(self, filename: str, head: bytes) -> None:
        return None

    def inspect(self, filename: str, data: bytes) -> SourceInfo:
        raise NotImplementedError


def test_the_registry_order_wins_over_the_profile_order() -> None:
    first, second, third = StandIn(SourceType.SAPN_SUMMARY), StandIn(SourceType.ELEC_BILL), StandIn(SourceType.GAS_BILL)
    registry = (first, second, third)
    forward = parsers_for(profile(SourceType.SAPN_SUMMARY, SourceType.GAS_BILL), parsers=registry)
    backward = parsers_for(profile(SourceType.GAS_BILL, SourceType.SAPN_SUMMARY), parsers=registry)
    assert forward == backward == (first, third)


def test_a_listed_source_without_a_parser_is_internal() -> None:
    with pytest.raises(InternalError):
        parsers_for(profile(SourceType.OFFER_MANUAL))


def test_the_sa_profile_is_valid_and_lists_only_parsers_that_exist() -> None:
    sa = wiring.load_region_profile("sa")
    assert sa.name == "sa"
    assert sa.parsers == (SourceType.SAPN_INTERVAL,)


def test_build_context_filters_parsers_by_the_region_profile(tmp_path: Path) -> None:
    assert tuple(wiring.build_context(make_workspace(tmp_path)).parsers) == (SAPN_INTERVAL,)
