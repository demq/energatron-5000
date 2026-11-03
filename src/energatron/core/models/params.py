"""`params.yaml`, kind `params` (D3 §10.2; P2 spec §5.1; P3 spec §4.1).

Version 2 adds `meter.*` (the time basis and the local zone) and `validation.*` (the tolerances, and the TOU windows
that `validate_timebase` fits). Version 3 adds `analysis.*` (the usage profile's settings, and the date-ranged TOU
window sets its split uses). The zone and the windows are region-specific, so core holds no default for them:
`pipeline sync` copies them from the region profile (`Params.defaults(profile)` for a new file), and a migrated
document carries `null` until then. `prepare_stage` refuses a stage that reads one while it is `null`.

Each phase that adds keys bumps the version and adds a migration that inserts its defaults. `pipeline sync` rewrites
an older file, because DVC reads declared keys from the file itself; energatron's readers still upgrade it in memory
(D1 §5).
"""

from datetime import date
from typing import Annotated, Any, ClassVar, Final, Literal, Self

from pydantic import AfterValidator, ConfigDict, Field, StrictInt, model_validator

from energatron.core.canonical import JSONValue
from energatron.core.errors import InvalidInputError
from energatron.core.models._common import (
    FrozenModel,
    ZoneKey,
    partitioned_windows,
    window_minutes,
    windows_partition_the_day,
)
from energatron.core.models.region import RegionProfile
from energatron.core.persisted import Persisted, migration

FIRST_SET_WINDOWS: Final = "analysis.tou.window_sets[0].windows"

REGION_FIELDS: Final = ("meter.local_tz", "validation.timebase.windows", FIRST_SET_WINDOWS)
"""The keys that hold region data and may be `null` until `pipeline sync` fills them. A `[n]` is a list index."""

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


