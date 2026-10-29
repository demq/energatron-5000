"""A stage's module set and code entry, on a synthetic package tree (D1 §11.1, §13).

The tree is a package named `pkg` under the test's `tmp_path`; its third-party distributions are a fake
`Distributions`, so nothing here depends on what the test venv has installed.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from energatron.adapters.pipeline.closure import module_closure
from energatron.adapters.pipeline.fingerprint import normalise, stage_code
from energatron.core.canonical import content_hash
from energatron.core.errors import InternalError


def write(root: Path, relative: str, text: str = "") -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@dataclass
class FakeDistributions:
    """`top_level`: module → distributions; `installed`: distribution → (version, requirements). Lookups normalise
    names, as `importlib.metadata` does."""

    top_level_map: Mapping[str, Sequence[str]] = field(default_factory=dict)
    installed: Mapping[str, tuple[str, Sequence[str]]] = field(default_factory=dict)

    def top_level(self) -> Mapping[str, Sequence[str]]:
        return self.top_level_map

    def _find(self, distribution: str) -> tuple[str, Sequence[str]] | None:
        wanted = normalise(distribution)
        return next((value for name, value in self.installed.items() if normalise(name) == wanted), None)

    def version(self, distribution: str) -> str | None:
        found = self._find(distribution)
        return None if found is None else found[0]

    def requires(self, distribution: str) -> Sequence[str]:
        found = self._find(distribution)
        return () if found is None else found[1]


NO_DISTRIBUTIONS = FakeDistributions()


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """`pkg`: a stage module importing in every way D1 §11.1 lists, and modules it doesn't reach."""
    root = tmp_path / "pkg"
    write(root, "__init__.py", '"""The package."""\n')
    write(root, "core/__init__.py")
    write(root, "core/models.py", "import json\n")
    write(root, "core/helpers.py", "")
    write(root, "core/typing_only.py", "")
    write(root, "core/nested.py", "")
    write(root, "core/unused.py", "")
    write(root, "lib/__init__.py")
    write(root, "lib/a.py", "")
    write(root, "lib/deep/__init__.py")
    write(root, "lib/deep/b.py", "")
    write(root, "stages/__init__.py")
    write(
        root,
        "stages/one.py",
        "from typing import TYPE_CHECKING\n"
        "from ..core import models\n"
        "from pkg.core.helpers import something\n"
        "from pkg.lib import *\n"
        "if TYPE_CHECKING:\n"
        "    from pkg.core import typing_only\n"
        "def body():\n"
        "    import pkg.core.nested\n",
    )
    write(root, "stages/two.py", "from pkg.core import unused\n")
    write(root, "data/table.csv", "a,b\n")
    return root


def test_every_kind_of_import_is_found(tree: Path) -> None:
    closure = module_closure("pkg.stages.one", package_dir=tree)
    assert closure.modules == (
        "pkg",
        "pkg.core",
        "pkg.core.helpers",
        "pkg.core.models",
        "pkg.core.nested",  # imported inside a function body
        "pkg.core.typing_only",  # imported under TYPE_CHECKING
        "pkg.lib",
        "pkg.lib.a",  # star import: every submodule, recursively
        "pkg.lib.deep",
        "pkg.lib.deep.b",
        "pkg.stages",  # a parent package's __init__.py runs on import
        "pkg.stages.one",
    )
    assert "core/unused.py" not in closure.files
    assert closure.files == tuple(sorted(closure.files))
    assert "__init__.py" in closure.files
    assert "stages/__init__.py" in closure.files
    assert "lib/deep/b.py" in closure.files
    assert closure.external == ("json", "typing")


def test_relative_import_from_a_package_init_resolves_against_the_package(tmp_path: Path) -> None:
    root = tmp_path / "pkg"
    write(root, "__init__.py")
    write(root, "sub/__init__.py", "from . import leaf\nfrom .. import top\n")
    write(root, "sub/leaf.py")
    write(root, "top.py")
    write(root, "stage.py", "import pkg.sub\n")
    closure = module_closure("pkg.stage", package_dir=root)
    assert closure.modules == ("pkg", "pkg.stage", "pkg.sub", "pkg.sub.leaf", "pkg.top")


def test_a_module_with_no_source_is_an_internal_error(tmp_path: Path) -> None:
    root = tmp_path / "pkg"
    write(root, "__init__.py")
    write(root, "stage.py", "import pkg.missing\n")
    with pytest.raises(InternalError):
        module_closure("pkg.stage", package_dir=root)


def test_a_relative_import_above_the_package_is_an_internal_error(tmp_path: Path) -> None:
    root = tmp_path / "pkg"
    write(root, "__init__.py")
    write(root, "stage.py", "from ... import x\n")
    with pytest.raises(InternalError):
        module_closure("pkg.stage", package_dir=root)


def test_type_checking_only_imports_are_marked(tmp_path: Path) -> None:
    root = tmp_path / "pkg"
    write(root, "__init__.py")
    write(root, "stage.py", "import typing\nif typing.TYPE_CHECKING:\n    from _typeshed import StrPath\n")
    closure = module_closure("pkg.stage", package_dir=root)
    assert closure.external == ("_typeshed", "typing")
    assert closure.checking_only == ("_typeshed",)


