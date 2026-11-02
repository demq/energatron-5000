"""`localise` reads the time zone from `tzdata`, never from the system (P2 spec §6, §13.3).

The stage runs twice in a child interpreter on identical copies of one workspace: once as it is, once with
`PYTHONTZPATH` pointing at a directory that holds a wrong `Australia/Adelaide`, a fixed-offset zone. The system
database is what `zoneinfo.ZoneInfo("Australia/Adelaide")` would read, so the outputs being byte for byte equal
shows the stage never touched it. The fixture spans both daylight-saving changes and its exports are generated under
`nem_aest`, so the verdict is `confirmed` and the gate lets the stage through. Nothing here touches the real workspace.
"""

import os
import shutil
import struct
import subprocess
import sys
from pathlib import Path
from typing import Final

import pytest

from tests.integration._stage_inputs import build_inputs

pytestmark = pytest.mark.integration

FIXED_OFFSET_SECONDS: Final = 3 * 3600
CHILD: Final = """\
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from energatron.adapters import wiring
from energatron.services.stages.registry import STAGES
from energatron.services.stages.runner import prepare_stage, run_stage

root = Path(sys.argv[1])
spec = STAGES["localise"]
prepared = prepare_stage(spec, root=root, params_file=root / "params.yaml")
code = wiring.stage_code("localise")
run_stage(spec, prepared.inputs, prepared.params, prepared.outputs, code=code, recorded_code=code.stage_sha256)
print(ZoneInfo("Australia/Adelaide").utcoffset(datetime(2025, 1, 1)))
"""


def fixed_offset_zone_file() -> bytes:
    """A version-1 TZif file with one local time type: a fixed offset, no transitions."""
    header = b"TZif" + b"\x00" + bytes(15) + struct.pack(">6l", 1, 1, 0, 0, 1, 4)
    local_time_type = struct.pack(">lBB", FIXED_OFFSET_SECONDS, 0, 0)
    return header + local_time_type + b"XXX\x00" + b"\x01" + b"\x01"


def run_child(root: Path, env: dict[str, str]) -> str:
    done = subprocess.run(
        [sys.executable, "-c", CHILD, str(root)], check=True, capture_output=True, text=True, env=env, cwd=root
    )
    return done.stdout.strip()


def tree(directory: Path) -> dict[str, bytes]:
    return {path.relative_to(directory).as_posix(): path.read_bytes() for path in sorted(directory.rglob("*"))}


def test_a_wrong_system_zone_file_changes_nothing(tmp_path: Path) -> None:
    template = tmp_path / "template"
    build_inputs("localise", template)
    normal, poisoned = tmp_path / "normal", tmp_path / "poisoned"
    shutil.copytree(template, normal)
    shutil.copytree(template, poisoned)

    zones = tmp_path / "zones"
    (zones / "Australia").mkdir(parents=True)
    (zones / "Australia" / "Adelaide").write_bytes(fixed_offset_zone_file())

    base = {key: value for key, value in os.environ.items() if key != "PYTHONTZPATH"}
    run_child(normal, base)
    system_view = run_child(poisoned, {**base, "PYTHONTZPATH": str(zones)})

    assert system_view == "3:00:00", "the poisoned system database took effect in the child"
    out = Path("data") / "interim" / "elec_interval_local"
    assert (poisoned / out / "intervals.parquet").is_file()
    assert tree(poisoned / out) == tree(normal / out)
