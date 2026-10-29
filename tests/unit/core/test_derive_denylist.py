"""`nmi_checksum` and `derive_denylist` (D2 §7.3, §14). Synthetic values only; test IDs name rules, never values."""

import pytest

from energatron.core.canonical import JSONValue
from energatron.core.models.private import PrivateConfig
from energatron.core.privacy import DenyEntry, derive_denylist, nmi_checksum
from tests.unit.core._private import NMI, NMI_WITH_CHECKSUM, synthetic_document

# AEMO's published worked examples for its NMI checksum algorithm: public, made-up NMIs.
WORKED_EXAMPLES: list[tuple[str, int]] = [
    ("QAAAVZZZZZ", 3),
    ("2001985732", 8),
    ("2001985733", 6),
    ("QCDWW00010", 2),
    ("SMVEW00085", 8),
    ("VAAA000065", 7),
    ("VAAA000066", 5),
    ("VAAA000067", 2),
]

type Row = tuple[str, str, str]


def derive(doc: dict[str, JSONValue]) -> list[Row]:
    return [(entry.label, entry.value, entry.match) for entry in derive_denylist(PrivateConfig.from_document(doc))]


def minimal_document() -> dict[str, JSONValue]:
    return {"kind": "private_config", "schema_version": 1, "salt": "a" * 32, "premises": {"nmi": NMI}}


def section(doc: dict[str, JSONValue], name: str) -> dict[str, JSONValue]:
    value = doc.setdefault(name, {})
    assert isinstance(value, dict)
    return value


# nmi_checksum


@pytest.mark.parametrize(
    ("nmi", "digit"), WORKED_EXAMPLES, ids=[f"aemo-example-{n}" for n in range(len(WORKED_EXAMPLES))]
)
def test_worked_checksum_examples(nmi: str, digit: int) -> None:
    assert nmi_checksum(nmi) == digit


def test_the_checksum_upper_cases_its_input() -> None:
    assert nmi_checksum(NMI.lower()) == nmi_checksum(NMI) == int(NMI_WITH_CHECKSUM[-1])


@pytest.mark.parametrize("bad", [NMI[:9], NMI_WITH_CHECKSUM, "QAAAV-ZZZZ", ""], ids=["short", "long", "dash", "empty"])
def test_the_checksum_needs_ten_letters_or_digits(bad: str) -> None:
    with pytest.raises(ValueError) as caught:
        nmi_checksum(bad)
    assert bad == "" or bad not in str(caught.value)


# derive_denylist: each row of D2 §7.3


def test_the_full_synthetic_document_gives_every_row() -> None:
    assert derive(synthetic_document()) == [
        ("account", "ELECACCTSYN", "substring"),
        ("account", "GASACCTSYN", "substring"),
        ("account", "K9", "word"),
        ("meter_serial", "MSYNTHAB", "substring"),
        ("meter_serial", "Q7X", "word"),
        ("mirn", "55512345678", "substring"),
        ("mirn_base", "5551234567", "substring"),
        ("nmi", NMI, "substring"),
        ("nmi_checksum", NMI_WITH_CHECKSUM, "substring"),
        ("postcode", "0000", "word"),
        ("street", "1 Synthetic Street", "substring"),
    ]


def test_salt_and_nmi_alone_give_both_nmi_entries() -> None:
    assert derive(minimal_document()) == [
        ("nmi", NMI, "substring"),
        ("nmi_checksum", NMI_WITH_CHECKSUM, "substring"),
    ]


def test_a_lower_case_nmi_gives_upper_case_entries() -> None:
    doc = minimal_document()
    section(doc, "premises")["nmi"] = NMI_WITH_CHECKSUM.lower()
    assert derive(doc) == [("nmi", NMI, "substring"), ("nmi_checksum", NMI_WITH_CHECKSUM, "substring")]


@pytest.mark.parametrize(("value", "match"), [("ABCD", "word"), ("ABCDE", "substring"), ("A", "word")])
def test_a_meter_serial_shorter_than_five_characters_is_a_word(value: str, match: str) -> None:
    doc = minimal_document()
    section(doc, "premises")["meter_serials"] = [value]
    assert ("meter_serial", value, match) in derive(doc)


