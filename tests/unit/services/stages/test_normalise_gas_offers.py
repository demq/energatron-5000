"""The `normalise_gas_offers` stage (P5 spec §8): the gas plans and gas manual offers of the fixture offer zone, the
same six checks as `normalise_offers`, the offer rules, and `gas_offer_tariffs`."""

import json
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from energatron.core.canonical import content_hash, sha256_hex
from energatron.core.errors import WarningCode
from energatron.core.models.catalog import SourceType
from energatron.core.models.costs import GasOfferTariffs, OffersSummary
from energatron.core.models.provenance import StageCode
from energatron.services.stages.registry import get_stage
from energatron.services.stages.runner import StageResult, prepare_stage, run_stage
from tests.fixtures import make_cdr as cdr
from tests.fixtures.offer_workspace import (
    CURRENT_GAS,
    GAS_ID,
    GAS_MANUAL_ID,
    GAS_SEASONS_ID,
    OfferZone,
    build_offer_zone,
)
from tests.fixtures.params_text import params_v5_text
from tests.fixtures.pipeline_workspace import empty_raw_zone, land_blob

CHECKS = [
    "blobs_match_catalog",
    "detector_known",
    "snapshot_ids_recompute",
    "rules_valid",
    "every_offer_accounted",
    "tariffs_partition",
]
OUT = Path("data/marts/offers_gas")


def normalise(root: Path) -> StageResult:
    spec = get_stage("normalise_gas_offers")
    prepared = prepare_stage(spec, root=root, params_file=root / "params.yaml")
    source = sha256_hex(b"normalise_gas_offers")
    digest = content_hash({"source_sha256": source, "packages": {}, "python": "3.14.0"})
    code = StageCode(stage_sha256=digest, source_sha256=source, python="3.14.0", packages={}, files=["__init__.py"])
    return run_stage(
        spec, prepared.inputs, prepared.params, prepared.outputs, code=code, recorded_code=code.stage_sha256
    )


@pytest.fixture
def zone(tmp_path: Path) -> OfferZone:
    root = tmp_path / "workspace"
    empty_raw_zone(root)
    (root / "params.yaml").write_text(params_v5_text(region=True), encoding="utf-8")
    return build_offer_zone(root)


def rows(root: Path) -> dict[str, dict[str, object]]:
    return {str(row["plan_id"]): row for row in pq.read_table(root / OUT / "offers.parquet").to_pylist()}


def test_every_check_passes_and_only_gas_offers_are_considered(zone: OfferZone) -> None:
    result = normalise(zone.root)
    assert [check.name for check in result.checks] == CHECKS
    assert all(check.status == "pass" for check in result.checks)
    found = rows(zone.root)
    assert sorted(found) == sorted([*CURRENT_GAS, GAS_MANUAL_ID])
    assert (found[GAS_MANUAL_ID]["source"], found[GAS_MANUAL_ID]["pricing_model"]) == ("manual", "SINGLE_RATE")
    assert all(row["status"] == "normalised" for row in found.values())


def test_tariffs_are_gas_offer_tariffs_with_both_seasons(zone: OfferZone) -> None:
    normalise(zone.root)
    tariffs = GasOfferTariffs.from_document(json.loads((zone.root / OUT / "tariffs.json").read_bytes()))
    by_plan = {tariff.plan_id: tariff for tariff in tariffs.tariffs}
    assert sorted(by_plan) == sorted([GAS_ID, GAS_SEASONS_ID, GAS_MANUAL_ID])
    assert [(season.start, season.end) for season in by_plan[GAS_SEASONS_ID].seasons] == [
        ("06-01", "09-30"),
        ("10-01", "05-31"),
    ]
    assert by_plan[GAS_ID].discounts[0].kind == "guaranteed"
    summary = OffersSummary.from_document(json.loads((zone.root / OUT / "summary.json").read_bytes()))
    assert (summary.current_plans, summary.manual, summary.statuses["normalised"]) == (2, 1, 3)


def test_a_local_rule_applies_to_a_gas_plan(zone: OfferZone) -> None:
    rule = (
        "kind: offer_rule\nschema_version: 1\nid: no-test-gas\ndescription: Made up.\n"
        f'match: {{brand: null, plan_id: "{GAS_ID}", display_name: null, from: null, to: null}}\n'
        'effect: {exclude: made_up}\nverified_on: "2026-11-05"\ncites: []\n'
    )
    (zone.root / "config/offer_rules.local/no-test-gas.yaml").write_text(rule, encoding="utf-8")
    normalise(zone.root)
    found = rows(zone.root)[GAS_ID]
    assert (found["status"], found["reasons"], found["rules"]) == (
        "excluded",
        ["rule:no-test-gas:made_up"],
        ["no-test-gas"],
    )


def test_a_selected_gas_plan_without_a_snapshot_is_warned_here(zone: OfferZone) -> None:
    newer = cdr.fetch_log(
        batch="fetch-20261102T000000Z",
        fetched_at="2026-11-02T00:00:00Z",
        retailers=[("alpha", [])],
        selected=[cdr.selected("NOT-LANDED@EME", fuel_type="GAS")],
        gas_distributor=cdr.GAS_DISTRIBUTOR,
    )
    land_blob(zone.root, SourceType.OFFER_CDR_INDEX, "fetch-log-3.json", cdr.fetch_log_body(newer))
    result = normalise(zone.root)
    assert [warning.code for warning in result.warnings] == [WarningCode.W_OFFERS_MISSING_SNAPSHOT]
    assert sorted(rows(zone.root)) == [GAS_MANUAL_ID]  # alpha's current list is now empty


def test_no_offer_data_gives_empty_outputs(tmp_path: Path) -> None:
    empty_raw_zone(tmp_path)
    (tmp_path / "params.yaml").write_text(params_v5_text(region=True), encoding="utf-8")
    normalise(tmp_path)
    assert GasOfferTariffs.from_document(json.loads((tmp_path / OUT / "tariffs.json").read_bytes())).tariffs == ()
