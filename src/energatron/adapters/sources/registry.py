"""Every source parser, one per source type, and the region profile's filter (D1 §4.3; D3 §5, §10.3)."""

from typing import Final

from energatron.adapters.sources.bill_pdf import ELEC_BILL, GAS_BILL
from energatron.adapters.sources.cdr_index import OFFER_CDR_INDEX
from energatron.adapters.sources.cdr_plan import OFFER_CDR
from energatron.adapters.sources.offer_manual import OFFER_MANUAL
from energatron.adapters.sources.retailer_txn import RETAILER_TXN
from energatron.adapters.sources.retailer_usage import RETAILER_USAGE
from energatron.adapters.sources.sapn_nem12 import SAPN_INTERVAL
from energatron.adapters.sources.sapn_summary import SAPN_SUMMARY
from energatron.core.errors import InternalError
from energatron.core.models.region import RegionProfile
from energatron.ports.sources import SourceParser

PARSERS: Final[tuple[SourceParser, ...]] = (
    SAPN_INTERVAL,
    SAPN_SUMMARY,
    RETAILER_USAGE,
    RETAILER_TXN,
    ELEC_BILL,
    GAS_BILL,
    OFFER_CDR,
    OFFER_CDR_INDEX,
    OFFER_MANUAL,
)
"""The registered parsers: P1's seven (D3 §5) and P4's two (P4 spec §3.3, §5.3). `gas_bill_record` has none yet."""


def parsers_for(profile: RegionProfile, parsers: tuple[SourceParser, ...] = PARSERS) -> tuple[SourceParser, ...]:
    """The `parsers` whose `source` the profile lists, in registry order; a listed source with none → `E_INTERNAL`."""
    available = {parser.source for parser in parsers}
    missing = sorted(source.value for source in profile.parsers if source not in available)
    if missing:
        raise InternalError("The region profile lists a source type with no parser.", sources=list(missing))
    return tuple(parser for parser in parsers if parser.source in profile.parsers)
