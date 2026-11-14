"""The access checks and headers (P10 spec §5.4): Host, token, cross-site writes, methods, and fixed bodies."""

from pathlib import Path

import pytest

from energatron.adapters.http.security import APP_POLICY, HEADERS, Access
from tests.unit.adapters._cli import FakeVcsState
from tests.unit.adapters.http._client import PORT, TOKEN, client
from tests.unit.services._read_workspace import make_read_workspace

PROBE = "probe-7f3a"
ROUTE = "/api/v1/version"


@pytest.fixture
def paths(tmp_path: Path, fake_vcs: FakeVcsState) -> object:
    return make_read_workspace(tmp_path)[0]


@pytest.mark.parametrize("host", ["evil.example:8765", f"127.0.0.1:{PORT + 1}", "localhost", f"{PROBE}:8765"])
def test_a_foreign_host_is_refused(paths: object, host: str) -> None:
    response = client(paths).get(ROUTE, headers={"Host": host})  # type: ignore[arg-type]
    assert response.status_code == 400 and PROBE not in response.text


def test_localhost_with_the_port_is_accepted(paths: object) -> None:
    assert client(paths).get(ROUTE, headers={"Host": f"localhost:{PORT}"}).status_code == 200  # type: ignore[arg-type]


def test_the_ipv6_server_accepts_only_its_own_hosts() -> None:
    access = Access(host="::1", port=PORT, token=TOKEN)
    assert access.hosts() == {f"[::1]:{PORT}", f"localhost:{PORT}"}


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer wrong"}, {"Authorization": f"Basic {TOKEN}"}])
def test_without_the_token_it_is_401(paths: object, headers: dict[str, str]) -> None:
    api = client(paths, signed_in=False)  # type: ignore[arg-type]
    response = api.get(f"{ROUTE}?{PROBE}=1", headers=headers)
    assert response.status_code == 401 and PROBE not in response.text
    assert api.get(f"{ROUTE}?token=wrong").status_code == 401
    api.cookies.set(f"energatron_session_{PORT}", "wrong")
    assert api.get(ROUTE).status_code == 401


def test_the_token_in_the_url_sets_the_cookie_and_is_dropped(paths: object) -> None:
    api = client(paths, signed_in=False)  # type: ignore[arg-type]
    response = api.get(f"{ROUTE}?a=1&token={TOKEN}&b=2")
    assert response.status_code == 303
    assert response.headers["location"] == f"{ROUTE}?a=1&b=2"
    cookie = response.headers["set-cookie"]
    assert cookie.startswith(f"energatron_session_{PORT}={TOKEN};")
    lowered = cookie.lower()
    assert "httponly" in lowered and "samesite=strict" in lowered and "path=/" in lowered
    assert "expires" not in lowered and "max-age" not in lowered and "secure" not in lowered
    assert api.get(ROUTE).status_code == 200  # the client kept the cookie


@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": "http://evil.example"},
        {"Origin": f"http://localhost:{PORT}"},  # the request's Host is 127.0.0.1, so this is another origin
        {"Sec-Fetch-Site": "cross-site"},
        {"Sec-Fetch-Site": "same-site"},
    ],
)
def test_a_cross_site_post_is_refused(paths: object, headers: dict[str, str]) -> None:
    response = client(paths).post("/api/v1/gas-bill/check", content=b"{}", headers=headers)  # type: ignore[arg-type]
    assert response.status_code == 403


def test_a_same_origin_post_reaches_the_route(paths: object) -> None:
    headers = {"Origin": f"http://127.0.0.1:{PORT}", "Sec-Fetch-Site": "same-origin"}
    response = client(paths).post("/api/v1/gas-bill/check", content=b"{}", headers=headers)  # type: ignore[arg-type]
    assert response.status_code not in (401, 403, 405)


@pytest.mark.parametrize(("method", "route"), [("PUT", ROUTE), ("DELETE", ROUTE), ("POST", ROUTE), ("PATCH", "/")])
def test_other_methods_are_405(paths: object, method: str, route: str) -> None:
    response = client(paths).request(method, route)  # type: ignore[arg-type]
    assert response.status_code == 405


@pytest.mark.parametrize("signed_in", [True, False])
def test_every_response_carries_the_headers_and_no_cors(paths: object, signed_in: bool) -> None:
    api = client(paths, signed_in=signed_in)  # type: ignore[arg-type]
    for response in (api.get(ROUTE), api.get("/nowhere"), api.get(ROUTE, headers={"Host": "evil.example"})):
        for name, value in HEADERS.items():
            assert response.headers[name] == value
        assert response.headers["content-security-policy"] == APP_POLICY
        assert not [name for name in response.headers if name.lower().startswith("access-control-")]


def test_an_unknown_path_is_a_fixed_404(paths: object) -> None:
    response = client(paths).get(f"/{PROBE}")  # type: ignore[arg-type]
    assert response.status_code == 404 and PROBE not in response.text
