"""A `TestClient` over the web app for a scratch workspace (P10 spec §4, §5.4)."""

from fastapi.testclient import TestClient

from energatron.adapters.http.app import create_app
from energatron.adapters.http.security import Access
from energatron.core.models.workspace import WorkspacePaths

TOKEN = "test-token-0123456789abcdefghijklmnopqrstuv"
PORT = 8765
BASE = f"http://127.0.0.1:{PORT}"
BEARER = {"Authorization": f"Bearer {TOKEN}"}


def client(paths: WorkspacePaths, *, allow_documents: bool = False, signed_in: bool = True) -> TestClient:
    """A client for the app; signed in with the bearer token unless told otherwise. Redirects are not followed."""
    access = Access(host="127.0.0.1", port=PORT, token=TOKEN)
    app = create_app(paths, access=access, allow_documents=allow_documents)
    return TestClient(app, base_url=BASE, headers=BEARER if signed_in else {}, follow_redirects=False)
