"""A region profile, kind `region_profile` (D3 §10.3): package data such as `regions/sa.yaml`.

`build_context()` loads the workspace region's profile and keeps the parsers it lists. Time zone, distributors and
network tariffs join the profile with the P2 and P4 specs, as new versions.
"""

from typing import Annotated, ClassVar, Literal

from pydantic import AfterValidator

from energatron.core.models._common import RegionName
from energatron.core.models.catalog import SourceType
from energatron.core.persisted import Persisted


def _unique(parsers: tuple[SourceType, ...]) -> tuple[SourceType, ...]:
    if len(set(parsers)) != len(parsers):
        raise ValueError("a source type is listed twice")
    return parsers


class RegionProfile(Persisted):
    """What a region uses: for now, the source types whose parsers apply."""

    KIND: ClassVar[str] = "region_profile"
    SCHEMA_VERSION: ClassVar[int] = 1
    kind: Literal["region_profile"] = "region_profile"
    schema_version: Literal[1] = 1

    name: RegionName
    parsers: Annotated[tuple[SourceType, ...], AfterValidator(_unique)]
