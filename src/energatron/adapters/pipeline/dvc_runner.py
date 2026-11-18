"""`DvcRunner`: the `PipelineRunner` port over `dvc status` and `dvc repro` subprocesses (D1 §4.5).

- `status()` writes nothing. It parses `dvc status --json` and adds `stages.<name>.stage_sha256` to `changed_params`
  for each stage whose live code entry differs from the recorded one, or has none, because DVC can't see a code
  change until `.energatron/code_fingerprint.json` is refreshed.
- `repro()` refreshes that file, then runs `dvc repro` with the run cache on and `ENERGATRON_REPRO_LOG` naming a new
  empty file under `.energatron/tmp/`. `energatron stage` appends a `start` and an `end` record to it from `main()`.
  `ran` comes from those records; `restored` from the `dvc.lock` entries that changed without a run; `cached` is the
  rest of the stages in scope. DVC's human-readable output is only logged, never parsed.
- While `dvc repro` runs, a watcher thread follows the repro log and reports each stage's start and end, with its
  position among the stages in scope and its time, to the `Progress` it was given (owner request M.2). A stage that
  started and never ended is reported as stopped (`exit_code` None) once the repro is over.
- The child runs in the workspace root, stdin from `/dev/null`, stdout and stderr merged into one pipe whose lines go
  to the `energatron.adapters.pipeline` logger. It gets the parent's environment minus `ENERGATRON_JSON`, with
  `ENERGATRON_WORKSPACE` set to the root and the running interpreter's directory first on `PATH`, so `dvc.yaml`'s
  `energatron stage` commands run the same installed tool. `dvc` comes from that directory too, else from `PATH`.

The stages, their order and their deps and outs come from the workspace's `dvc.yaml`.
"""

import json
import logging
import os
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import yaml
from pydantic import ValidationError

from energatron.adapters.pipeline.fingerprint import read_fingerprint, refresh_fingerprint
from energatron.core.errors import (
    CheckFailedError,
    InvalidInputError,
    PipelineNotSyncedError,
    StageFailedError,
    SubprocessError,
    UsageError,
)
from energatron.core.models.pipeline import ReproLogEntry, ReproReport, StageStatus
from energatron.core.models.provenance import CodeFingerprint, stale_stages
from energatron.core.models.workspace import WorkspacePaths
from energatron.ports.progress import Progress
from energatron.services.progress import NullProgress, duration
from energatron.services.stages.dvc_yaml import CODE_FINGERPRINT_PATH

PIPELINE_LOGGER: Final = "energatron.adapters.pipeline"
REPRO_LOG_ENV: Final = "ENERGATRON_REPRO_LOG"
"""The variable naming the repro log; only the CLI adapter reads it, stage code never does (D1 §9.4)."""

_JSON_ENV: Final = "ENERGATRON_JSON"
_WORKSPACE_ENV: Final = "ENERGATRON_WORKSPACE"
_TAIL: Final = 50
_CHECK_EXIT: Final = 7
_POLL_SECONDS: Final = 0.2

_log = logging.getLogger(PIPELINE_LOGGER)


@dataclass(frozen=True)
class _Stage:
    """A stage as `dvc.yaml` declares it: its name, and its dep and out paths."""

    name: str
    deps: tuple[str, ...]
    outs: tuple[str, ...]


