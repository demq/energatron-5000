"""`services.land.resolve_parser`: each D3 §5.1 rule, with stand-in parsers, then with the real registry.

The three-way PDF tie is tested twice: with three stand-ins that all accept any content, and with the real bill and
SAPN summary parsers, which all accept a bare PDF.
"""

from dataclasses import dataclass
from datetime import date, datetime

import pytest

from energatron.adapters.sources.registry import PARSERS
from energatron.core.errors import AmbiguousSourceError, InvalidInputError, UnknownSourceError, UsageError
from energatron.core.models.catalog import Detection, Evidence, SourceInfo, SourceType
from energatron.ports.sources import SourceParser
from energatron.services.land import resolve_parser
from tests.fixtures.make_nem12 import Nem12Spec, make_nem12, sapn_filename
from tests.fixtures.make_sources import (
    SummaryRow,
    make_cdr_plan_detail,
    make_pdf,
    make_retailer_txn_csv,
    make_retailer_usage_csv,
    make_sapn_summary_csv,
)
from tests.fixtures.synthetic import SYNTHETIC_NMI


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


# --- with the real parsers (P1.5.1) ---------------------------------------------------------------------------------

WINDOW = (date(2025, 1, 1), date(2025, 2, 1))
GENERATED = datetime(2025, 2, 1, 9, 0, 0)


def test_the_real_three_way_pdf_tie_is_ambiguous() -> None:
    with pytest.raises(AmbiguousSourceError) as caught:
        resolve_parser(PARSERS, "scan.pdf", make_pdf(creation_date="D:2025"))
    assert caught.value.details["candidates"] == ["elec_bill", "gas_bill", "sapn_summary"]


def test_a_sapn_named_pdf_resolves_to_the_summary() -> None:
    name = sapn_filename("SUMMARY", SYNTHETIC_NMI, WINDOW, GENERATED, ext="pdf")
    chosen, evidence = resolve_parser(PARSERS, name, make_pdf(creation_date="D:2025"))
    assert chosen.source == SUMMARY and evidence == ("filename", "content")


def test_a_bill_needs_source_and_then_resolves() -> None:
    for source in (ELEC, GAS):
        chosen, evidence = resolve_parser(PARSERS, "scan.pdf", make_pdf(creation_date="D:2025"), source=source)
        assert chosen.source == source and evidence == ("option",)


def test_each_generated_source_resolves_to_its_own_parser() -> None:
    cases: tuple[tuple[str, bytes, SourceType], ...] = (
        (
            sapn_filename("DETAILED", SYNTHETIC_NMI, WINDOW, GENERATED),
            make_nem12(Nem12Spec(start=date(2025, 1, 1), days=2)),
            INTERVAL,
        ),
        (
            sapn_filename("SUMMARY", SYNTHETIC_NMI, WINDOW, GENERATED),
            make_sapn_summary_csv((SummaryRow(date(2025, 1, 1), date(2025, 1, 31)),)),
            SUMMARY,
        ),
        ("usage_all.csv", make_retailer_usage_csv(2), SourceType.RETAILER_USAGE),
        (
            "transactions_all.csv",
            make_retailer_txn_csv((("Payment - x", datetime(2025, 1, 1), "1"),)),
            SourceType.RETAILER_TXN,
        ),
        ("plan.json", make_cdr_plan_detail(), SourceType.OFFER_CDR),
    )
    for name, data, source in cases:
        for filename in (name, "renamed.dat"):
            chosen, _ = resolve_parser(PARSERS, filename, data[: 64 * 1024])
            assert chosen.source == source


def test_a_real_parser_given_by_source_with_the_wrong_content_is_a_mismatch() -> None:
    with pytest.raises(InvalidInputError) as caught:
        resolve_parser(PARSERS, "plan.json", make_cdr_plan_detail(), source=ELEC)
    assert caught.value.details["reason"] == "content_mismatch"


def test_source_option_for_a_type_the_registry_has_no_parser_for_is_a_usage_error() -> None:
    registry = tuple(parser for parser in PARSERS if parser.source != SourceType.GAS_BILL_RECORD)  # P5 has them all
    with pytest.raises(UsageError):
        resolve_parser(registry, "x.csv", b"", source=SourceType.GAS_BILL_RECORD)
