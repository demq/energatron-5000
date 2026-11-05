"""`energatron verify` in a scratch workspace with real git and DVC (D1 §11.4, §13).

The workspace runs the real pipeline (`catalog_index`, `parse_nem12`, `merge_interval`, `validate_meter`,
`validate_timebase`, `localise`) over four synthetic landed blobs. `verify` refuses a stale fingerprint or pipeline; on
a current one it passes; an output changed by hand and committed to DVC, or a stored blob changed or removed, fails it
with `E_CHECK_FAILED` (`not_reproducible`), naming the stage or the dataset. Nothing here touches the real workspace.
"""

import json
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from energatron.adapters.cli.app import main
from energatron.adapters.storage.fs_blobs import FsBlobStore
from energatron.adapters.storage.jsonl_catalog import JsonlCatalog
from energatron.core.canonical import sha256_hex
from energatron.core.ids import dataset_id
from energatron.core.models.catalog import CatalogRecord, SourceType
from energatron.core.models.workspace import WorkspacePaths
from tests.fixtures.make_nem12 import Nem12Spec, make_nem12, sapn_filename
from tests.fixtures.make_sources import make_retailer_usage_from_nem12
from tests.fixtures.params_text import set_reference_months
from tests.fixtures.pipeline_workspace import LABELS, WINDOWS, make_record
from tests.fixtures.synthetic import SYNTHETIC_NMI
from tests.integration._scratch import Scratch, dvc, make_scratch

pytestmark = pytest.mark.integration

BIN = Path(sys.executable).parent
SOURCE_TS = datetime(2026, 1, 2, 3, 4, 5)
OUTPUT = Path("data") / "marts" / "catalog"


def envelope(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, dict[str, Any]]:
    capsys.readouterr()
    code = main(list(argv))
    out = capsys.readouterr().out
    assert out.count("\n") == 1, out
    return code, json.loads(out)


def land(paths: WorkspacePaths, source: SourceType, payload: bytes) -> str:
    """Store a synthetic blob and catalog it, as `land` will (D3 §7.3)."""
    digest = sha256_hex(payload)
    ident = dataset_id(source.value, SOURCE_TS, digest)
    key = paths.blob_key(source, ident, ".csv")
    FsBlobStore(paths).put(key, payload)
    record = CatalogRecord.from_document(
        {
            "kind": "catalog_record",
            "schema_version": 1,
            "dataset_id": ident,
            "source": source.value,
            "sha256": digest,
            "bytes": len(payload),
            "stored_path": key,
            "original_filename": "synthetic_original_name.csv",
            "landed_at": "2026-01-02T00:00:00Z",
            "file_mtime": None,
            "source_ts": "2026-01-02T03:04:05",
            "coverage": {"start": "2025-12-01", "end": "2025-12-31"},
            "detector": {"name": source.value, "version": 1, "evidence": ["filename", "content"]},
            "extra": {},
            "supersedes": None,
            "notes": "synthetic note",
        }
    )
    JsonlCatalog(paths).append(record)
    return ident


def land_interval_and_usage(paths: WorkspacePaths) -> tuple[str, str]:
    """Store a generated NEM12 file and a retailer export made from it, each catalogued with the record the real parser
    builds, which `parse_nem12` and `validate_timebase` read without a failed check. Forty winter days and three
    midnight-to-midnight periods give `validate_timebase` the evidence to confirm `nem_aest`, which `localise` needs."""
    spec = Nem12Spec(start=date(2025, 6, 1), days=40, interval_minutes=30)
    window = (spec.start, spec.start + timedelta(days=spec.days - 1))
    name = sapn_filename("DETAILED", SYNTHETIC_NMI, window, datetime(2025, 7, 11, 9, 0, 0))
    data = make_nem12(spec)
    usage = make_retailer_usage_from_nem12(
        data,
        basis="nem_aest",
        windows=WINDOWS,
        reads=[datetime(2025, 6, 5), datetime(2025, 6, 12), datetime(2025, 6, 19), datetime(2025, 6, 26)],
        labels=LABELS,
    )
    idents: list[str] = []
    for source, filename, payload in (
        (SourceType.SAPN_INTERVAL, name, data),
        (SourceType.RETAILER_USAGE, "synthetic_usage.csv", usage),
    ):
        record = make_record(source, filename, payload)
        FsBlobStore(paths).put(record.stored_path, payload)
        JsonlCatalog(paths).append(record)
        idents.append(record.dataset_id)
    return idents[0], idents[1]


@pytest.fixture
def scratch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> Scratch:
    """A current pipeline: synced, four blobs landed, fingerprinted and reproduced."""
    for name in ("ENERGATRON_JSON", "ENERGATRON_WORKSPACE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PATH", f"{BIN}{os.pathsep}{os.environ.get('PATH', '')}")
    scratch = make_scratch(tmp_path, remote=False)
    monkeypatch.chdir(scratch.root)
    assert envelope(capsys, "pipeline", "sync", "--json")[0] == 0
    set_reference_months(scratch.root / "params.yaml", 1)  # forty days from 1 June: June is the one covered month
    land_interval_and_usage(scratch.paths)
    land(scratch.paths, SourceType.ELEC_BILL, b"synthetic,bill\n3,4\n")
    land(scratch.paths, SourceType.ELEC_BILL, b"synthetic,earlier bill\n7,8\n")  # no stage reads elec_bill
    assert envelope(capsys, "fingerprint", "--json")[0] == 0
    dvc(scratch.root, "repro", "--quiet")
    return scratch


