"""Helpers for the architecture tests: the package's source files, its modules, and import-aware AST names."""

import ast
import importlib
import pkgutil
from collections.abc import Mapping
from pathlib import Path
from types import ModuleType

import energatron

PACKAGE_DIR = Path(energatron.__file__).resolve().parent


def source_files(subpackage: str = "") -> list[Path]:
    """Every `.py` file under `energatron/<subpackage>`, sorted."""
    return sorted((PACKAGE_DIR / subpackage).rglob("*.py"))


def import_all_modules() -> list[ModuleType]:
    """Import every module in the package except `__main__`, so that subclass scans see all of them."""
    modules: list[ModuleType] = [energatron]
    for info in pkgutil.walk_packages(energatron.__path__, prefix="energatron."):
        if info.name.rpartition(".")[2] != "__main__":
            modules.append(importlib.import_module(info.name))
    return modules


def import_aliases(tree: ast.Module) -> dict[str, str]:
    """Map each name bound by an absolute import anywhere in `tree` to the qualified name it stands for.

    Scopes are ignored: a scanner that over-matches is safe, one that misses an alias is not.
    """
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname is None:
                    top = alias.name.partition(".")[0]
                    aliases[top] = top
                else:
                    aliases[alias.asname] = alias.name
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module is not None:
            for alias in node.names:
                if alias.name != "*":
                    aliases[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    return aliases


def qualified_name(node: ast.expr, aliases: Mapping[str, str]) -> str | None:
    """The dotted name of a `Name` or `Attribute` chain, its root resolved through `aliases`; else None."""
    attributes: list[str] = []
    while isinstance(node, ast.Attribute):
        attributes.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    return ".".join([aliases.get(node.id, node.id), *reversed(attributes)])
