"""`fetch-offers`: list each CDR base, keep the plans that fit the household, fetch the changed details and land them
with one fetch log, in one `land` commit (P4 spec §3.2, §3.3).

The caller holds the workspace lock, for a dry run too. Every request goes through `ctx.offers["cdr"]`; the filter,
the selection and the log are worked out here. Details and the log are written verbatim to a staging directory under
`data/landing/`, which `land` reads with detection (no `--source`), then removed. The private postcode is used only to
filter: no output, log or error ever holds it, only `postcode_applied`.
"""

import json
import logging
import shutil
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, ClassVar, Final, Literal, cast

import yaml
from pydantic import Field

from energatron.core.canonical import JSONValue, sha256_hex
from energatron.core.errors import (
    EnergatronError,
    InternalError,
    InvalidInputError,
    NetworkError,
    PartialError,
    PrivateConfigMissingError,
    ResultWarning,
    UsageError,
    WarningCode,
)
from energatron.core.ids import fetch_batch_id, offer_snapshot_id
from energatron.core.models._common import CommitSha, DatasetId, FrozenModel
from energatron.core.models.catalog import SourceType
from energatron.core.models.offers import (
    FetchError,
    FetchFilter,
    LoggedPage,
    OfferFetchLog,
    RetailerFetch,
    SelectedPlan,
)
from energatron.core.models.params import OffersParams, Params
from energatron.core.models.workspace import WorkspacePaths
from energatron.ports.offers import OfferProvider
from energatron.services import land as land_service
from energatron.services import pipeline_sync
from energatron.services.context import ServiceContext, ServiceResult
from energatron.services.files import atomic_write_bytes, make_dirs, write_json

_log = logging.getLogger(__name__)

FETCH_LOG_NAME: Final = "fetch-log.json"
LEFT_OUT_REASONS: Final = ("malformed", "fuel", "customer_type", "distributor", "postcode", "effective")
_SAFE: Final = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789@._-")


class RetailerSummary(FrozenModel):
    """One base in the result: its counts, and why it left plans out (by the first rule each failed)."""

    base: str
    listed: Annotated[int, Field(ge=0)]
    selected: Annotated[int, Field(ge=0)]
    fetched: Annotated[int, Field(ge=0)]
    unchanged: Annotated[int, Field(ge=0)]
    failed: Annotated[int, Field(ge=0)]
    left_out: dict[str, int]
    error: FetchError | None = None


class FetchOffersResult(ServiceResult):
    """`fetch_offers_result` v1 (P4 spec §3.2 step 8). In a dry run no detail is fetched and nothing is written."""

    KIND: ClassVar[str] = "fetch_offers_result"
    SCHEMA_VERSION: ClassVar[int] = 1
    kind: Literal["fetch_offers_result"] = "fetch_offers_result"
    schema_version: Literal[1] = 1

    fetch_batch_id: str
    dry_run: bool
    postcode_applied: bool
    retailers: list[RetailerSummary]
    landed: list[DatasetId]  # new datasets: changed plans and the fetch log, sorted
    rejected: Annotated[int, Field(ge=0)]  # files `land` rejected
    commit: CommitSha | None


@dataclass
class _Base:
    """What one base produced while it was fetched."""

    base: str
    pages: list[LoggedPage] = field(default_factory=lambda: list[LoggedPage]())
    listed: int = 0
    left_out: dict[str, int] = field(default_factory=lambda: dict[str, int]())
    selected: list[SelectedPlan] = field(default_factory=lambda: list[SelectedPlan]())
    error: FetchError | None = None


