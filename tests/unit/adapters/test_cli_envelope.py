"""The CLI contract (D1 §8; D2 §5.9, §6.4, §8.5): the JSON envelope, exit codes, output streams and redaction.

Commands run in-process through `main()`, against scratch workspaces and a fake `VersionControl`.
"""

import json
import logging
import platform
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

import energatron
from energatron.adapters import wiring
from energatron.adapters.cli import output
from energatron.adapters.cli.commands import stage as stage_command
from energatron.adapters.cli.output import Invocation, confirm
from energatron.core.canonical import canonical_json
from energatron.core.errors import (
    Category,
    CheckFailedError,
    ConfirmationRequiredError,
    EnergatronError,
    ForeignPremisesError,
    InvalidInputError,
    LockedError,
    PartialError,
    ResultWarning,
    StorageError,
    UsageError,
    WarningCode,
)
from energatron.core.models.catalog import SourceType
from energatron.core.models.provenance import CodeFingerprint
from energatron.core.models.vcs import BackupStatus
from energatron.services import backup as backup_service
from energatron.services.backup import BackupStatusResult
from energatron.services.files import dump_json
from energatron.services.stages.dvc_yaml import CODE_FINGERPRINT_PATH
from tests.unit.adapters._cli import BUNDLE, FakeVcs, FakeVcsState, data_of, run_cli
from tests.unit.adapters._workspace import MINIMAL_PRIVATE_TOML, make_workspace, write
from tests.unit.core._private import NMI, NMI_WITH_CHECKSUM

ENVELOPE_KEYS = [
    "kind",
    "schema_version",
    "command",
    "ok",
    "exit_code",
    "energatron_version",
    "data",
    "warnings",
    "error",
]


def assert_success(envelope: dict[str, Any], command: str, data_kind: str) -> dict[str, Any]:
    assert list(envelope) == ENVELOPE_KEYS
    assert envelope["kind"] == "cli_result"
    assert envelope["schema_version"] == 1
    assert envelope["command"] == command
    assert envelope["ok"] is True
    assert envelope["exit_code"] == 0
    assert envelope["energatron_version"] == energatron.__version__
    assert envelope["error"] is None
    assert data_of(envelope)["kind"] == data_kind
    return envelope


def assert_failure(envelope: dict[str, Any], code: str, exit_code: int) -> dict[str, Any]:
    assert list(envelope) == ENVELOPE_KEYS
    assert envelope["ok"] is False
    assert envelope["exit_code"] == exit_code
    assert envelope["data"] is None
    error = envelope["error"]
    assert isinstance(error, dict)
    assert list(error) == ["code", "category", "message", "details"]
    assert error["code"] == code
    return error


# version


def test_version_prints_one_envelope_outside_a_workspace(capsys: pytest.CaptureFixture[str]) -> None:
    ran = run_cli(capsys, "version", "--json")
    assert ran.code == 0
    assert ran.err == ""
    envelope = assert_success(ran.envelope(), "version", "version_result")
    assert envelope["data"] == {
        "kind": "version_result",
        "schema_version": 1,
        "energatron_version": energatron.__version__,
        "python": platform.python_version(),
    }
    assert envelope["warnings"] == []


