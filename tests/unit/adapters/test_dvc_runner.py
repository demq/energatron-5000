"""`DvcRunner` against scratch DVC repos (D1 §4.5, §13; §11.5's three checks).

Most tests use fake stages: a small script that follows the repro-log protocol `energatron stage` follows, so a test
can make a stage fail, exit 7 or be killed. Its output holds its input and its own `stage_sha256`, as provenance
does, so a code change reaches the downstream stage. The last tests run the real `energatron stage catalog_index`.
Nothing here touches the real workspace.
"""

import json
import logging
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from energatron.adapters import wiring
from energatron.adapters.pipeline.dvc_runner import PIPELINE_LOGGER, DvcRunner
from energatron.core.canonical import content_hash, sha256_hex
from energatron.core.errors import CheckFailedError, EnergatronError, StageFailedError, SubprocessError
from energatron.core.models.catalog import SourceType
from energatron.core.models.pipeline import ReproReport, StageStatus
from energatron.core.models.provenance import CodeFingerprint, StageCode
from energatron.core.models.workspace import WorkspacePaths
from energatron.services.stages.dvc_yaml import CODE_FINGERPRINT_PATH, emit_dvc_yaml
from energatron.services.stages.registry import STAGES
from tests.fixtures.params_text import params_v2_text
from tests.unit.adapters._workspace import make_workspace, write

DVC = str(Path(sys.executable).parent / "dvc")
FP = CODE_FINGERPRINT_PATH

FAKE_STAGE = """\
import json, os, signal, sys
from pathlib import Path

name, source, out = sys.argv[1:4]
log = os.environ.get("ENERGATRON_REPRO_LOG")


def record(event, code=None):
    if log:
        entry = {"kind": "repro_log_entry", "schema_version": 1, "stage": name, "event": event, "exit_code": code}
        with open(log, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, sort_keys=True, separators=(",", ":")) + "\\n")


record("start")
print(f"fake stage {name} ran", flush=True)
control = Path("control") / name
action = control.read_text().split() if control.is_file() else []
if action[:1] == ["kill"]:
    os.kill(os.getpid(), signal.SIGKILL)
code = int(action[1]) if action[:1] == ["exit"] else 0
if code == 0:
    recorded = json.loads(Path(".energatron/code_fingerprint.json").read_text())["stages"][name]["stage_sha256"]
    source_path = Path(source)
    text = source_path.read_text() if source_path.is_file() else (source_path / "data.txt").read_text()
    Path(out).mkdir(parents=True, exist_ok=True)
    (Path(out) / "data.txt").write_text(text + recorded + "\\n")
record("end", code)
sys.exit(code)
"""

DVC_YAML = f"""\
stages:
  up:
    cmd: python fake_stage.py up in/up.txt out/up
    deps:
    - in/up.txt
    params:
    - p.v
    - {FP}:
      - stages.up.stage_sha256
    outs:
    - out/up
  down:
    cmd: python fake_stage.py down out/up out/down
    deps:
    - out/up
    params:
    - {FP}:
      - stages.down.stage_sha256
    outs:
    - out/down
  side:
    cmd: python fake_stage.py side in/side.txt out/side
    deps:
    - in/side.txt
    params:
    - {FP}:
      - stages.side.stage_sha256
    outs:
    - out/side
"""

ORDER = ("up", "down", "side")