class DvcRunner:
    """Runs and inspects the workspace pipeline through DVC. `fingerprint` computes the live code fingerprint;
    `progress` hears what `repro()` is doing."""

    def __init__(
        self,
        paths: WorkspacePaths,
        *,
        fingerprint: Callable[[], CodeFingerprint],
        bin_dir: Path | None = None,
        progress: Progress | None = None,
    ) -> None:
        self.paths = paths
        self.fingerprint = fingerprint
        self.progress: Progress = progress if progress is not None else NullProgress()
        self._bin = bin_dir if bin_dir is not None else Path(sys.executable).parent
        beside = self._bin / "dvc"
        self._dvc = str(beside) if beside.is_file() else "dvc"

    # status

    def status(self) -> list[StageStatus]:
        """Stale stages only, in `dvc.yaml` order: DVC's view plus each stage's live code entry. Writes nothing."""
        stages = self._stages()
        reported = self._dvc_status()
        code_stale = self._code_stale()
        statuses: list[StageStatus] = []
        for stage in stages:
            deps: set[str] = set()
            outs: set[str] = set()
            params: set[str] = set()
            command = False
            for item in _items(reported.get(stage.name)):
                if item == "changed command":
                    command = True
                elif isinstance(item, Mapping):
                    for path, state in _mapping(item.get("changed deps")).items():
                        if isinstance(state, Mapping):
                            params.update(str(key) for key in state)
                        else:
                            deps.add(str(path))
                    outs.update(str(path) for path in _mapping(item.get("changed outs")))
            if stage.name in code_stale:
                params.add(f"stages.{stage.name}.stage_sha256")
            if deps or outs or params or command:
                statuses.append(
                    StageStatus(
                        name=stage.name,
                        changed_deps=sorted(deps),
                        changed_outs=sorted(outs),
                        changed_params=sorted(params),
                        changed_cmd=command,
                    )
                )
        return statuses

    def _code_stale(self) -> set[str]:
        """The live stages whose recorded code entry differs or is missing. An invalid file counts as missing."""
        live = self.fingerprint()
        try:
            recorded = read_fingerprint(self.paths.root / CODE_FINGERPRINT_PATH)
        except InvalidInputError:
            recorded = None
        return set(stale_stages(live, recorded)) & set(live.stages)

    def _dvc_status(self) -> Mapping[str, object]:
        try:
            result = subprocess.run(
                [self._dvc, "status", "--json"],
                cwd=self.paths.root,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                env=self._environment(),
                check=False,
            )
        except OSError:
            raise SubprocessError("`dvc status` couldn't be started.", command="dvc status") from None
        if result.returncode != 0:
            for line in result.stderr.splitlines()[-_TAIL:]:
                _log.error("%s", line)
            raise SubprocessError("`dvc status` failed.", command="dvc status", exit_code=result.returncode)
        text = result.stdout.strip()
        try:
            document = json.loads(text) if text else {}
        except ValueError:
            raise SubprocessError("`dvc status` printed no JSON.", command="dvc status") from None
        if not isinstance(document, dict):
            raise SubprocessError("`dvc status` printed no JSON object.", command="dvc status")
        return document

    # repro

    def repro(self, targets: Sequence[str] = ()) -> ReproReport:
        """Refresh the fingerprint file, run `dvc repro [targets]`, and report what ran, was restored or cached."""
        stages = self._stages()
        names = [stage.name for stage in stages]
        unknown = sorted(set(targets) - set(names))
        if unknown:
            raise UsageError(f"Unknown stage: {', '.join(unknown)}.", known=names)
        scope = _scope(stages, targets)
        in_scope = [name for name in names if name in scope]
        self.progress.step("Updating the code fingerprint")
        refresh_fingerprint(self.paths.root / CODE_FINGERPRINT_PATH, self.fingerprint())
        before = self._lock()
        tmp = self.paths.root / ".energatron" / "tmp"
        tmp.mkdir(parents=True, exist_ok=True)
        handle, name = tempfile.mkstemp(prefix="repro-", suffix=".jsonl", dir=tmp)
        os.close(handle)
        log = Path(name)
        self.progress.step(
            f"Running the pipeline: {len(in_scope)} stages; those whose inputs, params and code are unchanged are "
            "skipped"
        )
        began = time.monotonic()
        try:
            with _StageWatcher(log, in_scope, self.progress):
                exit_code = self._run_repro(targets, log)
            entries = _read_log(log)
            after = self._lock()
        finally:
            log.unlink(missing_ok=True)

        ran = [stage for stage in names if any(_succeeded(entry, stage) for entry in entries)]
        restored = [stage for stage in names if after.get(stage) != before.get(stage) and stage not in ran]
        cached = [stage for stage in names if stage in scope and stage not in ran and stage not in restored]
        if exit_code != 0:
            raise _failure(entries, exit_code, ran, restored)
        self.progress.step(
            f"Pipeline done in {duration(time.monotonic() - began)}: {len(ran)} ran, {len(restored)} restored, "
            f"{len(cached)} up to date"
        )
        return ReproReport(ran=ran, restored=restored, cached=cached)

    def _run_repro(self, targets: Sequence[str], log: Path) -> int:
        environment = {**self._environment(), REPRO_LOG_ENV: str(log)}
        tail: deque[str] = deque(maxlen=_TAIL)
        try:
            process = subprocess.Popen(
                [self._dvc, "repro", *targets],
                cwd=self.paths.root,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=environment,
                text=True,
                errors="replace",
            )
        except OSError:
            raise SubprocessError("`dvc repro` couldn't be started.", command="dvc repro") from None
        assert process.stdout is not None
        with process.stdout:
            for raw in process.stdout:
                line = raw.rstrip("\n")
                tail.append(line)
                _log.info("%s", line)
        exit_code = process.wait()
        if exit_code != 0:
            for line in tail:
                _log.error("%s", line)
        return exit_code

    def _environment(self) -> dict[str, str]:
        """The child's environment: the parent's minus `ENERGATRON_JSON`, the workspace set, the venv first on PATH."""
        environment = {key: value for key, value in os.environ.items() if key != _JSON_ENV}
        environment[_WORKSPACE_ENV] = str(self.paths.root)
        inherited = environment.get("PATH")
        environment["PATH"] = str(self._bin) if not inherited else f"{self._bin}{os.pathsep}{inherited}"
        return environment

    # dvc.yaml and dvc.lock

    def _stages(self) -> list[_Stage]:
        document = _read_yaml(self.paths.root / "dvc.yaml", "dvc.yaml")
        if document is None:
            raise PipelineNotSyncedError("dvc.yaml is missing: run `energatron pipeline sync`.", drift=["dvc.yaml"])
        return [
            _Stage(name=str(name), deps=_paths(entry, "deps"), outs=_paths(entry, "outs"))
            for name, entry in _mapping(document.get("stages")).items()
        ]

    def _lock(self) -> dict[str, object]:
        document = _read_yaml(self.paths.root / "dvc.lock", "dvc.lock")
        return {} if document is None else dict(_mapping(document.get("stages")))


