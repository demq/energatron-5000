"""A minimal `params.yaml` at version 2, as text (P2 spec §5.1). Every value is made up.

DVC reads declared params from the file itself, so a scratch workspace that runs a stage with params needs v2 text, not
the v1 stub that the migration would upgrade in memory. By default the zone and windows stay `null`, as after a
migration; `region=True` fills them the way `pipeline sync` would, with the fixture's zone and windows.
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
