"""Running the web app for `energatron serve` (P10 spec §7): a loopback socket, a per-launch token, uvicorn on it.

The socket is bound and listening before anything is printed, so a busy port is an ordinary `E_USAGE` and the printed
address works at once. uvicorn runs with no logging config of its own and no access log (a URL can hold the token); its
loggers write through the same redacting stderr handler as the `energatron` loggers. It handles SIGINT and SIGTERM
itself and returns, so `serve` exits 0.
"""

import errno
import logging
import secrets
import signal
import socket
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from types import FrameType
from typing import ClassVar, Final, Literal

import uvicorn

from energatron.adapters.http.app import create_app
from energatron.adapters.http.security import LOOPBACK, Access
from energatron.core.errors import UsageError
from energatron.core.models.workspace import WorkspacePaths
from energatron.services.context import ServiceResult

DEFAULT_PORT: Final = 8765
_BIND: Final = {"127.0.0.1": ("127.0.0.1", socket.AF_INET), "localhost": ("127.0.0.1", socket.AF_INET),
                "::1": ("::1", socket.AF_INET6)}  # fmt: skip
_URL_HOST: Final = {"127.0.0.1": "127.0.0.1", "localhost": "localhost", "::1": "[::1]"}


class ServeResult(ServiceResult):
    """`serve_result` v1 (P10 spec §7): where the UI listens, and the address to open (with this launch's token)."""

    KIND: ClassVar[str] = "serve_result"
    SCHEMA_VERSION: ClassVar[int] = 1
    kind: Literal["serve_result"] = "serve_result"
    schema_version: Literal[1] = 1

    host: str
    port: int
    url: str
    allow_documents: bool


def bind(host: str, port: int) -> socket.socket:
    """A listening socket on a loopback address. Any other host, a port out of range or a busy port → `E_USAGE`."""
    if host not in LOOPBACK:
        raise UsageError("--host is 127.0.0.1, localhost or ::1: serve never listens beyond this machine.",
                         parameter="--host")  # fmt: skip
    if not 0 <= port <= 65535:
        raise UsageError("--port is between 0 and 65535.", parameter="--port")
    address, family = _BIND[host]
    sock = socket.socket(family, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((address, port))
        sock.listen(128)
    except OSError as exc:
        sock.close()
        if exc.errno == errno.EADDRINUSE:
            raise UsageError("The port is in use; pick another with --port (0 picks a free one).",
                             parameter="--port", reason="port_in_use") from None  # fmt: skip
        raise UsageError("serve couldn't listen on that address.", parameter="--host", reason="bind_failed") from None
    return sock


def prepare(host: str, sock: socket.socket, *, allow_documents: bool) -> tuple[Access, ServeResult]:
    """This launch's token and the result `serve` prints."""
    port = sock.getsockname()[1]
    access = Access(host=host, port=port, token=secrets.token_urlsafe(32))
    url = f"http://{_URL_HOST[host]}:{port}/?token={access.token}"
    return access, ServeResult(host=host, port=port, url=url, allow_documents=allow_documents)


class _Server(uvicorn.Server):
    """uvicorn's server, except that SIGINT or SIGTERM ends `serve` with exit 0: uvicorn re-raises a signal it caught
    once it has shut down, which would turn Ctrl-C into exit 130 and SIGTERM into a kill."""

    def handle_exit(self, sig: int, frame: FrameType | None) -> None:
        super().handle_exit(sig, frame)
        self._captured_signals.clear()


@contextmanager
def stop_signals() -> Iterator[threading.Event]:
    """From the moment the socket listens until uvicorn takes over its own handlers, SIGINT and SIGTERM only ask to
    stop (the event), so a signal right after the address is printed still ends `serve` with exit 0."""
    stop = threading.Event()
    previous = {sig: signal.signal(sig, lambda *_: stop.set()) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        yield stop
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def run(
    paths: WorkspacePaths, sock: socket.socket, access: Access, *, allow_documents: bool, verbose: bool,
    stop: threading.Event | None = None,
) -> None:  # fmt: skip
    """Serve the app on `sock` until SIGINT or SIGTERM; at once if `stop` is already set."""
    app = create_app(paths, access=access, allow_documents=allow_documents)
    ours = logging.getLogger("energatron")
    for name in ("uvicorn", "uvicorn.error"):
        logger = logging.getLogger(name)
        logger.handlers = list(ours.handlers)
        logger.propagate = False
        logger.setLevel(logging.INFO if verbose else logging.WARNING)
    config = uvicorn.Config(app, log_config=None, access_log=False, server_header=False, date_header=False)
    with sock:
        if stop is not None and stop.is_set():
            return
        _Server(config).run(sockets=[sock])
