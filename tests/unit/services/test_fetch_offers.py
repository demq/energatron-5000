"""`services.fetch_offers` (P4 spec §3.2, §3.3) with a fake provider and a scratch workspace whose `land` is real.

The workspace is `test_land`'s: the real catalog and blob store, a fake `VersionControl`, `params.yaml` with the test
region's retailers (`alpha`, `beta-energy`) and distributor. Plans come from `tests/fixtures/make_cdr.py`.
"""

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import pytest

from energatron.adapters.sources.cdr_index import OFFER_CDR_INDEX
from energatron.adapters.sources.cdr_plan import OFFER_CDR
from energatron.adapters.storage.jsonl_catalog import JsonlCatalog
from energatron.core.errors import (
    InvalidInputError,
    NetworkError,
    PartialError,
    PipelineNotSyncedError,
    PrivateConfigMissingError,
    UsageError,
    WarningCode,
)
from energatron.core.models.catalog import SourceType
from energatron.core.models.offers import Fetched, OfferFetchLog
from energatron.core.models.private import PrivateConfig
from energatron.core.models.workspace import WorkspacePaths
from energatron.services.context import ServiceContext
from energatron.services.fetch_offers import FetchOffersResult, fetch_offers, outcome
from tests.fixtures import make_cdr as cdr
from tests.fixtures.synthetic import synthetic_private_document
from tests.unit.services.test_context import FixedClock, build
from tests.unit.services.test_land import LandVcs, SpyBlobs
from tests.unit.services.test_land import paths as paths

POSTCODE = "9999"  # made up
NOW = FixedClock().now()


@dataclass
class FakeProvider:
    """Serves `lists[base]` as one page and `details[(base, plan_id)]`; a value that is an exception is raised."""

    lists: dict[str, Sequence[Mapping[str, Any]] | Exception] = field(default_factory=dict)
    details: dict[tuple[str, str], Mapping[str, Any] | Exception] = field(default_factory=dict)
    name: str = "cdr"
    detail_calls: list[tuple[str, str]] = field(default_factory=list)

    def list_plans(self, brand: str) -> list[Fetched]:
        found = self.lists.get(brand, [])
        if isinstance(found, Exception):
            raise found
        return [self._fetched(f"https://cdr.test/{brand}/plans", cdr.list_body(found))]

    def get_plan(self, brand: str, plan_id: str) -> Fetched:
        self.detail_calls.append((brand, plan_id))
        found = self.details[(brand, plan_id)]
        if isinstance(found, Exception):
            raise found
        return self._fetched(f"https://cdr.test/{brand}/plans/{plan_id}", cdr.detail_body(found))

    @staticmethod
    def _fetched(url: str, body: bytes) -> Fetched:
        return Fetched(
            url=url, request_headers={"x-v": "1"}, status=200, fetched_at=NOW, response_headers={}, body=body
        )

    def serve(self, base: str, *plans: Mapping[str, Any]) -> None:
        self.lists[base] = list(plans)
        for found in plans:
            self.details[(base, found["planId"])] = found


@dataclass
class TickingClock:
    """A minute later on every call, so each batch gets its own ID."""

    calls: int = 0

    def now(self) -> datetime:
        self.calls += 1
        return NOW + timedelta(minutes=self.calls)


def private(*, postcode: str | None = POSTCODE) -> PrivateConfig:
    document = synthetic_private_document()
    if postcode is not None:
        document["premises"] = {**document["premises"], "postcode": postcode}  # type: ignore[dict-item]
    return PrivateConfig.from_document(document)


@pytest.fixture
def provider() -> FakeProvider:
    return FakeProvider()


@pytest.fixture
def vcs(paths: WorkspacePaths) -> LandVcs:
    return LandVcs(paths)


