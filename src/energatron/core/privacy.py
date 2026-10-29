"""The denylist, redaction, the premises check and filename masking (D1 §8.7; D2 §7.3, §7.4; D3 §6, §8.2).

`derive_denylist()` turns the private config into `DenyEntry` values. `redact()` and `redact_json()` replace every
entry with `<redacted>`: the CLI passes everything it writes to stdout and stderr through them, as the last line of
defence. Outputs must not hold identifiers in the first place.

Matching follows the privacy guard's rules (D2 §7.4): the value is literal text, matching ignores case, and a `word`
entry matches only where no letter, digit, `_` or `-` touches it. Whitespace in a value is normalised first: each
run of it matches any run of whitespace, and whitespace at either end is ignored (`value.split()`; P0.2.5's guard
uses the same rule).

`check_premises()` compares a file's identifiers with the private config before landing (D3 §6); `mask_filename()`
hides what a file name could give away in output (D3 §8.2).

Nothing here ever shows an entry's or an identifier's value: not in a repr, not in an error, not in a warning.
"""

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from typing import TYPE_CHECKING, Literal

from energatron.core.canonical import JSONValue
from energatron.core.errors import (
    ForeignPremisesError,
    InternalError,
    InvalidInputError,
    ResultWarning,
    WarningCode,
)
from energatron.core.models.catalog import PUBLIC_SOURCES, SourceType

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
_DIGIT_RUN = re.compile(r"[A-Za-z0-9]+")
_DIGITS_TO_MASK = 4
_IDENTIFIER_KINDS = ("nmi", "meter_serial", "account")
_KIND_LABELS = {"meter_serial": "meter serial", "account": "account"}
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


def mask_filename(name: str, denylist: Iterable[DenyEntry]) -> str:
    """One path component as output may show it (D3 §8.2).

    First `redact()` with the denylist, then every maximal run of `[A-Za-z0-9]` that holds four or more digits
    becomes `<n>`. SAPN names keep their shape but lose the NMI and dates; a retailer name loses its numbers.
    """
    redacted = redact(name, denylist)
    return _DIGIT_RUN.sub(_mask_run, redacted)


def check_premises(
    source: SourceType, identifiers: Mapping[str, Sequence[str]], private: PrivateConfig
) -> list[ResultWarning]:
    """Check that a file belongs to the workspace's premises (D3 §6). Returns warnings or raises.

    Values are compared stripped and case-insensitively; an NMI on its first 10 characters. Blank values count as
    absent. Public sources aren't checked. An 11-character NMI with a wrong checksum digit raises `E_INVALID_INPUT`
    (`reason: "nmi_checksum"`); an NMI other than the premises' raises `E_FOREIGN_PREMISES`. Meter serials and
    accounts the private config doesn't hold give `W_UNKNOWN_IDENTIFIER`, once per kind. Without an NMI, a known
    serial or account confirms the premises; values of a kind whose private list isn't empty, with nothing known in
    any kind, are foreign; otherwise the result carries `W_PREMISES_UNCHECKED`.

    Errors and warnings name the kind in `details.identifier`, never a value. The caller adds the input's
    `position`. An identifier kind other than `nmi`, `meter_serial` and `account` is a bug (`E_INTERNAL`).
    """
    if SourceType(source) in PUBLIC_SOURCES:
        return []
    unknown_kinds = sorted(set(identifiers) - set(_IDENTIFIER_KINDS))
    if unknown_kinds:
        raise InternalError(
            "A parser returned an identifier kind the premises check doesn't know.", kinds=unknown_kinds
        )
    found = {kind: [_fold(value) for value in identifiers.get(kind, ()) if value.strip()] for kind in _IDENTIFIER_KINDS}
    known = {
        "meter_serial": {_fold(value) for value in private.premises.meter_serials},
        "account": {_fold(value) for value in (*private.accounts.electricity, *private.accounts.gas)},
    }

    nmis = found["nmi"]
    for nmi in nmis:
        if len(nmi) == 11 and _NMI.fullmatch(nmi[:10]) and nmi[10] != str(nmi_checksum(nmi[:10])):
            raise InvalidInputError(
                "The file's NMI has a wrong checksum digit.", reason="nmi_checksum", identifier="nmi"
            )
    premises_nmi = private.premises.nmi[:10].upper()
    if any(nmi[:10] != premises_nmi for nmi in nmis):
        raise ForeignPremisesError("The file belongs to another NMI than the workspace's.", identifier="nmi")

    warnings = [
        ResultWarning(
            code=WarningCode.W_UNKNOWN_IDENTIFIER,
            message=(
                f"The file holds a {_KIND_LABELS[kind]} that private/config.toml doesn't list, so the denylist "
                "doesn't cover it yet: add it to private/config.toml and run `energatron denylist`."
            ),
            details={"identifier": kind},
        )
        for kind in ("meter_serial", "account")
        if any(value not in known[kind] for value in found[kind])
    ]
    if nmis:
        return warnings
    if any(value in known[kind] for kind in ("meter_serial", "account") for value in found[kind]):
        return warnings
    checkable = [kind for kind in ("meter_serial", "account") if found[kind] and known[kind]]
    if checkable:
        raise ForeignPremisesError(
            f"The file's {_KIND_LABELS[checkable[0]]} doesn't belong to the workspace's premises.",
            identifier=checkable[0],
        )
    warnings.append(
        ResultWarning(
            code=WarningCode.W_PREMISES_UNCHECKED,
            message="Nothing in the file could confirm that it belongs to the workspace's premises.",
        )
    )
    return warnings


# Helpers


def _mask_run(match: re.Match[str]) -> str:
    run = match.group()
    return "<n>" if sum(character.isdigit() for character in run) >= _DIGITS_TO_MASK else run


def _fold(value: str) -> str:
    return value.strip().upper()


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
