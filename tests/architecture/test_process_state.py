"""No process-wide state changes anywhere in energatron (D1 §9.4, §13).

A stage's output may depend only on its code entry, inputs and params. A library setting, environment variable
or import-system change made by any module the `energatron stage` process imports would change outputs without
changing any code entry. Every form is matched by node shape, through import aliases, not only by call name.
The scoped forms (`pd.option_context`, `np.errstate`, `decimal.localcontext`, `warnings.catch_warnings`) are
allowed.
"""

import ast

import pytest

from tests.architecture._support import PACKAGE_DIR, import_aliases, qualified_name, source_files

BANNED_CALLS = frozenset(
    {
        "pandas.set_option",
        "numpy.seterr",
        "numpy.set_printoptions",
        "locale.setlocale",
        "warnings.filterwarnings",
        "warnings.simplefilter",
        "sys.setrecursionlimit",
        "os.chdir",
        "os.putenv",
        "os.unsetenv",
        "random.seed",
        "numpy.random.seed",
    }
)
# Any use at all, called or not: the context these return is process-wide.
BANNED_NAMES = frozenset({"decimal.getcontext", "decimal.setcontext"})
# Nothing may be assigned to, augmented or deleted under these.
GUARDED = ("pandas.options", "os.environ", "sys.path", "sys.modules")
# Methods that mutate os.environ, sys.path or sys.modules in place.
MUTATING_METHODS = frozenset(
    {"append", "insert", "extend", "pop", "popitem", "remove", "clear", "update", "setdefault"}
)
MUTABLE_GLOBALS = frozenset({"os.environ", "sys.path", "sys.modules"})


def state_changes(source: str) -> list[str]:
    """Every process-wide state change in `source`, as `line <n>: <what>`."""
    tree = ast.parse(source)
    aliases = import_aliases(tree)
    found: list[str] = []

    def report(node: ast.AST, what: str) -> None:
        found.append(f"line {getattr(node, 'lineno', '?')}: {what}")

    def check_target(target: ast.expr) -> None:
        if isinstance(target, ast.Tuple | ast.List):
            for element in target.elts:
                check_target(element)
            return
        if isinstance(target, ast.Starred):
            check_target(target.value)
            return
        base = target
        while isinstance(base, ast.Subscript):
            base = base.value
        name = qualified_name(base, aliases)
        if name is not None and any(name == root or name.startswith(f"{root}.") for root in GUARDED):
            report(target, f"writes {name}")

    for node in ast.walk(tree):
        match node:
            case ast.Call(func=func):
                name = qualified_name(func, aliases)
                if name in BANNED_CALLS:
                    report(node, f"calls {name}")
                if isinstance(func, ast.Attribute) and func.attr in MUTATING_METHODS:
                    owner = qualified_name(func.value, aliases)
                    if owner in MUTABLE_GLOBALS:
                        report(node, f"mutates {owner}")
            case ast.Assign(targets=targets) | ast.Delete(targets=targets):
                for target in targets:
                    check_target(target)
            case ast.AugAssign(target=target) | ast.AnnAssign(target=target):
                check_target(target)
            case ast.Name() | ast.Attribute():
                name = qualified_name(node, aliases)
                if name in BANNED_NAMES:
                    report(node, f"uses {name}")
            case _:
                pass
    return found


def test_energatron_changes_no_process_state() -> None:
    files = source_files()
    assert files, "found no energatron modules to scan"
    found = [
        f"{path.relative_to(PACKAGE_DIR)} {change}"
        for path in files
        for change in state_changes(path.read_text(encoding="utf-8"))
    ]
    assert found == []


@pytest.mark.parametrize(
    "source",
    [
        # library settings
        "import pandas as pd\npd.set_option('display.max_rows', 5)",
        "from pandas import set_option\nset_option('display.max_rows', 5)",
        "import pandas as pd\npd.options.display.max_rows = 5",
        "import pandas\npandas.options.display.precision += 1",
        "import pandas as pd\ndel pd.options.display.max_rows",
        "import numpy as np\nnp.seterr(all='raise')",
        "import numpy as np\nnp.set_printoptions(precision=3)",
        "import decimal\ndecimal.getcontext().prec = 5",
        "from decimal import getcontext\ncontext = getcontext()",
        "import decimal\ndecimal.setcontext(decimal.Context())",
        "import locale\nlocale.setlocale(locale.LC_ALL, '')",
        "import warnings\nwarnings.filterwarnings('ignore')",
        "import warnings\nwarnings.simplefilter('ignore')",
        "import sys\nsys.setrecursionlimit(10_000)",
        # the process environment
        "import os\nos.chdir('..')",
        "import os\nos.putenv('NAME', 'value')",
        "import os\nos.unsetenv('NAME')",
        "import os\nos.environ['NAME'] = 'value'",
        "from os import environ\nenviron['NAME'] = 'value'",
        "import os\nos.environ['NAME'] += 'value'",
        "import os\nos.environ['NAME']: str = 'value'",
        "import os\nos.environ = {}",
        "import os\nfirst, os.environ['NAME'] = 'a', 'b'",
        "import os\ndel os.environ['NAME']",
        "import os\nos.environ.pop('NAME')",
        "import os\nos.environ.popitem()",
        "import os\nos.environ.update(NAME='value')",
        "import os\nos.environ.setdefault('NAME', 'value')",
        "import os\nos.environ.clear()",
        # the import system
        "import sys\nsys.path = []",
        "import sys\nsys.path += ['src']",
        "import sys\nsys.path[0] = 'src'",
        "import sys\ndel sys.path[0]",
        "import sys\nsys.path.append('src')",
        "import sys\nsys.path.insert(0, 'src')",
        "import sys\nsys.path.extend(['src'])",
        "import sys\nsys.path.pop()",
        "import sys\nsys.path.remove('src')",
        "import sys\nsys.path.clear()",
        "import sys\nsys.modules['name'] = sys",
        "import sys\ndel sys.modules['name']",
        "import sys\nsys.modules.pop('name')",
        "import sys\nsys.modules.update({})",
        "import sys\nsys.modules.setdefault('name', sys)",
        "import sys\nsys.modules.clear()",
        # global random state
        "import random\nrandom.seed(1)",
        "import numpy as np\nnp.random.seed(1)",
        "from numpy import random as npr\nnpr.seed(1)",
    ],
)
def test_scan_flags_each_form(source: str) -> None:
    assert len(state_changes(source)) == 1


@pytest.mark.parametrize(
    "source",
    [
        "import pandas as pd\nwith pd.option_context('display.max_rows', 5):\n    pass",
        "import numpy as np\nwith np.errstate(all='raise'):\n    pass",
        "import decimal\nwith decimal.localcontext() as context:\n    context.prec = 5",
        "import warnings\nwith warnings.catch_warnings(action='ignore'):\n    pass",
        "import os\nvalue = os.environ.get('NAME')",
        "import os\nvalue = os.environ['NAME']",
        "import os\ncopy = os.environ.copy()",
        "import sys\nfirst = sys.path[0]",
        "import pandas as pd\nrows = pd.options.display.max_rows",
        "settings = {}\nsettings.update(name='value')",
        "import random\nrng = random.Random(1)",
        "import numpy as np\nrng = np.random.default_rng(1)",
    ],
)
def test_scan_allows_scoped_forms_and_reads(source: str) -> None:
    assert state_changes(source) == []
