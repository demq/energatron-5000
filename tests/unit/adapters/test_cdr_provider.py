"""`CdrProvider` (D1 §4.6; P4 spec §3.1) against a stdlib HTTP server on localhost: URLs, headers, paging, errors."""

import json
import threading
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from energatron.adapters.offers.cdr import MAX_PAGES, CdrProvider
from energatron.core.errors import NetworkError
from tests.fixtures import make_cdr as cdr

NOW = datetime(2026, 11, 1, 3, 4, 5, tzinfo=UTC)
type Route = Callable[[str], tuple[int, dict[str, str], bytes]]


class FixedClock:
    def now(self) -> datetime:
        return NOW


class Server:
    """Serves `routes[path_with_query]`, recording each request's path and headers."""

    def __init__(self) -> None:
        self.routes: dict[str, Route] = {}
        self.requests: list[tuple[str, dict[str, str]]] = []
        server = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                server.requests.append((self.path, {key.lower(): value for key, value in self.headers.items()}))
                route = server.routes.get(self.path)
                status, headers, body = route(self.path) if route else (404, {}, b'{"errors": []}')
                self.send_response(status)
                for key, value in headers.items():
                    self.send_header(key, value)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: Any) -> None:
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.root = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def json(self, path: str, body: bytes, *, status: int = 200, headers: dict[str, str] | None = None) -> None:
        sent = {"Content-Type": "application/json", "Set-Cookie": "session=secret", "ETag": '"abc"'} | (headers or {})
        self.routes[path] = lambda _: (status, sent, body)


@pytest.fixture
def server() -> Iterator[Server]:
    found = Server()
    found.thread.start()
    yield found
    found.httpd.shutdown()
    found.httpd.server_close()


def provider(server: Server) -> CdrProvider:
    return CdrProvider(clock=FixedClock(), user_agent="energatron/test", root=server.root)


LIST = "/alpha/cds-au/v1/energy/plans?effective=CURRENT&fuelType=ALL&page-size=1000"


def test_a_one_page_list_is_fetched_with_version_1_and_kept_verbatim(server: Server) -> None:
    body = cdr.list_body([cdr.plan("TEST1@EME")])
    server.json(LIST, body)
    (page,) = provider(server).list_plans("alpha")
    assert page.body == body
    assert (page.url, page.status, page.fetched_at) == (server.root + LIST, 200, NOW)
    assert page.request_headers == {"x-v": "1", "accept": "application/json", "user-agent": "energatron/test"}
    _, sent = server.requests[0]
    assert (sent["x-v"], sent["user-agent"]) == ("1", "energatron/test")
    assert "cookie" not in sent


def test_only_the_allow_listed_response_headers_are_kept(server: Server) -> None:
    server.json(LIST, cdr.list_body([]), headers={"x-v": "1", "X-Other": "dropped"})
    (page,) = provider(server).list_plans("alpha")
    assert set(page.response_headers) <= {
        "content-type",
        "date",
        "etag",
        "last-modified",
        "x-v",
        "x-fapi-interaction-id",
    }
    assert page.response_headers["etag"] == '"abc"'
    assert "set-cookie" not in page.response_headers
    assert "x-other" not in page.response_headers


def test_pages_are_followed_through_links_next(server: Server) -> None:
    second = "/alpha/cds-au/v1/energy/plans?page=2"
    server.json(LIST, cdr.list_body([cdr.plan("TEST1@EME")], next_url=server.root + second))
    server.json(second, cdr.list_body([cdr.plan("TEST2@EME")]))
    pages = provider(server).list_plans("alpha")
    assert [json.loads(page.body)["data"]["plans"][0]["planId"] for page in pages] == ["TEST1@EME", "TEST2@EME"]


def test_a_next_link_to_another_host_is_refused(server: Server) -> None:
    server.json(LIST, cdr.list_body([], next_url="https://elsewhere.test/plans?page=2"))
    with pytest.raises(NetworkError) as caught:
        provider(server).list_plans("alpha")
    assert caught.value.details["reason"] == "foreign_next"


def test_too_many_pages_is_a_network_error(server: Server) -> None:
    server.json(LIST, cdr.list_body([], next_url=server.root + LIST))  # a page that points at itself
    with pytest.raises(NetworkError) as caught:
        provider(server).list_plans("alpha")
    assert caught.value.details["reason"] == "too_many_pages"
    assert len(server.requests) == MAX_PAGES


def test_a_detail_is_fetched_with_version_3_and_the_plan_id_in_the_path(server: Server) -> None:
    body = cdr.detail_body(cdr.plan("TEST1@EME"))
    server.json("/alpha/cds-au/v1/energy/plans/TEST1@EME", body)
    fetched = provider(server).get_plan("alpha", "TEST1@EME")
    assert (fetched.body, fetched.request_headers["x-v"]) == (body, "3")


def test_unsafe_characters_in_a_plan_id_are_percent_encoded(server: Server) -> None:
    server.json("/alpha/cds-au/v1/energy/plans/A%2FB%20C", cdr.detail_body(cdr.plan("A/B C")))
    assert provider(server).get_plan("alpha", "A/B C").status == 200


@pytest.mark.parametrize("status", [400, 404, 429, 500, 503])
def test_an_http_error_status_is_a_network_error_with_the_status(server: Server, status: int) -> None:
    server.json(LIST, b"{}", status=status)
    with pytest.raises(NetworkError) as caught:
        provider(server).list_plans("alpha")
    assert (caught.value.details["status"], caught.value.details["reason"]) == (status, "http_status")
    assert caught.value.details["url"] == server.root + LIST


def test_a_body_that_is_not_json_is_a_network_error(server: Server) -> None:
    server.json("/alpha/cds-au/v1/energy/plans/X", b"<html>maintenance</html>")
    with pytest.raises(NetworkError) as caught:
        provider(server).get_plan("alpha", "X")
    assert caught.value.details["reason"] == "bad_json"


def test_an_unreachable_host_is_a_network_error_without_a_status() -> None:
    unreachable = CdrProvider(clock=FixedClock(), user_agent="energatron/test", root="http://127.0.0.1:9")
    with pytest.raises(NetworkError) as caught:
        unreachable.list_plans("alpha")
    assert (caught.value.details["status"], caught.value.details["reason"]) == (None, "unreachable")


def test_the_provider_is_named_cdr() -> None:
    assert CdrProvider(clock=FixedClock(), user_agent="x").name == "cdr"
