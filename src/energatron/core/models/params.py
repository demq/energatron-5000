"""`params.yaml`, kind `params` (D3 §10.2; P2 spec §5.1).

Version 2 adds `meter.*` (the time basis and the local zone) and `validation.*` (the tolerances, and the TOU windows
that `validate_timebase` fits). The zone and the windows are region-specific, so core holds no default for them:
`pipeline sync` copies them from the region profile (`Params.defaults(profile)` for a new file), and a migrated
document carries `null` until then. `prepare_stage` refuses a stage that reads one while it is `null`.

Each phase that adds keys bumps the version and adds a migration that inserts its defaults. `pipeline sync` rewrites
an older file, because DVC reads declared keys from the file itself; energatron's readers still upgrade it in memory
(D1 §5).
"""

from typing import Annotated, ClassVar, Final, Literal, Self

from pydantic import AfterValidator, Field

from energatron.core.canonical import JSONValue
from energatron.core.models._common import FrozenModel, ZoneKey, partitioned_windows
from energatron.core.models.region import RegionProfile
from energatron.core.persisted import Persisted, migration

REGION_FIELDS: Final = ("meter.local_tz", "validation.timebase.windows")
"""The dotted keys that hold region data and may be `null` until `pipeline sync` fills them."""

type TimebaseName = Literal["nem_aest", "local_wall", "local_standard"]

_WINDOWS: Final = "validation.timebase.windows"

_Windows = Annotated[dict[str, tuple[str, ...]], AfterValidator(partitioned_windows(_WINDOWS))]
_Tolerance = Annotated[float, Field(ge=0)]


class MeterParams(FrozenModel):
    timebase: TimebaseName
    local_tz: ZoneKey | None  # from the region profile; `null` until `pipeline sync`


class MeterValidation(FrozenModel):
    total_tolerance_kwh: _Tolerance


class TimebaseValidation(FrozenModel):
    windows: _Windows | None  # label → ["HH:MM-HH:MM", ...]; from the region profile, `null` until `pipeline sync`
    register_tolerance_kwh: _Tolerance
    register_tolerance_pct: _Tolerance
    total_tolerance_kwh: _Tolerance
    min_fit_periods: Annotated[int, Field(strict=True, ge=1)]


class ValidationParams(FrozenModel):
    meter: MeterValidation
    timebase: TimebaseValidation


class Params(Persisted):
    """The workspace's non-personal pipeline settings. `prepare_stage` validates the whole file through this model."""

    KIND: ClassVar[str] = "params"
    SCHEMA_VERSION: ClassVar[int] = 2
    kind: Literal["params"] = "params"
    schema_version: Literal[2] = 2

    meter: MeterParams
    validation: ValidationParams

    @classmethod
    def defaults(cls, profile: RegionProfile) -> Self:
        """The current version's defaults, with the zone and windows taken from `profile` (P2 spec §5.1)."""
        return cls(
            meter=MeterParams(timebase="nem_aest", local_tz=profile.local_tz),
            validation=ValidationParams(
                meter=MeterValidation(total_tolerance_kwh=0.01),
                timebase=TimebaseValidation(
                    windows=profile.tou_windows,
                    register_tolerance_kwh=0.05,
                    register_tolerance_pct=0.1,
                    total_tolerance_kwh=0.05,
                    min_fit_periods=3,
                ),
            ),
        )


@migration("params", from_version=1)
def _params_v1_to_v2(doc: dict[str, JSONValue]) -> dict[str, JSONValue]:
    """Insert `meter` and `validation` with the v2 defaults; the zone and windows are `null` (no region here)."""
    return {
        **doc,
        "schema_version": 2,
        "meter": {"timebase": "nem_aest", "local_tz": None},
        "validation": {
            "meter": {"total_tolerance_kwh": 0.01},
            "timebase": {
                "windows": None,
                "register_tolerance_kwh": 0.05,
                "register_tolerance_pct": 0.1,
                "total_tolerance_kwh": 0.05,
                "min_fit_periods": 3,
            },
        },
    }