type Weekday = Literal["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

_SEASONS_FIELD: Final = "analysis.seasons"
_SETS_FIELD: Final = "analysis.tou.window_sets"
_MONTHS: Final = frozenset(range(1, 13))
_DEFAULT_SEASONS: Final[dict[str, tuple[int, ...]]] = {
    "DJF": (12, 1, 2),
    "MAM": (3, 4, 5),
    "JJA": (6, 7, 8),
    "SON": (9, 10, 11),
}
_Percentile = Annotated[int, Field(strict=True, ge=0, le=100)]


class BaseLoadParams(FrozenModel):
    """The local-clock window and percentile that define base load (P3 spec §5.7); the file's keys are `from`, `to`."""

    model_config = ConfigDict(serialize_by_alias=True, validate_by_name=True)  # merged with FrozenModel's

    start: Annotated[str, Field(alias="from")]
    end: Annotated[str, Field(alias="to")]
    percentile: _Percentile

    @model_validator(mode="after")
    def _window_is_on_the_five_minute_grid(self) -> Self:
        try:
            window_minutes(f"{self.start}-{self.end}")
        except ValueError as error:
            raise InvalidInputError(
                f"The base-load window is malformed: {error}.", field="analysis.base_load"
            ) from None
        return self


class WindowSet(FrozenModel):
    """The TOU windows in force from `effective_from` (a local date) until the next set's; the first has no start."""

    effective_from: date | None
    windows: dict[str, tuple[str, ...]] | None  # label → ["HH:MM-HH:MM", ...]; `null` only in the first, until sync


class TouParams(FrozenModel):
    window_sets: Annotated[tuple[WindowSet, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def _sets_follow_the_rules(self) -> Self:
        """P3 spec §4.1: the first set has no start, the others strictly increasing starts, each set partitions the
        day, and every set has the same labels."""
        previous: date | None = None
        labels: frozenset[str] | None = None
        for index, found in enumerate(self.window_sets):
            entry = f"{_SETS_FIELD}[{index}]"
            if index == 0 and found.effective_from is not None:
                raise InvalidInputError("The first TOU window set has no start date.", field=f"{entry}.effective_from")
            if index > 0:
                if found.effective_from is None or (previous is not None and found.effective_from <= previous):
                    raise InvalidInputError(
                        "Each later TOU window set needs a start date after the one before it.",
                        field=f"{entry}.effective_from",
                    )
                previous = found.effective_from
            if found.windows is None:
                if index > 0:
                    raise InvalidInputError("Only the first TOU window set may be unset.", field=f"{entry}.windows")
                continue
            windows_partition_the_day(found.windows, field=f"{entry}.windows")
            if labels is not None and frozenset(found.windows) != labels:
                raise InvalidInputError("Every TOU window set needs the same labels.", field=f"{entry}.windows")
            labels = frozenset(found.windows)
        return self


class AnalysisParams(FrozenModel):
    """The usage profile's settings (P3 spec §4.1). Everything but the first window set has a default for any region."""

    reference_months: Annotated[int, Field(strict=True, ge=1)]
    coverage_threshold: Annotated[float, Field(gt=0, le=1)]
    seasons: dict[str, tuple[StrictInt, ...]]  # label → calendar months; together they cover 1-12 exactly once
    weekend: Annotated[tuple[Weekday, ...], Field(min_length=1, max_length=6)]
    band_percentiles: tuple[_Percentile, _Percentile, _Percentile]
    base_load: BaseLoadParams
    demand_minutes: Annotated[int, Field(strict=True, ge=5, le=60)]
    tou: TouParams

    @model_validator(mode="after")
    def _settings_are_consistent(self) -> Self:
        months = [month for found in self.seasons.values() for month in found]
        if (
            not self.seasons
            or any(not label or not found for label, found in self.seasons.items())
            or (sorted(months) != sorted(_MONTHS))
        ):
            raise InvalidInputError(
                "The seasons must name every month from 1 to 12 exactly once, each season at least one.",
                field=_SEASONS_FIELD,
            )
        if len(set(self.weekend)) != len(self.weekend):
            raise InvalidInputError("A weekday is listed twice.", field="analysis.weekend")
        low, middle, high = self.band_percentiles
        if not low < middle < high:
            raise InvalidInputError("The band percentiles must strictly increase.", field="analysis.band_percentiles")
        if self.demand_minutes % 5 or 60 % self.demand_minutes:
            raise InvalidInputError(
                "The demand block must be a multiple of 5 minutes that divides an hour.",
                field="analysis.demand_minutes",
            )
        return self

    @classmethod
    def defaults(cls, windows: dict[str, tuple[str, ...]] | None) -> Self:
        """The defaults of P3 spec §4.1, with `windows` as the first TOU window set (`None` until `pipeline sync`)."""
        return cls(
            reference_months=12,
            coverage_threshold=0.95,
            seasons=dict(_DEFAULT_SEASONS),
            weekend=("saturday", "sunday"),
            band_percentiles=(10, 50, 90),
            base_load=BaseLoadParams(start="01:00", end="05:00", percentile=10),
            demand_minutes=30,
            tou=TouParams(window_sets=(WindowSet(effective_from=None, windows=windows),)),
        )


class Params(Persisted):
    """The workspace's non-personal pipeline settings. `prepare_stage` validates the whole file through this model."""

    KIND: ClassVar[str] = "params"
    SCHEMA_VERSION: ClassVar[int] = 3
    kind: Literal["params"] = "params"
    schema_version: Literal[3] = 3

    meter: MeterParams
    validation: ValidationParams
    analysis: AnalysisParams

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
            analysis=AnalysisParams.defaults(profile.tou_windows),
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


@migration("params", from_version=2)
def _params_v2_to_v3(doc: dict[str, JSONValue]) -> dict[str, JSONValue]:
    """Insert `analysis` with the v3 defaults; the first window set's windows are `null` (no region here)."""
    analysis: Any = AnalysisParams.defaults(None).model_dump(mode="json")
    return {**doc, "schema_version": 3, "analysis": analysis}
