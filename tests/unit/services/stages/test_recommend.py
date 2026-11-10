"""The `recommend` stage (P6 spec §6, §7): the recommendation document, the current plan, savings and the action, the
content-derived ID, the page, and its four checks. The workspace is the fixture raw, offer and gas zones with every
upstream stage run and a synthetic private config."""

import json
import re
import shutil
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from energatron.core.errors import CheckFailedError, WarningCode
from energatron.core.ids import parse_rec_id, rec_id
from energatron.core.models.recommend import Recommendation
from tests.integration._stage_inputs import build_inputs, run_stage_named
from tests.unit.adapters._workspace import MINIMAL_PRIVATE_TOML

OUT = Path("data/reports/recommendations")


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("recommend") / "workspace"
    build_inputs("recommend", root)
    return root


@pytest.fixture
def root(template: Path, tmp_path: Path) -> Path:
    copy = tmp_path / "workspace"
    shutil.copytree(template, copy)
    return copy


def document(root: Path) -> Recommendation:
    return Recommendation.from_document(json.loads((root / OUT / "recommendation.json").read_bytes()))


def manual(root: Path, mart: str) -> str:
    tariffs = json.loads((root / "data/marts" / mart / "tariffs.json").read_bytes())["tariffs"]
    return next(tariff["offer_snapshot_id"] for tariff in tariffs if tariff["source"] == "manual")


def set_line(root: Path, pattern: str, line: str) -> None:
    path = root / "params.yaml"
    text = path.read_text(encoding="utf-8")
    changed = re.sub(pattern, line, text, count=1, flags=re.MULTILINE)
    assert changed != text
    path.write_text(changed, encoding="utf-8")


def open_ranges(root: Path) -> tuple[str, str]:
    """Name the two manual offers as the plans in force (open backtest ranges); returns their snapshots."""
    elec, gas = manual(root, "offers"), manual(root, "offers_gas")
    set_line(root, r"^  tariffs: .*$", f"  tariffs: {json.dumps([{'offer': elec, 'from': '2025-01-01', 'to': None}])}")
    set_line(
        root, r"^  gas_tariffs: .*$", f"  gas_tariffs: {json.dumps([{'offer': gas, 'from': '2025-01-01', 'to': None}])}"
    )
    run_stage_named("bundles", root)
    return elec, gas


def private(root: Path, electricity: str) -> None:
    text = MINIMAL_PRIVATE_TOML + f'\n[current_plans]\nelectricity = "{electricity}"\n'
    (root / "private" / "config.toml").write_text(text, encoding="utf-8")


def test_without_a_current_plan_the_action_says_so_and_every_check_passes(root: Path) -> None:
    result = run_stage_named("recommend", root)
    assert [(check.name, check.status) for check in result.checks] == [
        ("as_of_known", "pass"),
        ("options_present", "pass"),
        ("ids_resolve", "pass"),
        ("id_recomputes", "pass"),
    ]
    assert [(warning.code, warning.details["fuel"]) for warning in result.warnings] == [
        (WarningCode.W_REC_NO_CURRENT_PLAN, "electricity"),
        (WarningCode.W_REC_NO_CURRENT_PLAN, "gas"),
    ]
    found = document(root)
    assert found.action == "no_current_plan"
    assert found.recommendation_id is not None and found.as_of is not None
    assert parse_rec_id(found.recommendation_id)
    assert found.recommendation_id == rec_id(found.as_of, found.payload())
    assert found.recheck_by is not None and found.as_of < found.recheck_by
    assert 1 <= len(found.options) <= 5
    assert [option.rank for option in found.options] == list(range(1, len(found.options) + 1))
    assert all(option.saving_ongoing is None for option in found.options)
    assert found.priced["electricity"] and found.priced["gas"]
    assert [plan.rank for plan in found.priced["electricity"]] == list(range(1, len(found.priced["electricity"]) + 1))
    assert {variation.variation for variation in found.sensitivity} == {
        "conditions_not_met",
        "usage_up",
        "usage_down",
    }
    assert found.inputs.dataset_ids and found.inputs.offer_snapshot_ids
    assert found.inputs.stage_sha256 and found.inputs.params_sha256
    page = (root / OUT / "index.html").read_text(encoding="utf-8")
    assert found.recommendation_id in page
    assert "<script" not in page


