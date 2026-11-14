"""The JSON API (P10 spec §5): one route per CLI read path, answering exactly the envelope the CLI prints with `--json`.

`GET /api/v1/<name>` takes the read path's arguments as query parameters; `POST /api/v1/gas-bill/check` takes a gas
bill record as its body; `GET /api/v1/gas-bill/text/{id}` exists only with `--allow-documents`; `GET /api/v1` lists
the routes.
"""

from collections.abc import Callable
from typing import Final

from fastapi import APIRouter, Request, Response
from starlette.concurrency import run_in_threadpool

from energatron.adapters import envelope, reads
from energatron.adapters.http.reader import Answer, Reader, failed
from energatron.core.errors import UsageError

PREFIX: Final = "/api/v1"
MAX_BODY: Final = 1024 * 1024
"""The largest gas bill record a `POST` may carry (P10 spec §5.2)."""

QUERY_PATHS: Final = tuple(path for path in reads.READS if path not in (reads.GAS_BILL_CHECK, reads.GAS_BILL_TEXT))
"""The read paths answered by `GET /api/v1/<name>`."""


def respond(answer: Answer) -> Response:
    return Response(envelope.dumps(answer.document), status_code=answer.status, media_type="application/json")


def query_values(request: Request) -> dict[str, str]:
    """The query as a mapping: a repeated parameter is `E_USAGE`, named but not echoed."""
    values: dict[str, str] = {}
    for key, value in request.query_params.multi_items():
        if key in values:
            raise UsageError(f"Invalid value for {key}: given more than once.", parameter=key)
        values[key] = value
    return values


def read_from_query(reader: Reader, path: reads.ReadPath, request: Request) -> Answer:
    """Run a read path with the request's query parameters as its arguments."""
    try:
        values = query_values(request)
    except UsageError as exc:
        return failed(path.command, exc)
    return reader.answer(path, values)


def _query_route(reader: Reader, path: reads.ReadPath) -> Callable[[Request], Response]:
    def route(request: Request) -> Response:
        return respond(read_from_query(reader, path, request))

    route.__name__ = f"read_{path.name.replace('/', '_').replace('-', '_')}"
    route.__doc__ = f"`energatron {' '.join(path.argv)} --json`, with its arguments as query parameters."
    return route


async def read_body(request: Request) -> bytes:
    """The request body, up to `MAX_BODY`: beyond it `E_USAGE` (`reason: "too_large"`), without reading the rest."""
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > MAX_BODY:
        raise UsageError("The record is larger than 1 MiB.", reason="too_large")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_BODY:
            raise UsageError("The record is larger than 1 MiB.", reason="too_large")
    return bytes(body)


async def check_record(reader: Reader, request: Request) -> Answer:
    """`gas-bill check -` with the record's bytes: read-only (P10 spec §5.2)."""
    try:
        record = await read_body(request)
    except UsageError as exc:
        return failed(reads.GAS_BILL_CHECK.command, exc)
    return await run_in_threadpool(reader.answer, reads.GAS_BILL_CHECK, reads.GasBillCheckArgs(record=record))


def router(reader: Reader) -> APIRouter:
    """The API's routes for one reader."""
    api = APIRouter(prefix=PREFIX)

    @api.get("", summary="The API's routes")
    def index() -> Response:
        routes = [
            {
                "name": path.name,
                "method": "POST" if path is reads.GAS_BILL_CHECK else "GET",
                "route": f"{PREFIX}/{path.name}" + ("/{pdf_dataset_id}" if path is reads.GAS_BILL_TEXT else ""),
                "command": " ".join(path.argv),
                "parameters": []
                if path in (reads.GAS_BILL_CHECK, reads.GAS_BILL_TEXT)
                else list(path.args.model_fields),
            }
            for path in reads.READS
            if reader.allow_documents or not path.document
        ]
        return Response(envelope.dumps({"routes": routes}), media_type="application/json")

    for path in QUERY_PATHS:
        api.add_api_route(f"/{path.name}", _query_route(reader, path), methods=["GET"], summary=path.name)

    @api.post("/gas-bill/check", summary="gas-bill/check")
    async def gas_bill_check(request: Request) -> Response:
        return respond(await check_record(reader, request))

    if reader.allow_documents:

        @api.get("/gas-bill/text/{pdf_dataset_id}", summary="gas-bill/text")
        def gas_bill_text(pdf_dataset_id: str) -> Response:
            args = reads.GasBillTextArgs(pdf_dataset_id=pdf_dataset_id)
            return respond(reader.answer(reads.GAS_BILL_TEXT, args))

    return api
