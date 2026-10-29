"""`redact` and `redact_json` (D1 §8.7, §13; D2 §7.4's matching). Synthetic values only."""

import json
import re

from hypothesis import given
from hypothesis import strategies as st

from energatron.core.canonical import JSONValue, canonical_json
from energatron.core.models.private import PrivateConfig
from energatron.core.privacy import REDACTED, DenyEntry, derive_denylist, redact, redact_json
from tests.unit.core._private import NMI, NMI_WITH_CHECKSUM, SYNTHETIC_VALUES, synthetic_document
from tests.unit.core._strategies import json_text, json_values

DENYLIST = derive_denylist(PrivateConfig.from_document(synthetic_document()))
MIRN_TEXT = next(entry.value for entry in DENYLIST if entry.label == "mirn")
QUOTE = DenyEntry("manual:1", 'quo"te\\slash', "substring")
UNICODE = DenyEntry("manual:2", "Ünïcødé-Straße", "substring")
METACHARACTERS = DenyEntry("manual:3", "A.B*C(", "substring")
TRICKY = (QUOTE, UNICODE, METACHARACTERS)


def test_the_marker() -> None:
    assert REDACTED == "<redacted>"


# redact


def test_an_empty_denylist_changes_nothing() -> None:
    assert redact(f"x{NMI}y", ()) == f"x{NMI}y"


def test_a_substring_entry_is_redacted_anywhere_in_any_case() -> None:
    assert redact(f"file_{NMI.lower()}_x.csv", DENYLIST) == "file_<redacted>_x.csv"
    assert redact(f"{NMI}-{NMI}", DENYLIST) == "<redacted>-<redacted>"


def test_the_longest_entry_wins_so_no_checksum_digit_is_left() -> None:
    assert redact(f"[{NMI_WITH_CHECKSUM}]", DENYLIST) == "[<redacted>]"


def test_overlapping_and_adjacent_matches_become_one_marker() -> None:
    account = DenyEntry("account", "3SYNACCT", "substring")  # starts with the NMI's checksum digit
    denylist = (*DENYLIST, account)
    assert redact(f"[{NMI_WITH_CHECKSUM}SYNACCT]", denylist) == "[<redacted>]"  # they share the checksum digit
    assert redact(f"[{NMI}3SYNACCT x {NMI}3]", denylist) == "[<redacted> x <redacted>]"
    assert redact(f"{NMI}{NMI}", DENYLIST) == REDACTED
    assert redact(f"[{NMI}{NMI}3SYNACCT!]", denylist) == "[<redacted>!]"


def test_a_word_entry_matches_only_standing_alone() -> None:
    assert redact("SA 0000.", DENYLIST) == "SA <redacted>."
    assert redact("k9 and Q7X", DENYLIST) == "<redacted> and <redacted>"
    for text in ("energatron-0000", "100000", "0000_x", "K90", "AK9", "Q7X-1"):
        assert redact(text, DENYLIST) == text


def test_a_space_in_an_entry_matches_any_whitespace_run() -> None:
    assert redact("at 1\n  synthetic\tSTREET today", DENYLIST) == "at <redacted> today"
    assert redact("1Synthetic Street", DENYLIST) == "1Synthetic Street"


def test_whitespace_in_an_entry_is_normalised_before_matching() -> None:
    spaced = DenyEntry("manual:1", "  2  Synthetic\t Road ", "substring")
    word = DenyEntry("manual:2", " Q8Y\n", "word")
    assert redact("at 2 Synthetic Road today", (spaced,)) == "at <redacted> today"
    assert redact("at 2\nsynthetic   road.", (spaced,)) == "at <redacted>."
    assert redact("2Synthetic Road", (spaced,)) == "2Synthetic Road"
    assert redact("[Q8Y] and xQ8Y", (word,)) == "[<redacted>] and xQ8Y"


def test_entry_values_are_literal_text_not_patterns() -> None:
    assert redact("AxBBC(", TRICKY) == "AxBBC("
    assert redact("A.B*C(", TRICKY) == REDACTED


