"""`services.compare` (P9 spec §3.3): rankings from the recommendation, limits, brand filter, current rank, none."""

from pathlib import Path

import pytest

from energatron.core.errors import UsageError
from energatron.services.compare import compare
from tests.unit.services._read_workspace import REC_ID, context, make_read_workspace


def test_both_fuels_with_the_current_rank(tmp_path: Path) -> None:
    paths, _ = make_read_workspace(tmp_path)
    result = compare(context(paths))
    assert (result.recommendation_id, result.action, result.missing) == (REC_ID, "switch", [])
    assert [option.rank for option in result.options] == [1, 2]
    elec, gas = result.plans["electricity"], result.plans["gas"]
    assert (elec.total, elec.shown, elec.current_rank) == (3, 3, 3)
    assert [item.plan_id for item in elec.items] == ["SYN-E-1", "SYN-E-2", "SYN-E-NOW"]
    assert (gas.total, gas.current_rank) == (1, None)  # no current gas plan


def test_fuel_limit_and_brand(tmp_path: Path) -> None:
    paths, _ = make_read_workspace(tmp_path)
    result = compare(context(paths), fuel="electricity", limit=1)
    assert list(result.plans) == ["electricity"] and result.plans["electricity"].shown == 1
    assert len(result.options) == 1
    branded = compare(context(paths), brand="other")
    assert [item.plan_id for item in branded.plans["electricity"].items] == ["SYN-E-2"]
    assert branded.plans["electricity"].current_rank == 3  # the rank in the full list
    assert [option.rank for option in branded.options] == [2]


@pytest.mark.parametrize("limit", [0, 201])
def test_limit_out_of_range(tmp_path: Path, limit: int) -> None:
    paths, _ = make_read_workspace(tmp_path)
    with pytest.raises(UsageError):
        compare(context(paths), limit=limit)


def test_no_recommendation(tmp_path: Path) -> None:
    paths, _ = make_read_workspace(tmp_path)
    (paths.root / "data/reports/recommendations/recommendation.json").unlink()
    result = compare(context(paths))
    assert result.recommendation_id is None and result.options == [] and result.plans == {}
    assert result.missing == ["data/reports/recommendations/recommendation.json"]
