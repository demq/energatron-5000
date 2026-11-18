"""Progress on stderr (owner request M.2): `StderrProgress`'s lines, and `energatron run` wiring it in human mode only.

The `run` tests replace the run service with one that reports through the context, so they check what reaches the
service and the runner, not the pipeline itself (`test_dvc_runner.py` and `test_run.py` cover that).
"""

from typing import Any

import pytest

from energatron.adapters import wiring
from energatron.adapters.cli.commands import run as run_command
from energatron.adapters.cli.output import Invocation
from energatron.adapters.cli.progress import StderrProgress, progress_for
from energatron.adapters.pipeline.dvc_runner import DvcRunner
from energatron.core.models.recommend import ToolState
from energatron.core.privacy import DenyEntry
from energatron.services.context import ServiceContext
from energatron.services.progress import NullProgress, duration
from energatron.services.run import RunResult
from tests.unit.adapters._cli import FakeVcsState, data_of, run_cli
from tests.unit.adapters._workspace import make_workspace


@pytest.mark.parametrize(
    ("seconds", "text"),
    [(0.04, "0.0s"), (4.25, "4.2s"), (59.94, "59.9s"), (59.96, "1m 00s"), (112.4, "1m 52s"), (3725, "62m 05s")],
)
def test_durations_read_as_seconds_then_minutes(seconds: float, text: str) -> None:
    assert duration(seconds) == text


def test_steps_and_stages_are_one_line_each_on_stderr(capsys: pytest.CaptureFixture[str]) -> None:
    progress = StderrProgress(Invocation(json_mode=False, debug=False))
    progress.step("Running the pipeline: 20 stages")
    progress.stage_started("usage_profile", 7, 20)
    progress.stage_finished("usage_profile", 7, 20, exit_code=0, seconds=12.34)
    progress.stage_started("bundles", 18, 20)
    progress.stage_finished("bundles", 18, 20, exit_code=3, seconds=1.0)
    progress.stage_finished("recommend", 19, 20, exit_code=None, seconds=75)
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.splitlines() == [
        "Running the pipeline: 20 stages",
        "  [ 7/20] usage_profile ...",
        "  [ 7/20] usage_profile done in 12.3s",
        "  [18/20] bundles ...",
        "  [18/20] bundles FAILED after 1.0s (exit code 3)",
        "  [19/20] recommend stopped after 1m 15s",
    ]


def test_progress_is_redacted(capsys: pytest.CaptureFixture[str]) -> None:
    invocation = Invocation(
        json_mode=False, debug=False, denylist=(DenyEntry(label="manual", value="SECRET42", match="substring"),)
    )
    StderrProgress(invocation).step("Checking SECRET42")
    assert "SECRET42" not in capsys.readouterr().err


def test_progress_is_off_in_json_mode() -> None:
    assert isinstance(progress_for(Invocation(json_mode=True, debug=False)), NullProgress)
    assert isinstance(progress_for(Invocation(json_mode=False, debug=False)), StderrProgress)


TOOL = ToolState(energatron_version="0.1.0", install="editable", tool_commit=None, tool_dirty=False)


@pytest.fixture
def seen(monkeypatch: pytest.MonkeyPatch, fake_vcs: FakeVcsState) -> dict[str, Any]:
    """Replace the fingerprint and the run service; returns what the service was handed."""
    found: dict[str, Any] = {}
    live = wiring.code_fingerprint()
    monkeypatch.setattr(wiring, "code_fingerprint", lambda: live)

    def fake_run(ctx: ServiceContext, *, tool: ToolState, draft: bool = False) -> RunResult:
        found["context"] = ctx
        ctx.progress.step("Committing the run")
        return RunResult(
            run_id="run-20261115T000000Z-a1b2c3d",
            draft=draft,
            recommendation_id=None,
            action=None,
            robust=None,
            ran=0,
            restored=0,
            cached=20,
            commit="f" * 40,
        )

    monkeypatch.setattr(run_command.run_service, "run", fake_run)
    return found


def test_run_prints_its_progress_on_stderr_in_human_mode(
    capsys: pytest.CaptureFixture[str], tmp_path: Any, seen: dict[str, Any]
) -> None:
    paths = make_workspace(tmp_path)
    ran = run_cli(capsys, "run", "--workspace", str(paths.root))
    assert ran.code == 0
    assert ran.err.splitlines()[:2] == ["Computing the tool's code fingerprint", "Committing the run"]
    assert "Committing" not in ran.out
    context: ServiceContext = seen["context"]
    assert isinstance(context.progress, StderrProgress)
    assert isinstance(context.runner, DvcRunner)
    assert context.runner.progress is context.progress


def test_run_prints_no_progress_in_json_mode(
    capsys: pytest.CaptureFixture[str], tmp_path: Any, seen: dict[str, Any]
) -> None:
    paths = make_workspace(tmp_path)
    ran = run_cli(capsys, "run", "--json", "--workspace", str(paths.root))
    assert ran.code == 0
    assert data_of(ran.envelope())["kind"] == "run_result"
    assert ran.err == ""
    assert isinstance(seen["context"].progress, NullProgress)
