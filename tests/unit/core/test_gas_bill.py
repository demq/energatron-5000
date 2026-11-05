"""`core.gas_bill` (P5 spec §6, §7): record checks at the printed precision, combining estimates, gaps, the reference
year and the monthly spread. Synthetic records only."""

import copy
from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from energatron.core.gas_bill import (
    BillUsage,
    check_record,
    combine,
    monthly,
    overlapping,
    printed_unit,
    recomputed_mj,
    reference,
)
from energatron.core.models.gas import GasBillRecord, GasRead
from tests.fixtures import make_gas as gas

TOLERANCE = Decimal("0.05")
GST = Decimal("0.10")


def checked(document: dict[str, Any]) -> dict[str, bool]:
    result = check_record(GasBillRecord.from_document(document), tolerance=TOLERANCE, gst_rate=GST)
    return {
        name: getattr(result, name)
        for name in ("reads_recompute", "usage_matches_reads", "days_match", "lines_recompute", "totals_recompute")
    }


def failing(document: dict[str, Any]) -> list[str]:
    return [name for name, passed in checked(document).items() if not passed]


@pytest.mark.parametrize(("text", "unit"), [("583.17", "0.01"), ("812.4", "0.1"), ("1396", "1"), ("0.000", "0.001")])
def test_the_printed_unit_is_one_of_the_last_place(text: str, unit: str) -> None:
    assert printed_unit(Decimal(text)) == Decimal(unit)


@pytest.mark.parametrize("places", [0, 1, 2])
def test_a_read_recomputes_at_the_places_the_bill_printed(places: int) -> None:
    found = gas.read(date(2026, 9, 30), 1000, 1017, mj_places=places)
    read = GasRead.model_validate(found)
    assert recomputed_mj(read) == read.mj
    assert recomputed_mj(read).as_tuple().exponent == -places


def test_a_consistent_record_passes_every_check_and_recomputes_its_total() -> None:
    document = gas.record(volumes=(17, 24), read_types=["actual", "estimate"])
    assert failing(document) == []
    record = GasBillRecord.from_document(document)
    assert check_record(record, tolerance=TOLERANCE, gst_rate=GST).recomputed_total == record.total


def test_whole_mj_reads_pass_within_one_unit() -> None:
    assert failing(gas.record(volumes=(17, 24), mj_places=0)) == []


def bump(document: dict[str, Any], path: tuple[Any, ...], delta: str) -> dict[str, Any]:
    changed = copy.deepcopy(document)
    target: Any = changed
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = str(Decimal(target[path[-1]]) + Decimal(delta))
    return changed


@pytest.mark.parametrize(
    ("path", "delta", "check"),
    [
        (("reads", 0, "mj"), "0.02", ["reads_recompute", "usage_matches_reads"]),
        (("reads", 0, "end"), "1", ["reads_recompute"]),
        (("lines", 0, "quantity"), "2", ["usage_matches_reads", "lines_recompute"]),
        (("lines", 1, "quantity"), "1", ["days_match", "lines_recompute"]),
        (("lines", 0, "amount"), "0.06", ["lines_recompute", "totals_recompute"]),
        (("subtotal",), "0.06", ["totals_recompute"]),
        (("gst",), "0.06", ["totals_recompute"]),
        (("total",), "0.06", ["totals_recompute"]),
    ],
)
def test_each_inconsistency_fails_its_check(path: tuple[Any, ...], delta: str, check: list[str]) -> None:
    assert failing(bump(gas.record(), path, delta)) == check


def test_amounts_within_the_tolerance_pass() -> None:
    assert failing(bump(gas.record(), ("total",), "0.05")) == []


def test_two_supply_lines_add_up_to_the_days() -> None:
    document = gas.record(days=30)
    first = {**document["lines"][1], "quantity": "10", "amount": str(gas.money(Decimal(10) * gas.SUPPLY_RATE))}
    second = {**document["lines"][1], "quantity": "20", "amount": str(gas.money(Decimal(20) * gas.SUPPLY_RATE))}
    document["lines"] = [document["lines"][0], first, second]
    assert failing(document) == []


