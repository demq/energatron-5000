"""The whole P3 pipeline in a scratch workspace with real git and DVC (P3 spec §10, §13.3).

Extends `test_p2_pipeline.py` by two stages: `pipeline sync` writes `dvc.yaml` and `params.yaml` (params v3), the
fixture raw zone stands in for what `land` leaves, `DvcRunner.repro` refreshes the fingerprint and runs every
stage, and `verify` then reruns them in a scratch directory. What `usage_profile` and `usage_report` depend on is
checked by changing one input at a time and reading DVC's view of what is stale. Every value is synthetic.
"""

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq
import pytest
import yaml

from energatron.adapters import wiring
from energatron.adapters.cli.app import main
from energatron.adapters.pipeline.dvc_runner import DvcRunner
from energatron.core.canonical import content_hash, sha256_hex
from energatron.core.errors import CheckFailedError
from energatron.core.models.catalog import SourceType
from energatron.core.models.usage import UsageSummary
from energatron.core.usage_report import TITLE
from energatron.services.stages.registry import STAGES
from tests.fixtures.make_sources import make_retailer_usage_from_nem12
from tests.fixtures.params_text import set_reference_months
from tests.fixtures.pipeline_workspace import LABELS, WINDOWS, RawZone, build_raw_zone, land_blob
from tests.integration._scratch import Scratch, dvc, make_scratch
from tests.integration._stage_inputs import REFERENCE_MONTHS

pytestmark = pytest.mark.integration

BIN = Path(sys.executable).parent
ALL_STAGES = list(STAGES)
WINTER = [datetime(2025, month, 1) for month in range(5, 11)]
DOWNSTREAM = ["usage_profile", "usage_report"]
PROFILE = Path("data") / "marts" / "usage_profile"
REPORT = Path("data") / "reports" / "usage"
FINGERPRINT = Path(".energatron") / "code_fingerprint.json"
SECOND_WINDOWS = {
    "Peak": ["07:00-11:00", "16:00-02:00"],
    "Off Peak": ["02:00-07:00"],
    "Shoulder": ["11:00-16:00"],
}


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


def stale(scratch: Scratch) -> list[str]:
    return [status.name for status in runner(scratch).status()]


def set_second_window_set(scratch: Scratch, effective_from: str) -> None:
    """Append (or move) a second TOU window set in a synced `params.yaml`."""
    path = scratch.root / "params.yaml"
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    sets = doc["analysis"]["tou"]["window_sets"][:1]
    sets.append({"effective_from": effective_from, "windows": SECOND_WINDOWS})
    doc["analysis"]["tou"]["window_sets"] = sets
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")


def tou_rows(scratch: Scratch) -> list[dict[str, Any]]:
    return pq.read_table(scratch.root / PROFILE / "tou_daily.parquet").to_pylist()


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
    assert ALL_STAGES.index("usage_report") == ALL_STAGES.index("usage_profile") + 1
    assert runner(scratch).repro().ran == ALL_STAGES

    summary = UsageSummary.from_document(json.loads((scratch.root / PROFILE / "summary.json").read_text()))
    assert list(summary.suffixes) == ["E1"]
    assert summary.checks
    assert all(summary.checks.values())
    page = (scratch.root / REPORT / "index.html").read_text(encoding="utf-8")
    assert page.startswith("<!DOCTYPE html>") and f"<title>{TITLE}</title>" in page
    assert sorted(path.name for path in (scratch.root / REPORT).iterdir()) == ["_provenance.json", "index.html"]

    assert stale(scratch) == []
    assert json.loads(dvc(scratch.root, "status", "--json")) == {}
    code, result = envelope(capsys, "verify", "--json")
    assert code == 0, result["error"]
    assert result["data"]["stages"] == ALL_STAGES


def test_a_second_repro_runs_nothing(scratch: Scratch, zone: RawZone) -> None:
    runner(scratch).repro()
    assert runner(scratch).repro().ran == []


def test_changing_the_reference_months_makes_the_two_p3_stages_stale_and_nothing_upstream(
    scratch: Scratch, zone: RawZone
) -> None:
    runner(scratch).repro()
    set_reference_months(scratch.root / "params.yaml", REFERENCE_MONTHS - 1)
    assert stale(scratch) == ["usage_profile"]  # DVC reports the report once the marts it reads have changed
    assert runner(scratch).repro().ran == [*DOWNSTREAM, "cost_elec"]  # cost_elec reads the profile's summary
    summary = UsageSummary.from_document(json.loads((scratch.root / PROFILE / "summary.json").read_text()))
    assert summary.suffixes["E1"].window_start == "2025-06"


def test_changing_only_the_report_code_entry_leaves_usage_profile_up_to_date(scratch: Scratch, zone: RawZone) -> None:
    runner(scratch).repro()
    path = scratch.root / FINGERPRINT
    doc = json.loads(path.read_text())
    entry = doc["stages"]["usage_report"]  # a test double for a code change in that stage alone
    entry["source_sha256"] = sha256_hex(b"another source")
    entry["stage_sha256"] = content_hash(
        {"source_sha256": entry["source_sha256"], "packages": entry["packages"], "python": entry["python"]}
    )
    path.write_text(json.dumps(doc), encoding="utf-8")
    assert stale(scratch) == ["usage_report"]


def test_editing_a_window_sets_start_changes_the_time_of_use_tables_and_the_report(
    scratch: Scratch, zone: RawZone
) -> None:
    runner(scratch).repro()
    before_tou = tou_rows(scratch)
    before_page = (scratch.root / REPORT / "index.html").read_bytes()

    set_second_window_set(scratch, "2025-07-15")
    assert stale(scratch) == ["usage_profile"]
    assert runner(scratch).repro().ran == [*DOWNSTREAM, "cost_elec"]  # cost_elec reads the profile's summary
    middle_tou = tou_rows(scratch)
    middle_page = (scratch.root / REPORT / "index.html").read_bytes()
    assert middle_tou != before_tou
    assert middle_page != before_page
    assert {row["window_set"] for row in middle_tou} == {0, 1}

    set_second_window_set(scratch, "2025-08-01")
    assert runner(scratch).repro().ran == [*DOWNSTREAM, "cost_elec"]  # cost_elec reads the profile's summary
    assert tou_rows(scratch) != middle_tou
    assert (scratch.root / REPORT / "index.html").read_bytes() != middle_page


def test_winter_only_evidence_stops_at_localise_and_usage_profile_does_not_run(scratch: Scratch, zone: RawZone) -> None:
    replace_usage(zone, basis="local_wall", reads=WINTER)  # winter can't tell local_wall from local_standard
    set_basis(scratch, "local_wall")
    with pytest.raises(CheckFailedError) as caught:
        runner(scratch).repro()
    assert caught.value.details["stage"] == "localise"
    ran = caught.value.details["ran"]
    assert isinstance(ran, list)
    assert not set(DOWNSTREAM) & set(ran)
    assert not (scratch.root / PROFILE).exists()
    assert not (scratch.root / REPORT).exists()
