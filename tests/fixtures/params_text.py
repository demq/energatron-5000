"""A minimal `params.yaml` at version 2, 3 or 4, as text (P2 spec §5.1, P3 spec §4.1, P4 spec §4.1). Every value is
made up.

DVC reads declared params from the file itself, so a scratch workspace that runs a stage with params needs current text,
not the v1 stub that the migration would upgrade in memory. By default the zone and windows stay `null`, as after a
migration; `region=True` fills them the way `pipeline sync` would, with the fixture's zone and windows.
`params_v3_text` adds the `analysis` block, optionally with a second TOU window set; `params_v4_text` adds the
`offers`, `pricing`, `backtest` and `validation.backtest` blocks; `params_v5_text` adds P5's gas keys.
"""

import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Final

import yaml

from tests.fixtures.pipeline_workspace import WINDOWS

ZONE: Final = "Australia/Adelaide"
"""The zone the fixtures' TOU exports are generated in (see `tests/fixtures/oracle.py`)."""
RETAILERS: Final = ("alpha", "beta-energy")
"""Made-up CDR base URIs."""
DISTRIBUTOR: Final = "Test Networks"
"""A made-up distributor."""
GAS_DISTRIBUTOR: Final = "Test Gas Networks"
"""A made-up gas distributor."""

_TEMPLATE: Final = """\
kind: params
schema_version: 2
meter:
  timebase: {basis}
  local_tz: {zone}
validation:
  meter:
    total_tolerance_kwh: {tolerance}
  timebase:
    windows: {windows}
    register_tolerance_kwh: 0.05
    register_tolerance_pct: 0.1
    total_tolerance_kwh: 0.05
    min_fit_periods: {min_fit_periods}
"""


def params_v2_text(
    tolerance: float = 0.01,
    *,
    region: bool = False,
    basis: str = "nem_aest",
    windows: Mapping[str, Sequence[str]] | None = None,
    min_fit_periods: int = 3,
) -> str:
    """`params.yaml` v2 with `validation.meter.total_tolerance_kwh` set to `tolerance`.

    `region=True` sets `meter.local_tz` and `validation.timebase.windows` (`windows`, else the fixture's); otherwise
    both are `null`. `basis` is `meter.timebase`.
    """
    chosen = {label: list(found) for label, found in (windows or WINDOWS).items()}
    return _TEMPLATE.format(
        tolerance=tolerance,
        basis=basis,
        zone=ZONE if region else "null",
        windows=yaml.safe_dump(chosen, default_flow_style=True).strip() if region else "null",
        min_fit_periods=min_fit_periods,
    )


_V3_ANALYSIS: Final = """\
analysis:
  reference_months: {reference_months}
  coverage_threshold: {coverage_threshold}
  seasons:
    DJF: [12, 1, 2]
    MAM: [3, 4, 5]
    JJA: [6, 7, 8]
    SON: [9, 10, 11]
  weekend: [saturday, sunday]
  band_percentiles: [10, 50, 90]
  base_load:
    from: "01:00"
    to: "05:00"
    percentile: 10
  demand_minutes: 30
  tou:
    window_sets:
{sets}"""


def params_v3_text(
    tolerance: float = 0.01,
    *,
    region: bool = False,
    basis: str = "nem_aest",
    windows: Mapping[str, Sequence[str]] | None = None,
    min_fit_periods: int = 3,
    reference_months: int = 12,
    coverage_threshold: float = 0.95,
    second_set: tuple[str, Mapping[str, Sequence[str]]] | None = None,
) -> str:
    """`params.yaml` v3: `params_v2_text`'s sections (schema version 3) followed by a made-up `analysis` block.

    `region=True` fills the zone, `validation.timebase.windows` and the first TOU window set (`windows`, else the
    fixture's); otherwise all three are `null`. `second_set` is an `(effective_from, windows)` pair appended as the
    second window set.
    """
    base = params_v2_text(tolerance, region=region, basis=basis, windows=windows, min_fit_periods=min_fit_periods)
    chosen = {label: list(found) for label, found in (windows or WINDOWS).items()}
    first = yaml.safe_dump(chosen, default_flow_style=True).strip() if region else "null"
    sets = f"      - {{effective_from: null, windows: {first}}}\n"
    if second_set is not None:
        start, later = second_set
        text = yaml.safe_dump({label: list(found) for label, found in later.items()}, default_flow_style=True).strip()
        sets += f'      - {{effective_from: "{start}", windows: {text}}}\n'
    analysis = _V3_ANALYSIS.format(reference_months=reference_months, coverage_threshold=coverage_threshold, sets=sets)
    return base.replace("schema_version: 2", "schema_version: 3", 1) + analysis


_V4_BLOCKS: Final = """\
offers:
  retailers: {retailers}
  distributor: {distributor}
  customer_type: RESIDENTIAL
  fuel_types: [ELECTRICITY]
  unmet_eligibility: [EXISTING_SOLAR, EXISTING_BATTERY]
  controlled_load: {controlled_load}
pricing:
  gst_rate: "{gst_rate}"
  assume_conditions_met: [PAY_ON_TIME, DIRECT_DEBIT]
  demand_minutes: 30
backtest:
  tariffs: {tariffs}
"""
_V4_VALIDATION: Final = """\
  backtest:
    tolerance_pct: {tolerance_pct}
    invoice_window_days: 14
"""


