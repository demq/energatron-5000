"""A minimal `params.yaml` at version 2, as text (P2 spec §5.1). Every value is made up.

DVC reads declared params from the file itself, so a scratch workspace that runs a stage with params needs v2 text, not
the v1 stub that the migration would upgrade in memory. The zone and windows stay `null`, as after a migration.
"""

from typing import Final

_TEMPLATE: Final = """\
kind: params
schema_version: 2
meter:
  timebase: nem_aest
  local_tz: null
validation:
  meter:
    total_tolerance_kwh: {tolerance}
  timebase:
    windows: null
    register_tolerance_kwh: 0.05
    register_tolerance_pct: 0.1
    total_tolerance_kwh: 0.05
    min_fit_periods: 3
"""


def params_v2_text(tolerance: float = 0.01) -> str:
    """`params.yaml` v2 with `validation.meter.total_tolerance_kwh` set to `tolerance`."""
    return _TEMPLATE.format(tolerance=tolerance)
