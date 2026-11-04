"""`core/retailer_txn.read_txn` (P4 spec §10): kinds, datetimes and amounts, and no description in the result."""

from datetime import datetime
from decimal import Decimal

import pytest

from energatron.core.errors import InvalidInputError
from energatron.core.retailer_txn import TxnRow, read_txn
from tests.fixtures.make_sources import make_retailer_txn_csv

ENTRIES = [
    ("Invoice - 000000/1", datetime(2025, 6, 3, 19, 14, 32), "120.5000"),
    ("Payment - BPAY Payments", datetime(2025, 6, 20), "-120.5000"),
    ("Adjustment", datetime(2025, 6, 21), "-5.00"),
]


def test_rows_keep_kind_time_and_amount_only() -> None:
    assert read_txn(make_retailer_txn_csv(ENTRIES)) == (
        TxnRow("invoice", datetime(2025, 6, 3, 19, 14, 32), Decimal("120.5000")),
        TxnRow("payment", datetime(2025, 6, 20), Decimal("-120.5000")),
        TxnRow("other", datetime(2025, 6, 21), Decimal("-5.00")),
    )
    assert "000000" not in repr(read_txn(make_retailer_txn_csv(ENTRIES)))


@pytest.mark.parametrize(
    ("data", "reason"),
    [
        (b"Other,Header\r\n", "bad_header"),
        (b"Description,Date,Amount\r\nInvoice,2025-06-03T00:00:00\r\n", "bad_field_count"),
        (b"Description,Date,Amount\r\nInvoice,yesterday,1.00\r\n", "bad_datetime"),
        (b"Description,Date,Amount\r\nInvoice,2025-06-03T00:00:00,lots\r\n", "bad_number"),
    ],
)
def test_malformed_exports_are_rejected_with_a_reason(data: bytes, reason: str) -> None:
    with pytest.raises(InvalidInputError) as caught:
        read_txn(data)
    assert caught.value.details["reason"] == reason
