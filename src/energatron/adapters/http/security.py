"""Access checks and response headers for `energatron serve` (P10 spec §5.4).

Every request passes, in order: the `Host` header (DNS rebinding), the method, the per-launch token (other accounts on
the machine), and for a `POST` the origin (cross-site writes). Fixed bodies never echo the request. Every response gets
the same security headers, with a sandboxing Content-Security-Policy for the generated reports.
"""

import hmac
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Final
from urllib.parse import urlencode

from starlette.requests import Request
from starlette.responses import PlainTextResponse, RedirectResponse, Response

LOOPBACK: Final = ("127.0.0.1", "localhost", "::1")
"""The only `--host` values `serve` accepts (P10 spec §7)."""

REPORTS_PREFIX: Final = "/reports/"
POST_PATHS: Final = frozenset({"/api/v1/gas-bill/check", "/gas-bill"})
"""The only routes that take a `POST`: the gas bill record check, which writes nothing (P10 spec §5.2, §6.1)."""

APP_POLICY: Final = (
    "default-src 'none'; style-src 'self'; img-src 'self' data:; form-action 'self'; frame-ancestors 'none'; "
    "base-uri 'none'"
)
REPORT_POLICY: Final = (
    "default-src 'none'; style-src 'unsafe-inline'; img-src data:; frame-ancestors 'none'; base-uri 'none'; "
    "form-action 'none'; sandbox"
)
HEADERS: Final = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Cross-Origin-Opener-Policy": "same-origin",
}

BAD_HOST: Final = "Unexpected Host header.\n"
FORBIDDEN: Final = "Cross-site requests are refused.\n"
NOT_ALLOWED: Final = "Method not allowed.\n"
UNAUTHORISED: Final = (
    "Not signed in. Open the address `energatron serve` printed (it holds this session's token), or send\n"
    "`Authorization: Bearer <token>`.\n"
)


@dataclass(frozen=True)
class Access:
    """What the checks need: the bound address and port, and this launch's token."""

    host: str
    port: int
    token: str

    @property
    def cookie(self) -> str:
        """The session cookie's name. Cookies are shared across ports, so it names the port (P10 spec §5.4)."""
        return f"energatron_session_{self.port}"

    def hosts(self) -> frozenset[str]:
        """The `Host` header values a request may carry."""
        names = ("[::1]", "localhost") if self.host == "::1" else ("127.0.0.1", "localhost")
        return frozenset(f"{name}:{self.port}" for name in names)

    def origin_ok(self, request: Request) -> bool:
        origin = request.headers.get("origin")
        site = request.headers.get("sec-fetch-site")
        own = f"http://{request.headers.get('host', '')}"
        return (origin is None or origin == own) and site in (None, "same-origin", "none")

    def matches(self, candidate: str | None) -> bool:
        return candidate is not None and hmac.compare_digest(candidate.encode(), self.token.encode())

    def signed_in(self, request: Request) -> bool:
        if self.matches(request.cookies.get(self.cookie)):
            return True
        scheme, _, value = request.headers.get("authorization", "").partition(" ")
        return scheme.lower() == "bearer" and self.matches(value.strip())


def secure(response: Response, path: str) -> Response:
    """Add the security headers every response carries (P10 spec §5.4)."""
    for name, value in HEADERS.items():
        response.headers[name] = value
    policy = REPORT_POLICY if path.startswith(REPORTS_PREFIX) else APP_POLICY
    response.headers["Content-Security-Policy"] = policy
    return response


Next = Callable[[Request], Awaitable[Response]]


def middleware(access: Access) -> Callable[[Request, Next], Awaitable[Response]]:
    """The checks, as an HTTP middleware: `app.middleware("http")(middleware(access))`."""

    async def check(request: Request, call_next: Next) -> Response:
        path = request.url.path
        if request.headers.get("host") not in access.hosts():
            return secure(PlainTextResponse(BAD_HOST, status_code=400), path)
        allowed = ("GET", "HEAD", "POST") if path in POST_PATHS else ("GET", "HEAD")
        if request.method not in allowed:
            return secure(PlainTextResponse(NOT_ALLOWED, status_code=405, headers={"Allow": ", ".join(allowed)}), path)
        token = request.query_params.get("token")
        if token is not None and request.method == "GET" and access.matches(token):
            rest = [(key, value) for key, value in request.query_params.multi_items() if key != "token"]
            target = path + (f"?{urlencode(rest)}" if rest else "")
            redirect = RedirectResponse(target, status_code=303)
            redirect.set_cookie(access.cookie, access.token, httponly=True, samesite="strict", path="/")
            return secure(redirect, path)
        if not access.signed_in(request):
            return secure(PlainTextResponse(UNAUTHORISED, status_code=401), path)
        if request.method == "POST" and not access.origin_ok(request):
            return secure(PlainTextResponse(FORBIDDEN, status_code=403), path)
        if request.method == "HEAD":
            request.scope["method"] = "GET"  # answered as GET; the server sends no body for HEAD
        return secure(await call_next(request), path)

    return check
