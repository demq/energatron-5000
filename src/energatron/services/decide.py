"""`energatron decide` (P6 spec §9): record that the household acts on a recommendation.

`decide(ctx, rec_id, …)` checks that the recommendation is the current one on disk and that a non-draft run manifest
names it, that each chosen plan is priced in it (its projection is copied into the decision), and that every evidence
ID is landed; then writes `data/decisions/<DEC-id>.json`, tracks `data/decisions` and commits its pointer. It never
touches the private config: the result reminds the owner to set `current_plans` once the switch happens. The caller
holds the workspace lock.
"""

import json
import logging
from datetime import date
from typing import ClassVar, Final, Literal
from zoneinfo import ZoneInfo

from energatron.core.errors import (
    InvalidInputError,
    ResultWarning,
    SchemaTooNewError,
    UsageError,
    WarningCode,
)
from energatron.core.ids import decision_id, parse_decision_id, parse_rec_id
from energatron.core.models.recommend import (
    RECOMMENDATION_FILENAME,
    Choice,
    Decision,
    Fuel,
    PricedTariff,
    Recommendation,
    RunManifest,
)
from energatron.services.context import ServiceContext, ServiceResult
from energatron.services.files import write_json

_log = logging.getLogger(__name__)

DECISIONS_DIR: Final = "data/decisions"
RUNS_DIR: Final = "data/runs"
RECOMMENDATION: Final = f"data/reports/recommendations/{RECOMMENDATION_FILENAME}"
REMINDER: Final = (
    "Once the switch happens, set current_plans in private/config.toml and add the new plans to backtest.tariffs / "
    "backtest.gas_tariffs from the switch date."
)


class DecideResult(ServiceResult):
    """`decide_result` v1 (P6 spec §9.2)."""

    KIND: ClassVar[str] = "decide_result"
    SCHEMA_VERSION: ClassVar[int] = 1
    kind: Literal["decide_result"] = "decide_result"
    schema_version: Literal[1] = 1

    decision_id: str
    recommendation_id: str
    run_id: str
    path: str
    commit: str | None
    dry_run: bool


def decide(
    ctx: ServiceContext,
    rec_id: str,
    *,
    switch_date: date,
    electricity: str | None = None,
    gas: str | None = None,
    evidence: tuple[str, ...] = (),
    note: str | None = None,
    dry_run: bool = False,
) -> DecideResult:
    """Validate and record one decision (P6 spec §9.1)."""
    try:
        parse_rec_id(rec_id)
    except ValueError:
        raise UsageError("That isn't a recommendation ID (REC-YYYYMMDD-xxxxxxxx).", field="rec_id") from None
    if electricity is None and gas is None:
        raise UsageError("Choose a plan: --elec, --gas or both.", field="choices")
    recommendation = _current_recommendation(ctx)
    if recommendation.recommendation_id != rec_id:
        raise InvalidInputError(
            "That isn't the current recommendation: run `energatron run`, or check out the commit that made it.",
            field="rec_id",
            reason="not_current",
        )
    run_id = _clean_run(ctx, rec_id)
    choices: dict[Fuel, Choice | None] = {
        "electricity": _choice(recommendation.priced["electricity"], electricity, "electricity"),
        "gas": _choice(recommendation.priced["gas"], gas, "gas"),
    }
    known = {record.dataset_id for record in ctx.catalog.records()}
    unknown = sorted(set(evidence) - known)
    if unknown:
        raise InvalidInputError("An evidence ID isn't a landed dataset.", field="evidence", count=len(unknown))
    now = ctx.clock.now()
    day = now.astimezone(ZoneInfo(ctx.region.local_tz)).date() if ctx.region.local_tz else now.date()
    identifier = decision_id(day, _existing(ctx))
    document = Decision(
        decision_id=identifier,
        decided_at=now,
        recommendation_id=rec_id,
        run_id=run_id,
        switch_date=switch_date,
        choices=choices,
        evidence=sorted(set(evidence)),
        note=note,
    )
    path = f"{DECISIONS_DIR}/{identifier}.json"
    commit: str | None = None
    warnings: tuple[ResultWarning, ...] = ()
    if not dry_run:
        write_json(ctx.paths.root / path, document.model_dump(mode="json"))
        pointers = ctx.vcs.track([DECISIONS_DIR])
        commit = ctx.vcs.commit(sorted(pointers), f"decide: {identifier} {rec_id}")
        status = ctx.vcs.backup_status()
        warnings = (
            ResultWarning(
                code=WarningCode.W_BACKUP_PENDING,
                message="The decision's commit isn't backed up yet: run `energatron backup` when convenient.",
                details={"commits_not_backed_up": status.commits_not_backed_up},
            ),
        )
    _log.info("decide: %s on %s, dry run: %s", identifier, rec_id, dry_run)
    return DecideResult(
        decision_id=identifier,
        recommendation_id=rec_id,
        run_id=run_id,
        path=path,
        commit=commit,
        dry_run=dry_run,
        warnings=warnings,
    )


def _current_recommendation(ctx: ServiceContext) -> Recommendation:
    try:
        return Recommendation.from_document(json.loads((ctx.paths.root / RECOMMENDATION).read_bytes()))
    except OSError, ValueError, InvalidInputError, SchemaTooNewError:
        raise InvalidInputError(
            "There is no valid current recommendation: run `energatron run`.", field="rec_id", reason="not_current"
        ) from None


def _clean_run(ctx: ServiceContext, rec_id: str) -> str:
    """The newest non-draft run manifest naming `rec_id`; none → `E_INVALID_INPUT`."""
    found: list[str] = []
    for path in sorted((ctx.paths.root / RUNS_DIR).glob("run-*.json")):
        try:
            manifest = RunManifest.from_document(json.loads(path.read_bytes()))
        except OSError, ValueError, InvalidInputError, SchemaTooNewError:
            raise InvalidInputError("A run manifest is invalid.", file=path.name) from None
        if manifest.recommendation_id == rec_id and not manifest.draft:
            found.append(manifest.run_id)
    if not found:
        raise InvalidInputError(
            "No clean run made this recommendation: run `energatron run` without --draft.",
            field="rec_id",
            reason="not_in_clean_run",
        )
    return max(found)


def _choice(priced: tuple[PricedTariff, ...], snapshot: str | None, fuel: str) -> Choice | None:
    if snapshot is None:
        return None
    found = next((tariff for tariff in priced if tariff.offer_snapshot_id == snapshot), None)
    if found is None:
        raise InvalidInputError(f"The {fuel} plan isn't priced in this recommendation.", field=fuel)
    return Choice(offer_snapshot_id=snapshot, annual_ongoing=found.annual_ongoing, annual_year1=found.annual_year1)


def _existing(ctx: ServiceContext) -> list[str]:
    found: list[str] = []
    for path in (ctx.paths.root / DECISIONS_DIR).glob("DEC-*.json"):
        try:
            parse_decision_id(path.stem)
        except ValueError:
            continue
        found.append(path.stem)
    return sorted(found)