class _StageWatcher:
    """Follows the repro log from a thread while `dvc repro` runs, and reports each stage's start and end.

    Only complete lines count; one that isn't a valid record is skipped here (`_read_log` warns about it later). On
    exit the thread stops, the log is read one last time, and a stage still running is reported as stopped.
    """

    def __init__(self, log: Path, in_scope: Sequence[str], progress: Progress) -> None:
        self._log = log
        self._positions = {name: index for index, name in enumerate(in_scope, start=1)}
        self._progress = progress
        self._offset = 0
        self._partial = b""
        self._running: dict[str, float] = {}
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._follow, name="repro-progress", daemon=True)

    def __enter__(self) -> _StageWatcher:
        self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._stop.set()
        self._thread.join()
        self._poll()
        for name, began in list(self._running.items()):
            self._finished(name, None, began)

    def _follow(self) -> None:
        while not self._stop.wait(_POLL_SECONDS):
            self._poll()

    def _poll(self) -> None:
        try:
            with self._log.open("rb") as handle:
                handle.seek(self._offset)
                chunk = handle.read()
        except OSError:
            return
        self._offset += len(chunk)
        *lines, self._partial = (self._partial + chunk).split(b"\n")
        for line in lines:
            try:
                entry = ReproLogEntry.from_document(json.loads(line))
            except ValueError, ValidationError, InvalidInputError, TypeError:
                continue
            if entry.stage not in self._positions:
                continue
            if entry.event == "start":
                self._running[entry.stage] = time.monotonic()
                self._progress.stage_started(entry.stage, self._positions[entry.stage], len(self._positions))
            elif entry.stage in self._running:
                self._finished(entry.stage, entry.exit_code, self._running[entry.stage])

    def _finished(self, name: str, exit_code: int | None, began: float) -> None:
        del self._running[name]
        self._progress.stage_finished(
            name,
            self._positions[name],
            len(self._positions),
            exit_code=exit_code,
            seconds=time.monotonic() - began,
        )


