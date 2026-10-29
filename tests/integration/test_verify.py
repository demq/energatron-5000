"""`energatron verify` in a scratch workspace with real git and DVC (D1 §11.4, §13).

The workspace runs the real pipeline (`catalog_index`) over two synthetic landed blobs. `verify` refuses a stale
fingerprint or pipeline; on a current one it passes; an output changed by hand and committed to DVC, or a stored blob
changed or removed, fails it with `E_CHECK_FAILED` (`not_reproducible`), naming the stage or the dataset. Nothing here
touches the real workspace.
"""

import json
import os
import sys
from datetime import datetime
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


@pytest.fixture
def scratch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> Scratch:
    """A current pipeline: synced, two blobs landed, fingerprinted and reproduced."""
    for name in ("ENERGATRON_JSON", "ENERGATRON_WORKSPACE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PATH", f"{BIN}{os.pathsep}{os.environ.get('PATH', '')}")
    scratch = make_scratch(tmp_path, remote=False)
    monkeypatch.chdir(scratch.root)
    assert envelope(capsys, "pipeline", "sync", "--json")[0] == 0
    land(scratch.paths, SourceType.SAPN_INTERVAL, b"synthetic,interval\n1,2\n")
    land(scratch.paths, SourceType.ELEC_BILL, b"synthetic,bill\n3,4\n")
    assert envelope(capsys, "fingerprint", "--json")[0] == 0
    dvc(scratch.root, "repro", "--quiet")
    return scratch


def scratch_dir(scratch: Scratch) -> Path:
    return scratch.root / ".energatron" / "tmp" / "verify"


def test_a_current_pipeline_passes(capsys: pytest.CaptureFixture[str], scratch: Scratch) -> None:
    code, result = envelope(capsys, "verify", "--json")
    assert code == 0, result["error"]
    assert result["command"] == "verify"
    assert result["data"] == {"kind": "verify_result", "schema_version": 1, "stages": ["catalog_index"], "datasets": 2}
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
    assert result["error"]["details"]["stages"] == ["catalog_index"]


def test_a_stale_pipeline_is_refused(capsys: pytest.CaptureFixture[str], scratch: Scratch) -> None:
    land(scratch.paths, SourceType.GAS_BILL, b"synthetic,gas\n5,6\n")  # a new catalog line: catalog_index is stale
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
    records = list(JsonlCatalog(scratch.paths).records())
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
