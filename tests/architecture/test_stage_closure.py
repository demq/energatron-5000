"""Stages don't depend on each other's code (D1 §11.1, §13).

On the real package: no stage's module set holds the registry or another stage's module, unless it is listed in
`ALLOWED` as a deliberate import, and `services/stages/__init__.py` holds nothing beyond a docstring.
"""

import ast
from collections.abc import Mapping

import pytest

from energatron.adapters.pipeline.closure import module_closure
from energatron.services.stages.registry import STAGES
from tests.architecture._support import PACKAGE_DIR

REGISTRY = "energatron.services.stages.registry"
# stage name → the other stages' modules it imports on purpose. None yet.
ALLOWED: Mapping[str, frozenset[str]] = {}


@pytest.mark.parametrize("name", list(STAGES))
def test_no_stage_reaches_the_registry_or_another_stage(name: str) -> None:
    modules = set(module_closure(STAGES[name].impl.__module__, package_dir=PACKAGE_DIR).modules)
    assert REGISTRY not in modules
    others = {spec.impl.__module__ for spec in STAGES.values() if spec.name != name}
    assert modules & others <= ALLOWED.get(name, frozenset())


def test_the_stages_package_init_holds_only_a_docstring() -> None:
    tree = ast.parse((PACKAGE_DIR / "services" / "stages" / "__init__.py").read_text(encoding="utf-8"))
    body = tree.body
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        body = body[1:]
    assert body == []
