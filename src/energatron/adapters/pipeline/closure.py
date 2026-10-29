"""A stage's module set: its module plus every package module it imports, directly or not (D1 §11.1 step 1).

Found by parsing source with `ast`, never by importing. Every `import` and `from … import` counts wherever it sits
(function bodies, `if TYPE_CHECKING:` blocks): over-counting is safe, missing one is not. Relative imports resolve
against the importing module; `from pkg import name` adds `pkg.name` when that is a module and `pkg` either way;
`from pkg import *` adds `pkg` and every module under it; every parent package's `__init__.py` is added, because it
runs on import. A module with no `.py` source on disk → `E_INTERNAL`.

The package is the directory `package_dir`, named after it, so tests can use a synthetic tree.
"""

import ast
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from energatron.core.errors import InternalError

_INIT = "__init__.py"


@dataclass(frozen=True)
class Closure:
    """A module set: dotted module names and their package-relative POSIX files, both sorted; and the top-level
    modules outside the package that it imports, sorted, with those imported only under `TYPE_CHECKING`."""

    modules: tuple[str, ...]
    files: tuple[str, ...]
    external: tuple[str, ...]
    checking_only: tuple[str, ...]


def module_closure(module: str, *, package_dir: Path) -> Closure:
    """The module set of `module`, a dotted name inside the package at `package_dir`."""
    return _Walker(package_dir).run(module)


@dataclass(frozen=True)
class _Import:
    module: str  # absolute dotted name
    names: tuple[str, ...]  # for `from module import names`; empty for `import module`
    type_checking: bool


class _Walker:
    def __init__(self, package_dir: Path) -> None:
        self._dir = package_dir
        self._package = package_dir.name
        self._files: dict[str, Path] = {}
        self._queue: deque[str] = deque()
        self._external: dict[str, bool] = {}  # top-level name → imported outside TYPE_CHECKING somewhere

    def run(self, module: str) -> Closure:
        self._add(module)
        while self._queue:
            name = self._queue.popleft()
            for found in self._imports(name):
                self._follow(found)
        modules = tuple(sorted(self._files))
        files = tuple(sorted(path.relative_to(self._dir).as_posix() for path in self._files.values()))
        external = tuple(sorted(self._external))
        checking_only = tuple(sorted(name for name, runtime in self._external.items() if not runtime))
        return Closure(modules=modules, files=files, external=external, checking_only=checking_only)

    # Modules and their files

    def _source(self, module: str) -> Path | None:
        """The `.py` file of a package module, or None when there is none."""
        parts = module.split(".")
        if parts[0] != self._package:
            return None
        base = self._dir.joinpath(*parts[1:])
        if (base / _INIT).is_file():
            return base / _INIT
        if len(parts) > 1:
            file = base.with_name(f"{base.name}.py")
            if file.is_file():
                return file
        return None

    def _add(self, module: str) -> None:
        """Add `module` and every parent package to the set; queue the new ones to scan."""
        parts = module.split(".")
        for end in range(1, len(parts) + 1):
            name = ".".join(parts[:end])
            if name in self._files:
                continue
            source = self._source(name)
            if source is None:
                raise InternalError(f"Module {name} has no .py source in the package.", module=name)
            self._files[name] = source
            self._queue.append(name)

    def _is_module(self, module: str) -> bool:
        if self._source(module) is not None:
            return True
        directory = self._dir.joinpath(*module.split(".")[1:])
        if directory.is_dir():
            raise InternalError(f"Module {module} has no .py source in the package.", module=module)
        return False

    def _submodules(self, package: str) -> list[str]:
        """Every module under a package directory, recursively, skipping `__pycache__`."""
        source = self._files[package]
        if source.name != _INIT:
            return []
        found: list[str] = []
        for path in sorted(source.parent.rglob("*.py")):
            relative = path.relative_to(self._dir)
            if "__pycache__" in relative.parts:
                continue
            parts = [self._package, *relative.with_suffix("").parts]
            if parts[-1] == "__init__":
                parts.pop()
            found.append(".".join(parts))
        return found

    # Imports

    def _follow(self, found: _Import) -> None:
        top = found.module.partition(".")[0]
        if top != self._package:
            self._external[top] = self._external.get(top, False) or not found.type_checking
            return
        self._add(found.module)
        for name in found.names:
            if name == "*":
                for submodule in self._submodules(found.module):
                    self._add(submodule)
            elif self._is_module(f"{found.module}.{name}"):
                self._add(f"{found.module}.{name}")

    def _imports(self, module: str) -> list[_Import]:
        path = self._files[module]
        tree = ast.parse(path.read_bytes(), filename=path.name)
        is_package = path.name == _INIT
        found: list[_Import] = []

        def visit(node: ast.AST, type_checking: bool) -> None:
            match node:
                case ast.Import(names=names):
                    found.extend(_Import(alias.name, (), type_checking) for alias in names)
                case ast.ImportFrom(module=target, level=level, names=names):
                    absolute = self._absolute(module, is_package, target, level)
                    found.append(_Import(absolute, tuple(alias.name for alias in names), type_checking))
                case ast.If(test=test, body=body, orelse=orelse) if _is_type_checking(test):
                    for child in body:
                        visit(child, True)
                    for child in orelse:
                        visit(child, type_checking)
                    return
                case _:
                    pass
            for child in ast.iter_child_nodes(node):
                visit(child, type_checking)

        visit(tree, False)
        return found

    def _absolute(self, module: str, is_package: bool, target: str | None, level: int) -> str:
        """The absolute dotted name a `from` import names, resolving a relative one against `module`."""
        if level == 0:
            if target is None:
                raise InternalError(f"Module {module} has an import with no module.", module=module)
            return target
        base = module.split(".") if is_package else module.split(".")[:-1]
        if level - 1 >= len(base):
            raise InternalError(f"Module {module} has a relative import above its package.", module=module)
        base = base[: len(base) - (level - 1)]
        return ".".join([*base, *(target.split(".") if target else [])])


def _is_type_checking(test: ast.expr) -> bool:
    """`if TYPE_CHECKING:` or `if <anything>.TYPE_CHECKING:`."""
    match test:
        case ast.Name(id="TYPE_CHECKING") | ast.Attribute(attr="TYPE_CHECKING"):
            return True
        case _:
            return False
