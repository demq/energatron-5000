"""Fixture inputs for every registered stage, built by code from synthetic values (D3 §12).

`test_determinism.py` and `test_stage_loads.py` run every stage in `STAGES`; each needs a builder here, and a stage
without one fails both tests. Builders write into a fresh workspace root: `params.yaml` and the stage's inputs.
"""

from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Final

from energatron.adapters import wiring
from energatron.core.canonical import JSONValue, canonical_json, sha256_hex
from energatron.core.ids import dataset_id
from energatron.core.models.catalog import SourceType
from energatron.services.stages.registry import STAGES
from energatron.services.stages.runner import StageResult, prepare_stage, run_stage
from tests.fixtures.gas_workspace import build_gas_zone
from tests.fixtures.offer_workspace import build_offer_zone
from tests.fixtures.params_text import params_v5_text
from tests.fixtures.pipeline_workspace import build_raw_zone, empty_raw_zone

REFERENCE_MONTHS: Final = 5
"""The fixture raw zone covers May to September: five whole local months, so five fit the reference window."""
PARAMS_YAML: Final = params_v5_text(region=True, reference_months=REFERENCE_MONTHS)
"""The region fields are filled, as `pipeline sync` leaves them: `validate_timebase` and `normalise_offers` refuse
`null` ones."""
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


def _parse_nem12(root: Path) -> None:
    """The fixture raw zone of the P2 stages: two overlapping SAPN datasets, a SUMMARY CSV and two usage exports."""
    build_raw_zone(root)


def run_stage_named(name: str, root: Path) -> StageResult:
    """Run the registered stage `name` once on the workspace `root`, with its live code entry."""
    spec = STAGES[name]
    prepared = prepare_stage(spec, root=root, params_file=root / "params.yaml")
    code = wiring.stage_code(name)
    return run_stage(
        spec, prepared.inputs, prepared.params, prepared.outputs, code=code, recorded_code=code.stage_sha256
    )


def _run_upstream(root: Path, *names: str) -> None:
    """Run the registered stages `names`, in order, on the workspace `root`."""
    for name in names:
        spec = STAGES[name]
        prepared = prepare_stage(spec, root=root, params_file=root / "params.yaml")
        code = wiring.stage_code(name)
        run_stage(spec, prepared.inputs, prepared.params, prepared.outputs, code=code, recorded_code=code.stage_sha256)


def _merge_interval(root: Path) -> None:
    """The fixture raw zone, parsed: `merge_interval` reads `parse_nem12`'s tables."""
    build_raw_zone(root)
    _run_upstream(root, "parse_nem12")


def _validate_meter(root: Path) -> None:
    """The fixture raw zone, parsed and merged: `validate_meter` reads the merged table and the SUMMARY CSV."""
    build_raw_zone(root)
    _run_upstream(root, "parse_nem12", "merge_interval")


def _validate_timebase(root: Path) -> None:
    """The same inputs as `validate_meter`; the fixture raw zone's usage exports are the second raw input."""
    _validate_meter(root)


def _localise(root: Path) -> None:
    """The fixture raw zone, parsed, merged and judged: `localise` reads the merged table and the time-basis report.

    The zone's exports are generated under `nem_aest`, the basis in `PARAMS_YAML`, so the verdict is `confirmed`."""
    _validate_meter(root)
    _run_upstream(root, "validate_timebase")


def _usage_profile(root: Path) -> None:
    """Everything `localise` needs, run: `usage_profile` reads its table."""
    _localise(root)
    _run_upstream(root, "localise")


def _usage_report(root: Path) -> None:
    """Everything `usage_profile` needs, run: `usage_report` reads its marts."""
    _usage_profile(root)
    _run_upstream(root, "usage_profile")


def _normalise_offers(root: Path) -> None:
    """Empty catalogs beside the fixture offer zone: landed plans, two fetch logs, a manual offer, the rules folder."""
    empty_raw_zone(root)
    build_offer_zone(root)


def _cost_elec(root: Path) -> None:
    """The fixture raw and offer zones with every upstream stage run: `cost_elec` reads the local table, the usage
    profile's summary and the offers mart."""
    _usage_profile(root)
    build_offer_zone(root)
    _run_upstream(root, "usage_profile", "normalise_offers")


def _gas_bills(root: Path) -> None:
    """Empty catalogs beside the fixture gas zone: three bill PDFs, their records and one superseded record."""
    empty_raw_zone(root)
    build_gas_zone(root)


def _gas_profile(root: Path) -> None:
    """The fixture gas zone with `gas_bills` run: `gas_profile` reads its tables."""
    _gas_bills(root)
    _run_upstream(root, "gas_bills")


BUILDERS: Final[Mapping[str, Callable[[Path], None]]] = {
    "catalog_index": _catalog_index,
    "parse_nem12": _parse_nem12,
    "merge_interval": _merge_interval,
    "validate_meter": _validate_meter,
    "validate_timebase": _validate_timebase,
    "localise": _localise,
    "usage_profile": _usage_profile,
    "usage_report": _usage_report,
    "normalise_offers": _normalise_offers,
    "cost_elec": _cost_elec,
    "backtest": _cost_elec,  # the same inputs: offers, the local table; the raw zone's usage exports give the periods
    "gas_bills": _gas_bills,
    "gas_profile": _gas_profile,
}


def build_inputs(stage: str, root: Path) -> None:
    """Write `params.yaml` and the fixture inputs of `stage` under `root`."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "params.yaml").write_text(PARAMS_YAML, encoding="utf-8")
    BUILDERS[stage](root)
