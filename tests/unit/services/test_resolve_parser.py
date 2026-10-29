"""`services.land.resolve_parser`: each D3 §5.1 rule, with stand-in parsers (the real PDF parsers come in P1.5.1).

The three-way PDF tie is three stand-ins that all accept any content, as the two bill parsers and the SAPN summary
parser will all accept a bare PDF.
"""

from dataclasses import dataclass

import pytest

from energatron.core.errors import AmbiguousSourceError, InvalidInputError, UnknownSourceError, UsageError
from energatron.core.models.catalog import Detection, Evidence, SourceInfo, SourceType
from energatron.ports.sources import SourceParser
from energatron.services.land import resolve_parser


@dataclass
class StandIn:
    """A parser whose `detect` gives fixed evidence: `None` when `evidence` is empty."""

    source: SourceType
    evidence: tuple[Evidence, ...]
    version: int = 1

    def detect(self, filename: str, head: bytes) -> Detection | None:
        return Detection(source=self.source, evidence=self.evidence) if self.evidence else None

    def inspect(self, filename: str, data: bytes) -> SourceInfo:
        raise AssertionError("resolve_parser never inspects")


def parsers(*stand_ins: StandIn) -> tuple[SourceParser, ...]:
    return stand_ins


ELEC = SourceType.ELEC_BILL
GAS = SourceType.GAS_BILL
SUMMARY = SourceType.SAPN_SUMMARY
INTERVAL = SourceType.SAPN_INTERVAL


def test_the_one_parser_with_content_evidence_wins() -> None:
    chosen, evidence = resolve_parser(parsers(StandIn(INTERVAL, ("content",)), StandIn(SUMMARY, ())), "x.csv", b"head")
    assert chosen.source == INTERVAL
    assert evidence == ("content",)


def test_filename_and_content_rank_above_content_alone() -> None:
    chosen, evidence = resolve_parser(
        parsers(StandIn(ELEC, ("content",)), StandIn(SUMMARY, ("filename", "content"))), "x.pdf", b"%PDF"
    )
    assert chosen.source == SUMMARY
    assert evidence == ("filename", "content")


def test_filename_only_ranks_below_content() -> None:
    chosen, _ = resolve_parser(parsers(StandIn(INTERVAL, ("filename",)), StandIn(ELEC, ("content",))), "x", b"")
    assert chosen.source == ELEC


def test_the_three_way_pdf_tie_is_ambiguous_with_sorted_candidates() -> None:
    stand_ins = parsers(StandIn(SUMMARY, ("content",)), StandIn(GAS, ("content",)), StandIn(ELEC, ("content",)))
    with pytest.raises(AmbiguousSourceError) as caught:
        resolve_parser(stand_ins, "scan.pdf", b"%PDF-1.7")
    assert caught.value.details["candidates"] == ["elec_bill", "gas_bill", "sapn_summary"]


def test_a_tie_below_a_single_top_rank_is_not_ambiguous() -> None:
    stand_ins = parsers(
        StandIn(GAS, ("content",)), StandIn(ELEC, ("content",)), StandIn(SUMMARY, ("filename", "content"))
    )
    assert resolve_parser(stand_ins, "x.pdf", b"")[0].source == SUMMARY


def test_filename_only_matches_are_a_content_mismatch_naming_the_source() -> None:
    with pytest.raises(InvalidInputError) as caught:
        resolve_parser(parsers(StandIn(INTERVAL, ("filename",)), StandIn(ELEC, ())), "x.csv", b"junk")
    assert caught.value.details["reason"] == "content_mismatch"
    assert caught.value.details["source"] == "sapn_interval"


def test_no_detection_at_all_is_an_unknown_source() -> None:
    with pytest.raises(UnknownSourceError):
        resolve_parser(parsers(StandIn(INTERVAL, ()), StandIn(ELEC, ())), "notes.txt", b"hello")


def test_source_option_uses_that_parser_and_records_option_evidence() -> None:
    stand_ins = parsers(StandIn(SUMMARY, ("content",)), StandIn(GAS, ("content",)), StandIn(ELEC, ("content",)))
    chosen, evidence = resolve_parser(stand_ins, "scan.pdf", b"%PDF", source=GAS)
    assert chosen.source == GAS
    assert evidence == ("option",)


def test_source_option_without_content_evidence_is_a_content_mismatch() -> None:
    cases: tuple[tuple[Evidence, ...], ...] = ((), ("filename",))
    for evidence in cases:
        with pytest.raises(InvalidInputError) as caught:
            resolve_parser(parsers(StandIn(INTERVAL, evidence)), "x.csv", b"", source=INTERVAL)
        assert caught.value.details["reason"] == "content_mismatch"


def test_source_option_for_a_type_with_no_parser_is_a_usage_error() -> None:
    for source in (SourceType.GAS_BILL_RECORD, SourceType.OFFER_MANUAL, SourceType.OFFER_CDR_INDEX, ELEC):
        with pytest.raises(UsageError) as caught:
            resolve_parser(parsers(StandIn(INTERVAL, ("content",))), "x.csv", b"", source=source)
        assert source.value in caught.value.message