def context(
    paths: WorkspacePaths,
    vcs: LandVcs,
    provider: FakeProvider,
    *,
    config: PrivateConfig | None = None,
    missing_private: bool = False,
    clock: Any = None,
) -> ServiceContext:
    return build(
        paths=paths,
        private=None if missing_private else (config or private()),
        catalog=JsonlCatalog(paths),
        blobs=SpyBlobs(paths),
        parsers=(OFFER_CDR, OFFER_CDR_INDEX),
        vcs=vcs,
        clock=clock or FixedClock(),
        offers={"cdr": provider},
    )


@pytest.fixture
def ctx(paths: WorkspacePaths, vcs: LandVcs, provider: FakeProvider) -> ServiceContext:
    return context(paths, vcs, provider)


def landed_plan_ids(ctx: ServiceContext) -> list[str]:
    return sorted(str(record.extra["plan_id"]) for record in ctx.catalog.records(SourceType.OFFER_CDR))


def the_log(ctx: ServiceContext) -> OfferFetchLog:
    (record,) = ctx.catalog.records(SourceType.OFFER_CDR_INDEX)
    return OfferFetchLog.from_document(json.loads((ctx.paths.root / record.stored_path).read_bytes()))


# Selection and landing


def test_selected_plans_land_with_one_fetch_log_in_one_commit(
    ctx: ServiceContext, provider: FakeProvider, vcs: LandVcs
) -> None:
    provider.serve("alpha", cdr.plan("A1@EME"), cdr.plan("A2@EME"))
    provider.serve("beta-energy", cdr.plan("B1@EME", brand="beta"))
    result = fetch_offers(ctx)
    assert outcome(result) is None
    assert landed_plan_ids(ctx) == ["A1@EME", "A2@EME", "B1@EME"]
    assert len(vcs.commits) == 1
    assert len(result.landed) == 4  # three plans and the log
    assert [(found.base, found.listed, found.selected, found.fetched) for found in result.retailers] == [
        ("alpha", 2, 2, 2),
        ("beta-energy", 1, 1, 1),
    ]
    log = the_log(ctx)
    assert log.fetch_batch_id == "fetch-20261023T093000Z"
    assert [plan.plan_id for plan in log.selected] == ["A1@EME", "A2@EME", "B1@EME"]
    assert all(plan.offer_snapshot_id is not None and plan.sha256 is not None for plan in log.selected)
    assert log.retailers[0].pages[0].body() == cdr.list_body([cdr.plan("A1@EME"), cdr.plan("A2@EME")])
    assert not any((ctx.paths.root / "data" / "landing").glob(".fetch-*"))  # the staging directory is gone


@pytest.mark.parametrize(
    ("plan", "reason"),
    [
        (cdr.plan("X@EME", fuel="GAS"), "fuel"),
        (cdr.plan("X@EME", customer_type="BUSINESS"), "customer_type"),
        (cdr.plan("X@EME", distributors=["Other Networks"]), "distributor"),
        (cdr.plan("X@EME", postcodes=["1111"]), "postcode"),
        (cdr.plan("X@EME", effective_from="2026-12-01T00:00:00Z"), "effective"),
        (cdr.plan("X@EME", effective_to="2026-10-01T00:00:00Z"), "effective"),
        ({"planId": "X@EME", "lastUpdated": "yesterday"}, "malformed"),
    ],
)
def test_plans_are_left_out_by_the_first_rule_they_fail(
    ctx: ServiceContext, provider: FakeProvider, plan: dict[str, Any], reason: str
) -> None:
    provider.serve("alpha", cdr.plan("KEEP@EME"))
    provider.lists["alpha"] = [cdr.plan("KEEP@EME"), plan]
    result = fetch_offers(ctx, retailers=["alpha"])
    (alpha,) = result.retailers
    assert (alpha.listed, alpha.selected, alpha.left_out) == (2, 1, {reason: 1})
    assert landed_plan_ids(ctx) == ["KEEP@EME"]
    assert provider.detail_calls == [("alpha", "KEEP@EME")]