def test_energatron_json_turns_json_on(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENERGATRON_JSON", "1")
    assert_success(run_cli(capsys, "version").envelope(), "version", "version_result")


def test_version_in_human_mode(capsys: pytest.CaptureFixture[str]) -> None:
    ran = run_cli(capsys, "version")
    assert ran.code == 0
    assert ran.out == f"energatron {energatron.__version__} (Python {platform.python_version()})\n"


def test_python_dash_m_runs_the_cli(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "energatron", "version", "--json"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": "/usr/bin:/bin"},
    )
    assert result.returncode == 0
    assert result.stdout.count("\n") == 1
    assert '"version_result"' in result.stdout


def test_help_works_outside_a_workspace(capsys: pytest.CaptureFixture[str]) -> None:
    ran = run_cli(capsys, "--help")
    assert ran.code == 0
    assert "denylist" in ran.out
    assert "backup" in ran.out


# denylist


def test_denylist_writes_and_reports(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, fake_vcs: FakeVcsState
) -> None:
    paths = make_workspace(tmp_path)
    ran = run_cli(capsys, "denylist", "--json", "--workspace", str(paths.root))
    assert ran.code == 0
    envelope = assert_success(ran.envelope(), "denylist", "denylist_result")
    data = data_of(envelope)
    assert data["written"] is True
    assert data["pointer_files"] == ["private.dvc"]
    assert envelope["warnings"] == []
    assert fake_vcs.tracked == [["private"]]
    assert paths.denylist_generated.is_file()


def test_denylist_warnings_move_to_the_envelope(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, fake_vcs: FakeVcsState
) -> None:
    paths = make_workspace(tmp_path, private=MINIMAL_PRIVATE_TOML)
    envelope = run_cli(capsys, "denylist", "--json", "--workspace", str(paths.root)).envelope()
    assert "warnings" not in data_of(envelope)
    assert [warning["code"] for warning in envelope["warnings"]] == ["W_PRIVATE_FIELDS_MISSING"]
    assert list(envelope["warnings"][0]) == ["code", "message", "details"]


def test_denylist_check_and_dry_run(capsys: pytest.CaptureFixture[str], tmp_path: Path, fake_vcs: FakeVcsState) -> None:
    paths = make_workspace(tmp_path)
    workspace = ("--workspace", str(paths.root))
    stale = run_cli(capsys, "denylist", "--check", "--json", *workspace)
    assert stale.code == 4
    assert assert_failure(stale.envelope(), "E_DENYLIST_STALE", 4)["category"] == "state"
    dry = run_cli(capsys, "denylist", "--dry-run", "--json", *workspace)
    assert dry.code == 0
    assert data_of(dry.envelope())["dry_run"] is True
    assert not paths.denylist_generated.exists()
    assert run_cli(capsys, "denylist", *workspace).code == 0
    assert run_cli(capsys, "denylist", "--check", "--json", *workspace).code == 0


def test_denylist_without_private_config(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, fake_vcs: FakeVcsState
) -> None:
    paths = make_workspace(tmp_path, private=None)
    ran = run_cli(capsys, "denylist", "--json", "--workspace", str(paths.root))
    assert ran.code == 4
    assert_failure(ran.envelope(), "E_PRIVATE_CONFIG_MISSING", 4)


def test_denylist_finds_the_workspace_from_the_current_directory(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, fake_vcs: FakeVcsState, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = make_workspace(tmp_path)
    (paths.root / "data").mkdir()
    monkeypatch.chdir(paths.root / "data")
    assert run_cli(capsys, "denylist", "--json").code == 0
    monkeypatch.chdir(tmp_path / "outside")
    monkeypatch.setenv("ENERGATRON_WORKSPACE", str(paths.root))
    assert run_cli(capsys, "denylist", "--json").code == 0


def test_a_command_outside_a_workspace_is_not_a_workspace(capsys: pytest.CaptureFixture[str]) -> None:
    for command in (["denylist"], ["backup"], ["backup", "--status"]):
        ran = run_cli(capsys, *command, "--json")
        assert ran.code == 2
        error = assert_failure(ran.envelope(), "E_NOT_A_WORKSPACE", 2)
        assert error["category"] == "usage"


# backup


def test_backup_status(capsys: pytest.CaptureFixture[str], tmp_path: Path, fake_vcs: FakeVcsState) -> None:
    paths = make_workspace(tmp_path)
    ran = run_cli(capsys, "backup", "--status", "--json", "--workspace", str(paths.root))
    assert ran.code == 0
    envelope = assert_success(ran.envelope(), "backup", "backup_status_result")
    assert data_of(envelope)["refs_backed_up"] is False
    assert fake_vcs.backups == 0


def test_backup_status_in_human_mode_says_what_is_pending(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, fake_vcs: FakeVcsState, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = make_workspace(tmp_path)
    workspace = ("--workspace", str(paths.root))
    ran = run_cli(capsys, "backup", "--status", *workspace)
    assert ran.out.startswith("Backup pending: nothing is backed up yet.\n")
    assert "Run `icloud_backup.py backup` in your terminal." in ran.out

    def status(**changes: Any) -> BackupStatus:
        base = BackupStatus(
            head="1" * 40,
            last_backup_head="2" * 40,
            last_backup_at=None,
            refs_backed_up=False,
            commits_not_backed_up=3,
        )
        return base.model_copy(update=changes)

    cases = [
        (status(), "Backup pending: 3 commits not in iCloud."),
        (
            status(commits_not_backed_up=0),
            "Backup pending: refs changed since the last backup (for example a new tag).",
        ),
        (status(commits_not_backed_up=None), "Backup pending: refs changed since the last backup"),
        (status(refs_backed_up=True, commits_not_backed_up=0), "Backed up: nothing is pending."),
    ]
    for value, expected in cases:
        monkeypatch.setattr(FakeVcs, "backup_status", lambda self, value=value: value)
        assert expected in run_cli(capsys, "backup", "--status", *workspace).out


def test_backup_and_its_dry_run(capsys: pytest.CaptureFixture[str], tmp_path: Path, fake_vcs: FakeVcsState) -> None:
    paths = make_workspace(tmp_path)
    workspace = ("--workspace", str(paths.root))
    dry = run_cli(capsys, "backup", "--dry-run", "--json", *workspace)
    assert dry.code == 0
    assert data_of(dry.envelope()) == {"kind": "backup_result", "schema_version": 1, "report": None, "dry_run": True}
    assert fake_vcs.backups == 0

    fake_vcs.dataless = 2
    ran = run_cli(capsys, "backup", "--json", *workspace)
    assert ran.code == 0
    envelope = assert_success(ran.envelope(), "backup", "backup_result")
    report = data_of(envelope)["report"]
    assert isinstance(report, dict)
    assert report["bundle_name"] == BUNDLE
    assert [warning["code"] for warning in envelope["warnings"]] == ["W_BACKUP_DATALESS"]
    assert fake_vcs.backups == 1


def test_backup_status_and_dry_run_do_not_combine(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, fake_vcs: FakeVcsState
) -> None:
    paths = make_workspace(tmp_path)
    ran = run_cli(capsys, "backup", "--status", "--dry-run", "--json", "--workspace", str(paths.root))
    assert ran.code == 2
    assert_failure(ran.envelope(), "E_USAGE", 2)


# Argument errors (D1 §8.5: output mode is decided before parsing)


@pytest.mark.parametrize(
    ("argv", "command"),
    [
        (["backup", "--nope", "--json"], "backup"),
        (["denylist", "--bogus", "--json", "--check"], "denylist"),
        (["--nope", "version", "--json"], "version"),
        (["version", "--json", "extra"], "version"),
        (["nope", "--json"], None),
        (["--json"], None),
    ],
)
def test_argument_errors_still_print_one_envelope(
    capsys: pytest.CaptureFixture[str], argv: list[str], command: str | None
) -> None:
    ran = run_cli(capsys, *argv)
    assert ran.code == 2
    envelope = ran.envelope()
    assert envelope["command"] == command
    assert assert_failure(envelope, "E_USAGE", 2)["category"] == "usage"
    assert ran.err == ""


@pytest.mark.parametrize(
    "argv",
    [
        [f"{NMI_WITH_CHECKSUM}_x.csv", "--json"],
        ["--json", NMI.lower()],
        [f"{NMI}.csv", "backup", "--json"],
    ],
)
def test_an_unknown_command_is_null_in_the_envelope(capsys: pytest.CaptureFixture[str], argv: list[str]) -> None:
    """Owner decision 2026-10-29: `command` is a recognised command name or null, never the token typed."""
    ran = run_cli(capsys, *argv)
    assert ran.code == 2
    envelope = ran.envelope()
    assert envelope["command"] is None
    assert_failure(envelope, "E_USAGE", 2)
    assert NMI.lower() not in (ran.out + ran.err).lower()


def test_argument_errors_with_energatron_json(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ENERGATRON_JSON", "1")
    ran = run_cli(capsys, "backup", "--bogus")
    assert ran.code == 2
    assert_failure(ran.envelope(), "E_USAGE", 2)


def test_json_after_a_double_dash_does_not_count(capsys: pytest.CaptureFixture[str]) -> None:
    ran = run_cli(capsys, "version", "--", "--json")
    assert ran.code == 2
    assert ran.out == ""
    assert "E_USAGE" in ran.err


def test_a_bare_command_group_is_only_a_usage_error(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`energatron pipeline` with no subcommand prints no help: an error only, as the top-level app does."""
    ran = run_cli(capsys, "pipeline")
    assert ran.code == 2
    assert ran.out == ""
    assert ran.err.startswith("energatron: error E_USAGE: ")
    monkeypatch.setenv("ENERGATRON_JSON", "1")
    ran = run_cli(capsys, "pipeline")
    assert ran.code == 2
    envelope = ran.envelope()
    assert envelope["command"] == "pipeline"
    assert_failure(envelope, "E_USAGE", 2)


def test_argument_errors_in_human_mode_go_to_stderr(capsys: pytest.CaptureFixture[str]) -> None:
    ran = run_cli(capsys, "backup", "--nope")
    assert ran.code == 2
    assert ran.out == ""
    assert ran.err.startswith("energatron: error E_USAGE: ")


# Argument errors never echo what was typed (D1 §7, §8.7): click's text can quote a value, and the denylist isn't
# loaded before parsing, so nothing could redact it.


NMI_ARGUMENTS = [
    ["backup", f"{NMI_WITH_CHECKSUM}_x.csv"],
    ["backup", f"--{NMI_WITH_CHECKSUM}"],
    ["backup", f"--status={NMI_WITH_CHECKSUM}"],
    ["denylist", f"--check={NMI_WITH_CHECKSUM}"],
    [f"--{NMI_WITH_CHECKSUM}", "version"],
    ["version", "--", NMI_WITH_CHECKSUM],
    [f"{NMI_WITH_CHECKSUM}_x.csv"],
]


@pytest.mark.parametrize("argv", NMI_ARGUMENTS, ids=lambda argv: " ".join(argv).replace(NMI_WITH_CHECKSUM, "<NMI>"))
@pytest.mark.parametrize("json_mode", [True, False], ids=["json", "human"])
def test_argument_errors_never_echo_an_argument(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    fake_vcs: FakeVcsState,
    monkeypatch: pytest.MonkeyPatch,
    argv: list[str],
    json_mode: bool,
) -> None:
    paths = make_workspace(tmp_path)  # its private config holds the NMI
    monkeypatch.chdir(paths.root)
    if json_mode:
        monkeypatch.setenv("ENERGATRON_JSON", "1")
    ran = run_cli(capsys, *argv)
    assert ran.code == 2
    if json_mode:
        envelope = ran.envelope()
        error = assert_failure(envelope, "E_USAGE", 2)
        assert error["message"].startswith("Invalid arguments")
        # `command` is D1 §8.5's first non-option argument: an open question for the owner when it isn't a command
        shown = json.dumps({key: value for key, value in envelope.items() if key != "command"})
        assert ran.err == ""
    else:
        assert ran.out == ""
        assert ran.err.startswith("energatron: error E_USAGE: Invalid arguments")
        shown = ran.err
    assert NMI not in shown
    assert "x.csv" not in shown


def test_an_argument_error_names_only_a_declared_option(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ENERGATRON_JSON", "1")
    error = assert_failure(run_cli(capsys, "backup", "--status=on").envelope(), "E_USAGE", 2)
    assert error["details"] == {"parameter": "--status"}
    assert "--status" in error["message"]
    assert "on" not in error["message"].replace("--status", "")
    error = assert_failure(run_cli(capsys, "backup", "--workspace").envelope(), "E_USAGE", 2)
    assert error["details"] == {"parameter": "--workspace"}
    unknown = assert_failure(run_cli(capsys, "backup", "--made-up").envelope(), "E_USAGE", 2)
    assert unknown["details"] == {}
    assert "made-up" not in unknown["message"]


# Every error category, through one command


ONE_PER_CATEGORY: list[tuple[EnergatronError, int]] = [
    (UsageError("Bad arguments."), 2),
    (InvalidInputError("A file is invalid.", kind="backup_state"), 3),
    (LockedError("Another command holds the workspace lock."), 4),
    (ForeignPremisesError("Another premises."), 5),
    (StorageError("Unreachable.", location="bundle_dir", hint="run it in a terminal"), 6),
    (CheckFailedError("A check failed."), 7),
    (PartialError("Some items failed."), 8),
]


@pytest.mark.parametrize(("error", "exit_code"), ONE_PER_CATEGORY, ids=lambda value: getattr(value, "code", ""))
def test_every_error_category_gives_its_exit_code_and_one_envelope(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    fake_vcs: FakeVcsState,
    monkeypatch: pytest.MonkeyPatch,
    error: EnergatronError,
    exit_code: int,
) -> None:
    def fail(ctx: object) -> object:
        raise error

    monkeypatch.setattr(backup_service, "backup_status", fail)
    paths = make_workspace(tmp_path)
    ran = run_cli(capsys, "backup", "--status", "--json", "--workspace", str(paths.root))
    assert ran.code == exit_code
    envelope = ran.envelope()
    assert envelope["command"] == "backup"
    assert assert_failure(envelope, error.code, exit_code) == {
        "code": error.code,
        "category": error.category.value,
        "message": error.message,
        "details": error.details,
    }
    assert {member.value for member in Category} - {"internal"} == {
        type(item).category.value for item, _ in ONE_PER_CATEGORY
    }


def test_an_unexpected_exception_is_internal_with_a_traceback_only_in_debug(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, fake_vcs: FakeVcsState, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(ctx: object) -> object:
        raise RuntimeError("boom")

    monkeypatch.setattr(backup_service, "backup_status", fail)
    paths = make_workspace(tmp_path)
    workspace = ("--workspace", str(paths.root))
    ran = run_cli(capsys, "backup", "--status", "--json", *workspace)
    assert ran.code == 1
    error = assert_failure(ran.envelope(), "E_INTERNAL", 1)
    assert error["category"] == "internal"
    assert "Traceback" not in ran.err
    debug = run_cli(capsys, "backup", "--status", "--json", "--debug", *workspace)
    assert debug.code == 1
    assert_failure(debug.envelope(), "E_INTERNAL", 1)
    assert "Traceback" in debug.err
    assert "RuntimeError: boom" in debug.err


def test_human_mode_errors_go_to_stderr_with_the_hint(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, fake_vcs: FakeVcsState, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(ctx: object) -> object:
        raise StorageError("Unreachable.", location="bundle_dir", hint="run it in a terminal")

    monkeypatch.setattr(backup_service, "backup_status", fail)
    paths = make_workspace(tmp_path)
    ran = run_cli(capsys, "backup", "--status", "--workspace", str(paths.root))
    assert ran.code == 6
    assert ran.out == ""
    assert ran.err == "energatron: error E_STORAGE: Unreachable.\nenergatron: hint: run it in a terminal\n"


def test_logs_go_to_stderr_never_stdout(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, fake_vcs: FakeVcsState, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = backup_service.backup_status

    def noisy(ctx: Any) -> Any:
        logging.getLogger("energatron.services.backup").warning("a warning")
        logging.getLogger("energatron.services.backup").info("some info")
        return original(ctx)

    monkeypatch.setattr(backup_service, "backup_status", noisy)
    paths = make_workspace(tmp_path)
    workspace = ("--workspace", str(paths.root))
    quiet = run_cli(capsys, "backup", "--status", "--json", *workspace)
    quiet.envelope()
    assert "a warning" in quiet.err
    assert "some info" not in quiet.err
    verbose = run_cli(capsys, "backup", "--status", "--json", "-v", *workspace)
    verbose.envelope()
    assert "some info" in verbose.err


# Redaction (D1 §8.7): denylist strings never reach stdout or stderr


INJECTED = (NMI_WITH_CHECKSUM, "MSYNTHAB", "1 Synthetic Street", "ELECACCTSYN", "MANUALSECRET")
INJECTED_WORDS = ("Q7X", "0000", "K9")  # word entries: they count only where they stand alone (D2 §7.4)


def leaked(shown: str) -> list[str]:
    """The injected values that show: substring entries anywhere, word entries where they stand alone."""
    found = [value for value in INJECTED if value.lower() in shown.lower()]
    found += [
        value for value in INJECTED_WORDS if re.search(rf"(?<![0-9A-Za-z_-]){value}(?![0-9A-Za-z_-])", shown, re.I)
    ]
    return found


@pytest.mark.parametrize("mode", [("--json",), (), ("--json", "--debug"), ("--debug",)])
def test_denylist_values_never_reach_stdout_or_stderr(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    fake_vcs: FakeVcsState,
    monkeypatch: pytest.MonkeyPatch,
    mode: tuple[str, ...],
) -> None:
    paths = make_workspace(tmp_path)
    write(paths.root, "private/denylist.txt", "MANUALSECRET\n")
    text = " ".join((*INJECTED, *INJECTED_WORDS))

    def leaky(ctx: object) -> object:
        logging.getLogger("energatron.services.backup").warning("log: %s", text)
        raise StorageError(f"message: {text}", **{text: text, "nested": [{"value": text}]})

    monkeypatch.setattr(backup_service, "backup_status", leaky)
    ran = run_cli(capsys, "backup", "--status", *mode, "--workspace", str(paths.root))
    assert ran.code == 6
    shown = ran.out + ran.err
    assert "<redacted>" in shown
    assert leaked(shown) == []

    def crash(ctx: object) -> object:
        raise RuntimeError(f"crash: {text}")

    monkeypatch.setattr(backup_service, "backup_status", crash)
    ran = run_cli(capsys, "backup", "--status", *mode, "--workspace", str(paths.root))
    assert ran.code == 1
    assert leaked(ran.out + ran.err) == []

    def warns(ctx: object) -> BackupStatusResult:
        warning = ResultWarning(
            code=WarningCode.W_BACKUP_DATALESS,
            message=f"warning: {text}",
            details={text: text, "nested": [{"value": text}]},
        )
        return BackupStatusResult(
            head=None,
            last_backup_head=None,
            last_backup_at=None,
            refs_backed_up=False,
            commits_not_backed_up=None,
            warnings=(warning,),
        )

    monkeypatch.setattr(backup_service, "backup_status", warns)
    ran = run_cli(capsys, "backup", "--status", *mode, "--workspace", str(paths.root))
    assert ran.code == 0
    if "--json" in mode:
        warnings = ran.envelope()["warnings"]
        assert [warning["code"] for warning in warnings] == ["W_BACKUP_DATALESS"]
        assert "<redacted>" in json.dumps(warnings)
    else:
        assert "energatron: warning W_BACKUP_DATALESS: warning: <redacted>" in ran.err
    assert leaked(ran.out + ran.err) == []


# Confirmation (D1 §8.6). No P0 command asks; the helper every later one uses is checked here.


def test_confirmation_without_a_tty_needs_yes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)
    with pytest.raises(ConfirmationRequiredError) as caught:
        confirm(Invocation(json_mode=False, debug=False), "Go ahead?", yes=False)
    assert caught.value.exit_code == 2
    confirm(Invocation(json_mode=False, debug=False), "Go ahead?", yes=True)


def test_confirmation_in_json_mode_never_prompts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setattr(sys.stderr, "isatty", lambda: True, raising=False)
    monkeypatch.setattr(output, "ask", lambda question: pytest.fail("must not prompt"))
    with pytest.raises(ConfirmationRequiredError):
        confirm(Invocation(json_mode=True, debug=False), "Go ahead?", yes=False)


def test_confirmation_on_a_tty_prompts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setattr(sys.stderr, "isatty", lambda: True, raising=False)
    answers: list[str] = []
    monkeypatch.setattr(output, "ask", lambda question: answers.append(question) or True)
    confirm(Invocation(json_mode=False, debug=False), "Go ahead?", yes=False)
    assert answers == ["Go ahead?"]
    monkeypatch.setattr(output, "ask", lambda question: False)
    with pytest.raises(ConfirmationRequiredError):
        confirm(Invocation(json_mode=False, debug=False), "Go ahead?", yes=False)


# stage (D1 §9.5, §4.5): runs one stage; never locks; writes its repro-log records from main()


@pytest.fixture(scope="module")
def live_fingerprint() -> CodeFingerprint:
    return wiring.code_fingerprint()


def stage_workspace(tmp_path: Path, live: CodeFingerprint, *, record: bool = True) -> Path:
    """A workspace ready for `catalog_index`: `params.yaml`, empty catalogs and, unless `record` is off, the live
    code fingerprint recorded."""
    root = make_workspace(tmp_path).root
    write(root, "params.yaml", "kind: params\nschema_version: 1\n")
    for source in SourceType:
        write(root, f"data/raw/{source.value}/_catalog.jsonl", "")
    if record:
        write(root, CODE_FINGERPRINT_PATH, dump_json(live.model_dump(mode="json")))
    return root


def repro_log(path: Path) -> list[dict[str, Any]]:
    """The records in a repro log; each line compact canonical JSON of kind `repro_log_entry`."""
    lines = path.read_bytes().splitlines(keepends=True)
    for line in lines:
        assert line.endswith(b"\n")
        assert canonical_json(json.loads(line)) + b"\n" == line
    return [json.loads(line) for line in lines]


def start_end(stage: str, exit_code: int) -> list[dict[str, Any]]:
    entry = {"kind": "repro_log_entry", "schema_version": 1, "stage": stage}
    return [entry | {"event": "start", "exit_code": None}, entry | {"event": "end", "exit_code": exit_code}]


def test_stage_runs_a_stage_and_returns_its_result(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, fake_vcs: FakeVcsState, live_fingerprint: CodeFingerprint
) -> None:
    root = stage_workspace(tmp_path, live_fingerprint)
    ran = run_cli(capsys, "stage", "catalog_index", "--json", "--workspace", str(root))
    assert ran.code == 0, ran.out
    data: dict[str, Any] = data_of(assert_success(ran.envelope(), "stage", "stage_result"))
    assert list(data) == ["kind", "schema_version", "stage", "outputs", "checks"]
    assert data["stage"] == "catalog_index"
    outputs = data["outputs"]
    assert isinstance(outputs, dict)
    assert [file["path"] for file in outputs["catalog"]] == ["datasets.parquet"]
    assert outputs["catalog"][0]["rows"] == 0
    assert {check["status"] for check in data["checks"]} == {"pass"}
    provenance = json.loads((root / "data/marts/catalog/_provenance.json").read_text(encoding="utf-8"))
    assert provenance["code"] == live_fingerprint.stages["catalog_index"].model_dump(mode="json")


def test_stage_in_human_mode(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, fake_vcs: FakeVcsState, live_fingerprint: CodeFingerprint
) -> None:
    root = stage_workspace(tmp_path, live_fingerprint)
    ran = run_cli(capsys, "stage", "catalog_index", "--workspace", str(root))
    assert ran.code == 0
    assert ran.out == "Stage catalog_index done: 1 output, 1 file, 6 checks passed.\n"


def test_stage_refuses_a_stale_or_missing_code_entry(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, fake_vcs: FakeVcsState, live_fingerprint: CodeFingerprint
) -> None:
    root = stage_workspace(tmp_path, live_fingerprint, record=False)
    ran = run_cli(capsys, "stage", "catalog_index", "--json", "--workspace", str(root))
    assert assert_failure(ran.envelope(), "E_FINGERPRINT_STALE", 4)["details"] == {"stage": "catalog_index"}
    assert not (root / "data/marts/catalog").exists()


def test_a_failed_check_is_exit_7_after_the_outputs_are_written(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, fake_vcs: FakeVcsState, live_fingerprint: CodeFingerprint
) -> None:
    root = stage_workspace(tmp_path, live_fingerprint)
    write(root, "data/raw/sapn_interval/_catalog.jsonl", "not json\n")
    ran = run_cli(capsys, "stage", "catalog_index", "--json", "--workspace", str(root))
    assert assert_failure(ran.envelope(), "E_CHECK_FAILED", 7)["details"]["stage"] == "catalog_index"
    assert (root / "data/marts/catalog/_provenance.json").is_file()


@pytest.mark.parametrize(
    "argv",
    [
        ["stage"],
        ["stage", "nope"],
        ["stage", "catalog_index", "--input", "no-equals-sign"],
        ["stage", "catalog_index", "--input", "nope=x"],
        ["stage", "catalog_index", "--output", "=x"],
        ["stage", "catalog_index", "--emit-dvc-yaml"],
        ["stage", "--emit-dvc-yaml", "--output", "catalog=x"],
    ],
)
def test_stage_argument_errors_are_usage(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    fake_vcs: FakeVcsState,
    live_fingerprint: CodeFingerprint,
    argv: list[str],
) -> None:
    root = stage_workspace(tmp_path, live_fingerprint)
    ran = run_cli(capsys, *argv, "--json", "--workspace", str(root))
    envelope = ran.envelope()
    assert envelope["command"] == "stage"
    assert_failure(envelope, "E_USAGE", 2)


def test_stage_overrides_resolve_from_the_current_directory_and_provenance_keeps_the_default(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    fake_vcs: FakeVcsState,
    live_fingerprint: CodeFingerprint,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = stage_workspace(tmp_path, live_fingerprint)
    elsewhere = tmp_path / "elsewhere"
    write(elsewhere, "catalog.jsonl", "")
    monkeypatch.chdir(elsewhere)
    ran = run_cli(
        capsys,
        "stage",
        "catalog_index",
        "--input",
        "catalog.sapn_interval=catalog.jsonl",
        "--output",
        "catalog=out",
        "--json",
        "--workspace",
        str(root),
    )
    assert ran.code == 0, ran.out
    provenance = json.loads((elsewhere / "out" / "_provenance.json").read_text(encoding="utf-8"))
    paths = [item["path"] for item in provenance["inputs"]]
    assert "data/raw/sapn_interval/_catalog.jsonl" in paths
    assert str(tmp_path) not in json.dumps(provenance)
    assert not (root / "data/marts/catalog").exists()


def test_emit_dvc_yaml_prints_the_generated_file(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, fake_vcs: FakeVcsState, live_fingerprint: CodeFingerprint
) -> None:
    root = stage_workspace(tmp_path, live_fingerprint)
    golden = (Path(__file__).resolve().parents[2] / "fixtures" / "golden" / "dvc.yaml").read_text(encoding="utf-8")
    human = run_cli(capsys, "stage", "--emit-dvc-yaml", "--workspace", str(root))
    assert human.code == 0
    assert human.out == golden
    data = data_of(
        assert_success(
            run_cli(capsys, "stage", "--emit-dvc-yaml", "--json", "--workspace", str(root)).envelope(),
            "stage",
            "dvc_yaml_result",
        )
    )
    assert data == {"kind": "dvc_yaml_result", "schema_version": 1, "text": golden}


def test_stage_writes_start_and_end_records_to_the_repro_log(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    fake_vcs: FakeVcsState,
    live_fingerprint: CodeFingerprint,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = stage_workspace(tmp_path, live_fingerprint)
    log = tmp_path / "repro.jsonl"
    monkeypatch.setenv("ENERGATRON_REPRO_LOG", str(log))
    assert run_cli(capsys, "stage", "catalog_index", "--workspace", str(root)).code == 0
    assert repro_log(log) == start_end("catalog_index", 0)


@pytest.mark.parametrize(
    ("argv", "stage", "exit_code"),
    [
        (["stage", "catalog_index"], "catalog_index", 2),  # no workspace: fails before run_stage
        (["stage", "nope", "--json"], "nope", 2),  # unknown stage
        (["stage", "--workspace", "{root}", "catalog_index", "--bogus"], "catalog_index", 2),  # a parse error
        (["stage", "--input", "x=y", "catalog_index", "--workspace", "{root}"], "catalog_index", 2),
        (["stage", "catalog_index", "--workspace", "{bare}"], "catalog_index", 4),  # no params.yaml
    ],
)
def test_a_stage_failing_before_run_stage_still_writes_both_records(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    fake_vcs: FakeVcsState,
    live_fingerprint: CodeFingerprint,
    monkeypatch: pytest.MonkeyPatch,
    argv: list[str],
    stage: str,
    exit_code: int,
) -> None:
    root = stage_workspace(tmp_path, live_fingerprint)
    bare = make_workspace(tmp_path, name="bare").root
    log = tmp_path / "repro.jsonl"
    monkeypatch.setenv("ENERGATRON_REPRO_LOG", str(log))
    ran = run_cli(capsys, *(arg.format(root=root, bare=bare) for arg in argv))
    assert ran.code == exit_code
    assert repro_log(log) == start_end(stage, exit_code)


@pytest.mark.parametrize("argv", [["stage", "--emit-dvc-yaml"], ["version"], ["fingerprint", "--check"], ["stage"]])
def test_only_a_named_stage_writes_records(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    fake_vcs: FakeVcsState,
    live_fingerprint: CodeFingerprint,
    monkeypatch: pytest.MonkeyPatch,
    argv: list[str],
) -> None:
    root = stage_workspace(tmp_path, live_fingerprint)
    log = tmp_path / "repro.jsonl"
    monkeypatch.setenv("ENERGATRON_REPRO_LOG", str(log))
    run_cli(capsys, *argv, "--workspace", str(root))
    assert not log.exists()


def test_no_repro_log_without_the_variable(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, fake_vcs: FakeVcsState, live_fingerprint: CodeFingerprint
) -> None:
    root = stage_workspace(tmp_path, live_fingerprint)
    before = sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*"))
    assert run_cli(capsys, "stage", "catalog_index", "--workspace", str(root)).code == 0
    after = sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*"))
    assert [path for path in after if path not in before and "data/marts" not in path.as_posix()] == []


def test_an_interrupted_stage_writes_only_its_start_record(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    fake_vcs: FakeVcsState,
    live_fingerprint: CodeFingerprint,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stage stopped by Ctrl-C has no `end` record, so `DvcRunner` reports it as stopped, `exit_code: null`
    (D1 §4.5). typer turns the `KeyboardInterrupt` into exit code 130, which `main()` still returns."""
    root = stage_workspace(tmp_path, live_fingerprint)
    log = tmp_path / "repro.jsonl"
    monkeypatch.setenv("ENERGATRON_REPRO_LOG", str(log))

    def interrupt(*args: object, **kwargs: object) -> object:
        raise KeyboardInterrupt

    monkeypatch.setattr(stage_command, "run_stage", interrupt)
    ran = run_cli(capsys, "stage", "catalog_index", "--workspace", str(root))
    assert ran.code == 130
    assert ran.out == ""
    assert repro_log(log) == start_end("catalog_index", 0)[:1]
