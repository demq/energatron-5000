"""Every source parser, one per source type, and the region profile's filter (D1 §4.3; D3 §5, §10.3)."""

from typing import Final

from energatron.adapters.sources.sapn_nem12 import SAPN_INTERVAL
from energatron.core.errors import InternalError
from energatron.core.models.region import RegionProfile
from energatron.ports.sources import SourceParser

PARSERS: Final[tuple[SourceParser, ...]] = (SAPN_INTERVAL,)
"""The registered parsers. P1.5.1 adds the other five."""


def parsers_for(profile: RegionProfile, parsers: tuple[SourceParser, ...] = PARSERS) -> tuple[SourceParser, ...]:
    """The `parsers` whose `source` the profile lists, in registry order; a listed source with none → `E_INTERNAL`."""
    available = {parser.source for parser in parsers}
    missing = sorted(source.value for source in profile.parsers if source not in available)
    if missing:
        raise InternalError("The region profile lists a source type with no parser.", sources=list(missing))
    return tuple(parser for parser in parsers if parser.source in profile.parsers)
