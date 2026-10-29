"""The denylist and redaction (D1 §8.7; D2 §7.3, §7.4).

`derive_denylist()` turns the private config into `DenyEntry` values. `redact()` and `redact_json()` replace every
entry with `<redacted>`: the CLI passes everything it writes to stdout and stderr through them, as the last line of
defence. Outputs must not hold identifiers in the first place.

Matching follows the privacy guard's rules (D2 §7.4): the value is literal text, matching ignores case, and a `word`
entry matches only where no letter, digit, `_` or `-` touches it. Whitespace in a value is normalised first: each
run of it matches any run of whitespace, and whitespace at either end is ignored (`value.split()`; P0.2.5's guard
uses the same rule).

Nothing here ever shows an entry's value: not in a repr, not in an error.
"""

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from typing import TYPE_CHECKING, Literal

from energatron.core.canonical import JSONValue

if TYPE_CHECKING:  # the model imports nmi_checksum from here
    from energatron.core.models.private import PrivateConfig

type Match = Literal["substring", "word"]

REDACTED = "<redacted>"
"""What `redact()` puts in place of a denylist entry."""

WORD_MIN_SUBSTRING = 5
"""Meter serials and accounts shorter than this are `word` entries: as substrings they would match too much."""

_NMI = re.compile(r"[A-Z0-9]{10}")
_WHITESPACE = re.compile(r"\s+")
_WORD_EDGE_BEFORE = r"(?<![0-9A-Za-z_-])"
_WORD_EDGE_AFTER = r"(?![0-9A-Za-z_-])"
_MAX_PASSES = 8  # a bound, in case a hand-written entry matches inside `<redacted>` itself


def nmi_checksum(nmi: str) -> int:
    """AEMO's checksum digit for a 10-character NMI, upper-cased first (D2 §7.3).

    Walk the characters from the right; double the ASCII code of the rightmost and of every second one after it; add
    up the decimal digits of every resulting number; the checksum is `(10 - total % 10) % 10`.
    Raises `ValueError`, without repeating the value, unless the NMI is 10 letters or digits.
    """
    upper = nmi.upper()
    if _NMI.fullmatch(upper) is None:
        raise ValueError("an NMI checksum needs exactly 10 letters or digits")
    total = 0
    for position, character in enumerate(reversed(upper)):
        code = ord(character) * (2 if position % 2 == 0 else 1)
        total += sum(int(digit) for digit in str(code))
    return (10 - total % 10) % 10


@dataclass(frozen=True, slots=True)
class DenyEntry:
    """One denylist entry: a `label` naming what the value is, never the value itself.

    `match` is `substring`, or `word` for a value that only counts where it stands alone.
    """

    label: str
    value: str = field(repr=False)
    match: Match

    def __post_init__(self) -> None:
        if not self.label:
            raise ValueError("a denylist entry needs a label")
        if not self.value.strip():
            raise ValueError(f"the denylist entry {self.label} has an empty value")
        if self.match not in ("substring", "word"):
            raise ValueError(f"the denylist entry {self.label} has an unknown match kind")


def derive_denylist(private: PrivateConfig) -> tuple[DenyEntry, ...]:
    """The entries D2 §7.3 derives from the private config, sorted by `(label, value)`, without duplicates.

    Never included: the salt (random), the network tariff and the current plans (public codes and IDs).
    """
    premises = private.premises
    nmi = premises.nmi[:10]
    entries: set[DenyEntry] = {
        DenyEntry("nmi", nmi, "substring"),
        DenyEntry("nmi_checksum", f"{nmi}{nmi_checksum(nmi)}", "substring"),
    }
    entries.update(DenyEntry("meter_serial", serial, _by_length(serial)) for serial in premises.meter_serials)
    if premises.street is not None:
        entries.add(DenyEntry("street", " ".join(premises.street.split()), "substring"))
    if premises.postcode is not None:
        entries.add(DenyEntry("postcode", premises.postcode, "word"))
    if premises.gas_mirn is not None:
        entries.add(DenyEntry("mirn", premises.gas_mirn, "substring"))
        if len(premises.gas_mirn) == 11:
            entries.add(DenyEntry("mirn_base", premises.gas_mirn[:10], "substring"))
    accounts = (*private.accounts.electricity, *private.accounts.gas)
    entries.update(DenyEntry("account", account, _by_length(account)) for account in accounts)
    return tuple(sorted(entries, key=lambda entry: (entry.label, entry.value, entry.match)))


