"""`FrozenProvider`: an `OfferProvider` over fixed, in-memory CDR bodies (P8 spec §5.3). It never touches the network.

The demo hands it a plan-list page and the plan details it generated; `fetch-offers` then lands them exactly as it lands
a real fetch. Its `name` is `cdr`, so the fetch log reads as a normal fetch; URLs use the `demo://` scheme.
"""

from collections.abc import Mapping

from energatron.core.errors import NetworkError
from energatron.core.models.offers import Fetched
from energatron.ports.clock import Clock


class FrozenProvider:
    name: str = "cdr"

    def __init__(self, *, clock: Clock, lists: Mapping[str, bytes], details: Mapping[tuple[str, str], bytes]) -> None:
        """`lists[base]` is a base's one list page; `details[(base, plan_id)]` a plan's detail body."""
        self._clock = clock
        self._lists = dict(lists)
        self._details = dict(details)

    def _fetched(self, url: str, version: str, body: bytes) -> Fetched:
        return Fetched(
            url=url,
            request_headers={"x-v": version, "accept": "application/json"},
            status=200,
            fetched_at=self._clock.now(),
            response_headers={"content-type": "application/json", "x-v": version},
            body=body,
        )

    def list_plans(self, brand: str) -> list[Fetched]:
        url = f"demo://{brand}/plans"
        body = self._lists.get(brand)
        if body is None:
            raise NetworkError("The frozen offer set has no such base.", url=url, status=404, reason="http_status")
        return [self._fetched(url, "1", body)]

    def get_plan(self, brand: str, plan_id: str) -> Fetched:
        url = f"demo://{brand}/plans/{plan_id}"
        body = self._details.get((brand, plan_id))
        if body is None:
            raise NetworkError("The frozen offer set has no such plan.", url=url, status=404, reason="http_status")
        return self._fetched(url, "3", body)