def test_a_word_entry_left_standing_alone_by_a_replacement_is_redacted_too() -> None:
    assert redact("K9ELECACCTSYN", DENYLIST) == "<redacted><redacted>"
    assert redact("Q7XK9ELECACCTSYN", DENYLIST) == "Q7XK9<redacted>"  # glued words aren't words


def test_an_entry_matching_inside_the_marker_still_terminates() -> None:
    assert redact("x", (DenyEntry("manual:1", "edacted", "substring"),)) == "x"
    assert REDACTED in redact("redacted", (DenyEntry("manual:1", "edacted", "substring"),))


def test_quotes_backslashes_and_non_ascii_are_redacted() -> None:
    assert redact('a quo"te\\slash b', TRICKY) == "a <redacted> b"
    assert redact("[ünïcødé-straße|ÜNÏCØDÉ-STRAßE]", TRICKY) == "[<redacted>|<redacted>]"


def test_redact_accepts_any_iterable_of_entries() -> None:
    assert redact(NMI, iter(DENYLIST)) == REDACTED
    assert redact(NMI, list(DENYLIST)) == REDACTED


# redact_json


def test_string_values_and_keys_are_redacted_at_any_depth() -> None:
    value: JSONValue = {
        "a": [{"b": f"x{NMI}"}, NMI_WITH_CHECKSUM, 3, None, True, 1.5],
        f"key-{NMI}": {"MSYNTHAB": ["ok", ["deeper ELECACCTSYN"]]},
    }
    assert redact_json(value, DENYLIST) == {
        "a": [{"b": "x<redacted>"}, "<redacted>", 3, None, True, 1.5],
        "key-<redacted>": {"<redacted>": ["ok", ["deeper <redacted>"]]},
    }


MIRN = int(MIRN_TEXT)  # the synthetic all-digit MIRN, written as a JSON number


def test_an_int_whose_decimal_text_holds_an_entry_becomes_the_redacted_string() -> None:
    assert redact_json(MIRN, DENYLIST) == REDACTED
    assert redact_json({"mirn": MIRN, "n": -MIRN, "big": MIRN * 1000 + 7}, DENYLIST) == {
        "mirn": REDACTED,
        "n": "-<redacted>",
        "big": "<redacted>007",
    }
    assert redact_json([12345, 0, -1], DENYLIST) == [12345, 0, -1]  # no entry: stays a number


def test_a_float_whose_decimal_text_holds_an_entry_becomes_the_redacted_string() -> None:
    assert redact_json(float(MIRN), DENYLIST) == "<redacted>.0"
    assert redact_json([MIRN / 10], DENYLIST) == ["<redacted>.8"]  # the MIRN's first ten digits
    assert redact_json({"x": 1.5, "y": -0.0}, DENYLIST) == {"x": 1.5, "y": -0.0}


def test_a_word_entry_in_a_number_counts_only_standing_alone() -> None:
    postcode = DenyEntry("postcode", "4321", "word")
    assert redact_json([4321, 4321.5, 14321, 43210], (postcode,)) == [REDACTED, "<redacted>.5", 14321, 43210]


def test_a_bool_is_never_redacted() -> None:
    truthy = DenyEntry("manual:1", "true", "substring")
    one = DenyEntry("manual:2", "1", "word")
    assert redact_json([True, False], (truthy, one)) == [True, False]
    assert redact_json({"flag": True}, (truthy, one)) == {"flag": True}
    assert redact_json(1, (truthy, one)) == REDACTED  # an int with the same value is


def test_the_input_is_not_changed_and_tuples_become_lists() -> None:
    value: dict[str, JSONValue] = {"a": (NMI, "b"), "c": [NMI]}
    before = json.dumps(value)
    assert redact_json(value, DENYLIST) == {"a": ["<redacted>", "b"], "c": ["<redacted>"]}
    assert json.dumps(value) == before


def test_an_entry_cannot_hide_behind_json_escaping() -> None:
    value: JSONValue = {"note": 'see quo"te\\slash', 'quo"te\\slash': "Ünïcødé-Straße"}
    text = json.dumps(value)
    assert QUOTE.value not in text  # escaped, so a text-level redact would miss it ...
    assert redact(text, TRICKY) == text
    redacted = redact_json(value, TRICKY)  # ... while redacting the data does not
    assert redacted == {"note": "see <redacted>", "<redacted>": "<redacted>"}
    serialised = canonical_json(redacted).decode("utf-8")
    assert json.loads(serialised) == redacted
    for entry in TRICKY:
        assert entry.value not in serialised