def params_v4_text(
    tolerance: float = 0.01,
    *,
    region: bool = False,
    basis: str = "nem_aest",
    windows: Mapping[str, Sequence[str]] | None = None,
    min_fit_periods: int = 3,
    reference_months: int = 12,
    coverage_threshold: float = 0.95,
    second_set: tuple[str, Mapping[str, Sequence[str]]] | None = None,
    controlled_load: bool = False,
    gst_rate: str = "0.10",
    tariffs: Sequence[Mapping[str, str | None]] = (),
    tolerance_pct: float = 1.0,
) -> str:
    """`params.yaml` v4: `params_v3_text` (schema version 4) with `validation.backtest` and the P4 blocks.

    `region=True` also fills `offers.retailers` and `offers.distributor` (`RETAILERS`, `DISTRIBUTOR`); otherwise both
    are `null`. `tariffs` is `backtest.tariffs`, written with quoted dates.
    """
    base = params_v3_text(
        tolerance,
        region=region,
        basis=basis,
        windows=windows,
        min_fit_periods=min_fit_periods,
        reference_months=reference_months,
        coverage_threshold=coverage_threshold,
        second_set=second_set,
    ).replace("schema_version: 3", "schema_version: 4", 1)
    marker = "    min_fit_periods: "
    head, tail = base.split(marker, 1)
    line, rest = tail.split("\n", 1)
    with_validation = f"{head}{marker}{line}\n{_V4_VALIDATION.format(tolerance_pct=tolerance_pct)}{rest}"
    blocks = _V4_BLOCKS.format(
        retailers=yaml.safe_dump(list(RETAILERS), default_flow_style=True).strip() if region else "null",
        distributor=DISTRIBUTOR if region else "null",
        controlled_load="true" if controlled_load else "false",
        gst_rate=gst_rate,
        tariffs=json.dumps([dict(found) for found in tariffs]),
    )
    return with_validation + blocks


def set_reference_months(params_file: Path, months: int) -> None:
    """Set `analysis.reference_months` in a synced `params.yaml`: the defaults ask for a year, a fixture has fewer."""
    text = params_file.read_text(encoding="utf-8")
    changed = re.sub(r"(?m)^  reference_months: [0-9]+$", f"  reference_months: {months}", text)
    assert changed != text, "no analysis.reference_months to change"
    params_file.write_text(changed, encoding="utf-8")


def params_v5_text(
    tolerance: float = 0.01,
    *,
    region: bool = False,
    gas_tariffs: Sequence[Mapping[str, str | None]] = (),
    reference_bills: int = 12,
    full_year_days: int = 334,
    gas_tolerance: str = "0.05",
    fuel_types: Sequence[str] = ("ELECTRICITY", "GAS"),
    **v4: Any,
) -> str:
    """`params.yaml` v5: `params_v4_text` (schema version 5) with P5's keys (P5 spec §4.1).

    `region=True` also fills `offers.gas_distributor` (`GAS_DISTRIBUTOR`); otherwise it is `null`. `gas_tariffs` is
    `backtest.gas_tariffs`; the other keyword arguments go to `params_v4_text`.
    """
    base = params_v4_text(tolerance, region=region, **v4).replace("schema_version: 4", "schema_version: 5", 1)
    base = base.replace("  fuel_types: [ELECTRICITY]\n", f"  fuel_types: [{', '.join(fuel_types)}]\n", 1)
    distributor_line = next(line for line in base.splitlines() if line.startswith("  distributor: "))
    base = base.replace(
        distributor_line + "\n",
        f"{distributor_line}\n  gas_distributor: {GAS_DISTRIBUTOR if region else 'null'}\n",
        1,
    )
    marker = "    invoice_window_days: 14\n"
    base = base.replace(marker, f'{marker}  gas_bills:\n    tolerance: "{gas_tolerance}"\n', 1)
    tariffs_line = next(line for line in base.splitlines() if line.startswith("  tariffs: "))
    base = base.replace(
        tariffs_line + "\n", f"{tariffs_line}\n  gas_tariffs: {json.dumps([dict(found) for found in gas_tariffs])}\n", 1
    )
    return base + f"gas:\n  reference_bills: {reference_bills}\n  full_year_days: {full_year_days}\n"


def params_v6_text(
    tolerance: float = 0.01,
    *,
    top: int = 5,
    usage_scale_pct: str = "10",
    min_saving: str = "50",
    recheck_days: int = 90,
    price_reset: str = "07-01",
    **v5: Any,
) -> str:
    """`params.yaml` v6: `params_v5_text` (schema version 6) with the `recommend` block (P6 spec §3.1)."""
    base = params_v5_text(tolerance, **v5).replace("schema_version: 5", "schema_version: 6", 1)
    return base + (
        f'recommend:\n  top: {top}\n  usage_scale_pct: "{usage_scale_pct}"\n  min_saving: "{min_saving}"\n'
        f'  recheck_days: {recheck_days}\n  price_reset: "{price_reset}"\n'
    )
