"""`params.yaml`, kind `params` (D3 §10.2; P2 spec §5.1; P3 spec §4.1).

Version 2 adds `meter.*` (the time basis and the local zone) and `validation.*` (the tolerances, and the TOU windows
that `validate_timebase` fits). Version 3 adds `analysis.*` (the usage profile's settings, and the date-ranged TOU
window sets its split uses). Version 4 adds `offers.*` (what `fetch-offers` lists and `normalise_offers` keeps),
`pricing.*` (GST, the discount conditions assumed met, the demand block), `backtest.*` (the tariffs in force) and
`validation.backtest` (P4 spec §4.1). Version 5 adds `GAS` to the fuels, `offers.gas_distributor`, `gas.*` (the gas
reference year), `backtest.gas_tariffs` and `validation.gas_bills` (P5 spec §4.1). The zone, the windows, the CDR
retailers and the two distributors are region-specific, so core holds no default for them: `pipeline sync` copies
them from the region profile (`Params.defaults(profile)` for a new file), and a migrated document carries `null` until
then. `prepare_stage` refuses a stage that reads one while it is `null`.

Each phase that adds keys bumps the version and adds a migration that inserts its defaults. `pipeline sync` rewrites
an older file, because DVC reads declared keys from the file itself; energatron's readers still upgrade it in memory
(D1 §5).
"""

from collections.abc import Callable
from datetime import date
from decimal import Decimal
from typing import Annotated, Any, ClassVar, Final, Literal, Self

from pydantic import AfterValidator, ConfigDict, Field, StrictInt, model_validator

from energatron.core.canonical import JSONValue
from energatron.core.errors import InvalidInputError
from energatron.core.ids import parse_offer_snapshot_id
from energatron.core.models._common import (
    FrozenModel,
    ZoneKey,
    partitioned_windows,
    retailer_bases,
    trimmed_text,
    window_minutes,
    windows_partition_the_day,
)
from energatron.core.models.region import RegionProfile
from energatron.core.persisted import Persisted, migration

FIRST_SET_WINDOWS: Final = "analysis.tou.window_sets[0].windows"

REGION_FIELDS: Final = (
    "meter.local_tz",
    "validation.timebase.windows",
    FIRST_SET_WINDOWS,
    "offers.retailers",
    "offers.distributor",
    "offers.gas_distributor",
)
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


class BacktestValidation(FrozenModel):
    tolerance_pct: _Tolerance
    invoice_window_days: Annotated[int, Field(strict=True, ge=0, le=62)]


def _non_negative_decimal(field: str) -> Callable[[Decimal], Decimal]:
    def check(value: Decimal) -> Decimal:
        if not value.is_finite() or value < 0:
            raise ValueError(f"{field}: a decimal of 0 or more")
        return value

    return check


class GasBillsValidation(FrozenModel):
    """How closely a gas bill record must recompute (P5 spec §6): dollars per line and per bill, MJ per read."""

    tolerance: Annotated[Decimal, AfterValidator(_non_negative_decimal("validation.gas_bills.tolerance"))]


class ValidationParams(FrozenModel):
    meter: MeterValidation
    timebase: TimebaseValidation
    backtest: BacktestValidation
    gas_bills: GasBillsValidation


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


# `offers`, `pricing` and `backtest` (P4 spec §4.1)

type Fuel = Literal["ELECTRICITY", "GAS"]  # DUAL waits for P6
type CustomerType = Literal["RESIDENTIAL", "BUSINESS"]
type Condition = Literal["PAY_ON_TIME", "DIRECT_DEBIT"]
type Eligibility = Literal[
    "EXISTING_CUST",
    "EXISTING_POOL",
    "EXISTING_SOLAR",
    "EXISTING_BATTERY",
    "EXISTING_SMART_METER",
    "EXISTING_BASIC_METER",
    "SENIOR_CARD",
    "SMALL_BUSINESS",
    "NO_SOLAR_FIT",
    "NEW_CUSTOMER",
    "ONLINE_ONLY",
    "REQ_EQUIP_SUPPLIER",
    "THIRD_PARTY_ONLY",
    "SPORT_CLUB_MEMBER",
    "ORG_MEMBER",
    "SPECIFIC_LOCATION",
    "MINIMUM_USAGE",
    "LOYALTY_MEMBER",
    "GROUP_BUY_MEMBER",
    "CONTINGENT_PLAN",
    "OTHER",
]
"""The CDR energy standard's eligibility types (v1.36)."""