@pytest.mark.parametrize(("value", "match"), [("ABCD", "word"), ("ABCDE", "substring")])
def test_an_account_shorter_than_five_characters_is_a_word(value: str, match: str) -> None:
    doc = minimal_document()
    section(doc, "accounts")["gas"] = [value]
    assert ("account", value, match) in derive(doc)


def test_accounts_of_both_fuels_share_one_label_and_duplicates_go() -> None:
    doc = minimal_document()
    section(doc, "accounts").update({"electricity": ["ACCTONE", "ACCTTWO"], "gas": ["ACCTONE"]})
    section(doc, "premises")["meter_serials"] = ["SERIALX", "SERIALX"]
    rows = derive(doc)
    assert [row for row in rows if row[0] == "account"] == [
        ("account", "ACCTONE", "substring"),
        ("account", "ACCTTWO", "substring"),
    ]
    assert [row for row in rows if row[0] == "meter_serial"] == [("meter_serial", "SERIALX", "substring")]


def test_street_whitespace_runs_become_one_space() -> None:
    doc = minimal_document()
    section(doc, "premises")["street"] = "  12a\t\tExample \n Road "
    assert ("street", "12a Example Road", "substring") in derive(doc)


def test_the_postcode_is_always_a_word() -> None:
    doc = minimal_document()
    section(doc, "premises")["postcode"] = "0000"
    assert ("postcode", "0000", "word") in derive(doc)


def test_a_ten_digit_mirn_has_no_base_entry() -> None:
    doc = minimal_document()
    section(doc, "premises")["gas_mirn"] = "5551234567"
    rows = derive(doc)
    assert ("mirn", "5551234567", "substring") in rows
    assert [row for row in rows if row[0] == "mirn_base"] == []


def test_an_eleven_digit_mirn_adds_its_first_ten_digits() -> None:
    doc = minimal_document()
    section(doc, "premises")["gas_mirn"] = "55512345678"
    rows = derive(doc)
    assert ("mirn", "55512345678", "substring") in rows
    assert ("mirn_base", "5551234567", "substring") in rows


def test_salt_tariff_and_current_plans_are_never_included() -> None:
    doc = synthetic_document()
    excluded = [doc["salt"], section(doc, "premises")["network_tariff"], section(doc, "current_plans")["electricity"]]
    values = [value for _, value, _ in derive(doc)]
    for item in excluded:
        assert isinstance(item, str)
        assert all(item not in value for value in values)


def test_the_output_is_sorted_and_has_no_duplicates() -> None:
    entries = derive_denylist(PrivateConfig.from_document(synthetic_document()))
    keys = [(entry.label, entry.value) for entry in entries]
    assert keys == sorted(set(keys))
    assert isinstance(entries, tuple)


# DenyEntry


def test_a_deny_entry_never_shows_its_value() -> None:
    entry = DenyEntry("nmi", NMI, "substring")
    assert NMI not in repr(entry)
    assert NMI not in str(entry)
    assert "nmi" in repr(entry)


@pytest.mark.parametrize("value", ["", " ", "\t\n"], ids=["empty", "space", "whitespace"])
def test_a_deny_entry_needs_a_visible_value(value: str) -> None:
    with pytest.raises(ValueError, match="value"):
        DenyEntry("manual:1", value, "substring")


def test_a_deny_entry_needs_a_label_and_a_known_match() -> None:
    with pytest.raises(ValueError, match="label"):
        DenyEntry("", "ABCDEF", "substring")
    with pytest.raises(ValueError, match="match"):
        DenyEntry("x", "ABCDEF", "regex")  # pyright: ignore[reportArgumentType]


def test_deny_entries_are_frozen_and_hashable() -> None:
    entry = DenyEntry("nmi", NMI, "substring")
    assert len({entry, DenyEntry("nmi", NMI, "substring")}) == 1
    with pytest.raises(AttributeError):
        entry.value = "x"  # pyright: ignore[reportAttributeAccessIssue]
