"""A small TOML writer for energatron's own config models (P8 spec §7): `dumps(document) -> str`. Pure.

It writes the shapes those models use: strings, ints, bools and floats, lists of those, and nested tables. Keys of a
table are written in the given order, scalars and lists before sub-tables; a `None` value is left out (TOML has no
null, and a missing optional field reads back as `None`). Anything else (a list of tables, a date) is a bug →
`E_INTERNAL`. Comments of the file being rewritten are not kept.
"""

import math
import re
from collections.abc import Mapping, Sequence
from typing import Final

from energatron.core.canonical import JSONValue
from energatron.core.errors import InternalError

_BARE_KEY: Final = re.compile(r"[A-Za-z0-9_-]+")
_ESCAPES: Final = {'"': '\\"', "\\": "\\\\", "\b": "\\b", "\t": "\\t", "\n": "\\n", "\f": "\\f", "\r": "\\r"}


def _key(key: str) -> str:
    return key if _BARE_KEY.fullmatch(key) else _string(key)


def _string(value: str) -> str:
    out: list[str] = []
    for char in value:
        if char in _ESCAPES:
            out.append(_ESCAPES[char])
        elif ord(char) < 0x20 or ord(char) == 0x7F:
            out.append(f"\\u{ord(char):04x}")
        else:
            out.append(char)
    return '"' + "".join(out) + '"'


def _scalar(value: JSONValue) -> str:
    match value:
        case bool():
            return "true" if value else "false"
        case int():
            return str(value)
        case float() if math.isfinite(value):
            return repr(value)
        case str():
            return _string(value)
        case list():
            if any(isinstance(item, dict | list) for item in value):
                raise InternalError("The TOML writer can't write a list of tables or nested lists.")
            return "[" + ", ".join(_scalar(item) for item in value) + "]"
        case _:
            raise InternalError("The TOML writer was given a value it can't write.")


def _table(document: Mapping[str, JSONValue], path: Sequence[str], out: list[str]) -> None:
    scalars = [(key, value) for key, value in document.items() if value is not None and not isinstance(value, dict)]
    tables = [(key, value) for key, value in document.items() if isinstance(value, dict)]
    if path and (scalars or not tables):
        if out:
            out.append("")
        out.append("[" + ".".join(_key(part) for part in path) + "]")
    out.extend(f"{_key(key)} = {_scalar(value)}" for key, value in scalars)
    for key, value in tables:
        _table(value, [*path, key], out)


def dumps(document: Mapping[str, JSONValue]) -> str:
    """The document as TOML text, ending in a newline."""
    out: list[str] = []
    _table(document, [], out)
    return "\n".join(out) + "\n"
