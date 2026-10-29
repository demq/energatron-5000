"""`verify`: the safety net behind per-stage tracking, and the raw zone's integrity check (D1 §11.4).

On a current pipeline, every catalogued blob is re-hashed against its record, and every stage is run again, in
`STAGES` order and in this one process, into a scratch directory; each output must match the workspace's file for
file, `_provenance.json` included. Any difference fails with `E_CHECK_FAILED`, `details.reason: "not_reproducible"`.

Services can't compute code entries, so the caller passes the live fingerprint and the recorded one (D1 §11.4). The
caller holds the workspace lock and has removed a leftover scratch directory; this module deletes `scratch` when it
is done, whatever happens. It imports the registry, which no stage's module set contains (D1 §11.1).
"""

import logging
import shutil
from collections.abc import Mapping
from pathlib import Path
from typing import ClassVar, Final, Literal

from pydantic import Field

from energatron.core.canonical import sha256_hex
from energatron.core.errors import CheckFailedError, FingerprintStaleError, PipelineStaleError
from energatron.core.models.provenance import CodeFingerprint, stale_stages
from energatron.services.context import ServiceContext, ServiceResult
from energatron.services.stages.registry import STAGES
from energatron.services.stages.runner import StageSpec, prepare_stage, run_stage

_log = logging.getLogger(__name__)

_IGNORED: Final = frozenset({".DS_Store"})


class VerifyResult(ServiceResult):
    """`verify_result` v1 (D1 §11.4): the stages verified, in `STAGES` order, and the number of blobs checked."""

    KIND: ClassVar[str] = "verify_result"
    SCHEMA_VERSION: ClassVar[int] = 1
    kind: Literal["verify_result"] = "verify_result"
    schema_version: Literal[1] = 1

    stages: list[str]
    datasets: int = Field(ge=0)


def verify(
    ctx: ServiceContext, *, live: CodeFingerprint, recorded: CodeFingerprint | None, scratch: Path
) -> VerifyResult:
    """Check that the raw zone is intact and every stage reproduces its workspace outputs (D1 §11.4).

    - A stale code entry → `E_FINGERPRINT_STALE`; a stale stage → `E_PIPELINE_STALE`; both with `details.stages`.
    - Every blob and every stage is checked before failing, so one run lists every difference.
    - A stage whose own check fails counts as differing; any other error propagates.
    """
    stale = stale_stages(live, recorded)
    if stale or recorded is None:
        raise FingerprintStaleError(
            "The code fingerprint is stale: run `energatron fingerprint` and `dvc repro` first.", stages=stale
        )
    pipeline = [status.name for status in ctx.runner.status()]
    if pipeline:
        raise PipelineStaleError("The pipeline is stale: run `dvc repro` first.", stages=pipeline)

    try:
        datasets, checked = _check_blobs(ctx)
        stages = [name for name, spec in STAGES.items() if not _reproduces(ctx, spec, live, recorded, scratch)]
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    _log.info("verify: %d stages, %d blobs, %d differing", len(STAGES), checked, len(stages) + len(datasets))
    if stages or datasets:
        raise CheckFailedError(
            f"The workspace doesn't reproduce: {len(stages)} differing stage(s), {len(datasets)} differing blob(s).",
            reason="not_reproducible",
            stages=list(stages),
            datasets=list(datasets),
        )
    return VerifyResult(stages=list(STAGES), datasets=checked)


def _check_blobs(ctx: ServiceContext) -> tuple[list[str], int]:
    """The `dataset_id`s whose stored blob is missing or differs from its record, sorted; and the count checked."""
    differing: list[str] = []
    checked = 0
    for record in ctx.catalog.records():
        checked += 1
        if not ctx.blobs.exists(record.stored_path):
            differing.append(record.dataset_id)
            continue
        stored = ctx.blobs.stat(record.stored_path)
        if stored.sha256 != record.sha256 or stored.bytes != record.bytes:
            differing.append(record.dataset_id)
    return sorted(differing), checked


def _reproduces(
    ctx: ServiceContext, spec: StageSpec, live: CodeFingerprint, recorded: CodeFingerprint, scratch: Path
) -> bool:
    """Run `spec` on the workspace's inputs into `scratch/<stage>/<output>`, then compare every output."""
    root = ctx.paths.root
    targets = {name: scratch / spec.name / name for name in spec.outputs}
    prepared = prepare_stage(spec, root=root, params_file=root / "params.yaml", output_overrides=targets)
    try:
        run_stage(
            spec,
            prepared.inputs,
            prepared.params,
            prepared.outputs,
            code=live.stages[spec.name],
            recorded_code=recorded.stages[spec.name].stage_sha256,
        )
    except CheckFailedError:
        _log.info("verify: stage %s failed a check", spec.name)
        return False
    same = all(_hashes(prepared.outputs[name]) == _hashes(root / path) for name, path in spec.outputs.items())
    if not same:
        _log.info("verify: stage %s doesn't reproduce", spec.name)
    return same


def _hashes(directory: Path) -> Mapping[str, str] | None:
    """Every file under `directory` (relative POSIX path → sha256); None when it isn't a directory."""
    if not directory.is_dir():
        return None
    return {
        path.relative_to(directory).as_posix(): sha256_hex(path.read_bytes())
        for path in sorted(directory.rglob("*"))
        if path.is_file() and path.name not in _IGNORED
    }
