"""Stage code is deterministic and reaches code and data only through its code entry (D1 §9.4, §13).

An AST scan of every module in every registered stage's module set (D1 §11.1), not just `services/stages/**`.
Names are resolved through import aliases, so `np.random` and `from datetime import date; date.today()` are caught.
The two named exceptions are allowed by exact finding, per file.
"""

import ast
from collections.abc import Mapping

import pytest

from energatron.adapters.pipeline.closure import module_closure
from energatron.services.stages.registry import STAGES
from tests.architecture._support import PACKAGE_DIR, import_aliases, qualified_name

# Modules a stage may not import or reach, nor anything under them.
BANNED_MODULES = (
    "getpass",
    "locale",
    "random",
    "uuid",
    "secrets",
    "numpy.random",
    "importlib",
    "pkgutil",
    "runpy",
    "pickle",
    "marshal",
    "shelve",
)
# Qualified names a stage may not use, nor anything under them.
BANNED_NAMES = (
    "os.environ",
    "os.getenv",
    "os.getcwd",
    "os.getcwdb",
    "pathlib.Path.cwd",
    "pathlib.Path.home",
    "socket.gethostname",
    "platform.node",
    "time.time",
    "datetime.datetime.now",
    "datetime.datetime.utcnow",
    "datetime.datetime.today",
    "datetime.date.today",
)
# Bare names or attributes, whatever they belong to.
BANNED_IDENTIFIERS = frozenset(
    {
        "entry_points",
        "read_pickle",
        "__import__",
        "__file__",
        "__path__",
        "__spec__",
        "__loader__",
        "__version__",
    }
)
# Banned only as calls of the bare builtin: `re.compile` and `pd.eval` are fine.
BANNED_BUILTIN_CALLS = frozenset({"exec", "eval", "compile"})

# The two exceptions D1 §9.4 names, by package-relative file.
ALLOWED: Mapping[str, frozenset[str]] = {
    "__init__.py": frozenset({"imports importlib.metadata", "uses importlib.metadata.version", "uses __version__"}),
    "services/stages/runner.py": frozenset({"imports importlib.resources", "uses importlib.resources.files"}),
}


def _under(name: str, roots: tuple[str, ...]) -> bool:
    return any(name == root or name.startswith(f"{root}.") for root in roots)


