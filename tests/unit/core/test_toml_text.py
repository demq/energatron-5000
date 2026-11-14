"""The TOML writer (P8 spec §7): every shape round-trips through `tomllib`."""

import tomllib

import pytest
from hypothesis import given
from hypothesis import strategies as st

from energatron.core.errors import InternalError
from energatron.core.toml_text import dumps


def test_scalars_lists_and_nested_tables_round_trip() -> None:
    document = {
        "kind": "private_config",
        "schema_version": 1,
        "flag": True,
        "ratio": 0.5,
        "premises": {"nmi": "<NMI>", "meter_serials": ["a", "b"], "inner": {"x": 1}},
        "empty": {},
        "accounts": {"gas": []},
    }
    text = dumps(document)
    assert tomllib.loads(text) == document
    assert text.startswith('kind = "private_config"\nschema_version = 1\n')


def test_none_is_left_out() -> None:
    assert tomllib.loads(dumps({"a": None, "t": {"b": None, "c": "x"}})) == {"t": {"c": "x"}}


@given(st.text())
def test_any_string_round_trips(value: str) -> None:
    assert tomllib.loads(dumps({"s": value, "odd key.!": value})) == {"s": value, "odd key.!": value}


@pytest.mark.parametrize("value", [[{"a": 1}], [[1]], float("nan")])
def test_unsupported_values_are_bugs(value: object) -> None:
    with pytest.raises(InternalError):
        dumps({"x": value})  # type: ignore[dict-item]
