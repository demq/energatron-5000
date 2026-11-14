"""`services.usage_summary` (P9 spec §3.2): summaries and monthly tables, cut to N months; missing and bad inputs."""

import json
from decimal import Decimal
from pathlib import Path

import pytest

from energatron.core.errors import InvalidInputError, UsageError
from energatron.services.usage_summary import usage
from tests.unit.services._read_workspace import context, make_read_workspace


def test_electricity_and_gas(tmp_path: Path) -> None:
    paths, _ = make_read_workspace(tmp_path)
    result = usage(context(paths))
    assert result.missing == []
    assert result.electricity is not None and result.gas is not None
    e1 = result.electricity.suffixes["E1"]
    assert len(e1.months) == 13 and e1.months[0].month == "2025-01" and e1.months[-1].month == "2026-01"
    assert e1.months[-1].kwh == 22.0 and e1.months[-1].avg_daily_kwh is None  # NaN in the table
    assert e1.months[0].tou == {"Off Peak": 1.0, "Peak": 1.0}
    assert e1.tou_shares == {"Off Peak": 0.25, "Peak": 0.25, "Shoulder": 0.5}
    assert result.electricity.excluded_suffixes == ["B1"] and result.electricity.dst_days.short == 1
    assert result.gas.reference_mj == Decimal("2000.000000") and result.gas.gaps == 0 and result.gas.partial_year
    assert [month.month for month in result.gas.months] == ["2026-08", "2026-09"]


def test_months_cuts_each_list_to_the_newest(tmp_path: Path) -> None:
    paths, _ = make_read_workspace(tmp_path)
    result = usage(context(paths), months=1)
    assert result.electricity is not None and result.gas is not None
    assert [month.month for month in result.electricity.suffixes["E1"].months] == ["2026-01"]
    assert [month.month for month in result.gas.months] == ["2026-09"]


@pytest.mark.parametrize("months", [0, 61])
def test_months_out_of_range(tmp_path: Path, months: int) -> None:
    paths, _ = make_read_workspace(tmp_path)
    with pytest.raises(UsageError):
        usage(context(paths), months=months)


def test_a_missing_summary_leaves_its_fuel_null(tmp_path: Path) -> None:
    paths, _ = make_read_workspace(tmp_path)
    (paths.root / "data/marts/gas_profile/summary.json").unlink()
    result = usage(context(paths))
    assert result.gas is None and result.missing == ["data/marts/gas_profile/summary.json"]
    assert result.electricity is not None


def test_a_bad_table_or_summary_names_the_file(tmp_path: Path) -> None:
    paths, _ = make_read_workspace(tmp_path)
    (paths.root / "data/marts/usage_profile/monthly.parquet").write_bytes(b"not parquet")
    with pytest.raises(InvalidInputError) as caught:
        usage(context(paths))
    assert caught.value.details == {"file": "data/marts/usage_profile/monthly.parquet"}
    (paths.root / "data/marts/gas_profile/summary.json").write_text(json.dumps({"kind": "gas_profile_summary"}))
    (paths.root / "data/marts/usage_profile/summary.json").unlink()
    with pytest.raises(InvalidInputError) as caught:
        usage(context(paths))
    assert caught.value.details == {"file": "data/marts/gas_profile/summary.json"}
