"""The `bundles` stage (P6 spec §5.3): options per variation from the cost and sensitivity marts, bundle rules, history
excluded, and its three checks. The workspace is the fixture raw, offer and gas zones with every upstream stage run;
the fixture's plans are of brands `alpha` (both fuels), `beta` (electricity) and `manualco` (both, manual offers)."""

import json
import re
import shutil
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq
import pytest

from energatron.core.errors import CheckFailedError
from energatron.core.models.recommend import BundlesSummary
from energatron.core.ranking import Option
from energatron.services.stages import bundles
from tests.integration._stage_inputs import build_inputs, run_stage_named

OUT = Path("data/marts/costs/bundles")


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("bundles") / "workspace"
    build_inputs("bundles", root)
    return root


@pytest.fixture
def root(template: Path, tmp_path: Path) -> Path:
    copy = tmp_path / "workspace"
    shutil.copytree(template, copy)
    return copy


def summary(root: Path) -> BundlesSummary:
    return BundlesSummary.from_document(json.loads((root / OUT / "summary.json").read_bytes()))


def options(root: Path, variation: str = "base") -> list[dict[str, Any]]:
    rows = pq.read_table(root / OUT / "options.parquet").to_pylist()
    return sorted((row for row in rows if row["variation"] == variation), key=lambda row: row["rank"])


def manual_snapshots(root: Path) -> list[str]:
    tariffs = json.loads((root / "data/marts/offers/tariffs.json").read_bytes())["tariffs"]
    return [tariff["offer_snapshot_id"] for tariff in tariffs if tariff["source"] == "manual"]


def write_rule(root: Path, name: str, text: str) -> None:
    (root / "config/bundle_rules.local" / name).write_text(text, encoding="utf-8")


RULE = """\
kind: bundle_rule
schema_version: 1
id: alpha-dual
description: Synthetic dual-fuel credit.
brand: Alpha
effect: {annual_credit: "1000"}
verified_on: "2026-11-10"
cites: []
"""


def test_options_are_ranked_per_variation_with_every_check_passing(root: Path) -> None:
    result = run_stage_named("bundles", root)
    assert [(check.name, check.status) for check in result.checks] == [
        ("rules_valid", "pass"),
        ("eligibility_applied", "pass"),
        ("totals_add_up", "pass"),
    ]
    found = summary(root)
    assert found.variations == ["base", "conditions_not_met", "usage_up", "usage_down"]
    assert found.skipped == ["alt_window"]  # the fixture has too little history for either fuel
    assert (found.rules, found.rules_applied) == ([], [])
    base = options(root)
    assert [row["rank"] for row in base] == list(range(1, len(base) + 1))
    assert [row["annual_ongoing"] for row in base] == sorted(row["annual_ongoing"] for row in base)
    assert base[0]["kind"] == "independent" or base[0]["brand"] is not None
    assert {row["brand"] for row in base if row["kind"] == "same_brand"} <= {"alpha", "manualco"}
    for row in base:
        parts = (row["elec_annual_ongoing"] or Decimal(0)) + (row["gas_annual_ongoing"] or Decimal(0))
        assert row["annual_ongoing"] == parts + row["adjustment"]


def test_a_local_rule_adjusts_its_brands_pair(root: Path) -> None:
    write_rule(root, "alpha.yaml", RULE)
    run_stage_named("bundles", root)
    found = summary(root)
    assert (found.rules, found.rules_applied) == (["alpha-dual"], ["alpha-dual"])
    top = options(root)[0]  # a credit this large makes alpha's pair the cheapest option
    assert (top["kind"], top["brand"], top["rules"], top["adjustment"]) == (
        "same_brand",
        "alpha",
        ["alpha-dual"],
        Decimal("-1000.00"),
    )


def test_an_invalid_rule_fails_rules_valid_naming_the_file(root: Path) -> None:
    write_rule(root, "bad.yaml", "kind: bundle_rule\nschema_version: 1\n")
    with pytest.raises(CheckFailedError) as caught:
        run_stage_named("bundles", root)
    assert caught.value.details["failed"] == ["rules_valid"]
    assert summary(root).checks["rules_valid"] is False


def test_a_manual_offer_named_only_by_a_closed_range_is_history(root: Path) -> None:
    manual = manual_snapshots(root)
    assert len(manual) == 1
    path = root / "params.yaml"
    text = path.read_text(encoding="utf-8")
    tariffs = json.dumps([{"offer": manual[0], "from": "2024-01-01", "to": "2024-12-31"}])
    changed = re.sub(r"(?m)^  tariffs: .*$", f"  tariffs: {tariffs}", text, count=1)
    assert changed != text
    path.write_text(changed, encoding="utf-8")
    run_stage_named("bundles", root)
    found = summary(root)
    assert found.history_excluded == {"electricity": 1, "gas": 0}
    every = pq.read_table(root / OUT / "options.parquet").to_pylist()
    assert manual[0] not in {row["elec_snapshot_id"] for row in every}


def test_an_option_that_does_not_add_up_fails_totals_add_up(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real = bundles.options

    def skewed(*args: Any, **kwargs: Any) -> list[Option]:
        found = real(*args, **kwargs)
        return [Option(**{**option.__dict__, "annual_ongoing": option.annual_ongoing + 1}) for option in found]

    monkeypatch.setattr(bundles, "options", skewed)
    with pytest.raises(CheckFailedError) as caught:
        run_stage_named("bundles", root)
    assert caught.value.details["failed"] == ["totals_add_up"]


def test_a_mixed_brand_same_brand_option_fails_eligibility(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real = bundles.options

    def mislabelled(*args: Any, **kwargs: Any) -> list[Option]:
        found = real(*args, **kwargs)
        first = found[0]
        assert first.elec is not None and first.gas is not None
        gas = replace(first.gas, brand=f"not-{first.elec.brand}")
        return [replace(first, kind="same_brand", gas=gas), *found[1:]]

    monkeypatch.setattr(bundles, "options", mislabelled)
    with pytest.raises(CheckFailedError) as caught:
        run_stage_named("bundles", root)
    assert caught.value.details["failed"] == ["eligibility_applied"]


def test_the_outputs_are_byte_identical_on_a_rerun(root: Path) -> None:
    run_stage_named("bundles", root)
    first = {path.name: path.read_bytes() for path in (root / OUT).iterdir()}
    run_stage_named("bundles", root)
    assert {path.name: path.read_bytes() for path in (root / OUT).iterdir()} == first
