"""The whole P2 meter pipeline in a scratch workspace with real git and DVC (P2 spec §11, §13.3).

`pipeline sync` writes `dvc.yaml` and `params.yaml` (params v3, the zone and the windows from the region profile); the
fixture raw zone of `tests/fixtures/pipeline_workspace.py` stands in for what `land` leaves; `DvcRunner.repro` refreshes
the fingerprint and runs every stage; `verify` then reruns them in a scratch directory. Every value is synthetic, and
nothing here touches the real workspace.
"""

import json
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq
import pytest

from energatron.adapters import wiring
from energatron.adapters.cli.app import main
from energatron.adapters.pipeline.dvc_runner import DvcRunner
from energatron.core.errors import CheckFailedError
from energatron.core.models.catalog import SourceType
from energatron.core.models.meter import MergeSummary, TimebaseResult
from energatron.services.stages.registry import STAGES
from tests.fixtures.make_nem12 import Nem12Spec, make_nem12, sapn_filename
from tests.fixtures.make_sources import make_retailer_usage_from_nem12
from tests.fixtures.params_text import set_reference_months
from tests.fixtures.pipeline_workspace import LABELS, WINDOWS, RawZone, build_raw_zone, land_blob
from tests.fixtures.synthetic import SYNTHETIC_NMI
from tests.integration._scratch import Scratch, dvc, make_scratch
from tests.integration._stage_inputs import REFERENCE_MONTHS

pytestmark = pytest.mark.integration

BIN = Path(sys.executable).parent
ALL_STAGES = list(STAGES)
WINTER = [datetime(2025, month, 1) for month in range(5, 11)]
INTERVAL = Path("data") / "interim" / "elec_interval_local"
TIMEBASE = Path("data") / "reports" / "validation" / "timebase"
MERGE = Path("data") / "reports" / "validation" / "merge"


def envelope(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, dict[str, Any]]:
    capsys.readouterr()
    code = main(list(argv))
    out = capsys.readouterr().out
    assert out.count("\n") == 1, out
    return code, json.loads(out)


def runner(scratch: Scratch) -> DvcRunner:
    return DvcRunner(scratch.paths, fingerprint=wiring.code_fingerprint)


def set_basis(scratch: Scratch, basis: str) -> None:
    params = scratch.root / "params.yaml"
    params.write_text(re.sub(r"(?m)^  timebase: \w+$", f"  timebase: {basis}", params.read_text()), encoding="utf-8")


def replace_usage(zone: RawZone, *, basis: str, reads: list[datetime]) -> None:
    """Swap the zone's two usage exports for one generated under `basis` over `reads`."""
    usage = zone.root / "data" / "raw" / "retailer_usage"
    for blob in list(usage.rglob("*.csv")):
        blob.unlink()
    (usage / "_catalog.jsonl").write_bytes(b"")
    data = make_retailer_usage_from_nem12(zone.truth, basis=basis, windows=WINDOWS, reads=reads, labels=LABELS)
    land_blob(zone.root, SourceType.RETAILER_USAGE, "synthetic_usage.csv", data)


@pytest.fixture
def scratch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> Scratch:
    """A synced workspace holding the fixture raw zone, not yet reproduced."""
    for name in ("ENERGATRON_JSON", "ENERGATRON_WORKSPACE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PATH", f"{BIN}{os.pathsep}{os.environ.get('PATH', '')}")
    scratch = make_scratch(tmp_path, remote=False)
    monkeypatch.chdir(scratch.root)
    assert envelope(capsys, "pipeline", "sync", "--json")[0] == 0
    set_reference_months(scratch.root / "params.yaml", REFERENCE_MONTHS)  # the fixture covers five whole months
    return scratch


@pytest.fixture
def zone(scratch: Scratch) -> RawZone:
    return build_raw_zone(scratch.root)


def test_every_stage_passes_and_verify_accepts_the_clean_state(
    capsys: pytest.CaptureFixture[str], scratch: Scratch, zone: RawZone
) -> None:
    report = runner(scratch).repro()
    assert report.ran == ALL_STAGES

    merge = MergeSummary.from_document(json.loads((scratch.root / MERGE / "summary.json").read_text()))
    assert merge.conflict_days == 1
    timebase = TimebaseResult.from_document(json.loads((scratch.root / TIMEBASE / "result.json").read_text()))
    assert (timebase.configured, timebase.verdict) == ("nem_aest", "confirmed")
    assert (scratch.root / INTERVAL / "intervals.parquet").is_file()

    assert json.loads(dvc(scratch.root, "status", "--json")) == {}
    code, result = envelope(capsys, "verify", "--json")
    assert code == 0, result["error"]
    assert result["data"]["stages"] == ALL_STAGES


def test_winter_only_evidence_leaves_localise_without_a_table(scratch: Scratch, zone: RawZone) -> None:
    replace_usage(zone, basis="local_wall", reads=WINTER)  # winter can't tell local_wall from local_standard
    set_basis(scratch, "local_wall")
    with pytest.raises(CheckFailedError) as caught:
        runner(scratch).repro()
    assert caught.value.details["stage"] == "localise"
    ran = caught.value.details["ran"]
    assert isinstance(ran, list)
    assert "validate_timebase" in ran
    timebase = TimebaseResult.from_document(json.loads((scratch.root / TIMEBASE / "result.json").read_text()))
    assert timebase.verdict == "unconfirmed"
    assert sorted(path.name for path in (scratch.root / INTERVAL).iterdir()) == ["_provenance.json"]


def test_a_contradicted_basis_stops_the_pipeline_in_validate_timebase(scratch: Scratch, zone: RawZone) -> None:
    set_basis(scratch, "local_wall")  # the fixture's exports were made under nem_aest
    with pytest.raises(CheckFailedError) as caught:
        runner(scratch).repro()
    assert caught.value.details["stage"] == "validate_timebase"
    ran = caught.value.details["ran"]
    assert isinstance(ran, list)
    assert "localise" not in ran
    assert not (scratch.root / INTERVAL).exists()


def test_landing_a_sapn_dataset_makes_parse_nem12_and_what_follows_it_run_again(
    scratch: Scratch, zone: RawZone
) -> None:
    assert runner(scratch).repro().ran == ALL_STAGES
    start = zone.specs.b_only[1] + timedelta(days=1)
    spec = Nem12Spec(start=start, days=5, interval_minutes=30, seed=11)
    name = sapn_filename("DETAILED", SYNTHETIC_NMI, (start, start + timedelta(days=4)), datetime(2025, 10, 20, 9, 0))
    land_blob(scratch.root, SourceType.SAPN_INTERVAL, name, make_nem12(spec))

    stale = {status.name for status in runner(scratch).status()}
    assert {"catalog_index", "parse_nem12"} <= stale
    assert runner(scratch).repro().ran == ALL_STAGES
    index = pq.read_table(scratch.root / "data" / "staged" / "elec_interval" / "index.parquet")
    assert index.num_rows == 3