def test_a_gst_free_line_counts_after_gst() -> None:
    concession = {"line": "credit", "label": "Concession", "amount": "-5.00", "gst_free": True}
    document = gas.record(extra_lines=[concession])
    assert failing(document) == []
    taxed = copy.deepcopy(document)
    taxed["lines"][-1]["gst_free"] = False
    assert failing(taxed) == ["totals_recompute"]


def test_overlapping_ranges_are_found() -> None:
    assert overlapping([(date(2026, 9, 1), date(2026, 9, 30)), (date(2026, 9, 30), date(2026, 10, 30))])
    assert not overlapping([(date(2026, 9, 1), date(2026, 9, 30)), (date(2026, 10, 1), date(2026, 10, 30))])


# Periods


def bill(start: tuple[int, int], end: tuple[int, int], mj: str, kind: str = "actual") -> BillUsage:
    return BillUsage(date(2026, *start), date(2026, *end), Decimal(mj), kind)  # type: ignore[arg-type]


def test_an_estimate_ending_bill_is_combined_with_the_one_that_follows() -> None:
    found = combine(
        [bill((10, 1), (10, 31), "300"), bill((8, 1), (8, 31), "100"), bill((9, 1), (9, 30), "200", "estimate")]
    )
    assert [(p.start, p.end, p.mj, p.bills, p.estimated) for p in found.periods] == [
        (date(2026, 8, 1), date(2026, 8, 31), Decimal("100"), 1, False),
        (date(2026, 9, 1), date(2026, 10, 31), Decimal("500"), 2, False),
    ]
    assert (found.gaps, found.overlap) == ((), False)


def test_a_last_bill_ending_on_an_estimate_stays_estimated_and_a_chain_continues() -> None:
    found = combine(
        [
            bill((8, 1), (8, 31), "100", "estimate"),
            bill((9, 1), (9, 30), "200", "estimate"),
            bill((10, 1), (10, 31), "300", "estimate"),
        ]
    )
    assert [(p.bills, p.estimated, p.mj) for p in found.periods] == [(3, True, Decimal("600"))]


def test_a_gap_is_listed_and_breaks_the_combining() -> None:
    found = combine([bill((8, 1), (8, 31), "100", "estimate"), bill((9, 5), (9, 30), "200")])
    assert [p.bills for p in found.periods] == [1, 1]
    assert found.periods[0].estimated
    assert found.gaps == ((date(2026, 9, 1), date(2026, 9, 4)),)


def test_an_overlap_is_flagged() -> None:
    assert combine([bill((8, 1), (8, 31), "100"), bill((8, 31), (9, 30), "200")]).overlap


def test_the_reference_takes_whole_periods_until_it_holds_enough_bills() -> None:
    periods = combine(
        [
            bill((7, 1), (7, 31), "50"),
            bill((8, 1), (8, 31), "100"),
            bill((9, 1), (9, 30), "200", "estimate"),
            bill((10, 1), (10, 31), "300"),
        ]
    ).periods
    assert [(p.start.month, p.bills) for p in reference(periods, 1)] == [(9, 2)]  # straddles: taken whole
    assert [p.start.month for p in reference(periods, 3)] == [8, 9]
    assert [p.start.month for p in reference(periods, 12)] == [7, 8, 9]
    assert reference((), 12) == ()


def test_monthly_spreads_each_period_evenly_and_keeps_every_mj() -> None:
    periods = combine([bill((9, 16), (10, 15), "100.01")]).periods
    months = monthly(periods)
    assert [(m.month, m.days_covered) for m in months] == [("2026-09", 15), ("2026-10", 15)]
    assert months[0].mj == Decimal("50.005000")
    assert sum((m.mj for m in months), Decimal(0)) == Decimal("100.01")
    assert months[0].mj_per_day == Decimal("3.333667")


def test_a_bill_inside_an_earlier_one_joins_nothing() -> None:
    found = combine(
        [bill((8, 1), (9, 30), "100"), bill((8, 10), (8, 31), "10", "estimate"), bill((9, 1), (9, 30), "20")]
    )
    assert found.overlap
    assert all(period.bills == 1 for period in found.periods)