_Retailers = Annotated[tuple[str, ...], AfterValidator(retailer_bases("offers.retailers"))]
_Distributor = Annotated[str, AfterValidator(trimmed_text("offers.distributor"))]
_GasDistributor = Annotated[str, AfterValidator(trimmed_text("offers.gas_distributor"))]


def _unique_items[T](field: str) -> Callable[[tuple[T, ...]], tuple[T, ...]]:
    """A validator for a tuple with no repeated entry, naming `field`."""

    def check(items: tuple[T, ...]) -> tuple[T, ...]:
        if len(set(items)) != len(items):
            raise ValueError(f"{field}: an entry is listed twice")
        return items

    return check


class OffersParams(FrozenModel):
    """What `fetch-offers` lists and keeps, and what the household can't meet (P4 spec §3.2, §5.2)."""

    retailers: _Retailers | None  # CDR base URIs; from the region profile, `null` until `pipeline sync`
    distributor: _Distributor | None  # from the region profile, `null` until `pipeline sync`
    gas_distributor: _GasDistributor | None  # likewise (P5 spec §4.1)
    customer_type: CustomerType
    fuel_types: Annotated[tuple[Fuel, ...], Field(min_length=1), AfterValidator(_unique_items("offers.fuel_types"))]
    unmet_eligibility: Annotated[tuple[Eligibility, ...], AfterValidator(_unique_items("offers.unmet_eligibility"))]
    controlled_load: Annotated[bool, Field(strict=True)]


def _gst_rate(value: Decimal) -> Decimal:
    if not value.is_finite() or not Decimal(0) <= value < Decimal(1):
        raise ValueError("pricing.gst_rate: a decimal from 0 up to but not including 1")
    return value


class PricingParams(FrozenModel):
    """How the engine prices an offer (P4 spec §7)."""

    gst_rate: Annotated[Decimal, AfterValidator(_gst_rate)]  # written as a decimal string, e.g. "0.10"
    assume_conditions_met: Annotated[
        tuple[Condition, ...], AfterValidator(_unique_items("pricing.assume_conditions_met"))
    ]
    demand_minutes: Annotated[int, Field(strict=True, ge=5, le=60)]

    @model_validator(mode="after")
    def _demand_block_divides_an_hour(self) -> Self:
        if self.demand_minutes % 5 or 60 % self.demand_minutes:
            raise InvalidInputError(
                "The demand block must be a multiple of 5 minutes that divides an hour.",
                field="pricing.demand_minutes",
            )
        return self


def _snapshot_id(value: str) -> str:
    parse_offer_snapshot_id(value)  # its ValueError never repeats the value
    return value


class TariffInForce(FrozenModel):
    """An offer snapshot in force over meter dates `from`-`to`, both inclusive; `to` is open when `null`."""

    model_config = ConfigDict(serialize_by_alias=True, validate_by_name=True)  # merged with FrozenModel's

    offer: Annotated[str, AfterValidator(_snapshot_id)]
    start: Annotated[date, Field(alias="from")]
    end: Annotated[date | None, Field(alias="to")]


def _check_tariffs_in_force(tariffs: tuple[TariffInForce, ...], field: str) -> None:
    ordered = sorted(tariffs, key=lambda found: found.start)
    for index, found in enumerate(ordered):
        if found.end is not None and found.end < found.start:
            raise InvalidInputError("A tariff in force ends before it starts.", field=field)
        following = ordered[index + 1] if index + 1 < len(ordered) else None
        if following is not None and (found.end is None or found.end >= following.start):
            raise InvalidInputError("Two tariffs in force overlap.", field=field)


class BacktestParams(FrozenModel):
    """The tariffs in force for the electricity backtest (P4 spec §10) and the gas backtest (P5 spec §10); within each
    list, ranges must not overlap."""

    tariffs: tuple[TariffInForce, ...]
    gas_tariffs: tuple[TariffInForce, ...]

    @model_validator(mode="after")
    def _ranges_are_valid_and_disjoint(self) -> Self:
        _check_tariffs_in_force(self.tariffs, "backtest.tariffs")
        _check_tariffs_in_force(self.gas_tariffs, "backtest.gas_tariffs")
        return self


class GasParams(FrozenModel):
    """The gas reference year (P5 spec §4.1, §7): the latest `reference_bills` bills; shorter than `full_year_days`
    days is a partial year."""

    reference_bills: Annotated[int, Field(strict=True, ge=1)]
    full_year_days: Annotated[int, Field(strict=True, ge=1, le=366)]


