"""The JSON API (P10 spec §5): each route answers exactly the CLI's `--json` envelope, with §5.1's status codes."""

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from energatron.adapters import reads
from energatron.adapters.http.api import MAX_BODY, PREFIX, QUERY_PATHS
from energatron.core.models.catalog import SourceType
from energatron.core.models.params import Params
from tests.fixtures import make_gas as gas
from tests.fixtures.make_sources import make_pdf
from tests.fixtures.pipeline_workspace import land_blob
from tests.unit.adapters._cli import FakeVcsState, run_cli
from tests.unit.adapters._workspace import make_workspace, write
from tests.unit.adapters.http._client import client
from tests.unit.services._read_workspace import REC_ID, make_read_workspace
from tests.unit.services.test_context import TEST_REGION

# Each query route's arguments, as query parameters and as the CLI's words after the command.
CASES: dict[str, tuple[dict[str, str], list[str]]] = {
    "status": ({}, []),
    "datasets": ({"source": "gas_bill", "limit": "5"}, ["--source", "gas_bill", "--limit", "5"]),
    "usage": ({"months": "2"}, ["--months", "2"]),
    "compare": ({"fuel": "gas", "brand": "other"}, ["--fuel", "gas", "--brand", "other"]),
    "explain": ({"rec_id": REC_ID, "options": "1"}, [REC_ID, "--options", "1"]),
    "offers/diff": ({}, []),
    "landing": ({}, []),
    "backup/status": ({}, []),
    "diagnose": ({}, []),
    "fingerprint/check": ({}, []),
    "pipeline/check": ({}, []),
    "schedule/status": ({}, []),
    "version": ({}, []),
}


def test_every_query_route_has_a_case() -> None:
    assert [path.name for path in QUERY_PATHS] == list(CASES)


@pytest.mark.parametrize("path", QUERY_PATHS, ids=lambda path: path.name)
def test_each_route_answers_the_cli_envelope(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, fake_vcs: FakeVcsState, path: reads.ReadPath
) -> None:
    paths, _ = make_read_workspace(tmp_path)
    query, words = CASES[path.name]
    response = client(paths).get(f"{PREFIX}/{path.name}", params=query)
    ran = run_cli(capsys, *path.argv, *words, "--json", "--workspace", str(paths.root))
    expected = ran.envelope()
    assert response.headers["content-type"] == "application/json"
    assert response.json() == expected
    assert response.status_code == {0: 200, 1: 500, 2: 400, 3: 422, 4: 409, 6: 502, 7: 422}[expected["exit_code"]]


def test_reads_succeed_where_the_workspace_has_what_they_need(tmp_path: Path, fake_vcs: FakeVcsState) -> None:
    paths, _ = make_read_workspace(tmp_path)
    api = client(paths)
    for name in ("status", "datasets", "usage", "compare", "explain", "landing", "version"):
        response = api.get(f"{PREFIX}/{name}")
        assert response.status_code == 200 and response.json()["ok"], name


@pytest.mark.parametrize(
    ("route", "query", "parameter"),
    [
        ("datasets", {"limit": "0"}, "limit"),
        ("usage", {"months": "61"}, "months"),
        ("compare", {"fuel": "probe-value"}, "fuel"),
        ("status", {"probe-name": "probe-value"}, "probe-name"),
    ],
)
def test_a_bad_query_is_a_usage_error_naming_the_parameter_only(
    tmp_path: Path, fake_vcs: FakeVcsState, route: str, query: dict[str, str], parameter: str
) -> None:
    paths, _ = make_read_workspace(tmp_path)
    response = client(paths).get(f"{PREFIX}/{route}", params=query)
    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "E_USAGE" and error["details"]["parameter"] == parameter
    assert "probe-value" not in response.text


def test_a_repeated_parameter_is_a_usage_error(tmp_path: Path, fake_vcs: FakeVcsState) -> None:
    paths, _ = make_read_workspace(tmp_path)
    response = client(paths).get(f"{PREFIX}/usage?months=2&months=3")
    assert response.status_code == 400 and response.json()["error"]["details"]["parameter"] == "months"


def test_an_input_error_is_422(tmp_path: Path, fake_vcs: FakeVcsState) -> None:
    paths, _ = make_read_workspace(tmp_path)
    response = client(paths).get(f"{PREFIX}/explain", params={"rec_id": "REC-20260101-00000000"})
    assert response.status_code == 422 and response.json()["error"]["code"] == "E_INVALID_INPUT"