def test_a_plan_listing_the_postcode_is_kept(ctx: ServiceContext, provider: FakeProvider) -> None:
    provider.serve("alpha", cdr.plan("A1@EME", postcodes=["1111", POSTCODE]))
    assert fetch_offers(ctx, retailers=["alpha"]).retailers[0].selected == 1


def test_without_a_postcode_the_filter_is_skipped_and_said(
    paths: WorkspacePaths,
    vcs: LandVcs,
    provider: FakeProvider,
) -> None:
    provider.serve("alpha", cdr.plan("A1@EME", postcodes=["1111"]))
    result = fetch_offers(context(paths, vcs, provider, config=private(postcode=None)), retailers=["alpha"])
    assert result.postcode_applied is False
    assert result.retailers[0].selected == 1


def test_an_unchanged_plan_is_not_fetched_again_unless_all_is_given(
    paths: WorkspacePaths,
    vcs: LandVcs,
    provider: FakeProvider,
) -> None:
    ctx = context(paths, vcs, provider, clock=TickingClock())
    provider.serve("alpha", cdr.plan("A1@EME"))
    fetch_offers(ctx, retailers=["alpha"])
    provider.detail_calls.clear()
    again = fetch_offers(ctx, retailers=["alpha"])
    assert (again.retailers[0].unchanged, again.retailers[0].fetched, provider.detail_calls) == (1, 0, [])
    assert len(again.landed) == 1  # only the new fetch log
    forced = fetch_offers(ctx, retailers=["alpha"], all_details=True)
    assert (forced.retailers[0].fetched, provider.detail_calls) == (1, [("alpha", "A1@EME")])
    assert len(forced.landed) == 1  # the same snapshot is `land`'s no-op


def test_a_changed_plan_lands_as_a_new_snapshot(ctx: ServiceContext, provider: FakeProvider) -> None:
    provider.serve("alpha", cdr.plan("A1@EME"))
    fetch_offers(ctx, retailers=["alpha"])
    provider.serve("alpha", cdr.plan("A1@EME", last_updated="2026-10-20T00:00:00Z", time_zone="AEST"))
    result = fetch_offers(ctx, retailers=["alpha"])
    assert result.retailers[0].fetched == 1
    assert landed_plan_ids(ctx) == ["A1@EME", "A1@EME"]


def test_a_plan_listed_twice_is_selected_once(ctx: ServiceContext, provider: FakeProvider) -> None:
    provider.serve("alpha", cdr.plan("A1@EME"))
    provider.lists["alpha"] = [cdr.plan("A1@EME"), cdr.plan("A1@EME")]
    assert fetch_offers(ctx, retailers=["alpha"]).retailers[0].selected == 1


# Failures


def test_a_failing_base_is_recorded_and_the_others_land(ctx: ServiceContext, provider: FakeProvider) -> None:
    provider.lists["alpha"] = NetworkError("down", url="x", status=503, reason="http_status")
    provider.serve("beta-energy", cdr.plan("B1@EME"))
    result = fetch_offers(ctx)
    assert result.retailers[0].error is not None
    assert result.retailers[0].error.status == 503
    assert landed_plan_ids(ctx) == ["B1@EME"]
    assert isinstance(outcome(result), PartialError)
    assert [warning.code for warning in result.warnings].count(WarningCode.W_FETCH_FAILED) == 1
    assert the_log(ctx).retailers[0].error is not None


def test_a_failing_detail_is_marked_failed_and_the_rest_land(ctx: ServiceContext, provider: FakeProvider) -> None:
    provider.serve("alpha", cdr.plan("A1@EME"), cdr.plan("A2@EME"))
    provider.details[("alpha", "A2@EME")] = NetworkError("down", url="x", status=None, reason="unreachable")
    result = fetch_offers(ctx, retailers=["alpha"])
    assert (result.retailers[0].fetched, result.retailers[0].failed) == (1, 1)
    assert landed_plan_ids(ctx) == ["A1@EME"]
    assert [plan.action for plan in the_log(ctx).selected] == ["fetched", "failed"]
    error = outcome(result)
    assert isinstance(error, PartialError)
    assert error.details["failed_details"] == 1


