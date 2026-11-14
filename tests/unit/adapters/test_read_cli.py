"""The P9 read commands on the command line (P9 spec §3): one document each, no lock taken, and argument errors."""

from pathlib import Path

import pytest

from tests.unit.adapters._cli import FakeVcsState, run_cli
from tests.unit.adapters.test_locking import held_by_another_process
from tests.unit.services._read_workspace import REC_ID, make_read_workspace


@pytest.mark.parametrize(
    ("argv", "kind"),
    [
        (["datasets", "--source", "gas_bill", "--limit", "5"], "datasets_result"),
        (["usage", "--months", "2"], "usage_result"),
        (["compare", "--fuel", "gas", "--brand", "other"], "compare_result"),
        (["explain", REC_ID, "--options", "1"], "explain_result"),
    ],
)
def test_each_prints_one_document_while_the_lock_is_held(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, fake_vcs: FakeVcsState, argv: list[str], kind: str
) -> None:
    paths, _ = make_read_workspace(tmp_path)
    with held_by_another_process(paths):
        ran = run_cli(capsys, *argv, "--json", "--workspace", str(paths.root))
    assert ran.code == 0, ran.out
    envelope = ran.envelope()
    assert (envelope["command"], envelope["data"]["kind"]) == (argv[0], kind)


@pytest.mark.parametrize("command", ["datasets", "usage", "compare", "explain"])
def test_human_output(capsys: pytest.CaptureFixture[str], tmp_path: Path, fake_vcs: FakeVcsState, command: str) -> None:
    paths, _ = make_read_workspace(tmp_path)
    ran = run_cli(capsys, command, "--workspace", str(paths.root))
    assert ran.code == 0 and ran.out.endswith("\n") and ran.out.strip()


@pytest.mark.parametrize(
    "argv",
    [
        ["datasets", "--source", "nope"],
        ["datasets", "--limit", "0"],
        ["usage", "--months", "61"],
        ["compare", "--fuel", "water"],
        ["explain", "--options", "0"],
    ],
)
def test_bad_arguments_are_usage_errors(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, fake_vcs: FakeVcsState, argv: list[str]
) -> None:
    paths, _ = make_read_workspace(tmp_path)
    ran = run_cli(capsys, *argv, "--json", "--workspace", str(paths.root))
    assert ran.code == 2 and ran.envelope()["error"]["code"] == "E_USAGE"


def test_explain_refuses_an_older_id(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, fake_vcs: FakeVcsState
) -> None:
    paths, _ = make_read_workspace(tmp_path)
    ran = run_cli(capsys, "explain", "REC-20261031-0123abcd", "--json", "--workspace", str(paths.root))
    error = ran.envelope()["error"]
    assert error["code"] == "E_INVALID_INPUT" and error["details"]["reason"] == "not_current"
