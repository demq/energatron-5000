"""`services.explain` (P9 spec §3.4): the recommendation, its run, inputs, how far behind, outcomes; errors."""

import json
from pathlib import Path

import pytest

from energatron.core.errors import InvalidInputError, UsageError
from energatron.services.explain import explain
from tests.unit.services._read_workspace import REC_ID, RUN_ID, context, make_read_workspace


def test_explains_the_current_recommendation(tmp_path: Path) -> None:
    paths, _ = make_read_workspace(tmp_path)
    result = explain(context(paths), code_stale=["recommend", "bundles"])
    assert (result.recommendation_id, result.action, result.robust, result.missing) == (REC_ID, "switch", True, [])
    assert [option.rank for option in result.options] == [1, 2]
    assert list(result.chosen) == ["electricity"] and result.chosen["electricity"].display_name == "Synthetic Saver"
    assert result.run is not None and (result.run.run_id, result.run.draft) == (RUN_ID, False)
    assert result.inputs is not None and (result.inputs.dataset_count, result.inputs.offer_snapshot_count) == (1, 2)
    assert result.behind.model_dump() == {"datasets_not_in_run": 1, "code_stale": ["bundles", "recommend"]}
    assert result.outcomes is not None and result.outcomes.decisions == ["DEC-20250101-01"]
    assert result.trace["options"] == ["data/marts/costs/bundles"]
    assert len(result.sensitivity) == 1 and result.checks["id_recomputes"]


def test_options_limit_and_the_named_id(tmp_path: Path) -> None:
    paths, _ = make_read_workspace(tmp_path)
    assert len(explain(context(paths), REC_ID, options=1, code_stale=[]).options) == 1
    with pytest.raises(UsageError):
        explain(context(paths), options=21, code_stale=[])


def test_an_older_id_is_not_current(tmp_path: Path) -> None:
    paths, _ = make_read_workspace(tmp_path)
    with pytest.raises(InvalidInputError) as caught:
        explain(context(paths), "REC-20261031-0123abcd", code_stale=[])
    assert caught.value.details["reason"] == "not_current"


def test_no_recommendation_and_no_decision(tmp_path: Path) -> None:
    paths, _ = make_read_workspace(tmp_path)
    outcomes = json.loads((paths.root / "data/reports/outcomes/summary.json").read_text())
    outcomes |= {"decisions": [], "outcomes": {}}
    (paths.root / "data/reports/outcomes/summary.json").write_text(json.dumps(outcomes))
    assert explain(context(paths), code_stale=[]).outcomes is None
    (paths.root / "data/reports/recommendations/recommendation.json").unlink()
    result = explain(context(paths), code_stale=[])
    assert result.recommendation_id is None and result.run is None and result.options == []
    assert result.missing == ["data/reports/recommendations/recommendation.json"]
    assert result.behind.datasets_not_in_run == 2
    with pytest.raises(InvalidInputError):
        explain(context(paths), REC_ID, code_stale=[])


def test_an_invalid_outcomes_summary_names_the_file(tmp_path: Path) -> None:
    paths, _ = make_read_workspace(tmp_path)
    (paths.root / "data/reports/outcomes/summary.json").write_text("{}")
    with pytest.raises(InvalidInputError) as caught:
        explain(context(paths), code_stale=[])
    assert caught.value.details == {"file": "data/reports/outcomes/summary.json"}
