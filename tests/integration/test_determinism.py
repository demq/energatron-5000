"""Every registered stage, run twice on the same fixture inputs into fresh directories, writes byte-identical files,
`_provenance.json` included (D1 §9.4, §10, §13). From P6, the whole pipeline twice (plan §13)."""

from pathlib import Path

import pytest

from energatron.adapters import wiring
from energatron.services.stages.registry import STAGES
from energatron.services.stages.runner import prepare_stage, run_stage
from tests.integration._stage_inputs import BUILDERS, build_inputs

pytestmark = pytest.mark.integration


def test_every_registered_stage_has_fixture_inputs() -> None:
    assert sorted(BUILDERS) == sorted(STAGES)


def files_of(directory: Path) -> dict[str, bytes]:
    return {
        path.relative_to(directory).as_posix(): path.read_bytes() for path in directory.rglob("*") if path.is_file()
    }


def run_once(name: str, base: Path) -> dict[str, dict[str, bytes]]:
    """Build the inputs in a fresh root under `base` and run the stage; every output's files by relative path."""
    root = base / "workspace"
    build_inputs(name, root)
    spec = STAGES[name]
    prepared = prepare_stage(spec, root=root, params_file=root / "params.yaml")
    code = wiring.stage_code(name)
    run_stage(spec, prepared.inputs, prepared.params, prepared.outputs, code=code, recorded_code=code.stage_sha256)
    return {output: files_of(path) for output, path in prepared.outputs.items()}


@pytest.mark.parametrize("name", list(STAGES))
def test_a_stage_run_twice_writes_the_same_bytes(tmp_path: Path, name: str) -> None:
    first = run_once(name, tmp_path / "first")
    second = run_once(name, tmp_path / "second")
    assert first == second
    for output, files in first.items():
        assert "_provenance.json" in files, output
        assert str(tmp_path).encode() not in files["_provenance.json"]
