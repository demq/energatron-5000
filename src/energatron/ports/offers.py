"""The `OfferProvider` port: fetch public retail offers (D1 §4.6). Used from P4; the signatures are fixed now."""

from typing import Protocol

from energatron.core.models.offers import Fetched


class OfferProvider(Protocol):
    """Providers never filter or interpret anything. Network failures and HTTP 400+ raise `E_NETWORK`."""

    name: str  # "cdr"

    def list_plans(self, brand: str) -> list[Fetched]:
        """One `Fetched` per page."""
        ...

    def get_plan(self, brand: str, plan_id: str) -> Fetched: ...
