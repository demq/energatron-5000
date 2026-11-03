"""A minimal `params.yaml` at version 2 or 3, as text (P2 spec §5.1, P3 spec §4.1). Every value is made up.

DVC reads declared params from the file itself, so a scratch workspace that runs a stage with params needs current text,
not the v1 stub that the migration would upgrade in memory. By default the zone and windows stay `null`, as after a
migration; `region=True` fills them the way `pipeline sync` would, with the fixture's zone and windows.
`params_v3_text` adds the `analysis` block, optionally with a second TOU window set.
"""

from collections.abc import Mapping, Sequence
from typing import Final

import yaml

from tests.fixtures.pipeline_workspace import WINDOWS

ZONE: Final = "Australia/Adelaide"
"""The zone the fixtures' TOU exports are generated in (see `tests/fixtures/oracle.py`)."""

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
