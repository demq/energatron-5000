"""What the `OfferProvider` port returns (D1 §4.6). Created in P0 because the port names it; used from P4."""

from energatron.core.models._common import FrozenModel, UtcDatetime


class Fetched(FrozenModel):
    """One HTTP response, kept verbatim. The P4 spec fixes the response-header allow-list."""

    url: str
    request_headers: dict[str, str]  # includes x-v
    status: int
    fetched_at: UtcDatetime
    response_headers: dict[str, str]  # a fixed allow-list; never cookies
    body: bytes  # verbatim
