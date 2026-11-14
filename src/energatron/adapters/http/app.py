"""The web app for one workspace (P10 spec §4): the access checks, the JSON API, and (P10.2.3) the pages.

Routes call the shared read paths in process (`adapters/reads.py`, D1 §3), one at a time, opening the workspace again
for every request so a changed private config or denylist applies at once. Nothing writes under the workspace and no
route takes the workspace lock. Every answer is the CLI's own envelope, redacted the same way.
"""

from fastapi import FastAPI

from energatron.adapters.http import api, security
from energatron.adapters.http.reader import Reader
from energatron.core.models.workspace import WorkspacePaths


def create_app(paths: WorkspacePaths, *, access: security.Access, allow_documents: bool = False) -> FastAPI:
    """The app for one workspace. Generated docs are off (they load scripts from a CDN); the OpenAPI document is at
    `/api/openapi.json`, behind the access checks."""
    app = FastAPI(
        title="energatron",
        docs_url=None,
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    reader = Reader(paths, allow_documents=allow_documents)
    app.include_router(api.router(reader))
    app.middleware("http")(security.middleware(access))
    return app
