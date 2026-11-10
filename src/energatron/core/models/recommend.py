"""The P6 kinds (P6 spec §4-§10): sensitivity, bundles, the recommendation, run manifests, decisions and outcomes.

The tables' pyarrow schemas live in `services/stages/tables.py` (core holds no pyarrow, D1 §3). Workspace data: the
documents hold the household's usage-derived costs and are never written to the tool repo.
"""

from typing import Annotated, ClassVar, Final, Literal

from pydantic import Field

from energatron.core.models._common import FrozenModel
from energatron.core.persisted import Persisted

type Fuel = Literal["electricity", "gas"]
FUELS: Final[tuple[Fuel, ...]] = ("electricity", "gas")
_Count = Annotated[int, Field(ge=0)]

# `sensitivity` (P6 spec §4)

SENSITIVITY_VARIATIONS: Final = ("usage_up", "usage_down", "alt_window")
SENSITIVITY_TOTAL_KIND: Final = "sensitivity_total"
SENSITIVITY_TOTAL_VERSION: Final = 1
SENSITIVITY_TOTAL_SORT: Final = ("fuel", "variation", "rank")
SENSITIVITY_TOTALS_FILENAME: Final = "totals.parquet"
SENSITIVITY_SUMMARY_FILENAME: Final = "summary.json"


class VariationInfo(FrozenModel):
    """Whether a variation was priced for a fuel; why not; the window it priced; the usage factor applied."""

    available: bool
    reason: str | None  # no_usage, no_tariffs, too_little_history, not_contiguous; None when available
    window_start: str | None  # electricity YYYY-MM, gas YYYY-MM-DD
    window_end: str | None
    factor: str | None  # a decimal string for usage_up / usage_down


class FuelSensitivity(FrozenModel):
    tariffs: _Count
    variations: dict[str, VariationInfo]


class SensitivitySummary(Persisted):
    """`sensitivity_summary` v1 (P6 spec §4)."""

    KIND: ClassVar[str] = "sensitivity_summary"
    SCHEMA_VERSION: ClassVar[int] = 1
    kind: Literal["sensitivity_summary"] = "sensitivity_summary"
    schema_version: Literal[1] = 1

    usage_scale_pct: str
    fuels: dict[Fuel, FuelSensitivity]
    checks: dict[str, bool]


# `bundles` (P6 spec §5.3)

BUNDLE_VARIATIONS: Final = ("base", "conditions_not_met", *SENSITIVITY_VARIATIONS)
BUNDLE_OPTION_KIND: Final = "bundle_option"
BUNDLE_OPTION_VERSION: Final = 1
BUNDLE_OPTION_SORT: Final = ("variation", "rank")
BUNDLES_OPTIONS_FILENAME: Final = "options.parquet"
BUNDLES_SUMMARY_FILENAME: Final = "summary.json"


class BundlesSummary(Persisted):
    """`bundles_summary` v1 (P6 spec §5.3)."""

    KIND: ClassVar[str] = "bundles_summary"
    SCHEMA_VERSION: ClassVar[int] = 1
    kind: Literal["bundles_summary"] = "bundles_summary"
    schema_version: Literal[1] = 1

    variations: list[str]  # used, in BUNDLE_VARIATIONS order
    skipped: list[str]
    candidates: dict[Fuel, _Count]
    history_excluded: dict[Fuel, _Count]
    rules: list[str]  # loaded, by id
    rules_applied: list[str]
    options: dict[str, _Count]  # per variation used
    checks: dict[str, bool]