def _read_yaml(path: Path, name: str) -> Mapping[str, object] | None:
    """A YAML mapping, or None when the file is missing. Anything else → `E_INVALID_INPUT`."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError:
        raise InvalidInputError(f"{name} isn't valid YAML.", file=name) from None
    if document is None:
        return {}
    if not isinstance(document, dict):
        raise InvalidInputError(f"{name} isn't a mapping.", file=name)
    return document


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _items(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def _paths(entry: object, key: str) -> tuple[str, ...]:
    """A stage's dep or out paths; an entry is a path, or a mapping from the path to its options."""
    found: list[str] = []
    for item in _items(_mapping(entry).get(key)):
        if isinstance(item, str):
            found.append(item)
        elif isinstance(item, Mapping):
            found.extend(str(path) for path in item)
    return tuple(found)


def _overlaps(dep: str, out: str) -> bool:
    return dep == out or dep.startswith(f"{out}/") or out.startswith(f"{dep}/")


def _scope(stages: Sequence[_Stage], targets: Sequence[str]) -> set[str]:
    """Every stage when there are no targets; otherwise the targets and everything upstream of them."""
    if not targets:
        return {stage.name for stage in stages}
    by_name = {stage.name: stage for stage in stages}
    scope: set[str] = set()
    pending = list(targets)
    while pending:
        name = pending.pop()
        if name in scope:
            continue
        scope.add(name)
        deps = by_name[name].deps
        pending.extend(other.name for other in stages if any(_overlaps(dep, out) for dep in deps for out in other.outs))
    return scope


def _read_log(path: Path) -> list[ReproLogEntry]:
    """The log's records, in order. A line that isn't a valid record is logged and skipped."""
    entries: list[ReproLogEntry] = []
    for number, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            document = json.loads(line)
            if not isinstance(document, dict):
                raise ValueError("not an object")
            entries.append(ReproLogEntry.from_document(document))
        except ValueError, ValidationError, InvalidInputError:
            _log.warning("repro log line %d isn't a valid record; skipped", number)
    return entries


def _succeeded(entry: ReproLogEntry, stage: str) -> bool:
    return entry.stage == stage and entry.event == "end" and entry.exit_code == 0


def _failure(
    entries: Sequence[ReproLogEntry], exit_code: int, ran: list[str], restored: list[str]
) -> SubprocessError | StageFailedError | CheckFailedError:
    """The error for a failed `dvc repro`: the failed stage's, or DVC's own when the log names no failed stage."""
    ended: dict[str, int | None] = {}
    started: list[str] = []
    for entry in entries:
        if entry.event == "start":
            started.append(entry.stage)
            ended.pop(entry.stage, None)
        else:
            ended[entry.stage] = entry.exit_code
    failed = next(
        (stage for stage in started if stage not in ended or ended[stage] != 0),
        None,
    )
    if failed is None:
        return SubprocessError(
            f"`dvc repro` failed with exit code {exit_code}.",
            command="dvc repro",
            stage=None,
            exit_code=exit_code,
            ran=list(ran),
            restored=list(restored),
        )
    stage_exit = ended.get(failed)
    details = {"stage": failed, "exit_code": stage_exit, "ran": list(ran), "restored": list(restored)}
    if stage_exit == _CHECK_EXIT:
        return CheckFailedError(f"Stage {failed} failed a check.", **details)
    if stage_exit is None:
        return StageFailedError(f"Stage {failed} was stopped before it finished.", **details)
    return StageFailedError(f"Stage {failed} failed with exit code {stage_exit}.", **details)
