"""No region-specific values in code (plan §14.1; P2 spec §5.2, §13.1).

A time zone key (`Region/City` form) or a TOU window (`HH:MM-HH:MM`) written as a string literal in `core/**` or
`services/**` is a region's value that belongs in its profile (`regions/<name>.yaml`). Docstrings are prose and are
skipped. Adapters may name them: `wiring.py` reads the profile, and nothing else there does.
"""

import ast
import re

import pytest

from energatron.core.models.params import AnalysisParams
from tests.architecture._support import PACKAGE_DIR, source_files

ZONE_KEY = re.compile(r"[A-Z][A-Za-z0-9_+-]*(?:/[A-Za-z0-9_+-]+)+")
WINDOW = re.compile(r"[0-9]{1,2}:[0-9]{2}\s*-\s*[0-9]{1,2}:[0-9]{2}")
LAYERS = ("core", "services")


def region_values(source: str) -> list[str]:
    """Every string literal in `source` that is a zone key or a window, as `line <n>: zone key|window`."""
    tree = ast.parse(source)
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            first = node.body[0] if node.body else None
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                docstrings.add(id(first.value))
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
            text = node.value.strip()
            if ZONE_KEY.fullmatch(text):
                found.append(f"line {node.lineno}: zone key")
            elif WINDOW.fullmatch(text):
                found.append(f"line {node.lineno}: window")
    return found


def layer_files() -> list[str]:
    return [path.relative_to(PACKAGE_DIR).as_posix() for layer in LAYERS for path in source_files(layer)]


@pytest.mark.parametrize("relative", layer_files())
def test_core_and_services_hold_no_zone_key_or_tou_window(relative: str) -> None:
    assert region_values((PACKAGE_DIR / relative).read_text(encoding="utf-8")) == []


def test_the_scan_covers_core_and_services() -> None:
    files = layer_files()
    assert "core/models/params.py" in files
    assert "services/stages/runner.py" in files


def strings_in(value: object) -> list[str]:
    """Every string in a dumped document, keys and values."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for key, item in value.items() for text in [str(key), *strings_in(item)]]
    if isinstance(value, list):
        return [text for item in value for text in strings_in(item)]
    return []


def test_the_analysis_defaults_hold_no_zone_key_or_window() -> None:
    """P3 spec §13.1: the season labels (`DJF`…) name months, not zones, and the first window set is `null`."""
    dumped = AnalysisParams.defaults(None).model_dump(mode="json")
    assert dumped["tou"]["window_sets"][0]["windows"] is None
    assert [text for text in strings_in(dumped) if ZONE_KEY.fullmatch(text) or WINDOW.fullmatch(text)] == []


@pytest.mark.parametrize(
    ("source", "finding"),
    [
        ('ZONE = "Australia/Adelaide"\n', "line 1: zone key"),
        ('def f():\n    return ["Europe/Isle_of_Man"]\n', "line 2: zone key"),
        ('x = "America/Argentina/Buenos_Aires"\n', "line 1: zone key"),
        ('x = "Etc/GMT+10"\n', "line 1: zone key"),
        ('x = "06:00-10:00"\n', "line 1: window"),
        ('x = ["15:00-01:00", "01:00-06:00"]\n', "line 1: window"),
        ('x = "6:00 - 10:00"\n', "line 1: window"),
    ],
)
def test_the_scan_catches_a_zone_key_or_window(source: str, finding: str) -> None:
    assert finding in region_values(source)


@pytest.mark.parametrize(
    "source",
    [
        '"""A zone such as Australia/Adelaide, and a window 06:00-10:00."""\n',
        'def f():\n    """Region/City"""\n',
        'class A:\n    """08:00-20:00"""\n',
        'x = "data/raw/sapn_interval"\n',
        'x = "regions/sa.yaml"\n',
        'x = "HH:MM-HH:MM"\n',
        'x = "a window is HH:MM-HH:MM"\n',
        'x = "UTC"\n',
    ],
)
def test_the_scan_skips_docstrings_and_lookalikes(source: str) -> None:
    assert region_values(source) == []
