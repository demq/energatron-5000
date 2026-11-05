"""The parser registry and the region profile's filter (D1 §4.3; D3 §10.3, §13)."""

from pathlib import Path

import pytest

from energatron.adapters import wiring
from energatron.adapters.sources.bill_pdf import ELEC_BILL, GAS_BILL
from energatron.adapters.sources.cdr_index import OFFER_CDR_INDEX
from energatron.adapters.sources.cdr_plan import OFFER_CDR
from energatron.adapters.sources.gas_bill_record import GAS_BILL_RECORD
from energatron.adapters.sources.offer_manual import OFFER_MANUAL
from energatron.adapters.sources.registry import PARSERS, parsers_for
from energatron.adapters.sources.retailer_txn import RETAILER_TXN
from energatron.adapters.sources.retailer_usage import RETAILER_USAGE
from energatron.adapters.sources.sapn_nem12 import SAPN_INTERVAL
from energatron.adapters.sources.sapn_summary import SAPN_SUMMARY
from energatron.core.errors import InternalError
from energatron.core.models.catalog import SourceInfo, SourceType
from energatron.core.models.region import RegionProfile
from tests.unit.adapters._workspace import make_workspace


def profile(*sources: SourceType) -> RegionProfile:
    return RegionProfile(
        name="sa",
        local_tz=None,
        tou_windows=None,
        distributor=None,
        cdr_retailers=None,
        gas_distributor=None,
        parsers=sources,
    )


def test_one_parser_per_source_and_each_source_is_a_source_type() -> None:
    sources = [parser.source for parser in PARSERS]
    assert len(sources) == len(set(sources))
    assert all(isinstance(source, SourceType) for source in sources)
    assert all(isinstance(parser.version, int) and parser.version >= 1 for parser in PARSERS)


ALL = (
    SAPN_INTERVAL,
    SAPN_SUMMARY,
    RETAILER_USAGE,
    RETAILER_TXN,
    ELEC_BILL,
    GAS_BILL,
    GAS_BILL_RECORD,
    OFFER_CDR,
    OFFER_CDR_INDEX,
    OFFER_MANUAL,
)
P1_SOURCES = {
    SourceType.ELEC_BILL,
    SourceType.GAS_BILL,
    SourceType.OFFER_CDR,
    SourceType.RETAILER_TXN,
    SourceType.RETAILER_USAGE,
    SourceType.SAPN_INTERVAL,
    SourceType.SAPN_SUMMARY,
}
REGISTERED_SOURCES = P1_SOURCES | {SourceType.OFFER_CDR_INDEX, SourceType.OFFER_MANUAL, SourceType.GAS_BILL_RECORD}


def test_the_seven_p1_two_p4_and_one_p5_parsers_are_registered() -> None:
    assert PARSERS == ALL
    assert {parser.source for parser in PARSERS} == REGISTERED_SOURCES


def test_every_source_type_has_a_parser() -> None:
    assert set(SourceType) == {parser.source for parser in PARSERS}  # P5 gave gas_bill_record the last one


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
        parsers_for(profile(SourceType.GAS_BILL_RECORD), parsers=(SAPN_INTERVAL,))


def test_the_sa_profile_is_valid_and_lists_only_parsers_that_exist() -> None:
    sa = wiring.load_region_profile("sa")
    assert sa.name == "sa"
    assert set(sa.parsers) == REGISTERED_SOURCES
    assert [source.value for source in sa.parsers] == sorted(source.value for source in sa.parsers)


def test_build_context_filters_parsers_by_the_region_profile(tmp_path: Path) -> None:
    assert tuple(wiring.build_context(make_workspace(tmp_path)).parsers) == ALL