def test_keys_that_redact_to_the_same_text_are_all_kept() -> None:
    value: JSONValue = {f"a{NMI}": 1, f"a{NMI.lower()}": 2, "a<redacted> (2)": 3}
    redacted = redact_json(value, DENYLIST)
    assert isinstance(redacted, dict)
    assert sorted(item for item in redacted.values() if isinstance(item, int)) == [1, 2, 3]
    assert len(redacted) == 3
    assert all(NMI.lower() not in key.lower() for key in redacted)


def test_no_synthetic_value_survives_redact_json_of_the_private_document() -> None:
    redacted = redact_json(synthetic_document(), DENYLIST)
    serialised = canonical_json(redacted).decode("utf-8")
    assert_clean(serialised)
    assert "synthetic" not in serialised.lower()  # the street, whose spaces assert_clean doesn't model
    assert SYNTHETIC_VALUES  # the salt and the public codes aren't in the denylist, so they stay


# Properties

SUBSTRING_VALUES = [entry.value for entry in DENYLIST if entry.match == "substring"]
WORD_PATTERNS = [
    re.compile(rf"(?<![0-9A-Za-z_-]){re.escape(entry.value)}(?![0-9A-Za-z_-])", re.IGNORECASE)
    for entry in DENYLIST
    if entry.match == "word"
]
texts_with_entries = st.lists(json_text | st.sampled_from([entry.value for entry in DENYLIST]), max_size=6).map("".join)


def assert_clean(text: str) -> None:
    lowered = text.lower()
    assert [value for value in SUBSTRING_VALUES if " " not in value and value.lower() in lowered] == []
    assert [pattern.pattern for pattern in WORD_PATTERNS if pattern.search(text)] == []


@given(texts_with_entries)
def test_redact_leaves_no_entry_behind(text: str) -> None:
    assert_clean(redact(text, DENYLIST))


@given(st.dictionaries(texts_with_entries, json_values | texts_with_entries, max_size=4))
def test_redact_json_leaves_no_entry_behind_and_stays_json(value: dict[str, JSONValue]) -> None:
    redacted = redact_json(value, DENYLIST)
    assert isinstance(redacted, dict)
    assert len(redacted) == len(value)
    serialised = canonical_json(redacted).decode("utf-8")
    assert json.loads(serialised) == redacted
    for key, item in redacted.items():
        assert_clean(key)
        if isinstance(item, str):
            assert_clean(item)


# Overlapping entries: the redacted text is what marking every character of every entry's match gives.

OVERLAPPING = (
    DenyEntry("nmi", NMI, "substring"),
    DenyEntry("nmi_checksum", NMI_WITH_CHECKSUM, "substring"),
    DenyEntry("account", "3XYZ0123", "substring"),
    DenyEntry("account", "Z3XY", "substring"),
    DenyEntry("meter_serial", "0123AB", "substring"),
)
_FRAGMENTS = [
    piece for entry in OVERLAPPING for piece in (entry.value, entry.value[:3], entry.value[-3:], entry.value.lower())
]


def covered_runs_redacted(text: str, entries: tuple[DenyEntry, ...]) -> str:
    """The reference: mark every character inside any entry's match, then put one marker per marked run."""
    covered = [False] * len(text)
    for entry in entries:
        single = re.compile(re.escape(entry.value), re.IGNORECASE)
        for start in range(len(text)):
            found = single.match(text, start)
            if found is not None:
                covered[found.start() : found.end()] = [True] * (found.end() - found.start())
    pieces: list[str] = []
    for index, character in enumerate(text):
        if not covered[index]:
            pieces.append(character)
        elif index == 0 or not covered[index - 1]:
            pieces.append(REDACTED)
    return "".join(pieces)


@given(st.lists(json_text | st.sampled_from(_FRAGMENTS), max_size=8).map("".join))
def test_redact_covers_the_union_of_every_match(text: str) -> None:
    assert redact(text, OVERLAPPING) == covered_runs_redacted(text, OVERLAPPING)
