"""`energatron gas-bill check|write|text|new` (P9 spec §4.2): exit codes, stdin, the lock, the real PDF reader."""

import io
import sys
from pathlib import Path

import pytest
import yaml

from energatron.core.models.catalog import SourceType
from energatron.core.models.params import Params
from energatron.core.models.workspace import WorkspacePaths
from tests.fixtures import make_gas as gas
from tests.fixtures.make_sources import make_pdf
from tests.fixtures.pipeline_workspace import land_blob
from tests.unit.adapters._cli import FakeVcsState, run_cli
from tests.unit.adapters._workspace import make_workspace, write
from tests.unit.adapters.test_locking import held_by_another_process
from tests.unit.services.test_context import TEST_REGION


@pytest.fixture
def workspace(tmp_path: Path) -> tuple[Path, str]:
    paths = make_workspace(tmp_path)
    write(paths.root, "params.yaml", yaml.safe_dump(Params.defaults(TEST_REGION).model_dump(mode="json")))
    pdf = land_blob(paths.root, SourceType.GAS_BILL, "bill.pdf", make_pdf(creation_date="D:20261103120000+10'30'"))
    return paths.root, pdf.dataset_id


def stdin(monkeypatch: pytest.MonkeyPatch, data: bytes) -> None:
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(data)))


def test_check_passes_from_a_file_and_from_stdin(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, workspace: tuple[Path, str],
    fake_vcs: FakeVcsState, tmp_path: Path,
) -> None:  # fmt: skip
    root, pdf_id = workspace
    record = tmp_path / "record.yaml"
    record.write_bytes(gas.record_bytes(gas.record(pdf_dataset_id=pdf_id)))
    ran = run_cli(capsys, "gas-bill", "check", str(record), "--json", "--workspace", str(root))
    assert ran.code == 0 and ran.envelope()["data"]["kind"] == "gas_bill_check_result"
    stdin(monkeypatch, record.read_bytes())
    ran = run_cli(capsys, "gas-bill", "check", "-", "--workspace", str(root))
    assert ran.code == 0 and "Every check passed" in ran.out


def test_a_failing_check_exits_7_with_the_checks_in_details(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, workspace: tuple[Path, str],
    fake_vcs: FakeVcsState,
) -> None:  # fmt: skip
    root, pdf_id = workspace
    document = gas.record(pdf_dataset_id=pdf_id)
    document["total"] = "1.00"
    stdin(monkeypatch, gas.record_bytes(document))
    ran = run_cli(capsys, "gas-bill", "write", "-", "--json", "--workspace", str(root))
    envelope = ran.envelope()
    assert ran.code == 7 and envelope["data"] is None
    assert envelope["error"]["code"] == "E_CHECK_FAILED" and envelope["error"]["details"]["failed"] == [
        "totals_recompute"
    ]
    assert not (root / "data/landing").exists()


def test_write_takes_the_lock_and_check_does_not(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, workspace: tuple[Path, str],
    fake_vcs: FakeVcsState,
) -> None:  # fmt: skip
    root, pdf_id = workspace
    data = gas.record_bytes(gas.record(pdf_dataset_id=pdf_id))
    paths = WorkspacePaths(root=root)
    with held_by_another_process(paths):
        stdin(monkeypatch, data)
        assert run_cli(capsys, "gas-bill", "check", "-", "--json", "--workspace", str(root)).code == 0
        stdin(monkeypatch, data)
        ran = run_cli(capsys, "gas-bill", "write", "-", "--json", "--workspace", str(root))
        assert ran.envelope()["error"]["code"] == "E_LOCKED"
    stdin(monkeypatch, data)
    ran = run_cli(capsys, "gas-bill", "write", "-", "--json", "--workspace", str(root))
    assert ran.code == 0 and ran.envelope()["data"]["written"] is True


def test_text_reads_the_landed_pdf(
    capsys: pytest.CaptureFixture[str], workspace: tuple[Path, str], fake_vcs: FakeVcsState
) -> None:
    root, pdf_id = workspace
    ran = run_cli(capsys, "gas-bill", "text", pdf_id, "--json", "--workspace", str(root))
    data = ran.envelope()["data"]
    assert ran.code == 0 and data["kind"] == "gas_bill_text_result" and data["pages"] >= 1


def test_new_fills_extracted_by_and_an_unreadable_source_is_invalid(
    capsys: pytest.CaptureFixture[str], workspace: tuple[Path, str], fake_vcs: FakeVcsState, tmp_path: Path
) -> None:
    root, pdf_id = workspace
    ran = run_cli(capsys, "gas-bill", "new", pdf_id, "--extracted-by", "manual", "--json", "--workspace", str(root))
    assert ran.code == 0
    ran = run_cli(capsys, "gas-bill", "check", str(tmp_path / "missing.yaml"), "--json", "--workspace", str(root))
    assert ran.envelope()["error"]["details"] == {"field": "source"}
