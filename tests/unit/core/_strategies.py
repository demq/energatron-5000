"""Hypothesis strategies shared by the core unit tests."""

from hypothesis import strategies as st

from energatron.core.canonical import JSONValue

# Strings without lone surrogates, which UTF-8 can't encode.
json_text = st.text(alphabet=st.characters(codec="utf-8"), max_size=12)

json_scalars: st.SearchStrategy[JSONValue] = (
    st.none() | st.booleans() | st.integers() | st.floats(allow_nan=False, allow_infinity=False) | json_text
)

json_values: st.SearchStrategy[JSONValue] = st.recursive(
    json_scalars,
    lambda children: st.lists(children, max_size=4) | st.dictionaries(json_text, children, max_size=4),
    max_leaves=16,
)

json_objects: st.SearchStrategy[dict[str, JSONValue]] = st.dictionaries(json_text, json_values, max_size=4)
