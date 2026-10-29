"""The closure against what each stage really loads (D1 §11.1, §13).

For every registered stage, the test computes its `StageCode`, then runs the stage on fixture inputs in a fresh
interpreter. The child rebuilds the code entry from JSON on stdin with the core model, never importing the fingerprint
adapter; it snapshots `sys.modules` before its first `energatron` import and imports only `runner.py` and the
stage's module. Every `energatron.*` module it added must be in the module set, and every non-stdlib top-level module
it added must map to a distribution in `packages`.
"""

import importlib.metadata
import json
import re
import subprocess
import sys
import sysconfig
from pathlib import Path

import pytest

from energatron.adapters import wiring
from energatron.core.models.provenance import StageCode
from energatron.services.stages.registry import STAGES
from tests.integration._stage_inputs import build_inputs

pytestmark = pytest.mark.integration

CHILD = """\
import json, sys
before = set(sys.modules)
from energatron.core.models.provenance import StageCode
from energatron.services.stages import runner
from {module} import SPEC as spec
from pathlib import Path
root = Path(sys.argv[1])
code = StageCode.model_validate_json(sys.stdin.read())
prepared = runner.prepare_stage(spec, root=root, params_file=root / "params.yaml")
runner.run_stage(spec, prepared.inputs, prepared.params, prepared.outputs, code=code, recorded_code=code.stage_sha256)
added = sorted(set(sys.modules) - before)
print(json.dumps({{name: getattr(sys.modules[name], "__file__", None) for name in added}}))
"""


def normalise(distribution: str) -> str:
    return re.sub(r"[-_.]+", "-", distribution).lower()


def module_file(name: str) -> set[str]:
    """The package-relative files a module `energatron.<…>` can come from: a module or a package's `__init__.py`."""
    parts = name.split(".")[1:]
    if not parts:
        return {"__init__.py"}
    stem = "/".join(parts)
    return {f"{stem}.py", f"{stem}/__init__.py"}


def is_stdlib(top: str, file: str | None) -> bool:
    """A standard-library module: named in `sys.stdlib_module_names`, or a file in the stdlib directory outside
    site-packages, such as `_sysconfigdata_…`, which the list leaves out."""
    if top in sys.stdlib_module_names:
        return True
    if file is None:
        return False
    path = Path(file).resolve()
    stdlib = Path(sysconfig.get_paths()["stdlib"]).resolve()
    purelib = Path(sysconfig.get_paths()["purelib"]).resolve()
    return path.is_relative_to(stdlib) and not path.is_relative_to(purelib)


def is_runtime_artefact(top: str, file: str | None) -> bool:
    """Modules Cython registers at run time, with no file and no distribution."""
    return file is None and (top == "cython_runtime" or top.startswith("_cython_"))


def owned_by(file: str | None, packages: set[str]) -> bool:
    """Whether one of `packages` installed `file`: an extension module placed at top level from inside a
    distribution's own directory, such as pandas' `_cyutility`."""
    if file is None:
        return False
    target = Path(file).resolve()
    for distribution in importlib.metadata.distributions():
        name = distribution.metadata.get("Name")
        if name is None or normalise(name) not in packages:
            continue
        for item in distribution.files or ():
            if Path(str(distribution.locate_file(item))).resolve() == target:
                return True
    return False


@pytest.mark.parametrize("name", list(STAGES))
def test_a_stage_loads_only_what_its_code_entry_covers(tmp_path: Path, name: str) -> None:
    spec = STAGES[name]
    code = wiring.stage_code(name)
    root = tmp_path / "workspace"
    build_inputs(name, root)
    child = subprocess.run(
        [sys.executable, "-c", CHILD.format(module=spec.impl.__module__), str(root)],
        input=code.model_dump_json(),
        capture_output=True,
        text=True,
        cwd=tmp_path,
        check=False,
    )
    assert child.returncode == 0, child.stderr
    loaded: dict[str, str | None] = json.loads(child.stdout)
    added = list(loaded)
    assert StageCode.model_validate_json(code.model_dump_json()) == code

    files = set(code.files)
    outside = [module for module in added if module.split(".")[0] == "energatron" and not module_file(module) & files]
    assert outside == [], "energatron modules loaded outside the module set"

    packages = {normalise(distribution) for distribution in code.packages}
    owners = importlib.metadata.packages_distributions()
    tops = sorted({module.split(".")[0] for module in added} - {"energatron"})
    unmapped = [
        top
        for top in tops
        if not is_stdlib(top, loaded.get(top))
        and not is_runtime_artefact(top, loaded.get(top))
        and not {normalise(owner) for owner in owners.get(top, [])} & packages
        and not owned_by(loaded.get(top), packages)
    ]
    assert unmapped == [], "third-party modules loaded that no distribution in packages provides"