def test_when_every_base_fails_the_network_error_ends_the_command(ctx: ServiceContext, provider: FakeProvider) -> None:
    for base in ("alpha", "beta-energy"):
        provider.lists[base] = NetworkError("down", url="x", status=None, reason="unreachable")
    assert isinstance(outcome(fetch_offers(ctx)), NetworkError)


# Preconditions and the dry run


def test_a_dry_run_lists_and_filters_but_fetches_and_writes_nothing(
    ctx: ServiceContext, provider: FakeProvider, vcs: LandVcs
) -> None:
    provider.serve("alpha", cdr.plan("A1@EME"))
    before = sorted(path for path in ctx.paths.root.rglob("*"))
    result = fetch_offers(ctx, retailers=["alpha"], dry_run=True)
    assert (result.dry_run, result.retailers[0].selected, result.landed, result.commit) == (True, 1, [], None)
    assert provider.detail_calls == []
    assert sorted(path for path in ctx.paths.root.rglob("*")) == before
    assert vcs.commits == []


def test_no_private_config_is_refused(paths: WorkspacePaths, vcs: LandVcs, provider: FakeProvider) -> None:
    with pytest.raises(PrivateConfigMissingError):
        fetch_offers(context(paths, vcs, provider, missing_private=True))


def test_an_unsynced_workspace_is_refused(ctx: ServiceContext) -> None:
    (ctx.paths.root / "dvc.yaml").unlink()
    with pytest.raises(PipelineNotSyncedError):
        fetch_offers(ctx)


def test_null_retailers_without_an_option_is_a_usage_error(ctx: ServiceContext) -> None:
    params = ctx.paths.root / "params.yaml"
    params.write_text(params.read_text().replace("  - alpha\n  - beta-energy\n", ""), encoding="utf-8")
    text = params.read_text()
    params.write_text(text.replace("retailers:\n", "retailers: null\n"), encoding="utf-8")
    with pytest.raises((UsageError, PipelineNotSyncedError)):
        fetch_offers(ctx)


def test_an_unset_distributor_is_invalid_input(ctx: ServiceContext) -> None:
    params = ctx.paths.root / "params.yaml"
    params.write_text(params.read_text().replace("distributor: Test Networks", "distributor: null"), encoding="utf-8")
    with pytest.raises((InvalidInputError, PipelineNotSyncedError)):
        fetch_offers(ctx, retailers=["alpha"])


# Privacy


def test_no_field_of_the_result_or_log_records_the_households_postcode(
    ctx: ServiceContext, provider: FakeProvider
) -> None:
    """The list pages are public and kept verbatim (with every plan's postcode list); the log's own fields and the
    result say only whether a postcode was applied."""
    provider.serve("alpha", cdr.plan("A1@EME", postcodes=[POSTCODE]))
    result = fetch_offers(ctx, retailers=["alpha"])
    assert POSTCODE not in json.dumps(result.model_dump(mode="json"))
    log = the_log(ctx).model_dump(mode="json")
    for retailer in log["retailers"]:
        for page in retailer["pages"]:
            page["body_b64"] = ""
    assert POSTCODE not in json.dumps(log)
    assert log["filter"]["postcode_applied"] is True


def test_the_result_round_trips_through_its_kind() -> None:
    result = FetchOffersResult(
        fetch_batch_id="fetch-20261023T093000Z",
        dry_run=True,
        postcode_applied=True,
        retailers=[],
        landed=[],
        rejected=0,
        commit=None,
    )
    assert FetchOffersResult.from_document(result.model_dump(mode="json")) == result
