"""`gas_bill_record` v1 (P5 spec §3.2): the model's rules and conventions, and the skeleton. Synthetic values."""

import copy
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
import yaml

from energatron.core.errors import InvalidInputError
from energatron.core.models.gas import GasBillRecord, skeleton
from tests.fixtures import make_gas as gas


def doc(**changes: Any) -> dict[str, Any]:
    return copy.deepcopy(gas.record()) | changes


def with_line(index: int, **changes: Any) -> dict[str, Any]:
    found = doc()
    found["lines"][index] = found["lines"][index] | changes
    return found


def with_read(index: int, **changes: Any) -> dict[str, Any]:
    found = doc()
    found["reads"][index] = found["reads"][index] | changes
    return found


def test_a_consistent_record_validates_and_keeps_its_printed_places() -> None:
    record = GasBillRecord.from_document(gas.record(volumes=(10, 20), read_types=["actual", "estimate"]))
    assert (record.period_start, record.period_end, record.days) == (date(2026, 9, 1), date(2026, 9, 30), 30)
    assert [read.type for read in record.reads] == ["actual", "estimate"]
    assert record.reads[0].heating_value == Decimal("38.5000")
    assert record.reads[0].heating_value.as_tuple().exponent == -4
    assert record.lines[0].unit == "MJ"
    assert record.lines[0].gst_free is False
    dumped = record.model_dump(mode="json")
    assert dumped["reads"][0]["date"] == record.reads[0].read_date.isoformat()  # the file's key, not the field name
    assert dumped["subtotal"] == str(record.subtotal)


@pytest.mark.parametrize(
    "document",
    [
        doc(period_start="2026-10-01"),  # after the end
        doc(reads=[]),
        doc(lines=[]),
        doc(pdf_dataset_id="offer_manual-20261101T000000-0123abcd"),
        doc(transcribed_by="someone"),
        doc(retailer=" padded"),
        doc(total=12.5),  # a YAML number, not a quoted string
        doc(subtotal="1,000.00"),
        doc(account_number="<account>"),  # no identifier field exists
        with_read(0, end="999"),  # below the start: a rollover
        with_read(0, heating_value="0"),
        with_read(0, type="guess"),
        with_line(0, quantity=None),
        with_line(0, unit="day"),
        with_line(1, unit="MJ"),
        with_line(1, rate=None),
        with_line(0, gst_free="yes"),
        with_line(0, amount="12.345"),  # money holds cents
        with_line(0, rate="0.12345678901"),  # rates hold ten places
        with_read(0, heating_value="38.5000123"),  # quantities hold six
        doc(gst="1.001"),
    ],
)
def test_a_record_breaking_a_rule_is_invalid_input(document: dict[str, Any]) -> None:
    with pytest.raises(InvalidInputError):
        GasBillRecord.from_document(document)


def test_other_lines_may_leave_quantity_and_rate_out_but_not_just_one() -> None:
    credit = {"line": "credit", "label": "Credit", "amount": "-5.00"}
    assert GasBillRecord.from_document(gas.record(extra_lines=[credit])).lines[-1].quantity is None
    with pytest.raises(InvalidInputError):
        GasBillRecord.from_document(gas.record(extra_lines=[{**credit, "quantity": "1"}]))


def test_a_gst_free_line_is_kept_as_such() -> None:
    concession = {"line": "credit", "label": "Concession", "amount": "-5.00", "gst_free": True}
    assert GasBillRecord.from_document(gas.record(extra_lines=[concession])).lines[-1].gst_free is True


def test_the_skeleton_names_its_pdf_reads_as_yaml_and_does_not_validate() -> None:
    text = skeleton(gas.PDF_ID)
    document = yaml.safe_load(text)
    assert (document["kind"], document["schema_version"], document["pdf_dataset_id"]) == (
        "gas_bill_record",
        1,
        gas.PDF_ID,
    )
    assert [line["line"] for line in document["lines"]] == ["usage", "supply"]
    with pytest.raises(InvalidInputError):
        GasBillRecord.from_document(document)


def test_reads_listed_newest_first_are_refused() -> None:
    document = gas.record(volumes=(10, 20))
    document["reads"] = list(reversed(document["reads"]))
    with pytest.raises(InvalidInputError):
        GasBillRecord.from_document(document)
