"""Canonical JSON and the hash primitives (D1 §6.1, §13)."""

import json
import random
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from energatron.core.canonical import JSONValue, canonical_json, content_hash, sha256_hex, tree_hash
from tests.unit.core._strategies import json_values

EMPTY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
ABC_SHA256 = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


def test_sha256_hex_gives_lowercase_hex_of_the_exact_bytes() -> None:
    assert sha256_hex(b"") == EMPTY_SHA256
    assert sha256_hex(b"abc") == ABC_SHA256
    assert sha256_hex(b"abc\r\n") != sha256_hex(b"abc\n")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ({"b": 1, "a": [True, None, 2.5, "x"]}, b'{"a":[true,null,2.5,"x"],"b":1}'),
        ({"b": {"d": 1, "c": 2}, "a": 0}, b'{"a":0,"b":{"c":2,"d":1}}'),
        ({"name": "Zo\u00eb \u26a1"}, '{"name":"Zo\u00eb \u26a1"}'.encode()),
        ((1, "two"), b'[1,"two"]'),
        ('quote " and backslash \\', b'"quote \\" and backslash \\\\"'),
        (0.10, b"0.1"),
        (1, b"1"),
        (1.0, b"1.0"),
        (None, b"null"),
    ],
)
def test_canonical_json_examples(value: JSONValue, expected: bytes) -> None:
    assert canonical_json(value) == expected


def test_canonical_json_hashes_strings_as_given() -> None:
    composed, decomposed = "\u00e9", "e\u0301"
    assert canonical_json(composed) != canonical_json(decomposed)


@pytest.mark.parametrize("value", [Decimal("1.10"), datetime(2026, 10, 23, tzinfo=UTC), {"at": b"bytes"}])
def test_canonical_json_refuses_values_that_need_explicit_conversion(value: object) -> None:
    with pytest.raises(TypeError):
        canonical_json(value)  # pyright: ignore[reportArgumentType]


@pytest.mark.parametrize("value", [float("nan"), float("inf"), [float("-inf")]])
def test_canonical_json_refuses_non_finite_floats(value: JSONValue) -> None:
    with pytest.raises(ValueError):
        canonical_json(value)


def test_content_hash_is_the_sha256_of_canonical_json() -> None:
    value: JSONValue = {"b": [1, 2], "a": "x"}
    assert content_hash(value) == sha256_hex(canonical_json(value))
    assert content_hash({}) == sha256_hex(b"{}")


def shuffled(value: JSONValue, rng: random.Random) -> JSONValue:
    """`value` with the keys of every object, at any depth, in a random order."""
    if isinstance(value, dict):
        keys = list(value)
        rng.shuffle(keys)
        return {key: shuffled(value[key], rng) for key in keys}
    if isinstance(value, list):
        return [shuffled(item, rng) for item in value]
    return value


@given(
    value=json_values,
    indent=st.sampled_from([None, 0, 2, "\t"]),
    separators=st.sampled_from([(",", ":"), (", ", ": "), (" ,\n", " :  ")]),
    ensure_ascii=st.booleans(),
    rng=st.randoms(use_true_random=False),
)
def test_canonical_json_ignores_key_order_and_whitespace(
    value: JSONValue,
    indent: int | str | None,
    separators: tuple[str, str],
    ensure_ascii: bool,
    rng: random.Random,
) -> None:
    source = json.dumps(shuffled(value, rng), indent=indent, separators=separators, ensure_ascii=ensure_ascii)
    assert canonical_json(json.loads(source)) == canonical_json(value)


def test_tree_hash_hashes_sorted_path_and_sha256_lines() -> None:
    first, second = sha256_hex(b"first"), sha256_hex(b"second")
    expected = sha256_hex(f"a/x.csv\0{first}\nb.txt\0{second}\n".encode())
    assert tree_hash([("b.txt", second), ("a/x.csv", first)]) == expected


def test_tree_hash_sorts_by_code_point() -> None:
    digest = sha256_hex(b"x")
    expected = sha256_hex("".join(f"{path}\0{digest}\n" for path in ["B", "a.b", "a/b", "\u00e9"]).encode())
    assert tree_hash([("\u00e9", digest), ("a/b", digest), ("a.b", digest), ("B", digest)]) == expected


def test_tree_hash_of_no_files_is_the_hash_of_nothing() -> None:
    assert tree_hash([]) == EMPTY_SHA256


@pytest.mark.parametrize(
    "entries",
    [
        [("a.csv", EMPTY_SHA256), ("a.csv", ABC_SHA256)],
        [("", EMPTY_SHA256)],
        [("/abs.csv", EMPTY_SHA256)],
        [("a/../b.csv", EMPTY_SHA256)],
        [("./b.csv", EMPTY_SHA256)],
        [("a//b.csv", EMPTY_SHA256)],
        [("dir/", EMPTY_SHA256)],
        [("a\nb.csv", EMPTY_SHA256)],
        [("a\0b.csv", EMPTY_SHA256)],
        [("a.csv", EMPTY_SHA256.upper())],
        [("a.csv", EMPTY_SHA256[:63])],
    ],
)
def test_tree_hash_refuses_malformed_entries(entries: list[tuple[str, str]]) -> None:
    with pytest.raises(ValueError):
        tree_hash(entries)


path_segments = st.text(alphabet=st.characters(codec="utf-8", exclude_characters="/\0\n"), min_size=1, max_size=6)
relative_paths = st.lists(path_segments.filter(lambda s: s not in {".", ".."}), min_size=1, max_size=3).map("/".join)


@given(
    files=st.dictionaries(relative_paths, st.binary(max_size=8).map(sha256_hex), max_size=8),
    rng=st.randoms(use_true_random=False),
)
def test_tree_hash_ignores_entry_order(files: dict[str, str], rng: random.Random) -> None:
    entries = list(files.items())
    rng.shuffle(entries)
    assert tree_hash(entries) == tree_hash(sorted(files.items()))
