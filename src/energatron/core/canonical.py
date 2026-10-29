"""Canonical JSON and the hash primitives (D1 §6.1)."""

import hashlib
import json
import re
from collections.abc import Iterable, Mapping, Sequence

type JSONValue = bool | int | float | str | Sequence[JSONValue] | Mapping[str, JSONValue] | None
"""Plain JSON data.

The containers are typed `Sequence` and `Mapping`, which are covariant, so a `dict[str, int]` or a `list[str]`
passes without a cast. At run time `json.dumps` decides: dicts, lists and tuples are accepted.
"""

_SHA256_HEX = re.compile(r"[0-9a-f]{64}")


def sha256_hex(data: bytes) -> str:
    """The SHA-256 of the exact bytes, as 64 lowercase hex characters."""
    return hashlib.sha256(data).hexdigest()


def canonical_json(value: JSONValue) -> bytes:
    """`value` as canonical JSON: sorted keys, no whitespace, UTF-8, finite numbers only.

    `value` must already be plain JSON data: convert a pydantic model with `model_dump(mode="json")` first. A
    `Decimal`, `datetime` or other value that needs converting raises `TypeError`, a NaN or infinity raises
    `ValueError`, and a string holding a lone surrogate raises `UnicodeEncodeError`. Strings are hashed as given,
    with no Unicode normalisation. Floats use Python's shortest round-trip form, so `0.10` and `0.1` give the
    same bytes, while `1` and `1.0` don't.
    """
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return text.encode("utf-8")


def content_hash(value: JSONValue) -> str:
    """The SHA-256 of `canonical_json(value)`."""
    return sha256_hex(canonical_json(value))


def tree_hash(entries: Iterable[tuple[str, str]]) -> str:
    """The hash of a set of files, given as `(relative POSIX path, sha256 hex)` pairs, one per file.

    The entries are sorted by path in code-point order, and the hash covers the UTF-8 concatenation of
    `f"{path}\\0{sha256}\\n"`. Which files to include is the caller's decision (D1 §10, §11).

    Raises `ValueError` for a path given twice, a path that isn't relative and normalised (no empty, `.` or `..`
    segment), a path holding a NUL or a newline, or a hash that isn't 64 lowercase hex characters.
    """
    lines: list[str] = []
    previous: str | None = None
    for path, sha256 in sorted(entries, key=lambda entry: entry[0]):
        if path == previous:
            raise ValueError("tree_hash got the same path twice")
        if "\0" in path or "\n" in path or any(segment in {"", ".", ".."} for segment in path.split("/")):
            raise ValueError("tree_hash needs relative, normalised POSIX paths")
        if _SHA256_HEX.fullmatch(sha256) is None:
            raise ValueError("tree_hash needs each sha256 as 64 lowercase hex characters")
        lines.append(f"{path}\0{sha256}\n")
        previous = path
    return sha256_hex("".join(lines).encode("utf-8"))
