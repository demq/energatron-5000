"""`gas_bill_record` (P5 spec §3.2): the YAML parser's detect and inspect. Synthetic records only."""

from datetime import UTC, date, datetime

import pytest

from energatron.adapters.sources.gas_bill_record import GAS_BILL_RECORD
from energatron.adapters.sources.offer_manual import OFFER_MANUAL
from energatron.adapters.sources.registry import PARSERS
from energatron.core.errors import InvalidInputError
from energatron.core.models.catalog import Coverage, SourceType
from energatron.core.models.gas import skeleton
from tests.fixtures import make_cdr as cdr
from tests.fixtures import make_gas as gas

TEXT = gas.record_text(gas.record())


def test_the_parser_is_registered_for_gas_bill_record_version_2() -> None:
    assert (GAS_BILL_RECORD.source, GAS_BILL_RECORD.version) == (SourceType.GAS_BILL_RECORD, 2)
    assert GAS_BILL_RECORD in PARSERS


def test_detect_is_by_kind_and_never_takes_an_offer_manual_or_the_other_way_round() -> None:
    assert GAS_BILL_RECORD.detect("anything.txt", TEXT.encode()) is not None
    assert GAS_BILL_RECORD.detect("x.yaml", cdr.offer_manual_text().encode()) is None
    assert OFFER_MANUAL.detect("x.yaml", TEXT.encode()) is None


def test_inspect_gives_the_issue_date_the_period_and_no_identifier() -> None:
    info = GAS_BILL_RECORD.inspect("bill.yaml", TEXT.encode())
    assert info.source_ts == datetime(2026, 10, 4, tzinfo=UTC)
    assert info.coverage == Coverage(start=date(2026, 9, 1), end=date(2026, 9, 30))
    assert info.extra == {
        "pdf_dataset_id": gas.PDF_ID,
        "extracted_by": "agent:test-model",
        "period_start": "2026-09-01",
        "period_end": "2026-09-30",
        "source_ts_from": "content",
    }
    assert info.identifiers == {}
    assert info.extension == ".yaml"


def test_a_v1_record_is_read_as_v2() -> None:
    document = gas.record()
    del document["extracted_by"]
    document |= {"schema_version": 1, "transcribed_by": "owner"}
    info = GAS_BILL_RECORD.inspect("bill.yaml", gas.record_bytes(document))
    assert info.extra["extracted_by"] == "manual"


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("kind: gas_bill_record\n  bad: [indent\n", "bad_yaml"),
        ("- kind: gas_bill_record\n", "not_a_mapping"),
        (TEXT.replace('issue_date: "2026-10-04"', "issue_date: 2026-10-04"), "unquoted_date"),
        (skeleton(gas.PDF_ID), "invalid_gas_bill_record"),
        (TEXT + "mirn: <MIRN>\n", "invalid_gas_bill_record"),
    ],
)
def test_malformed_records_are_rejected_with_a_reason(text: str, reason: str) -> None:
    with pytest.raises(InvalidInputError) as caught:
        GAS_BILL_RECORD.inspect("bill.yaml", text.encode())
    assert caught.value.details == {"source": "gas_bill_record", "reason": reason}


def test_a_file_that_is_not_utf8_is_rejected() -> None:
    with pytest.raises(InvalidInputError) as caught:
        GAS_BILL_RECORD.inspect("bill.yaml", b"kind: gas_bill_record\n\xff\n")
    assert caught.value.details["reason"] == "not_utf8"
