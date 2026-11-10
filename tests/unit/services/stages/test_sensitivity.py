"""The `sensitivity` stage (P6 spec §4): every tariff repriced with usage scaled and in the previous window, ranked per
fuel and variation, with its check and warning. The workspace is the fixture raw, offer and gas zones with the upstream
stages run (five covered electricity months, three gas bills)."""

import json
import re
import shutil
from decimal import Decimal
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq
import pytest

from energatron.core.errors import CheckFailedError, WarningCode
from energatron.core.models.recommend import SensitivitySummary
from energatron.services.stages import costing
from tests.integration._stage_inputs import build_inputs, run_stage_named

OUT = Path("data/marts/costs/sensitivity")


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("sensitivity") / "workspace"
    build_inputs("sensitivity", root)
    for name in ("cost_elec", "cost_gas"):  # the base figures the variations are compared with
        run_stage_named(name, root)
    return root


@pytest.fixture
def root(template: Path, tmp_path: Path) -> Path:
    copy = tmp_path / "workspace"
    shutil.copytree(template, copy)
    return copy


def summary(root: Path) -> SensitivitySummary:
    return SensitivitySummary.from_document(json.loads((root / OUT / "summary.json").read_bytes()))


def totals(root: Path) -> list[dict[str, Any]]:
    return pq.read_table(root / OUT / "totals.parquet").to_pylist()


def set_param(root: Path, pattern: str, value: str) -> None:
    path = root / "params.yaml"
    text = path.read_text(encoding="utf-8")
    changed = re.sub(pattern, value, text, count=1, flags=re.MULTILINE)
    assert changed != text
    path.write_text(changed, encoding="utf-8")


def cost_totals(root: Path, fuel: str) -> dict[str, Decimal]:
    directory = "elec" if fuel == "electricity" else "gas"
    rows = pq.read_table(root / "data/marts/costs" / directory / "totals.parquet").to_pylist()
    return {row["offer_snapshot_id"]: row["annual_ongoing"] for row in rows if row["scenario"] == "conditions_met"}


def test_usage_variations_are_priced_for_both_fuels_and_ranked(root: Path) -> None:
    result = run_stage_named("sensitivity", root)
    assert [(check.name, check.status) for check in result.checks] == [("energy_scaled", "pass")]
    found = summary(root)
    assert found.usage_scale_pct == "10"
    rows = totals(root)
    for fuel in ("electricity", "gas"):
        base = cost_totals(root, fuel)
        assert found.fuels[fuel].tariffs == len(base)
        for variation, factor in (("usage_up", "1.1"), ("usage_down", "0.9")):
            info = found.fuels[fuel].variations[variation]
            assert (info.available, info.factor) == (True, factor)
            priced = {
                row["offer_snapshot_id"]: row for row in rows if (row["fuel"], row["variation"]) == (fuel, variation)
            }
            assert set(priced) == set(base)
            assert sorted(row["rank"] for row in priced.values()) == list(range(1, len(base) + 1))
            ranked = sorted(priced.values(), key=lambda row: row["rank"])
            assert [row["annual_ongoing"] for row in ranked] == sorted(row["annual_ongoing"] for row in ranked)
            for snapshot, row in priced.items():  # more usage costs more, less costs less
                assert (row["annual_ongoing"] > base[snapshot]) == (variation == "usage_up")


def test_too_little_history_makes_the_alternative_window_unavailable_and_warns(root: Path) -> None:
    result = run_stage_named("sensitivity", root)
    found = summary(root)
    for fuel in ("electricity", "gas"):
        alt = found.fuels[fuel].variations["alt_window"]
        assert (alt.available, alt.reason) == (False, "too_little_history")
    assert not [row for row in totals(root) if row["variation"] == "alt_window"]
    assert [(warning.code, warning.details["fuel"]) for warning in result.warnings] == [
        (WarningCode.W_SENSITIVITY_UNAVAILABLE, "electricity"),
        (WarningCode.W_SENSITIVITY_UNAVAILABLE, "gas"),
    ]


def test_with_a_shorter_reference_the_earlier_window_is_priced(root: Path) -> None:
    set_param(root, r"^  reference_months: [0-9]+$", "  reference_months: 2")
    set_param(root, r"^  reference_bills: [0-9]+$", "  reference_bills: 1")
    for name in ("usage_profile", "gas_profile", "cost_elec", "cost_gas"):
        run_stage_named(name, root)
    result = run_stage_named("sensitivity", root)
    assert list(result.warnings) == []
    found = summary(root)
    elec = found.fuels["electricity"]
    assert elec.variations["usage_up"].window_start is not None
    alt = elec.variations["alt_window"]
    assert alt.available
    assert alt.window_end is not None and alt.window_end < str(elec.variations["usage_up"].window_start)
    assert len(costing.months(str(alt.window_start), alt.window_end)) == 2
    gas_alt = found.fuels["gas"].variations["alt_window"]
    assert gas_alt.available
    assert str(gas_alt.window_end) < str(found.fuels["gas"].variations["usage_up"].window_start)
    variations = {(row["fuel"], row["variation"]) for row in totals(root)}
    assert ("electricity", "alt_window") in variations
    assert ("gas", "alt_window") in variations


def test_a_fuel_with_no_usage_is_unavailable_and_does_not_warn(root: Path) -> None:
    periods = root / "data/marts/gas_profile/periods.parquet"
    table = pq.read_table(periods)
    pq.write_table(table.slice(0, 0), periods)
    result = run_stage_named("sensitivity", root)
    gas = summary(root).fuels["gas"]
    assert {info.reason for info in gas.variations.values()} == {"no_usage"}
    assert [warning.details["fuel"] for warning in result.warnings] == ["electricity"]


def test_lost_energy_fails_energy_scaled(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(costing, "energy_conserved", lambda *args, **kwargs: False)
    with pytest.raises(CheckFailedError) as caught:
        run_stage_named("sensitivity", root)
    assert caught.value.details["failed"] == ["energy_scaled"]
    assert summary(root).checks == {"energy_scaled": False}


def test_the_outputs_are_byte_identical_on_a_rerun(root: Path) -> None:
    run_stage_named("sensitivity", root)
    first = {path.name: path.read_bytes() for path in (root / OUT).iterdir()}
    run_stage_named("sensitivity", root)
    assert {path.name: path.read_bytes() for path in (root / OUT).iterdir()} == first