def impurities(source: str) -> set[str]:
    """Every banned import or use in `source`, as `imports <module>`, `uses <name>` or `calls <builtin>`."""
    tree = ast.parse(source)
    aliases = import_aliases(tree)
    inner = {id(node.value) for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    found: set[str] = set()
    for node in ast.walk(tree):
        match node:
            case ast.Import(names=names):
                found |= {f"imports {alias.name}" for alias in names if _under(alias.name, BANNED_MODULES)}
            case ast.ImportFrom(module=str(module), level=0, names=names):
                if _under(module, BANNED_MODULES):
                    found.add(f"imports {module}")
                for alias in names:
                    if _under(f"{module}.{alias.name}", BANNED_MODULES):
                        found.add(f"imports {module}.{alias.name}")
                    if alias.name in BANNED_IDENTIFIERS:
                        found.add(f"uses {alias.name}")
            case ast.Call(func=ast.Name(id=name)) if name in BANNED_BUILTIN_CALLS:
                found.add(f"calls {name}")
            case ast.Name() | ast.Attribute() if id(node) not in inner:
                last = node.id if isinstance(node, ast.Name) else node.attr
                name = qualified_name(node, aliases)
                if last in BANNED_IDENTIFIERS:
                    found.add(f"uses {last}")
                elif name is not None and _under(name, BANNED_MODULES + BANNED_NAMES):
                    found.add(f"uses {name}")
            case _:
                pass
    return found


def stage_module_files() -> list[str]:
    """Every package-relative file in any registered stage's module set, sorted."""
    files: set[str] = set()
    for spec in STAGES.values():
        files |= set(module_closure(spec.impl.__module__, package_dir=PACKAGE_DIR).files)
    return sorted(files)


@pytest.mark.parametrize("relative", stage_module_files())
def test_stage_module_set_is_pure(relative: str) -> None:
    found = impurities((PACKAGE_DIR / relative).read_text(encoding="utf-8"))
    assert found - ALLOWED.get(relative, frozenset()) == set()


def test_the_stage_module_sets_include_the_two_named_exceptions() -> None:
    files = stage_module_files()
    assert "__init__.py" in files
    assert "services/stages/runner.py" in files


# The matcher, on synthetic sources: each banned form is caught.


@pytest.mark.parametrize(
    ("source", "finding"),
    [
        ("import os\nos.environ['X']\n", "uses os.environ"),
        ("import os\nos.environ.get('X')\n", "uses os.environ.get"),
        ("from os import getenv\ngetenv('X')\n", "uses os.getenv"),
        ("import os\nos.getcwd()\n", "uses os.getcwd"),
        ("from pathlib import Path\nPath.cwd()\n", "uses pathlib.Path.cwd"),
        ("from pathlib import Path\nPath.home()\n", "uses pathlib.Path.home"),
        ("import socket\nsocket.gethostname()\n", "uses socket.gethostname"),
        ("import platform\nplatform.node()\n", "uses platform.node"),
        ("import getpass\n", "imports getpass"),
        ("import locale\n", "imports locale"),
        ("import random\n", "imports random"),
        ("from uuid import uuid4\n", "imports uuid"),
        ("import secrets\n", "imports secrets"),
        ("import numpy.random\n", "imports numpy.random"),
        ("from numpy import random\n", "imports numpy.random"),
        ("import numpy as np\nnp.random.default_rng(0)\n", "uses numpy.random.default_rng"),
        ("import time\ntime.time()\n", "uses time.time"),
        ("from datetime import datetime\ndatetime.now()\n", "uses datetime.datetime.now"),
        ("import datetime\ndatetime.datetime.utcnow()\n", "uses datetime.datetime.utcnow"),
        ("from datetime import date\ndate.today()\n", "uses datetime.date.today"),
        ("import importlib\n", "imports importlib"),
        ("from importlib import import_module\n", "imports importlib"),
        ("__import__('x')\n", "uses __import__"),
        ("import pkgutil\n", "imports pkgutil"),
        ("import runpy\n", "imports runpy"),
        ("exec('x = 1')\n", "calls exec"),
        ("eval('1')\n", "calls eval"),
        ("compile('1', 'f', 'eval')\n", "calls compile"),
        ("from importlib.metadata import entry_points\n", "uses entry_points"),
        ("import pickle\n", "imports pickle"),
        ("import marshal\n", "imports marshal"),
        ("import shelve\n", "imports shelve"),
        ("import pandas as pd\npd.read_pickle('x')\n", "uses read_pickle"),
        ("x = __file__\n", "uses __file__"),
        ("import energatron\nenergatron.__path__\n", "uses __path__"),
        ("x = __spec__\n", "uses __spec__"),
        ("x = __loader__\n", "uses __loader__"),
        ("import energatron\nenergatron.__version__\n", "uses __version__"),
    ],
)
def test_the_matcher_catches_each_banned_form(source: str, finding: str) -> None:
    assert finding in impurities(source)


@pytest.mark.parametrize(
    "source",
    [
        "import re\nre.compile('x')\n",
        "import pandas as pd\npd.eval('1 + 1')\n",
        "import numpy as np\nnp.array([1])\n",
        "from datetime import datetime\ndatetime(2026, 1, 1)\n",
        "import pandas as pd\nwith pd.option_context('mode.copy_on_write', True):\n    pass\n",
    ],
)
def test_the_matcher_allows_the_harmless_lookalikes(source: str) -> None:
    assert impurities(source) == set()
