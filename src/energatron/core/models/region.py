"""A region profile, kind `region_profile` (D3 §10.3; P2 spec §5.2): package data such as `regions/sa.yaml`.

`build_context()` loads the workspace region's profile and keeps the parsers it lists. Version 2 adds the local time
zone and the retailer's TOU windows, the region-specific values plan §14.1 keeps out of code. Distributors and network
tariffs join the profile with the P4 spec, as new versions.
"""

from typing import Annotated, ClassVar, Literal

from pydantic import AfterValidator

from energatron.core.canonical import JSONValue
from energatron.core.models._common import RegionName, ZoneKey, partitioned_windows
from energatron.core.models.catalog import SourceType
from energatron.core.persisted import Persisted, migration

_Windows = Annotated[dict[str, tuple[str, ...]], AfterValidator(partitioned_windows("tou_windows"))]


def _unique(parsers: tuple[SourceType, ...]) -> tuple[SourceType, ...]:
    if len(set(parsers)) != len(parsers):
        raise ValueError("a source type is listed twice")
    return parsers


class RegionProfile(Persisted):
    """What a region uses: its time zone, its TOU windows and the source types whose parsers apply.

    `local_tz` and `tou_windows` are `null` only in a document migrated from version 1; `build_context()` refuses a
    profile that has either unset.
    """

    KIND: ClassVar[str] = "region_profile"
    SCHEMA_VERSION: ClassVar[int] = 2
    kind: Literal["region_profile"] = "region_profile"
    schema_version: Literal[2] = 2

    name: RegionName
    local_tz: ZoneKey | None
    tou_windows: _Windows | None  # TOU label → ["HH:MM-HH:MM", ...], partitioning the day
    parsers: Annotated[tuple[SourceType, ...], AfterValidator(_unique)]


@migration("region_profile", from_version=1)
def _region_profile_v1_to_v2(doc: dict[str, JSONValue]) -> dict[str, JSONValue]:
    """Insert `null` for the zone and the windows: a migration has no region data to put there."""
    return {**doc, "schema_version": 2, "local_tz": None, "tou_windows": None}