def fetch_offers(
    ctx: ServiceContext,
    *,
    retailers: Sequence[str] | None = None,
    all_details: bool = False,
    dry_run: bool = False,
) -> FetchOffersResult:
    """Fetch, filter and land the offers of `retailers` (default: `params.offers.retailers`)."""
    if ctx.private is None:
        raise PrivateConfigMissingError("fetch-offers needs private/config.toml for the postcode filter.")
    provider = ctx.offers.get("cdr")
    if provider is None:
        raise InternalError("No CDR offer provider is configured.")
    pipeline_sync.check(ctx)
    offers = _offers_params(ctx.paths)
    bases = list(retailers) if retailers else list(offers.retailers or ())
    if not bases:
        raise UsageError("No CDR retailers to fetch: name one with --retailer or run `energatron pipeline sync`.")
    if offers.distributor is None:
        raise InvalidInputError(
            "params.yaml has no value for offers.distributor yet: run `energatron pipeline sync`.",
            field="offers.distributor",
        )
    now = ctx.clock.now().astimezone(UTC).replace(microsecond=0)
    batch = fetch_batch_id(now)
    postcode = ctx.private.premises.postcode
    rules = _Filter(offers=offers, distributor=offers.distributor, postcode=postcode, now=now)
    newest = _newest_landed(ctx)
    staging = ctx.paths.root / "data" / "landing" / f".fetch-{batch}"
    found = [_fetch_base(provider, base, rules, newest, all_details=all_details) for base in bases]
    warnings = [warning for base in found for warning in _warnings(base)]
    summaries = [_summary(base) for base in found]
    if dry_run:
        return _result(batch, summaries, postcode, warnings, dry_run=True)
    details = _fetch_details(provider, found, staging)
    warnings += [warning for base in found for warning in _detail_warnings(base)]
    summaries = [_summary(base) for base in found]
    log = OfferFetchLog(
        fetch_batch_id=batch,
        fetched_at=now,
        filter=FetchFilter(
            fuel_types=tuple(offers.fuel_types),
            customer_type=offers.customer_type,
            distributor=offers.distributor,
            postcode_applied=postcode is not None,
        ),
        retailers=tuple(
            RetailerFetch(
                base=base.base, pages=tuple(base.pages), listed=base.listed, left_out=base.left_out, error=base.error
            )
            for base in found
        ),
        selected=tuple(plan for base in found for plan in base.selected),
    )
    make_dirs(staging)
    write_json(staging / FETCH_LOG_NAME, cast(JSONValue, log.model_dump(mode="json")))
    _log.info("fetch-offers: %d bases, %d details written to staging", len(found), details)
    landed = land_service.land(ctx, [staging])  # a whole-command error leaves the staging directory for inspection
    shutil.rmtree(staging)
    warnings += list(landed.warnings)
    rejected = sum(item.status == "rejected" for item in landed.items)
    return _result(
        batch,
        summaries,
        postcode,
        warnings,
        dry_run=False,
        landed=landed.landed,
        rejected=rejected,
        commit=landed.commit,
    )


def outcome(result: FetchOffersResult) -> EnergatronError | None:
    """The error the command ends with: None when every base and detail was fetched; the base's own error when every
    base failed; otherwise `E_PARTIAL` (exit 8), whose `data` is the result."""
    failed_bases = [summary for summary in result.retailers if summary.error is not None]
    failed_details = sum(summary.failed for summary in result.retailers)
    if not failed_bases and not failed_details and not result.rejected:
        return None
    if len(failed_bases) == len(result.retailers):
        error = failed_bases[0].error
        assert error is not None
        return NetworkError(error.message, status=error.status, bases=len(failed_bases))
    return PartialError(
        f"{len(failed_bases)} retailer lists and {failed_details} plan details couldn't be fetched, and "
        f"{result.rejected} files were rejected; the rest landed.",
        failed_retailers=len(failed_bases),
        failed_details=failed_details,
        rejected=result.rejected,
    )


# Params and the catalog


def _offers_params(paths: WorkspacePaths) -> OffersParams:
    try:
        document = yaml.safe_load((paths.root / "params.yaml").read_text(encoding="utf-8"))
    except OSError, UnicodeDecodeError, yaml.YAMLError:
        raise InvalidInputError("params.yaml can't be read as YAML.", kind=Params.KIND) from None
    if not isinstance(document, dict):
        raise InvalidInputError("params.yaml isn't a mapping.", kind=Params.KIND)
    return Params.from_document(cast(dict[str, JSONValue], document)).offers


def _newest_landed(ctx: ServiceContext) -> dict[str, datetime]:
    """Each landed plan's newest `lastUpdated` (the `offer_cdr` records' `source_ts`)."""
    newest: dict[str, datetime] = {}
    for record in ctx.catalog.records(SourceType.OFFER_CDR):
        plan_id = record.extra.get("plan_id")
        if isinstance(plan_id, str) and (plan_id not in newest or record.source_ts > newest[plan_id]):
            newest[plan_id] = record.source_ts
    return newest


# The local filter (§3.2 step 3)


@dataclass(frozen=True)
class _Filter:
    offers: OffersParams
    distributor: str
    postcode: str | None
    now: datetime

    def left_out(self, item: Mapping[str, JSONValue]) -> str | None:
        """Why a list item is left out (the first rule it fails), or None when it is selected."""
        plan_id, last_updated = item.get("planId"), _instant(item.get("lastUpdated"))
        if not isinstance(plan_id, str) or not plan_id or last_updated is None:
            return "malformed"
        if item.get("fuelType") not in self.offers.fuel_types:
            return "fuel"
        if item.get("customerType") != self.offers.customer_type:
            return "customer_type"
        geography = item.get("geography")
        geography = geography if isinstance(geography, dict) else {}
        distributors = geography.get("distributors")
        if not isinstance(distributors, list) or self.distributor not in distributors:
            return "distributor"
        if self.postcode is not None:
            included, excluded = geography.get("includedPostcodes"), geography.get("excludedPostcodes")
            if (isinstance(included, list) and self.postcode not in included) or (
                isinstance(excluded, list) and self.postcode in excluded
            ):
                return "postcode"
        starts, ends = _instant(item.get("effectiveFrom")), _instant(item.get("effectiveTo"))
        if (starts is not None and starts > self.now) or (ends is not None and ends <= self.now):
            return "effective"
        return None


