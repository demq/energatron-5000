"""Core does no I/O (D1 §3, §13).

`lint-imports` keeps I/O modules out of `energatron.core`; this AST scan catches the calls that import rules can't
see: built-in file and console access, path and file methods, pandas readers and writers, and clock reads.
"""

import ast

import pytest

from tests.architecture._support import PACKAGE_DIR, import_aliases, qualified_name, source_files

BANNED_BUILTINS = frozenset({"open", "input", "print"})
BANNED_ATTRIBUTE_CALLS = frozenset(
    {
        # files and directories
        "read_text",
        "read_bytes",
        "write_text",
        "write_bytes",
        "open",
        "mkdir",
        "unlink",
        "rmdir",
        "rename",
        "replace",
        "touch",
        "iterdir",
        "glob",
        "rglob",
        # pandas writers
        "to_csv",
        "to_parquet",
        "to_json",
        "to_excel",
        # the clock: datetime.now, utcnow, today and date.today, on any receiver
        "now",
        "utcnow",
        "today",
    }
)
BANNED_FUNCTIONS = frozenset({"time.time", "time.time_ns"})


def impurities(source: str) -> list[str]:
    """Every call in `source` that core may not make, as `line <n>: <call>`."""
    tree = ast.parse(source)
    aliases = import_aliases(tree)
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = qualified_name(func, aliases)
        if isinstance(func, ast.Name) and func.id in BANNED_BUILTINS:
            found.append(f"line {node.lineno}: {func.id}()")
        elif isinstance(func, ast.Attribute) and func.attr in BANNED_ATTRIBUTE_CALLS:
            found.append(f"line {node.lineno}: .{func.attr}()")
        elif name is not None and (name in BANNED_FUNCTIONS or _is_pandas_reader(name)):
            found.append(f"line {node.lineno}: {name}()")
    return found


def _is_pandas_reader(name: str) -> bool:
    return name.startswith("pandas.") and name.rpartition(".")[2].startswith("read_")


def test_core_does_no_io() -> None:
    files = source_files("core")
    assert files, "found no core modules to scan"
    found = [
        f"{path.relative_to(PACKAGE_DIR)} {impurity}"
        for path in files
        for impurity in impurities(path.read_text(encoding="utf-8"))
    ]
    assert found == []


@pytest.mark.parametrize(
    "source",
    [
        "open('f')",
        "input()",
        "print('x')",
        "p.read_text()",
        "p.read_bytes()",
        "p.write_text('x')",
        "p.write_bytes(b'x')",
        "p.open()",
        "p.mkdir()",
        "p.unlink()",
        "p.rmdir()",
        "p.rename('g')",
        "p.replace('g')",
        "p.touch()",
        "p.iterdir()",
        "p.glob('*')",
        "p.rglob('*')",
        "import pandas as pd\npd.read_csv('f')",
        "import pandas\npandas.read_parquet('f')",
        "from pandas import read_json\nread_json('f')",
        "frame.to_csv('f')",
        "frame.to_parquet('f')",
        "frame.to_json('f')",
        "frame.to_excel('f')",
        "from datetime import datetime\ndatetime.now()",
        "import datetime\ndatetime.datetime.utcnow()",
        "from datetime import datetime\ndatetime.today()",
        "from datetime import date\ndate.today()",
        "import time\ntime.time()",
        "import time as clock\nclock.time()",
        "from time import time\ntime()",
        "import time\ntime.time_ns()",
    ],
)
def test_scan_flags_each_banned_form(source: str) -> None:
    assert len(impurities(source)) == 1


@pytest.mark.parametrize(
    "source",
    [
        "import re\nre.compile('x')",
        "from datetime import UTC, datetime\ndatetime(2026, 1, 2, tzinfo=UTC).astimezone(UTC)",
        "import datetime\ndatetime.time(12, 30)",
        "import json\njson.dumps({'a': 1})",
        "import hashlib\nhashlib.sha256(b'x').hexdigest()",
        "import pandas as pd\npd.DataFrame({'a': [1]})",
        "reader.reading()",
    ],
)
def test_scan_allows_pure_calls(source: str) -> None:
    assert impurities(source) == []