def test_the_open_backtest_ranges_are_the_current_plans_and_give_savings(root: Path) -> None:
    elec, gas = open_ranges(root)
    result = run_stage_named("recommend", root)
    assert list(result.warnings) == []
    found = document(root)
    assert found.current.electricity is not None and found.current.gas is not None
    assert (found.current.electricity.offer_snapshot_id, found.current.electricity.source) == (elec, "backtest")
    assert (found.current.gas.offer_snapshot_id, found.current.gas.source) == (gas, "backtest")
    assert found.current.annual_ongoing is not None
    top = found.options[0]
    saving = top.saving_ongoing
    assert saving == found.current.annual_ongoing - top.annual_ongoing
    assert saving is not None
    switch = top.option_key != f"{elec}|{gas}" and saving >= 50
    assert found.action == ("switch" if switch else "stay")


def test_the_private_config_names_the_current_plan_first(root: Path) -> None:
    open_ranges(root)
    chosen = document_after(root).priced["electricity"][-1].offer_snapshot_id
    private(root, chosen)
    run_stage_named("recommend", root)
    current = document(root).current.electricity
    assert current is not None
    assert (current.offer_snapshot_id, current.source) == (chosen, "private_config")


def document_after(root: Path) -> Recommendation:
    run_stage_named("recommend", root)
    return document(root)


def test_a_named_plan_that_is_not_priced_warns_and_counts_as_none(root: Path) -> None:
    private(root, "SYN-NOT-PRICED~20260101T000000Z~0123abcd")
    result = run_stage_named("recommend", root)
    assert (WarningCode.W_REC_CURRENT_NOT_PRICED, "electricity") in [
        (warning.code, warning.details["fuel"]) for warning in result.warnings
    ]
    assert document(root).current.electricity is None


def test_the_id_is_stable_and_changes_with_a_cited_input(root: Path) -> None:
    run_stage_named("recommend", root)
    first = (root / OUT / "recommendation.json").read_bytes()
    page = (root / OUT / "index.html").read_bytes()
    run_stage_named("recommend", root)
    assert (root / OUT / "recommendation.json").read_bytes() == first
    assert (root / OUT / "index.html").read_bytes() == page
    before = document(root).recommendation_id
    set_line(root, r"^  min_saving: .*$", '  min_saving: "75"')
    run_stage_named("recommend", root)
    assert document(root).recommendation_id != before


def rewrite(path: Path, change: Any) -> None:
    table = pq.read_table(path)
    pq.write_table(pa.Table.from_pylist(change(table.to_pylist()), schema=table.schema), path)


def test_a_dataset_the_catalog_lacks_fails_ids_resolve(root: Path) -> None:
    rewrite(root / "data/marts/catalog/datasets.parquet", lambda rows: rows[:1])
    with pytest.raises(CheckFailedError) as caught:
        run_stage_named("recommend", root)
    assert caught.value.details["failed"] == ["ids_resolve"]


def test_no_options_fails_options_present(root: Path) -> None:
    rewrite(root / "data/marts/costs/bundles/options.parquet", lambda rows: [])
    with pytest.raises(CheckFailedError) as caught:
        run_stage_named("recommend", root)
    assert caught.value.details["failed"] == ["options_present"]


def test_an_unknown_as_of_fails_and_leaves_no_id(root: Path) -> None:
    for mart in ("offers", "offers_gas"):
        path = root / "data/marts" / mart / "tariffs.json"
        doc = json.loads(path.read_bytes())
        path.write_text(json.dumps({**doc, "as_of": None}), encoding="utf-8")
    with pytest.raises(CheckFailedError) as caught:
        run_stage_named("recommend", root)
    assert caught.value.details["failed"] == ["as_of_known", "id_recomputes"]
    assert document(root).recommendation_id is None


def test_an_unreadable_private_config_names_no_value(root: Path) -> None:
    (root / "private" / "config.toml").write_text('salt = "secret-looking value\n', encoding="utf-8")
    with pytest.raises(Exception) as caught:
        run_stage_named("recommend", root)
    assert "secret" not in str(caught.value) + json.dumps(getattr(caught.value, "details", {}))
