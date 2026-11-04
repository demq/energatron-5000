"""`CdrProvider`: the AER-hosted CDR Energy Product Reference Data API over stdlib `urllib` (D1 §4.6; P4 spec §3.1).

- List: `GET <root>/<base>/cds-au/v1/energy/plans?effective=CURRENT&fuelType=ALL&page-size=1000`, header `x-v: 1`,
  following `links.next` (on the same host only) for at most `MAX_PAGES` pages.
- Detail: `GET <root>/<base>/cds-au/v1/energy/plans/<planId>`, header `x-v: 3`.
- Requests carry `x-v`, `accept` and `user-agent` only: no cookies, no contact details. Responses keep a fixed header
  allow-list. Requests are sequential, with a 30-second timeout and no retries.
- A transport failure, a timeout, a body that isn't JSON or a status of 400 or more → `E_NETWORK` with `url`,
  `status` (or null) and `reason`. The provider never filters or interprets plans.
"""

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from typing import Final, Protocol

from energatron.core.errors import NetworkError
from energatron.core.models.offers import Fetched
from energatron.ports.clock import Clock

DEFAULT_ROOT: Final = "https://cdr.energymadeeasy.gov.au"
MAX_PAGES: Final = 50
TIMEOUT_SECONDS: Final = 30.0
RESPONSE_HEADERS: Final = ("content-type", "date", "etag", "last-modified", "x-v", "x-fapi-interaction-id")
_LIST_QUERY: Final = "effective=CURRENT&fuelType=ALL&page-size=1000"


class _Response(Protocol):
    status: int

    def read(self) -> bytes: ...
    def getheaders(self) -> list[tuple[str, str]]: ...
    def __enter__(self) -> _Response: ...
    def __exit__(self, *args: object) -> None: ...


type Opener = Callable[[urllib.request.Request, float], _Response]


def _urlopen(request: urllib.request.Request, timeout: float) -> _Response:
    return urllib.request.urlopen(request, timeout=timeout)


class CdrProvider:
    """The `OfferProvider` for CDR (D1 §4.6). `root` may point at a local test server."""

    name: str = "cdr"

    def __init__(self, *, clock: Clock, user_agent: str, root: str = DEFAULT_ROOT, opener: Opener = _urlopen) -> None:
        self._clock = clock
        self._user_agent = user_agent
        self._root = root.rstrip("/")
        self._opener = opener

    @property
    def root(self) -> str:
        """The API root requests go to."""
        return self._root

    def list_plans(self, brand: str) -> list[Fetched]:
        """Every list page of base URI `brand`, following `links.next`."""
        url: str | None = f"{self._root}/{_segment(brand)}/cds-au/v1/energy/plans?{_LIST_QUERY}"
        pages: list[Fetched] = []
        while url is not None:
            if len(pages) == MAX_PAGES:
                raise NetworkError("The plan list has too many pages.", url=url, status=None, reason="too_many_pages")
            page = self._get(url, version="1")
            pages.append(page)
            url = self._next(page)
        return pages

    def get_plan(self, brand: str, plan_id: str) -> Fetched:
        """The detail of one plan."""
        return self._get(f"{self._root}/{_segment(brand)}/cds-au/v1/energy/plans/{_segment(plan_id)}", version="3")

    def _get(self, url: str, *, version: str) -> Fetched:
        headers = {"x-v": version, "accept": "application/json", "user-agent": self._user_agent}
        request = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with self._opener(request, TIMEOUT_SECONDS) as response:
                body = response.read()
                status = response.status
                received = response.getheaders()
        except urllib.error.HTTPError as error:
            raise NetworkError(
                f"The CDR API answered HTTP {error.code}.", url=url, status=error.code, reason="http_status"
            ) from None
        except urllib.error.URLError, TimeoutError, OSError:
            raise NetworkError("The CDR API couldn't be reached.", url=url, status=None, reason="unreachable") from None
        if status >= 400:
            raise NetworkError(f"The CDR API answered HTTP {status}.", url=url, status=status, reason="http_status")
        try:
            json.loads(body)
        except ValueError, RecursionError:
            raise NetworkError(
                "The CDR API answered with a body that isn't JSON.", url=url, status=status, reason="bad_json"
            ) from None
        return Fetched(
            url=url,
            request_headers=headers,
            status=status,
            fetched_at=self._clock.now(),
            response_headers=_kept(received),
            body=body,
        )

    def _next(self, page: Fetched) -> str | None:
        document = json.loads(page.body)
        links = document.get("links") if isinstance(document, dict) else None
        following = links.get("next") if isinstance(links, dict) else None
        if not isinstance(following, str) or not following:
            return None
        if urllib.parse.urlsplit(following).netloc != urllib.parse.urlsplit(self._root).netloc:
            raise NetworkError(
                "The plan list points to another host.", url=page.url, status=None, reason="foreign_next"
            )
        return following


def _segment(value: str) -> str:
    """A path segment, percent-encoded; `@` is kept, as plan IDs use it."""
    return urllib.parse.quote(value, safe="@")


def _kept(headers: list[tuple[str, str]] | Mapping[str, str]) -> dict[str, str]:
    pairs = headers.items() if isinstance(headers, Mapping) else headers
    kept: dict[str, str] = {}
    for name, value in pairs:
        lowered = name.lower()
        if lowered in RESPONSE_HEADERS:
            kept[lowered] = value
    return dict(sorted(kept.items()))
