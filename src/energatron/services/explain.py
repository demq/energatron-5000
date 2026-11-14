"""`energatron explain` (P9 spec §3.4): what the current recommendation says, how sure it is and where it came from.

Read-only. The recommendation (`recommendation.json`), the newest run manifest naming it, the outcomes summary when a
decision exists, and how far the workspace has moved since that run. The CLI passes the stages whose code changed
(services can't compute the live fingerprint, D1 §11.4). Older recommendations live in the workspace's git history,
not on disk, so a `rec_id` other than the current one → `E_INVALID_INPUT` (`reason: "not_current"`).
"""

from datetime import date, datetime
from typing import ClassVar, Final, Literal

from energatron.core.errors import InvalidInputError, UsageError
from energatron.core.models._common import FrozenModel
from energatron.core.models.recommend import (
    OUTCOMES_SUMMARY_FILENAME,
    CurrentPair,
    Fuel,
    OutcomesSummary,
    PricedTariff,
    RecommendationUsage,
    RecommendedOption,
    SensitivityVerdict,
)
from energatron.services.context import ServiceContext, ServiceResult
from energatron.services.status import RECOMMENDATION, read_manifests, read_recommendation
from energatron.services.usage_summary import read_document

DEFAULT_OPTIONS: Final = 3
MAX_OPTIONS: Final = 20
OUTCOMES: Final = f"data/reports/outcomes/{OUTCOMES_SUMMARY_FILENAME}"
TRACE: Final[dict[str, list[str]]] = {
    "recommendation": [RECOMMENDATION],
    "options": ["data/marts/costs/bundles"],
    "electricity": ["data/marts/costs/elec"],
    "gas": ["data/marts/costs/gas"],
    "usage": ["data/marts/usage_profile", "data/marts/gas_profile"],
    "offers": ["data/marts/offers", "data/marts/offers_gas"],
}
"""Where each kind of figure comes from (the paths `explain-recommendation` lists)."""


class ChosenPlan(FrozenModel):
    offer_snapshot_id: str
    brand: str
    display_name: str
    status: str
    approximations: tuple[str, ...]
    notes: tuple[str, ...]


class RunInfo(FrozenModel):
    run_id: str
    draft: bool
    finished_at: datetime
    workspace_head: str
    tool_commit: str | None
    tool_dirty: bool


class InputsInfo(FrozenModel):
    dataset_count: int
    offer_snapshot_count: int
    params_sha256: str
    stage_sha256: str


class Behind(FrozenModel):
    datasets_not_in_run: int
    code_stale: list[str]


class ExplainResult(ServiceResult):
    """`explain_result` v1 (P9 spec §3.4)."""

    KIND: ClassVar[str] = "explain_result"
    SCHEMA_VERSION: ClassVar[int] = 1
    kind: Literal["explain_result"] = "explain_result"
    schema_version: Literal[1] = 1

    recommendation_id: str | None
    as_of: date | None
    recheck_by: date | None
    action: str | None
    robust: bool | None
    usage: RecommendationUsage | None
    current: CurrentPair | None
    options: list[RecommendedOption]
    sensitivity: list[SensitivityVerdict]
    checks: dict[str, bool]
    chosen: dict[Fuel, ChosenPlan]
    run: RunInfo | None
    inputs: InputsInfo | None
    behind: Behind
    outcomes: OutcomesSummary | None
    trace: dict[str, list[str]]
    missing: list[str]


def explain(
    ctx: ServiceContext, rec_id: str | None = None, *, options: int = DEFAULT_OPTIONS, code_stale: list[str]
) -> ExplainResult:
    """The current recommendation explained; `rec_id`, when given, must be its ID."""
    if not 1 <= options <= MAX_OPTIONS:
        raise UsageError(f"--options must be between 1 and {MAX_OPTIONS}.", field="options")
    rec = read_recommendation(ctx)
    current_id = rec.recommendation_id if rec else None
    if rec_id is not None and rec_id != current_id:
        raise InvalidInputError(
            "That isn't the current recommendation; older ones are in the workspace's git history.",
            field="recommendation_id",
            reason="not_current",
        )
    datasets = {record.dataset_id for record in ctx.catalog.records()}
    manifest = next((run for run in reversed(read_manifests(ctx)) if rec and run.recommendation_id == current_id), None)
    behind = Behind(
        datasets_not_in_run=len(datasets - set(manifest.datasets)) if manifest else len(datasets),
        code_stale=sorted(code_stale),
    )
    if rec is None:
        return ExplainResult(
            recommendation_id=None, as_of=None, recheck_by=None, action=None, robust=None, usage=None, current=None,
            options=[], sensitivity=[], checks={}, chosen={}, run=None, inputs=None, behind=behind, outcomes=None,
            trace=TRACE, missing=[RECOMMENDATION],
        )  # fmt: skip
    chosen: dict[Fuel, ChosenPlan] = {}
    if rec.options:
        top = rec.options[0]
        pairs: tuple[tuple[Fuel, PricedTariff | None], ...] = (("electricity", top.electricity), ("gas", top.gas))
        for fuel, plan in pairs:
            if plan is not None:
                chosen[fuel] = ChosenPlan(
                    offer_snapshot_id=plan.offer_snapshot_id,
                    brand=plan.brand,
                    display_name=plan.display_name,
                    status=plan.status,
                    approximations=plan.approximations,
                    notes=plan.notes,
                )
    outcomes = None
    if (ctx.paths.root / OUTCOMES).exists():
        summary = read_document(ctx.paths.root, OUTCOMES, OutcomesSummary)
        outcomes = summary if summary.decisions else None
    return ExplainResult(
        recommendation_id=rec.recommendation_id,
        as_of=rec.as_of,
        recheck_by=rec.recheck_by,
        action=rec.action,
        robust=rec.robust,
        usage=rec.usage,
        current=rec.current,
        options=list(rec.options[:options]),
        sensitivity=list(rec.sensitivity),
        checks=rec.checks,
        chosen=chosen,
        run=None
        if manifest is None
        else RunInfo(
            run_id=manifest.run_id,
            draft=manifest.draft,
            finished_at=manifest.finished_at,
            workspace_head=manifest.workspace_head,
            tool_commit=manifest.tool.tool_commit,
            tool_dirty=manifest.tool.tool_dirty,
        ),
        inputs=InputsInfo(
            dataset_count=len(rec.inputs.dataset_ids),
            offer_snapshot_count=len(rec.inputs.offer_snapshot_ids),
            params_sha256=rec.inputs.params_sha256,
            stage_sha256=rec.inputs.stage_sha256,
        ),
        behind=behind,
        outcomes=outcomes,
        trace=TRACE,
        missing=[],
    )
