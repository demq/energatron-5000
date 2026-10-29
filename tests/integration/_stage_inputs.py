"""Fixture inputs for every registered stage, built by code from synthetic values (D3 §12).

`test_determinism.py` and `test_stage_loads.py` run every stage in `STAGES`; each needs a builder here, and a stage
without one fails both tests. Builders write into a fresh workspace root: `params.yaml` and the stage's inputs.
"""

from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Final

from energatron.core.canonical import JSONValue, canonical_json, sha256_hex
from energatron.core.ids import dataset_id
from energatron.core.models.catalog import SourceType

PARAMS_YAML: Final = "kind: params\nschema_version: 1\n"
_SOURCE_TS: Final = datetime(2026, 1, 2, 3, 4, 5)


def _record(source: str, payload: bytes) -> dict[str, JSONValue]:
    digest = sha256_hex(payload)
    ident = dataset_id(source, _SOURCE_TS, digest)
    return {
        "kind": "catalog_record",
        "schema_version": 1,
        "dataset_id": ident,
        "source": source,
        "sha256": digest,
        "bytes": len(payload),
        "stored_path": f"data/raw/{source}/2026/{ident}.csv",
        "original_filename": "synthetic_original_name.csv",
        "landed_at": "2026-01-02T00:00:00Z",
        "file_mtime": None,
        "source_ts": "2026-01-02T03:04:05",
        "coverage": {"start": "2025-12-01", "end": "2025-12-31"},
        "detector": {"name": source, "version": 1, "evidence": ["filename", "content"]},
        "extra": {"interval_minutes": 5},
        "supersedes": None,
        "notes": "synthetic note",
    }


def _catalog_index(root: Path) -> None:
    """Every source's catalog: two synthetic records for two sources, the rest empty."""
    filled = {
        SourceType.SAPN_INTERVAL: [b"synthetic interval one", b"synthetic interval two"],
        SourceType.ELEC_BILL: [b"synthetic bill"],
    }
    for source in SourceType:
        directory = root / "data" / "raw" / source.value
        directory.mkdir(parents=True, exist_ok=True)
        lines = b"".join(canonical_json(_record(source.value, payload)) + b"\n" for payload in filled.get(source, []))
        (directory / "_catalog.jsonl").write_bytes(lines)


BUILDERS: Final[Mapping[str, Callable[[Path], None]]] = {"catalog_index": _catalog_index}


def build_inputs(stage: str, root: Path) -> None:
    """Write `params.yaml` and the fixture inputs of `stage` under `root`."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "params.yaml").write_text(PARAMS_YAML, encoding="utf-8")
    BUILDERS[stage](root)
