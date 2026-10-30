"""The text helpers the format readers share (P2 spec §4, D3 §5). Synthetic text only."""

from datetime import date, datetime

import pytest

from energatron.core.errors import InvalidInputError
from energatron.core.models.catalog import SourceType
from energatron.core.text import (
    BOM,
    add_unique,
    compact_date,
    compact_datetime,
    csv_rows,
    decode,
    fail,
    is_decimal,
    lines,
    parse_datetime,
)

SOURCE = SourceType.SAPN_INTERVAL


def test_lines_split_on_lf_and_crlf_and_drop_a_leading_bom() -> None:
    assert lines("a\nb\n") == ["a", "b", ""]
    assert lines("a\r\nb\r\n") == ["a", "b", ""]
    assert lines(f"{BOM}a\nb") == ["a", "b"]
    assert lines("") == [""]


def test_lines_drop_a_bom_only_at_the_start() -> None:
    assert lines(f"a\n{BOM}b") == ["a", f"{BOM}b"]


def test_lines_keep_a_lone_carriage_return_inside_a_line() -> None:
    assert lines("a\rb\nc") == ["a\rb", "c"]


def test_decode_returns_utf8_text_and_keeps_the_bom() -> None:
    assert decode(SOURCE, "café".encode()) == "café"
    assert decode(SOURCE, f"{BOM}x".encode()) == f"{BOM}x"


def test_decode_rejects_other_bytes_with_not_utf8() -> None:
    with pytest.raises(InvalidInputError) as caught:
        decode(SOURCE, b"\xff\xfe")
    assert caught.value.details["reason"] == "not_utf8"
    assert caught.value.details["source"] == "sapn_interval"


def test_fail_names_the_source_and_the_reason_and_nothing_else() -> None:
    error = fail(SourceType.RETAILER_USAGE, "A message.", "bad_thing")
    assert isinstance(error, InvalidInputError)
    assert error.message == "A message."
    assert error.details == {"source": "retailer_usage", "reason": "bad_thing"}


def test_csv_rows_skip_blank_rows_and_handle_quoted_fields() -> None:
    text = 'a,b,c\n\n"x,y",z,"say ""hi"""\n   ,  ,\n'
    assert csv_rows(text) == [["a", "b", "c"], ["x,y", "z", 'say "hi"']]


def test_csv_rows_drop_a_leading_bom_and_accept_crlf() -> None:
    assert csv_rows(f"{BOM}a,b\r\nc,d\r\n") == [["a", "b"], ["c", "d"]]


@pytest.mark.parametrize("text", ["1", "1.5", ".5", "1.", "+2", "-0.25", " 3.0 ", "007"])
def test_is_decimal_accepts(text: str) -> None:
    assert is_decimal(text)


@pytest.mark.parametrize("text", ["", " ", "1e5", "nan", "inf", "1,5", "--1", "1.2.3", "0x10", "1 2", "abc"])
def test_is_decimal_rejects(text: str) -> None:
    assert not is_decimal(text)


def test_parse_datetime_strips_and_returns_none_on_failure() -> None:
    assert parse_datetime(" 2025-01-02T03:04:05 ", "%Y-%m-%dT%H:%M:%S") == datetime(2025, 1, 2, 3, 4, 5)
    assert parse_datetime("2025-13-02T03:04:05", "%Y-%m-%dT%H:%M:%S") is None
    assert parse_datetime("", "%Y") is None


def test_add_unique_appends_a_stripped_non_empty_value_once_in_first_seen_order() -> None:
    values: list[str] = []
    for value in (" b ", "a", "b", "", "  ", "a "):
        add_unique(values, value)
    assert values == ["b", "a"]


def test_compact_date_takes_exactly_eight_digits_and_a_real_date() -> None:
    assert compact_date("20250131") == date(2025, 1, 31)
    for text in ("", "2025013", "202501311", "2025-01-1", "abcdefgh", "20250230", "20251301"):
        assert compact_date(text) is None


def test_compact_datetime_takes_exactly_the_digit_count_and_a_real_time() -> None:
    assert compact_datetime("20250120100345", "%Y%m%d%H%M%S", 14) == datetime(2025, 1, 20, 10, 3, 45)
    assert compact_datetime("202501201003", "%Y%m%d%H%M", 12) == datetime(2025, 1, 20, 10, 3)
    for text, pattern, digits in (
        ("2025012010034", "%Y%m%d%H%M%S", 14),
        ("20250120250000", "%Y%m%d%H%M%S", 14),
        ("202501201003", "%Y%m%d%H%M%S", 14),
        ("2025012010", "%Y%m%d%H%M", 12),
        ("", "%Y", 4),
    ):
        assert compact_datetime(text, pattern, digits) is None