_DEFAULT_OFFERS: Final = {
    "customer_type": "RESIDENTIAL",
    "fuel_types": ("ELECTRICITY", "GAS"),
    "unmet_eligibility": ("EXISTING_SOLAR", "EXISTING_BATTERY"),
    "controlled_load": False,
}


def _offers_defaults(
    retailers: tuple[str, ...] | None, distributor: str | None, gas_distributor: str | None
) -> OffersParams:
    return OffersParams.model_validate(
        {"retailers": retailers, "distributor": distributor, "gas_distributor": gas_distributor, **_DEFAULT_OFFERS}
    )


def _pricing_defaults() -> PricingParams:
    return PricingParams(
        gst_rate=Decimal("0.10"), assume_conditions_met=("PAY_ON_TIME", "DIRECT_DEBIT"), demand_minutes=30
    )


def _backtest_validation_defaults() -> BacktestValidation:
    return BacktestValidation(tolerance_pct=1.0, invoice_window_days=14)


def _gas_bills_validation_defaults() -> GasBillsValidation:
    return GasBillsValidation(tolerance=Decimal("0.05"))


def _gas_defaults() -> GasParams:
    return GasParams(reference_bills=12, full_year_days=334)


class Params(Persisted):
    """The workspace's non-personal pipeline settings. `prepare_stage` validates the whole file through this model."""

    KIND: ClassVar[str] = "params"
    SCHEMA_VERSION: ClassVar[int] = 5
    kind: Literal["params"] = "params"
    schema_version: Literal[5] = 5

    meter: MeterParams
    validation: ValidationParams
    analysis: AnalysisParams
    offers: OffersParams
    pricing: PricingParams
    backtest: BacktestParams
    gas: GasParams

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
                backtest=_backtest_validation_defaults(),
                gas_bills=_gas_bills_validation_defaults(),
            ),
            analysis=AnalysisParams.defaults(profile.tou_windows),
            offers=_offers_defaults(profile.cdr_retailers, profile.distributor, profile.gas_distributor),
            pricing=_pricing_defaults(),
            backtest=BacktestParams(tariffs=(), gas_tariffs=()),
            gas=_gas_defaults(),
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


@migration("params", from_version=3)
def _params_v3_to_v4(doc: dict[str, JSONValue]) -> dict[str, JSONValue]:
    """Insert `offers`, `pricing`, `backtest` and `validation.backtest` with the v4 defaults; the retailers and the
    distributor are `null` (no region here)."""
    validation: Any = doc.get("validation")
    if isinstance(validation, dict):
        validation = {**validation, "backtest": _backtest_validation_defaults().model_dump(mode="json")}
    offers: Any = {
        "retailers": None,
        "distributor": None,
        "customer_type": "RESIDENTIAL",
        "fuel_types": ["ELECTRICITY"],
        "unmet_eligibility": ["EXISTING_SOLAR", "EXISTING_BATTERY"],
        "controlled_load": False,
    }  # the v4 content, written out: the defaults now hold v5's
    pricing: Any = _pricing_defaults().model_dump(mode="json")
    return {
        **doc,
        "schema_version": 4,
        "validation": validation,
        "offers": offers,
        "pricing": pricing,
        "backtest": {"tariffs": []},
    }


@migration("params", from_version=4)
def _params_v4_to_v5(doc: dict[str, JSONValue]) -> dict[str, JSONValue]:
    """Add `GAS` to `offers.fuel_types` and insert `offers.gas_distributor` (`null`: no region here), `gas`,
    `backtest.gas_tariffs` and `validation.gas_bills` with the v5 defaults (P5 spec §4.1)."""
    offers: Any = doc.get("offers")
    if isinstance(offers, dict):
        fuels = offers.get("fuel_types")
        if isinstance(fuels, list) and fuels and "GAS" not in fuels:  # an empty list stays invalid
            fuels = [*fuels, "GAS"]
        offers = {**offers, "fuel_types": fuels, "gas_distributor": None}
    backtest: Any = doc.get("backtest")
    if isinstance(backtest, dict):
        backtest = {**backtest, "gas_tariffs": []}
    validation: Any = doc.get("validation")
    if isinstance(validation, dict):
        validation = {**validation, "gas_bills": _gas_bills_validation_defaults().model_dump(mode="json")}
    gas: Any = _gas_defaults().model_dump(mode="json")
    return {
        **doc,
        "schema_version": 5,
        "validation": validation,
        "offers": offers,
        "backtest": backtest,
        "gas": gas,
    }