def test_an_unexpected_exception_is_an_internal_error_without_a_trace(
    tmp_path: Path, fake_vcs: FakeVcsState, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, _ = make_read_workspace(tmp_path)

    def boom(*_: Any, **__: Any) -> Any:
        raise RuntimeError("probe-secret")

    monkeypatch.setattr("energatron.services.usage_summary.usage", boom)
    response = client(paths).get(f"{PREFIX}/usage")
    assert response.status_code == 500 and response.json()["error"]["code"] == "E_INTERNAL"
    assert "probe-secret" not in response.text and "Traceback" not in response.text


def test_the_denylist_redacts_api_answers(tmp_path: Path, fake_vcs: FakeVcsState) -> None:
    paths, _ = make_read_workspace(tmp_path)
    write(paths.root, "private/denylist.txt", "Synthetic Saver\n")
    response = client(paths).get(f"{PREFIX}/compare")
    assert response.status_code == 200
    assert "Synthetic Saver" not in response.text and "<redacted>" in response.text


@pytest.fixture
def bill_workspace(tmp_path: Path) -> tuple[Any, str]:
    paths = make_workspace(tmp_path)
    write(paths.root, "params.yaml", yaml.safe_dump(Params.defaults(TEST_REGION).model_dump(mode="json")))
    pdf = land_blob(paths.root, SourceType.GAS_BILL, "bill.pdf", make_pdf(creation_date="D:20261103120000+10'30'"))
    return paths, pdf.dataset_id


def test_the_gas_bill_check_passes_and_fails_like_the_cli(
    bill_workspace: tuple[Any, str], fake_vcs: FakeVcsState
) -> None:
    paths, pdf_id = bill_workspace
    api = client(paths)
    good = api.post(f"{PREFIX}/gas-bill/check", content=gas.record_bytes(gas.record(pdf_dataset_id=pdf_id)))
    assert good.status_code == 200 and good.json()["data"]["kind"] == "gas_bill_check_result"
    document = gas.record(pdf_dataset_id=pdf_id)
    document["total"] = "1.00"
    bad = api.post(f"{PREFIX}/gas-bill/check", content=json.dumps(document))
    assert bad.status_code == 422
    assert bad.json()["error"]["code"] == "E_CHECK_FAILED" and bad.json()["error"]["details"]["failed"]
    assert not list((paths.root / "data/landing").glob("*.yaml"))


def test_the_gas_bill_check_refuses_a_body_over_1_mib(bill_workspace: tuple[Any, str], fake_vcs: FakeVcsState) -> None:
    paths, _ = bill_workspace
    response = client(paths).post(f"{PREFIX}/gas-bill/check", content=b"x" * (MAX_BODY + 1))
    assert response.status_code == 400 and response.json()["error"]["details"]["reason"] == "too_large"


def test_bill_text_only_with_allow_documents(bill_workspace: tuple[Any, str], fake_vcs: FakeVcsState) -> None:
    paths, pdf_id = bill_workspace
    assert client(paths).get(f"{PREFIX}/gas-bill/text/{pdf_id}").status_code == 404
    response = client(paths, allow_documents=True).get(f"{PREFIX}/gas-bill/text/{pdf_id}")
    assert response.status_code == 200 and response.json()["data"]["kind"] == "gas_bill_text_result"


def test_the_index_lists_every_route(tmp_path: Path, fake_vcs: FakeVcsState) -> None:
    paths, _ = make_read_workspace(tmp_path)
    names = [route["name"] for route in client(paths).get(PREFIX).json()["routes"]]
    assert names == [path.name for path in reads.READS if not path.document]
    with_documents = client(paths, allow_documents=True).get(PREFIX).json()["routes"]
    assert [route["name"] for route in with_documents] == [path.name for path in reads.READS]


def test_generated_docs_are_off_and_openapi_is_served(tmp_path: Path, fake_vcs: FakeVcsState) -> None:
    paths, _ = make_read_workspace(tmp_path)
    api = client(paths)
    assert api.get("/docs").status_code == 404 and api.get("/redoc").status_code == 404
    assert api.get("/api/openapi.json").json()["info"]["title"] == "energatron"


def test_head_is_answered_like_get(tmp_path: Path, fake_vcs: FakeVcsState) -> None:
    paths, _ = make_read_workspace(tmp_path)
    assert client(paths).head(f"{PREFIX}/version").status_code == 200
