"""The pages (P10 spec §6): every page renders, every link answers, values are escaped and redacted, failures show."""

import json
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import pytest
import yaml

from energatron.adapters.http.pages import NAV
from energatron.adapters.http.security import APP_POLICY, REPORT_POLICY
from energatron.adapters.http.views import PAGES
from energatron.core.models.catalog import SourceType
from energatron.core.models.params import Params
from tests.fixtures import make_gas as gas
from tests.fixtures.make_sources import make_pdf
from tests.fixtures.pipeline_workspace import land_blob
from tests.unit.adapters._cli import FakeVcsState
from tests.unit.adapters._workspace import make_workspace, write
from tests.unit.adapters.http._client import client
from tests.unit.services._read_workspace import make_read_workspace, recommendation
from tests.unit.services.test_context import TEST_REGION

ROUTES = (*PAGES, "/gas-bill")


class _Links(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for name, value in attrs:
            if name in ("href", "action") and value:
                self.links.append(value)


def links(text: str) -> list[str]:
    parser = _Links()
    parser.feed(text)
    return parser.links


def write_reports(root: Path, text: str = "<!doctype html><title>report</title><p>Report body</p>") -> None:
    write(root, "data/reports/usage/index.html", text)
    write(root, "data/reports/recommendations/index.html", text)


@pytest.mark.parametrize("route", ROUTES)
def test_every_page_renders_on_the_read_workspace(tmp_path: Path, fake_vcs: FakeVcsState, route: str) -> None:
    paths, _ = make_read_workspace(tmp_path)
    response = client(paths).get(route)
    assert response.headers["content-type"].startswith("text/html")
    assert response.text.startswith("<!doctype html>") and '<html lang="en">' in response.text
    assert "<script" not in response.text.lower()
    assert response.headers["content-security-policy"] == APP_POLICY
    if route != "/health":  # its fingerprint and pipeline checks fail in this scratch workspace, as on the CLI
        assert response.status_code == 200, response.text


@pytest.mark.parametrize("route", ROUTES)
def test_every_page_renders_on_an_empty_workspace(tmp_path: Path, fake_vcs: FakeVcsState, route: str) -> None:
    paths = make_workspace(tmp_path)
    response = client(paths).get(route)
    assert response.status_code < 500 and response.text.startswith("<!doctype html>")


def test_every_link_on_every_page_answers(tmp_path: Path, fake_vcs: FakeVcsState) -> None:
    paths, _ = make_read_workspace(tmp_path)
    write_reports(paths.root)
    api = client(paths)
    seen: set[str] = set()
    for route in ROUTES:
        for link in links(api.get(route).text):
            if link in seen:
                continue
            seen.add(link)
            response = api.get(link.replace("&amp;", "&"))
            # A JSON link answers its read's own status (a failing check is 409 or 422); none is a missing route.
            assert response.status_code not in (400, 401, 404, 405) and response.status_code < 500, link
    assert {path for path, _ in NAV} <= seen


def test_the_nav_marks_the_current_page(tmp_path: Path, fake_vcs: FakeVcsState) -> None:
    paths, _ = make_read_workspace(tmp_path)
    assert '<a href="/usage" aria-current="page">' in client(paths).get("/usage").text


def test_values_are_escaped(tmp_path: Path, fake_vcs: FakeVcsState) -> None:
    paths, _ = make_read_workspace(tmp_path)
    document = recommendation()
    document["priced"]["electricity"][0]["display_name"] = '<script>alert("x")</script>'
    write(paths.root, "data/reports/recommendations/recommendation.json", json.dumps(document))
    text = client(paths).get("/recommendation").text
    assert "<script>" not in text and "&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;" in text


def test_query_values_reach_the_reads_and_links(tmp_path: Path, fake_vcs: FakeVcsState) -> None:
    paths, _ = make_read_workspace(tmp_path)
    text = client(paths).get("/recommendation", params={"fuel": "gas", "options": "1"}).text
    assert 'href="/api/v1/compare?fuel=gas"' in text and 'href="/api/v1/explain?options=1"' in text
    assert '<option value="gas" selected>' in text


@pytest.mark.parametrize(("route", "query"), [("/usage", "months=0"), ("/recommendation", "probe=1"), ("/", "x=1")])
def test_a_bad_query_shows_a_usage_error(tmp_path: Path, fake_vcs: FakeVcsState, route: str, query: str) -> None:
    paths, _ = make_read_workspace(tmp_path)
    response = client(paths).get(f"{route}?{query}")
    assert response.status_code == 400 and "E_USAGE" in response.text


def test_a_failing_read_shows_in_its_section_and_the_rest_render(tmp_path: Path, fake_vcs: FakeVcsState) -> None:
    paths, _ = make_read_workspace(tmp_path)
    response = client(paths).get("/health")
    assert response.status_code in (409, 422)
    assert 'class="error"' in response.text
    for title in ("Backup", "Code fingerprint", "Pipeline files", "Weekly schedule", "Diagnosis"):
        assert f"<h2>{title} " in response.text
    assert "Commits not backed up" in response.text  # the backup section rendered its data


def test_a_broken_document_is_an_input_error_page(tmp_path: Path, fake_vcs: FakeVcsState) -> None:
    paths, _ = make_read_workspace(tmp_path)
    write(paths.root, "data/marts/usage_profile/summary.json", "{not json")
    response = client(paths).get("/usage")
    assert response.status_code == 422 and "E_INVALID_INPUT" in response.text


def test_the_denylist_redacts_pages_api_and_reports(tmp_path: Path, fake_vcs: FakeVcsState) -> None:
    paths, _ = make_read_workspace(tmp_path)
    write_reports(paths.root, "<!doctype html><p>Synthetic Saver is cheapest</p>")
    write(paths.root, "private/denylist.txt", "Synthetic Saver\n")
    api = client(paths)
    for route in ("/", "/recommendation", "/api/v1/compare", "/reports/recommendation", "/reports/usage"):
        response = api.get(route)
        assert "Synthetic Saver" not in response.text, route
    assert "&lt;redacted&gt;" in api.get("/recommendation").text


def test_reports_are_served_with_the_sandbox_policy(tmp_path: Path, fake_vcs: FakeVcsState) -> None:
    paths, _ = make_read_workspace(tmp_path)
    write_reports(paths.root)
    api = client(paths)
    for name in ("usage", "recommendation"):
        response = api.get(f"/reports/{name}")
        assert response.status_code == 200 and "Report body" in response.text
        assert response.headers["content-security-policy"] == REPORT_POLICY


def test_a_missing_or_unknown_report_is_404(tmp_path: Path, fake_vcs: FakeVcsState) -> None:
    paths, _ = make_read_workspace(tmp_path)
    api = client(paths)
    missing = api.get("/reports/usage")
    assert missing.status_code == 404 and "run the pipeline" in missing.text
    unknown = api.get("/reports/probe-name")
    assert unknown.status_code == 404 and "probe-name" not in unknown.text
    assert api.get("/reports/..%2Fparams.yaml").status_code == 404


def test_the_stylesheet(tmp_path: Path, fake_vcs: FakeVcsState) -> None:
    paths, _ = make_read_workspace(tmp_path)
    response = client(paths).get("/style.css")
    assert response.status_code == 200 and response.headers["content-type"].startswith("text/css")
    assert "prefers-color-scheme: dark" in response.text and "url(" not in response.text


@pytest.fixture
def bill_workspace(tmp_path: Path) -> tuple[Any, str]:
    paths = make_workspace(tmp_path)
    write(paths.root, "params.yaml", yaml.safe_dump(Params.defaults(TEST_REGION).model_dump(mode="json")))
    pdf = land_blob(paths.root, SourceType.GAS_BILL, "bill.pdf", make_pdf(creation_date="D:20261103120000+10'30'"))
    return paths, pdf.dataset_id


def test_the_gas_bill_form_checks_a_record_and_writes_nothing(
    bill_workspace: tuple[Any, str], fake_vcs: FakeVcsState
) -> None:
    paths, pdf_id = bill_workspace
    api = client(paths)
    form = api.get("/gas-bill")
    assert form.status_code == 200 and '<form class="record" method="post" action="/gas-bill">' in form.text
    good = gas.record_bytes(gas.record(pdf_dataset_id=pdf_id)).decode()
    passed = api.post("/gas-bill", data={"record": good}, headers={"Origin": "http://127.0.0.1:8765"})
    assert passed.status_code == 200 and "Recomputed total" in passed.text
    document = gas.record(pdf_dataset_id=pdf_id)
    document["total"] = "1.00"
    failed = api.post("/gas-bill", data={"record": gas.record_bytes(document).decode()})
    assert failed.status_code == 422 and "E_CHECK_FAILED" in failed.text and "totals_recompute" in failed.text
    assert re.search(r"<textarea[^>]*>[^<]*total", failed.text)  # the record stays in the form to fix
    assert not list((paths.root / "data/landing").glob("*.yaml"))


def test_bill_text_page_only_with_allow_documents(bill_workspace: tuple[Any, str], fake_vcs: FakeVcsState) -> None:
    paths, pdf_id = bill_workspace
    assert client(paths).get(f"/gas-bill/text/{pdf_id}").status_code == 404
    response = client(paths, allow_documents=True).get(f"/gas-bill/text/{pdf_id}")
    assert response.status_code == 200 and "<pre>" in response.text
