"""The page routes (P10 spec §6.1): each runs its read paths through the `Reader` and hands the redacted documents to
`pages.py`, which only formats them. A failed read shows in its own section; the page's status is that of the first
failure, and the other sections still render.
"""

from collections.abc import Callable, Mapping, Sequence
from typing import Any
from urllib.parse import parse_qs, quote

from fastapi import APIRouter, Request, Response
from fastapi.responses import HTMLResponse
from starlette.concurrency import run_in_threadpool

from energatron.adapters import reads
from energatron.adapters.http import pages
from energatron.adapters.http.api import PREFIX, query_values, read_body
from energatron.adapters.http.pages import Field, Page, Part, Section
from energatron.adapters.http.reader import Answer, Reader, failed
from energatron.adapters.http.style import STYLE
from energatron.core.errors import UsageError


def _api(path: reads.ReadPath, query: Mapping[str, object] | None = None) -> str:
    return pages.href(f"{PREFIX}/{path.name}", query)


def _status(answers: Sequence[Answer]) -> int:
    return next((answer.status for answer in answers if answer.status != 200), 200)


def _version(reader: Reader) -> str | None:
    data = reader.answer(reads.VERSION, reads.NoArgs()).document.get("data") or {}
    return data.get("energatron_version") if isinstance(data, Mapping) else None


def _split(values: Mapping[str, str], *paths: reads.ReadPath) -> list[dict[str, str]]:
    """The query, split between the read paths whose arguments name each key: a key none takes is `E_USAGE`."""
    unknown = [key for key in values if not any(key in path.args.model_fields for path in paths)]
    if unknown:
        raise UsageError(f"Invalid value for {unknown[0]}.", parameter=unknown[0])
    return [{key: value for key, value in values.items() if key in path.args.model_fields} for path in paths]


def _html(page: Page, status: int) -> HTMLResponse:
    return HTMLResponse(pages.page_html(page), status_code=status)


def _bad_query(reader: Reader, title: str, path: str, exc: UsageError) -> HTMLResponse:
    answer = failed(None, exc)
    section = Section("Query", answer.document, pages.href(path))
    return _html(Page(title, path, (section,), version=_version(reader)), answer.status)


PageBuilder = Callable[[Reader, dict[str, str]], tuple[Page, int]]


def _overview(reader: Reader, values: dict[str, str]) -> tuple[Page, int]:
    _split(values, reads.STATUS)
    status = reader.answer(reads.STATUS, reads.NoArgs())
    parts = (
        Part("Pipeline", single="pipeline"),
        Part("Last run", single="last_run"),
        Part("Recommendation", single="recommendation"),
        Part("Offers", single="offers"),
        Part("Inbox", single="landing"),
        Part("Decisions", single="decisions"),
        Part("Backup", single="backup"),
        Part("Restore drill", single="drill"),
        Part("Runs and datasets", keys=("today", "last_clean_run_id", "datasets_not_in_last_clean_run")),
    )
    section = Section("Where things stand", status.document, _api(reads.STATUS), parts, "Reminders show as warnings.")
    return Page("Overview", "/", (section,)), _status([status])


def _recommendation(reader: Reader, values: dict[str, str]) -> tuple[Page, int]:
    explain_query, compare_query = _split(values, reads.EXPLAIN, reads.COMPARE)
    explain = reader.answer(reads.EXPLAIN, explain_query)
    compare = reader.answer(reads.COMPARE, compare_query)
    form = pages.form_html(
        "/recommendation",
        (
            Field("options", "Options", values.get("options")),
            Field("fuel", "Fuel", values.get("fuel"), ("electricity", "gas")),
            Field("brand", "Brand contains", values.get("brand")),
            Field("limit", "Plans per fuel", values.get("limit")),
        ),
    )
    sections = (
        Section(
            "What to do",
            explain.document,
            _api(reads.EXPLAIN, explain_query),
            (
                Part("", keys=("recommendation_id", "as_of", "recheck_by", "action", "robust")),
                Part("Options", single="options"),
                Part("What to check before switching", single="chosen"),
                Part("Sensitivity", single="sensitivity"),
                Part("Current plans", single="current"),
                Part("Usage behind it", single="usage"),
                Part("Run and inputs", keys=("run", "inputs", "behind", "checks")),
                Part("Outcomes of past decisions", single="outcomes"),
                Part("Where the figures come from", single="trace"),
                Part("Missing", single="missing"),
            ),
        ),
        Section(
            "Plans ranked",
            compare.document,
            _api(reads.COMPARE, compare_query),
            (Part("", keys=("recommendation_id", "as_of", "action")), Part("Plans per fuel", single="plans")),
        ),
    )
    return Page("Recommendation", "/recommendation", sections, before=form), _status([explain, compare])


def _single(
    path: reads.ReadPath, title: str, route: str, fields: Callable[[dict[str, str]], Sequence[Field]] | None = None,
    parts: tuple[Part, ...] = (Part(""),),
) -> PageBuilder:  # fmt: skip
    def build(reader: Reader, values: dict[str, str]) -> tuple[Page, int]:
        (query,) = _split(values, path)
        answer = reader.answer(path, query)
        before = pages.form_html(route, fields(values)) if fields else ""
        section = Section(title, answer.document, _api(path, query), parts)
        return Page(title, route, (section,), before=before), answer.status

    return build


