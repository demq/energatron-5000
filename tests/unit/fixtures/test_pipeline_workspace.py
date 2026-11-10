"""The fixture raw zone (P2 spec §13.2): what it holds and that the pieces agree. Every value is synthetic."""

import math
from datetime import timedelta
from itertools import pairwise
from pathlib import Path

import pytest

from energatron.core.models.catalog import SourceType, parse_catalog
from energatron.core.nem12 import read_nem12
from tests.fixtures import oracle
from tests.fixtures.make_nem12 import make_nem12
from tests.fixtures.pipeline_workspace import RawZone, build_raw_zone, day_values, total_kwh


@pytest.fixture(scope="module")
def zone(tmp_path_factory: pytest.TempPathFactory) -> RawZone:
    return build_raw_zone(tmp_path_factory.mktemp("zone"))


def catalog(zone: RawZone, source: SourceType) -> list[str]:
    data = (zone.root / "data" / "raw" / source.value / "_catalog.jsonl").read_bytes()
    return [record.dataset_id for record in parse_catalog(data, source)]


def test_every_source_has_a_catalog_and_only_three_have_records(zone: RawZone) -> None:
    counts = {source: len(catalog(zone, source)) for source in SourceType}
    filled = {source: count for source, count in counts.items() if count}
    assert filled == {SourceType.SAPN_INTERVAL: 2, SourceType.SAPN_SUMMARY: 1, SourceType.RETAILER_USAGE: 2}


def test_the_catalog_lists_the_datasets_in_landing_order_and_every_blob_exists(zone: RawZone) -> None:
    assert catalog(zone, SourceType.SAPN_INTERVAL) == [dataset.dataset_id for dataset in zone.interval]
    assert catalog(zone, SourceType.RETAILER_USAGE) == [dataset.dataset_id for dataset in zone.usage]
    for dataset in (*zone.interval, zone.summary, *zone.usage):
        assert (zone.root / dataset.stored_path).is_file()


def test_the_span_covers_both_daylight_saving_changes_of_the_year(zone: RawZone) -> None:
    april, october = oracle.sa_dst_dates(2025)
    first, last = zone.specs.a_only[0], zone.specs.b_only[1]
    assert first < april - timedelta(days=1) and last > october + timedelta(days=1)


def test_the_two_datasets_overlap_on_twenty_days_with_one_conflict_day(zone: RawZone) -> None:
    specs = zone.specs
    overlap = [specs.overlap[0] + timedelta(days=n) for n in range((specs.overlap[1] - specs.overlap[0]).days + 1)]
    assert len(overlap) == 20
    a = day_values(make_nem12(specs.a), overlap)
    b = day_values(make_nem12(specs.b), overlap)
    assert {day for day in overlap if a[day] != b[day]} == {specs.conflict_day}


def test_the_conflict_day_is_newer_in_b(zone: RawZone) -> None:
    a = read_nem12(make_nem12(zone.specs.a)).table
    b = read_nem12(make_nem12(zone.specs.b)).table
    day = zone.specs.conflict_day
    a_update = a[a["meter_date"] == day]["update_dt"].iloc[0]
    b_update = b[b["meter_date"] == day]["update_dt"].iloc[0]
    assert b_update > a_update


def test_the_truth_is_every_day_once_with_the_conflict_day_as_b_has_it(zone: RawZone) -> None:
    specs = zone.specs
    truth = oracle.day_sums(zone.truth)
    a = oracle.day_sums(make_nem12(specs.a))
    b = oracle.day_sums(make_nem12(specs.b))
    expected = {**a, **b}  # B's days win where both hold one
    assert truth.keys() == expected.keys()
    assert all(math.isclose(truth[key], expected[key], rel_tol=1e-12) for key in truth)
    assert math.isclose(total_kwh(zone.truth), math.fsum(truth.values()), rel_tol=1e-12)


def test_the_periods_tile_the_span(zone: RawZone) -> None:
    periods = zone.periods
    assert periods[0][0] == zone.specs.a_only[0] and periods[-1][1] == zone.specs.b_only[1]
    for (_, end), (start, _) in pairwise(periods):
        assert start == end + timedelta(days=1)


def test_the_newest_usage_row_of_the_second_export_is_empty(zone: RawZone) -> None:
    text = (zone.root / zone.usage[1].stored_path).read_text()
    assert text.splitlines()[-1].endswith(",,,,")
    assert ",,,," not in (zone.root / zone.usage[0].stored_path).read_text()


def test_no_blob_is_written_outside_the_raw_zone(zone: RawZone, tmp_path: Path) -> None:
    files = [path.relative_to(zone.root) for path in zone.root.rglob("*") if path.is_file()]
    readmes = {  # the folders `pipeline sync` creates
        Path(folder, "README.md")
        for folder in ("config/offer_rules.local", "config/bundle_rules.local", "data/runs", "data/decisions")
    }
    readmes.add(Path("private", "config.toml"))  # synthetic, written when missing
    assert all(path.parts[:2] == ("data", "raw") or path in readmes for path in files)
