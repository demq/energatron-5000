"""`services.offer_diff` (P4 spec §11): field changes between each plan's two newest landed snapshots."""

from pathlib import Path

import pytest

from energatron.adapters.storage.fs_blobs import FsBlobStore
from energatron.adapters.storage.jsonl_catalog import JsonlCatalog
from energatron.core.errors import UsageError
from energatron.core.models.workspace import WorkspacePaths
from energatron.services.context import ServiceContext
from energatron.services.offer_diff import OfferDiffResult, offer_diff
from tests.fixtures.offer_workspace import TOU_ID, build_offer_zone
from tests.fixtures.pipeline_workspace import empty_raw_zone
from tests.unit.adapters._workspace import make_workspace
from tests.unit.services.test_context import build


@pytest.fixture
def ctx(tmp_path: Path) -> ServiceContext:
    paths: WorkspacePaths = make_workspace(tmp_path, private=None)
    empty_raw_zone(paths.root)
    build_offer_zone(paths.root)
    return build(paths=paths, catalog=JsonlCatalog(paths), blobs=FsBlobStore(paths))


def test_the_changed_peak_rate_is_reported_with_its_signed_difference(ctx: ServiceContext) -> None:
    result = offer_diff(ctx)
    assert result.plans_compared == 1  # only the TOU plan has two snapshots
    paths = {change.path: change for change in result.changes if change.plan_id == TOU_ID}
    peak = next(
        change for path, change in paths.items() if path.endswith("blocks[0].unit_price") and change.old == "0.45"
    )
    assert (peak.new, peak.delta) == ("0.40", "-0.05")
    assert "offer_snapshot_id" not in paths


def test_one_plan_can_be_named(ctx: ServiceContext) -> None:
    assert offer_diff(ctx, plan=TOU_ID).plans_compared == 1
    with pytest.raises(UsageError):
        offer_diff(ctx, plan="NOT-LANDED@EME")


def test_a_plan_with_one_snapshot_shows_no_change(ctx: ServiceContext) -> None:
    assert offer_diff(ctx, plan="TEST-FLAT@EME") == OfferDiffResult(plans_compared=0, changes=[])


def test_the_result_round_trips_through_its_kind(ctx: ServiceContext) -> None:
    result = offer_diff(ctx)
    assert OfferDiffResult.from_document(result.model_dump(mode="json")) == result