def scratch_dir(scratch: Scratch) -> Path:
    return scratch.root / ".energatron" / "tmp" / "verify"


def test_a_current_pipeline_passes(capsys: pytest.CaptureFixture[str], scratch: Scratch) -> None:
    code, result = envelope(capsys, "verify", "--json")
    assert code == 0, result["error"]
    assert result["command"] == "verify"
    assert result["data"] == {
        "kind": "verify_result",
        "schema_version": 1,
        "stages": [
            "catalog_index",
            "parse_nem12",
            "merge_interval",
            "validate_meter",
            "validate_timebase",
            "localise",
            "usage_profile",
            "usage_report",
            "normalise_offers",
            "cost_elec",
            "backtest",
            "gas_bills",
            "gas_profile",
            "normalise_gas_offers",
        ],
        "datasets": 4,
    }
    assert not scratch_dir(scratch).exists()
    assert json.loads(dvc(scratch.root, "status", "--json")) == {}


def test_a_leftover_scratch_directory_is_replaced_and_none_is_left(
    capsys: pytest.CaptureFixture[str], scratch: Scratch
) -> None:
    scratch.write(".energatron/tmp/verify/catalog_index/catalog/stale.txt", "left by a killed run\n")
    code, result = envelope(capsys, "verify", "--json")
    assert code == 0, result["error"]
    assert not scratch_dir(scratch).exists()


def test_a_stale_fingerprint_is_refused(capsys: pytest.CaptureFixture[str], scratch: Scratch) -> None:
    (scratch.root / ".energatron" / "code_fingerprint.json").unlink()
    code, result = envelope(capsys, "verify", "--json")
    assert code == 4
    assert result["error"]["code"] == "E_FINGERPRINT_STALE"
    assert result["error"]["details"]["stages"] == [
        "backtest",
        "catalog_index",
        "cost_elec",
        "gas_bills",
        "gas_profile",
        "localise",
        "merge_interval",
        "normalise_gas_offers",
        "normalise_offers",
        "parse_nem12",
        "usage_profile",
        "usage_report",
        "validate_meter",
        "validate_timebase",
    ]


def test_a_stale_pipeline_is_refused(capsys: pytest.CaptureFixture[str], scratch: Scratch) -> None:
    land(scratch.paths, SourceType.ELEC_BILL, b"synthetic,bill\n5,6\n")  # a new catalog line: catalog_index is stale
    code, result = envelope(capsys, "verify", "--json")
    assert code == 4
    assert result["error"]["code"] == "E_PIPELINE_STALE"
    assert result["error"]["details"]["stages"] == ["catalog_index"]
    assert not scratch_dir(scratch).exists()


def test_an_output_changed_by_hand_and_committed_to_dvc_is_not_reproducible(
    capsys: pytest.CaptureFixture[str], scratch: Scratch
) -> None:
    provenance = scratch.root / OUTPUT / "_provenance.json"
    provenance.chmod(0o644)
    provenance.write_text(provenance.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    dvc(scratch.root, "commit", "--force", "--quiet", "catalog_index")
    assert json.loads(dvc(scratch.root, "status", "--json")) == {}
    code, result = envelope(capsys, "verify", "--json")
    assert code == 7
    error = result["error"]
    assert error["code"] == "E_CHECK_FAILED"
    assert error["details"] == {"reason": "not_reproducible", "stages": ["catalog_index"], "datasets": []}
    assert not scratch_dir(scratch).exists()


def test_a_stored_blob_changed_or_removed_by_hand_is_named(
    capsys: pytest.CaptureFixture[str], scratch: Scratch
) -> None:
    # the interval and usage blobs are left alone: `parse_nem12` and `validate_timebase` read them, so changing one
    # makes that stage stale instead (as the SAPN SUMMARY blobs would, for `validate_meter`; none is landed here)
    records = [r for r in JsonlCatalog(scratch.paths).records() if r.source == SourceType.ELEC_BILL]
    changed, removed = (scratch.root / record.stored_path for record in records)
    changed.chmod(0o644)
    changed.write_bytes(changed.read_bytes() + b"altered\n")
    removed.chmod(0o644)
    removed.unlink()
    code, result = envelope(capsys, "verify", "--json")
    assert code == 7
    error = result["error"]
    assert error["code"] == "E_CHECK_FAILED"
    assert error["details"] == {
        "reason": "not_reproducible",
        "stages": [],
        "datasets": sorted(record.dataset_id for record in records),
    }


def test_a_changed_interval_blob_is_refused_as_a_stale_pipeline(
    capsys: pytest.CaptureFixture[str], scratch: Scratch
) -> None:
    """`parse_nem12` depends on the whole interval directory, so a blob changed by hand makes that stage stale."""
    (record,) = (r for r in JsonlCatalog(scratch.paths).records() if r.source is SourceType.SAPN_INTERVAL)
    blob = scratch.root / record.stored_path
    blob.chmod(0o644)
    blob.write_bytes(blob.read_bytes() + b"altered\n")
    code, result = envelope(capsys, "verify", "--json")
    assert code == 4
    assert result["error"]["code"] == "E_PIPELINE_STALE"
    assert result["error"]["details"]["stages"] == ["parse_nem12"]
    assert not scratch_dir(scratch).exists()