def _instant(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        instant = datetime.fromisoformat(value.upper())
    except ValueError:
        return None
    return instant.astimezone(UTC) if instant.utcoffset() is not None else None


# Fetching


def _fetch_base(
    provider: OfferProvider,
    base: str,
    rules: _Filter,
    newest: Mapping[str, datetime],
    *,
    all_details: bool,
) -> _Base:
    found = _Base(base=base)
    try:
        pages = provider.list_plans(base)
    except NetworkError as error:
        found.error = FetchError(code=error.code, message=error.message, status=_status(error))
        return found
    seen: set[str] = set()
    for page in pages:
        found.pages.append(LoggedPage.of(page))
        for item in _plans(page.body):
            found.listed += 1
            reason = rules.left_out(item)
            if reason is None and cast(str, item["planId"]) in seen:
                continue  # listed twice: counted once
            if reason is not None:
                found.left_out[reason] = found.left_out.get(reason, 0) + 1
                continue
            plan_id = cast(str, item["planId"])
            seen.add(plan_id)
            last_updated = _instant(item.get("lastUpdated"))
            assert last_updated is not None
            unchanged = not all_details and newest.get(plan_id) == last_updated
            found.selected.append(
                SelectedPlan(
                    base=base,
                    plan_id=plan_id,
                    last_updated=last_updated,
                    action="unchanged" if unchanged else "fetched",
                )
            )
    found.left_out = {reason: found.left_out[reason] for reason in LEFT_OUT_REASONS if reason in found.left_out}
    return found


def _plans(body: bytes) -> list[Mapping[str, JSONValue]]:
    document = json.loads(body)  # the provider checked it is JSON
    data = document.get("data") if isinstance(document, dict) else None
    plans = data.get("plans") if isinstance(data, dict) else None
    if not isinstance(plans, list):
        return []
    return [cast(Mapping[str, JSONValue], item) for item in plans if isinstance(item, dict)]


def _fetch_details(provider: OfferProvider, found: Sequence[_Base], staging: Path) -> int:
    """Fetch every `fetched` plan's detail into `staging`; a failure marks the plan `failed`. Returns how many."""
    written = 0
    make_dirs(staging)
    for base in found:
        for index, plan in enumerate(base.selected):
            if plan.action != "fetched":
                continue
            try:
                fetched = provider.get_plan(base.base, plan.plan_id)
            except NetworkError:
                base.selected[index] = plan.model_copy(update={"action": "failed"})
                continue
            atomic_write_bytes(staging / f"{_safe(base.base)}__{_safe(plan.plan_id)}.json", fetched.body)
            written += 1
            base.selected[index] = plan.model_copy(
                update={"sha256": sha256_hex(fetched.body), "offer_snapshot_id": _snapshot(fetched.body)}
            )
    return written


def _snapshot(body: bytes) -> str | None:
    """The detail's offer snapshot ID, when its `data` holds a usable plan ID and `lastUpdated`."""
    try:
        document = json.loads(body)
        data = document["data"]
        last_updated = _instant(data["lastUpdated"])
        if last_updated is None:
            return None
        return offer_snapshot_id(data["planId"], last_updated, data)
    except KeyError, TypeError, ValueError, RecursionError:
        return None


def _safe(text: str) -> str:
    return "".join(character if character in _SAFE else "_" for character in text)


def _status(error: EnergatronError) -> int | None:
    status = error.details.get("status")
    return status if isinstance(status, int) else None


# The result


def _summary(base: _Base) -> RetailerSummary:
    actions = [plan.action for plan in base.selected]
    return RetailerSummary(
        base=base.base,
        listed=base.listed,
        selected=len(base.selected),
        fetched=actions.count("fetched"),
        unchanged=actions.count("unchanged"),
        failed=actions.count("failed"),
        left_out=dict(base.left_out),
        error=base.error,
    )


def _warnings(base: _Base) -> list[ResultWarning]:
    if base.error is None:
        return []
    return [
        ResultWarning(
            code=WarningCode.W_FETCH_FAILED,
            message="A retailer's plan list couldn't be fetched.",
            details={"base": base.base, "status": base.error.status},
        )
    ]


def _detail_warnings(base: _Base) -> list[ResultWarning]:
    failed = sum(plan.action == "failed" for plan in base.selected)
    if not failed:
        return []
    return [
        ResultWarning(
            code=WarningCode.W_FETCH_FAILED,
            message="Some plan details couldn't be fetched.",
            details={"base": base.base, "failed": failed},
        )
    ]


def _result(
    batch: str,
    summaries: list[RetailerSummary],
    postcode: str | None,
    warnings: list[ResultWarning],
    *,
    dry_run: bool,
    landed: Sequence[str] = (),
    rejected: int = 0,
    commit: str | None = None,
) -> FetchOffersResult:
    return FetchOffersResult(
        fetch_batch_id=batch,
        dry_run=dry_run,
        postcode_applied=postcode is not None,
        retailers=summaries,
        landed=sorted(landed),
        rejected=rejected,
        commit=commit,
        warnings=tuple(warnings),
    )