@pytest.fixture(autouse=True)
def isolated_dvc(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    """DVC's global, system and site configuration in scratch folders; no analytics."""
    home = tmp_path_factory.mktemp("dvc-config")
    monkeypatch.setenv("DVC_NO_ANALYTICS", "1")
    for name in ("DVC_GLOBAL_CONFIG_DIR", "DVC_SYSTEM_CONFIG_DIR", "DVC_SITE_CACHE_DIR"):
        folder = home / name.lower()
        folder.mkdir()
        monkeypatch.setenv(name, str(folder))


def fake_code(label: str) -> StageCode:
    source = sha256_hex(label.encode())
    digest = content_hash({"source_sha256": source, "packages": {}, "python": "3.14.0"})
    return StageCode(stage_sha256=digest, source_sha256=source, python="3.14.0", packages={}, files=["__init__.py"])


def dvc_init(root: Path) -> None:
    subprocess.run([DVC, "init", "--no-scm", "--quiet"], cwd=root, check=True, capture_output=True)


def dvc_status(root: Path) -> dict[str, object]:
    out = subprocess.run([DVC, "status", "--json"], cwd=root, check=True, capture_output=True, text=True).stdout
    return json.loads(out)


@dataclass
class Fake:
    """A scratch DVC repo with three fake stages: `up` → `down`, and `side` on its own."""

    paths: WorkspacePaths
    labels: dict[str, str] = field(default_factory=lambda: dict.fromkeys(ORDER, "v1"))

    @property
    def root(self) -> Path:
        return self.paths.root

    def live(self) -> CodeFingerprint:
        return CodeFingerprint(
            energatron_version="0.1.0",
            install="index",
            tool_commit=None,
            tool_dirty=False,
            stages={name: fake_code(self.labels[name]) for name in ORDER},
        )

    def runner(self) -> DvcRunner:
        return DvcRunner(self.paths, fingerprint=self.live)

    def write(self, relative: str, text: str) -> None:
        write(self.root, relative, text)

    def control(self, stage: str, action: str) -> None:
        self.write(f"control/{stage}", action)

    def fingerprint_text(self) -> str:
        return (self.root / FP).read_text(encoding="utf-8")

    def leftovers(self) -> list[Path]:
        tmp = self.root / ".energatron" / "tmp"
        return sorted(tmp.iterdir()) if tmp.exists() else []


@pytest.fixture
def fake(tmp_path: Path) -> Fake:
    pipeline = Fake(make_workspace(tmp_path, private=None))
    dvc_init(pipeline.root)
    pipeline.write("fake_stage.py", FAKE_STAGE)
    pipeline.write("dvc.yaml", DVC_YAML)
    pipeline.write("params.yaml", "p:\n  v: 1\n")
    pipeline.write("in/up.txt", "up\n")
    pipeline.write("in/side.txt", "side\n")
    return pipeline


@pytest.fixture
def built(fake: Fake) -> Fake:
    """The fake pipeline after a first repro."""
    assert fake.runner().repro() == ReproReport(ran=list(ORDER), restored=[], cached=[])
    return fake


def report(ran: list[str] | None = None, restored: list[str] | None = None, cached: list[str] | None = None):
    return ReproReport(ran=ran or [], restored=restored or [], cached=cached or [])


# repro: ran, restored, cached


def test_the_first_repro_refreshes_the_fingerprint_then_runs_every_stage(fake: Fake) -> None:
    assert not (fake.root / FP).exists()
    assert fake.runner().repro() == report(ran=["up", "down", "side"])
    assert CodeFingerprint.from_document(json.loads(fake.fingerprint_text())) == fake.live()
    assert fake.leftovers() == []
    assert dvc_status(fake.root) == {}


def test_up_to_date_stages_are_cached(built: Fake) -> None:
    assert built.runner().repro() == report(cached=["up", "down", "side"])


def test_a_downstream_stage_that_reran_because_its_upstream_ran_is_in_ran(built: Fake) -> None:
    built.write("in/up.txt", "up, changed\n")
    assert built.runner().repro() == report(ran=["up", "down"], cached=["side"])


def test_a_param_revert_is_restored_from_the_run_cache(built: Fake) -> None:
    """The fake's output doesn't hold its params, so `down` sees the same dep and stays cached."""
    built.write("params.yaml", "p:\n  v: 2\n")
    assert built.runner().repro() == report(ran=["up"], cached=["down", "side"])
    built.write("params.yaml", "p:\n  v: 1\n")
    assert built.runner().repro() == report(restored=["up"], cached=["down", "side"])
    assert dvc_status(built.root) == {}


def test_a_code_entry_returning_to_an_earlier_value_is_restored_from_the_run_cache(built: Fake) -> None:
    """D1 §11.5 check 2."""
    built.labels["up"] = "v2"
    assert built.runner().repro() == report(ran=["up", "down"], cached=["side"])
    built.labels["up"] = "v1"
    assert built.runner().repro() == report(restored=["up", "down"], cached=["side"])


def test_a_changed_code_entry_makes_only_that_stage_and_its_downstream_stale(built: Fake) -> None:
    """D1 §11.5 check 1: DVC reads the nested key of the JSON fingerprint file as a param."""
    built.labels["side"] = "v2"
    assert built.runner().repro() == report(ran=["side"], cached=["up", "down"])
    built.labels["up"] = "v2"
    assert built.runner().repro() == report(ran=["up", "down"], cached=["side"])


def test_repro_refreshes_a_stale_fingerprint_file_first(built: Fake) -> None:
    built.labels["down"] = "v2"
    before = built.fingerprint_text()
    built.runner().repro()
    assert built.fingerprint_text() != before
    assert CodeFingerprint.from_document(json.loads(built.fingerprint_text())) == built.live()


def test_targets_limit_the_repro_to_them_and_their_upstream(built: Fake) -> None:
    built.write("in/side.txt", "side, changed\n")
    assert built.runner().repro(["down"]) == report(cached=["up", "down"])
    assert [status.name for status in built.runner().status()] == ["side"]
    assert built.runner().repro(["side"]) == report(ran=["side"])


def test_the_log_reaches_the_stage_through_dvc_and_is_removed(built: Fake) -> None:
    """D1 §11.5 check 3: `ran` comes only from the log, so a stage in `ran` got `ENERGATRON_REPRO_LOG`."""
    built.write("in/side.txt", "side, changed\n")
    assert built.runner().repro().ran == ["side"]
    assert built.leftovers() == []


# status


def test_status_lists_nothing_when_everything_is_current(built: Fake) -> None:
    assert built.runner().status() == []


def test_status_reports_deps_params_and_outs_of_the_changed_stage_only(built: Fake) -> None:
    built.write("in/up.txt", "up, changed\n")
    built.write("params.yaml", "p:\n  v: 2\n")
    for path in (built.root / "out" / "side").iterdir():
        path.unlink()
    (built.root / "out" / "side").rmdir()
    assert built.runner().status() == [
        StageStatus(name="up", changed_deps=["in/up.txt"], changed_outs=[], changed_params=["p.v"], changed_cmd=False),
        StageStatus(name="side", changed_deps=[], changed_outs=["out/side"], changed_params=[], changed_cmd=False),
    ]


def test_status_reports_a_changed_code_entry_before_the_file_is_refreshed(built: Fake) -> None:
    built.labels["side"] = "v2"
    before = built.fingerprint_text()
    assert dvc_status(built.root) == {}  # DVC alone can't see it
    assert built.runner().status() == [
        StageStatus(
            name="side",
            changed_deps=[],
            changed_outs=[],
            changed_params=["stages.side.stage_sha256"],
            changed_cmd=False,
        )
    ]
    assert built.fingerprint_text() == before
    assert built.leftovers() == []


def test_status_reports_every_stage_when_no_fingerprint_is_recorded(fake: Fake) -> None:
    names = [status.name for status in fake.runner().status()]
    assert names == list(ORDER)
    assert not (fake.root / FP).exists()


def test_status_reports_a_changed_command(built: Fake) -> None:
    built.write("dvc.yaml", DVC_YAML.replace("fake_stage.py side in/side.txt", "fake_stage.py  side in/side.txt"))
    statuses = built.runner().status()
    assert [(status.name, status.changed_cmd) for status in statuses] == [("side", True)]


# Failures


def failure(call: Callable[[], object]) -> EnergatronError:
    with pytest.raises(EnergatronError) as caught:
        call()
    return caught.value


def test_a_stage_failing_a_check_is_check_failed(built: Fake) -> None:
    built.write("in/up.txt", "up, changed\n")
    built.control("up", "exit 7")
    error = failure(built.runner().repro)
    assert isinstance(error, CheckFailedError)
    assert error.details == {"stage": "up", "exit_code": 7, "ran": [], "restored": []}
    assert built.leftovers() == []


def test_another_stage_failure_is_stage_failed_with_its_exit_code(
    built: Fake, caplog: pytest.LogCaptureFixture
) -> None:
    built.write("in/up.txt", "up, changed\n")
    built.write("in/side.txt", "side, changed\n")
    built.control("side", "exit 3")
    with caplog.at_level(logging.INFO, logger=PIPELINE_LOGGER):
        error = failure(built.runner().repro)
    assert isinstance(error, StageFailedError)
    assert error.details == {"stage": "side", "exit_code": 3, "ran": ["up", "down"], "restored": []}
    errors = [record.getMessage() for record in caplog.records if record.levelno == logging.ERROR]
    assert "fake stage side ran" in errors
    assert built.leftovers() == []


def test_a_killed_stage_is_stage_failed_with_no_exit_code(built: Fake) -> None:
    built.write("in/up.txt", "up, changed\n")
    built.control("up", "kill")
    error = failure(built.runner().repro)
    assert isinstance(error, StageFailedError)
    assert error.details == {"stage": "up", "exit_code": None, "ran": [], "restored": []}
    assert built.leftovers() == []


def test_a_dvc_error_with_no_failed_stage_is_a_subprocess_error(built: Fake) -> None:
    built.write("dvc.yaml", DVC_YAML.replace("- in/side.txt", "- in/missing.txt"))
    error = failure(built.runner().repro)
    assert isinstance(error, SubprocessError)
    assert isinstance(error.details["exit_code"], int)
    assert error.details["exit_code"] != 0
    assert error.details["ran"] == []
    assert error.details["restored"] == []
    assert error.details["stage"] is None
    assert built.leftovers() == []


# The real `energatron stage`


@pytest.fixture
def real(tmp_path: Path) -> WorkspacePaths:
    """A scratch workspace holding the real pipeline (every registered stage) over empty catalogs."""
    paths = make_workspace(tmp_path, private=None)
    dvc_init(paths.root)
    write(paths.root, "dvc.yaml", emit_dvc_yaml(STAGES))
    write(paths.root, "params.yaml", params_v2_text())  # DVC reads the declared keys from the file itself
    for source in SourceType:
        write(paths.root, f"data/raw/{source.value}/_catalog.jsonl", "")
    return paths


def test_the_venv_energatron_runs_whatever_path_the_caller_has_and_prints_nothing(
    real: WorkspacePaths,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setenv("ENERGATRON_JSON", "1")
    runner = DvcRunner(real, fingerprint=wiring.code_fingerprint)
    with caplog.at_level(logging.INFO, logger=PIPELINE_LOGGER):
        assert runner.repro() == report(ran=["catalog_index", "parse_nem12", "merge_interval", "validate_meter"])
    captured = capfd.readouterr()
    assert captured.out == ""
    messages = [record.getMessage() for record in caplog.records if record.name == PIPELINE_LOGGER]
    assert any("catalog_index" in message for message in messages), "the child's output reaches the logger"
    assert not any("cli_result" in message for message in messages), "no child prints an envelope"
    assert (real.root / "data" / "marts" / "catalog" / "_provenance.json").is_file()
    assert runner.status() == []


def test_a_stage_failing_before_run_stage_is_stage_failed(real: WorkspacePaths) -> None:
    (real.root / "params.yaml").unlink()
    error = failure(DvcRunner(real, fingerprint=wiring.code_fingerprint).repro)
    assert isinstance(error, StageFailedError)
    assert error.details == {"stage": "catalog_index", "exit_code": 4, "ran": [], "restored": []}
    assert not (real.root / ".energatron" / "tmp").exists() or list((real.root / ".energatron" / "tmp").iterdir()) == []
