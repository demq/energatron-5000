"""A region profile, kind `region_profile` (D3 §10.3; P2 spec §5.2): package data such as `regions/sa.yaml`.

`build_context()` loads the workspace region's profile and keeps the parsers it lists. Version 2 adds the local time
zone and the retailer's TOU windows, the region-specific values plan §14.1 keeps out of code. Version 3 adds the
electricity distributor and the CDR retailer base URIs that `fetch-offers` lists (P4 spec §4.2); `pipeline sync` copies
both into `params.yaml`. Version 4 adds the gas distributor (P5 spec §4.2), copied the same way. Network tariffs join
the profile when a phase needs them.
"""

from typing import Annotated, ClassVar, Literal

from pydantic import AfterValidator

from energatron.core.canonical import JSONValue
from energatron.core.models._common import RegionName, ZoneKey, partitioned_windows, retailer_bases, trimmed_text
from energatron.core.models.catalog import SourceType
from energatron.core.persisted import Persisted, migration

_Windows = Annotated[dict[str, tuple[str, ...]], AfterValidator(partitioned_windows("tou_windows"))]


def _unique(parsers: tuple[SourceType, ...]) -> tuple[SourceType, ...]:
    if len(set(parsers)) != len(parsers):
        raise ValueError("a source type is listed twice")
    return parsers


class RegionProfile(Persisted):
    """What a region uses: its time zone, its TOU windows, its distributor and CDR retailers, and the source types
    whose parsers apply.

    `local_tz` and `tou_windows` are `null` only in a document migrated from version 1, `distributor` and
    `cdr_retailers` only in one migrated from version 2, `gas_distributor` only in one migrated from version 3;
    `build_context()` refuses a profile with any of them unset.
    """

    KIND: ClassVar[str] = "region_profile"
    SCHEMA_VERSION: ClassVar[int] = 4
    kind: Literal["region_profile"] = "region_profile"
    schema_version: Literal[4] = 4

    name: RegionName
    local_tz: ZoneKey | None
    tou_windows: _Windows | None  # TOU label → ["HH:MM-HH:MM", ...], partitioning the day
    distributor: Annotated[str, AfterValidator(trimmed_text("distributor"))] | None
    cdr_retailers: Annotated[tuple[str, ...], AfterValidator(retailer_bases("cdr_retailers"))] | None
    gas_distributor: Annotated[str, AfterValidator(trimmed_text("gas_distributor"))] | None
    parsers: Annotated[tuple[SourceType, ...], AfterValidator(_unique)]


@migration("region_profile", from_version=1)
def _region_profile_v1_to_v2(doc: dict[str, JSONValue]) -> dict[str, JSONValue]:
    """Insert `null` for the zone and the windows: a migration has no region data to put there."""
    return {**doc, "schema_version": 2, "local_tz": None, "tou_windows": None}


@migration("region_profile", from_version=2)
def _region_profile_v2_to_v3(doc: dict[str, JSONValue]) -> dict[str, JSONValue]:
    """Insert `null` for the distributor and the retailers: a migration has no region data to put there."""
    return {**doc, "schema_version": 3, "distributor": None, "cdr_retailers": None}


@migration("region_profile", from_version=3)
def _region_profile_v3_to_v4(doc: dict[str, JSONValue]) -> dict[str, JSONValue]:
    """Insert `null` for the gas distributor: a migration has no region data to put there."""
    return {**doc, "schema_version": 4, "gas_distributor": None}