def _health(reader: Reader, values: dict[str, str]) -> tuple[Page, int]:
    _split(values)
    paths = (
        ("Backup", reads.BACKUP_STATUS),
        ("Code fingerprint", reads.FINGERPRINT_CHECK),
        ("Pipeline files", reads.PIPELINE_CHECK),
        ("Weekly schedule", reads.SCHEDULE_STATUS),
        ("Diagnosis", reads.DIAGNOSE),
    )
    answers = [reader.answer(path, reads.NoArgs()) for _, path in paths]
    sections = tuple(
        Section(title, answer.document, _api(path)) for (title, path), answer in zip(paths, answers, strict=True)
    )
    return Page("Health", "/health", sections), _status(answers)


PAGES: dict[str, PageBuilder] = {
    "/": _overview,
    "/recommendation": _recommendation,
    "/usage": _single(
        reads.USAGE, "Usage", "/usage", lambda values: (Field("months", "Months", values.get("months")),),
        (Part("Electricity", single="electricity"), Part("Gas", single="gas"), Part("Missing", single="missing")),
    ),
    "/datasets": _single(
        reads.DATASETS, "Datasets", "/datasets",
        lambda values: (Field("source", "Source", values.get("source")), Field("limit", "Show", values.get("limit"))),
        (
            Part("", keys=("total", "shown")), Part("Per source", single="counts"),
            Part("Newest first", single="datasets"),
        ),
    ),
    "/offers": _single(
        reads.OFFERS_DIFF, "Offer changes", "/offers", lambda values: (Field("plan", "Plan ID", values.get("plan")),),
    ),
    "/inbox": _single(reads.LANDING, "Inbox", "/inbox"),
    "/health": _health,
}  # fmt: skip
"""The read pages, by path (P10 spec §6.1)."""


def _page_route(reader: Reader, route: str, build: PageBuilder) -> Callable[[Request], Response]:
    def view(request: Request) -> Response:
        title = next((label for path, label in pages.NAV if path == route), route)
        try:
            values = query_values(request)
            page, status = build(reader, values)
        except UsageError as exc:
            return _bad_query(reader, title, route, exc)
        page = Page(page.title, page.path, page.sections, page.before, _version(reader))
        return _html(page, status)

    view.__name__ = "page_" + (route.strip("/").replace("-", "_") or "overview")
    return view


def router(reader: Reader) -> APIRouter:
    """The page routes for one reader."""
    web = APIRouter(include_in_schema=False)

    for route, build in PAGES.items():
        web.add_api_route(route, _page_route(reader, route, build), methods=["GET"])

    @web.get("/style.css")
    def style() -> Response:
        return Response(STYLE, media_type="text/css")

    @web.get("/gas-bill")
    def gas_bill_form() -> Response:
        page = Page("Gas bill check", "/gas-bill", before=pages.gas_bill_form_html(), version=_version(reader))
        return _html(page, 200)

    @web.post("/gas-bill")
    async def gas_bill_check(request: Request) -> Response:
        try:
            body = await read_body(request)
            fields = parse_qs(body.decode("utf-8", errors="replace"), keep_blank_values=True)
            record = (fields.get("record") or [""])[0]
        except UsageError as exc:
            answer: Answer = failed(reads.GAS_BILL_CHECK.command, exc)
            record = ""
        else:
            args = reads.GasBillCheckArgs(record=record.encode("utf-8"))
            answer = await run_in_threadpool(reader.answer, reads.GAS_BILL_CHECK, args)
        section = Section("Result", answer.document, pages.href(f"{PREFIX}/{reads.GAS_BILL_CHECK.name}"))
        version = await run_in_threadpool(_version, reader)
        page = Page("Gas bill check", "/gas-bill", (section,), pages.gas_bill_form_html(record), version)
        return _html(page, answer.status)

    if reader.allow_documents:

        @web.get("/gas-bill/text/{pdf_dataset_id}")
        def gas_bill_text(pdf_dataset_id: str) -> Response:
            path = reads.GAS_BILL_TEXT
            answer = reader.answer(path, reads.GasBillTextArgs(pdf_dataset_id=pdf_dataset_id))
            api = pages.href(f"{PREFIX}/{path.name}/{quote(pdf_dataset_id, safe='')}")
            data: Any = answer.document.get("data") or {}
            section = Section("Bill text", answer.document, api, (Part("", keys=("pdf_dataset_id", "pages")),))
            before = pages.preformatted_html(data.get("text", "")) if isinstance(data, Mapping) else ""
            page = Page("Gas bill text", "", (section,), version=_version(reader), extra=[before])
            return _html(page, answer.status)

    @web.get("/reports/{name}")
    def report(name: str) -> Response:
        title = f"{name.capitalize()} report"
        if name not in ("usage", "recommendation"):
            return HTMLResponse(pages.message_html("Not found", "There is no such report."), status_code=404)
        text = reader.report(name)
        if text is None:
            body = pages.message_html(
                title, "No report yet: run the pipeline (energatron run).", path=f"/reports/{name}"
            )
            return HTMLResponse(body, status_code=404)
        return HTMLResponse(text)

    return web