# stage_code: what changes stage_sha256


def code_of(root: Path, module: str = "pkg.stages.one", package_data: Sequence[str] = ()) -> str:
    return stage_code(module, package_data, package_dir=root, distributions=NO_DISTRIBUTIONS).stage_sha256


def test_editing_a_module_in_the_set_changes_the_stage_hash(tree: Path) -> None:
    before = code_of(tree)
    write(tree, "core/helpers.py", "something = 1\n")
    assert code_of(tree) != before


def test_editing_a_module_outside_the_set_doesnt(tree: Path) -> None:
    before = code_of(tree)
    write(tree, "core/unused.py", "x = 1\n")
    write(tree, "stages/two.py", "y = 2\n")
    assert code_of(tree) == before


def test_a_changed_declared_package_data_file_changes_the_stage_hash(tree: Path) -> None:
    before = code_of(tree, package_data=("data",))
    write(tree, "data/table.csv", "a,b\n1,2\n")
    assert code_of(tree, package_data=("data",)) != before


def test_the_entry_lists_package_data_and_hashes_its_parts(tree: Path) -> None:
    code = stage_code("pkg.stages.one", ("data/table.csv",), package_dir=tree, distributions=NO_DISTRIBUTIONS)
    assert "data/table.csv" in code.files
    assert code.stage_sha256 == content_hash(
        {"source_sha256": code.source_sha256, "packages": {}, "python": code.python}
    )


# packages


def stage_importing(tmp_path: Path, source: str) -> Path:
    root = tmp_path / "pkg"
    write(root, "__init__.py")
    write(root, "stage.py", source)
    return root


def packages_of(root: Path, distributions: FakeDistributions) -> dict[str, str]:
    return stage_code("pkg.stage", (), package_dir=root, distributions=distributions).packages


def test_packages_follow_requirements_recursively_and_skip_what_isnt_installed(tmp_path: Path) -> None:
    root = stage_importing(tmp_path, "import frame\nimport os\n")
    distributions = FakeDistributions(
        top_level_map={"frame": ["Frame_Lib"], "other": ["other"]},
        installed={
            "Frame_Lib": ("3.0", ["Num.Py>=2; python_version >= '3.14'", "absent>=1"]),
            "num-py": ("2.5", ["base"]),
            "base": ("1.0", []),
            "other": ("9.9", []),  # installed, but the stage doesn't import it
        },
    )
    assert packages_of(root, distributions) == {"frame-lib": "3.0", "num-py": "2.5", "base": "1.0"}


def test_an_installed_extra_is_included(tmp_path: Path) -> None:
    root = stage_importing(tmp_path, "import frame\n")
    distributions = FakeDistributions(
        top_level_map={"frame": ["frame"]},
        installed={
            "frame": ("3.0", ['speedup>=1; extra == "performance"', 'tests>=1; extra == "test"']),
            "speedup": ("1.2", []),
        },
    )
    assert packages_of(root, distributions) == {"frame": "3.0", "speedup": "1.2"}


def test_an_import_with_no_installed_distribution_is_an_internal_error(tmp_path: Path) -> None:
    root = stage_importing(tmp_path, "import mystery\n")
    with pytest.raises(InternalError) as raised:
        packages_of(root, NO_DISTRIBUTIONS)
    assert raised.value.details["module"] == "mystery"


def test_a_namespace_module_brings_in_every_distribution(tmp_path: Path) -> None:
    root = stage_importing(tmp_path, "import space.one\n")
    distributions = FakeDistributions(
        top_level_map={"space": ["space-one", "space-two"]},
        installed={"space-one": ("1", []), "space-two": ("2", [])},
    )
    assert packages_of(root, distributions) == {"space-one": "1", "space-two": "2"}


def test_typeshed_is_skipped_under_type_checking(tmp_path: Path) -> None:
    root = stage_importing(
        tmp_path, "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from _typeshed import StrPath\n"
    )
    assert packages_of(root, NO_DISTRIBUTIONS) == {}


def test_typeshed_elsewhere_is_an_internal_error(tmp_path: Path) -> None:
    root = stage_importing(tmp_path, "import _typeshed\n")
    with pytest.raises(InternalError):
        packages_of(root, NO_DISTRIBUTIONS)


def test_a_changed_package_version_changes_the_stage_hash(tmp_path: Path) -> None:
    root = stage_importing(tmp_path, "import frame\n")
    old = FakeDistributions(top_level_map={"frame": ["frame"]}, installed={"frame": ("3.0", [])})
    new = FakeDistributions(top_level_map={"frame": ["frame"]}, installed={"frame": ("3.1", [])})
    assert (
        stage_code("pkg.stage", (), package_dir=root, distributions=old).stage_sha256
        != stage_code("pkg.stage", (), package_dir=root, distributions=new).stage_sha256
    )