def redact(text: str, denylist: Iterable[DenyEntry]) -> str:
    """`text` with every denylist entry replaced by `<redacted>`.

    Every character inside any entry's match is redacted, so overlapping entries leave no tail behind; each run of
    redacted characters (overlapping or adjacent matches) becomes one `<redacted>`. This repeats until nothing
    changes, because a replacement can leave a `word` entry standing alone: in `K9ACCOUNTNUMBER`, `K9` isn't a word
    until the account after it becomes `<redacted>`.
    """
    patterns = _patterns(_key(denylist))
    if not patterns:
        return text
    return _sub(patterns, text)


def redact_json(value: JSONValue, denylist: Iterable[DenyEntry]) -> JSONValue:
    """A copy of plain JSON data with every string value, object key and number redacted, at any depth.

    Redacting the data rather than its serialised text means a replacement can't break the JSON, and an entry
    can't hide behind JSON escaping (D1 §8.7). An int or float whose JSON text holds an entry comes back as that
    text redacted, a string (an all-digit identifier can be written as a number); one that holds none stays a
    number. Booleans, `None` and the numbers are otherwise returned as they are. Lists and tuples come back as
    lists. Keys that redact to the same text are all kept: the later ones get a ` (2)`, ` (3)` … suffix.
    """
    patterns = _patterns(_key(denylist))
    if not patterns:
        return _copy(value)
    return _redact_value(value, patterns)


# Helpers


def _by_length(value: str) -> Match:
    return "word" if len(value) < WORD_MIN_SUBSTRING else "substring"


def _key(denylist: Iterable[DenyEntry]) -> tuple[DenyEntry, ...]:
    return tuple(sorted(set(denylist), key=lambda entry: (entry.label, entry.value, entry.match)))


@lru_cache(maxsize=8)
def _patterns(entries: tuple[DenyEntry, ...]) -> tuple[re.Pattern[str], ...]:
    """One regex per entry. Each is a lookahead, so `finditer` finds a match at every start, overlapping or not."""
    return tuple(re.compile(f"(?=({_entry_pattern(entry)}))", re.IGNORECASE) for entry in entries)


def _sub(patterns: tuple[re.Pattern[str], ...], text: str) -> str:
    for _ in range(_MAX_PASSES):
        redacted = _redact_once(patterns, text)
        if redacted == text:
            break
        text = redacted
    return text


def _redact_once(patterns: tuple[re.Pattern[str], ...], text: str) -> str:
    """Each run of characters inside any entry's match, replaced by one marker."""
    spans = sorted(
        (found.start(1), found.end(1))
        for pattern in patterns
        for found in pattern.finditer(text)
        if found.end(1) > found.start(1)
    )
    pieces: list[str] = []
    position = 0
    run_end = -1
    for start, end in spans:
        if start > run_end:  # a new run: keep the text before it
            pieces.append(text[position:start])
            pieces.append(REDACTED)
        run_end = max(run_end, end)
        position = run_end
    pieces.append(text[position:])
    return "".join(pieces)


def _entry_pattern(entry: DenyEntry) -> str:
    body = r"\s+".join(re.escape(part) for part in entry.value.split())
    if entry.match == "word":
        return f"{_WORD_EDGE_BEFORE}{body}{_WORD_EDGE_AFTER}"
    return body


def _redact_value(value: JSONValue, patterns: tuple[re.Pattern[str], ...]) -> JSONValue:
    if isinstance(value, str):
        return _sub(patterns, value)
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        text = json.dumps(value)  # the number's JSON text, as `canonical_json` writes it
        redacted = _sub(patterns, text)
        return value if redacted == text else redacted
    if isinstance(value, Mapping):
        result: dict[str, JSONValue] = {}
        for key, item in value.items():
            result[_unique(_sub(patterns, key), result)] = _redact_value(item, patterns)
        return result
    if isinstance(value, Sequence):
        return [_redact_value(item, patterns) for item in value]
    return value


def _unique(key: str, taken: Mapping[str, JSONValue]) -> str:
    if key not in taken:
        return key
    number = 2
    while f"{key} ({number})" in taken:
        number += 1
    return f"{key} ({number})"


def _copy(value: JSONValue) -> JSONValue:
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        return {key: _copy(item) for key, item in value.items()}
    if isinstance(value, Sequence):
        return [_copy(item) for item in value]
    return value
