"""Gas bills checked and turned into usage periods (P5 spec §6, §7). Pure: no I/O.

- **Checks** of one record recompute it from its own reads and lines: each read's MJ from its index, heating value and
  pressure factor, rounded half up to the places the bill printed and allowed one unit of that place; the usage lines
  against the reads; the supply days against the period; every priced line's amount; and the subtotal, GST and total.
  Money is compared within the tolerance (`validation.gas_bills.tolerance`).
- **Periods:** a bill whose last read is an estimate is combined with the next bill when that bill starts the day after
  it ends; the next actual read corrects the estimate, so the combined MJ is metered usage. A bill that doesn't follow
  on leaves a gap (listed) or overlaps (an error the caller reports).
- **Reference:** the newest periods until they hold at least the configured number of bills; a combined period is taken
  whole. Its days are the sum of the periods' days.
- **Monthly:** each period's MJ spread evenly over its days, to six places, the period's last month taking the rest.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from itertools import pairwise
from typing import Final

from energatron.core.models.gas import GasBillRecord, GasRead, ReadType

CENT: Final = Decimal("0.01")
SIX_PLACES: Final = Decimal("0.000001")
ONE_DAY: Final = timedelta(days=1)


def money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def printed_unit(value: Decimal) -> Decimal:
    """One unit of the last place `value` was written with: `Decimal("583.17")` → `0.01`, `Decimal("583")` → `1`."""
    exponent = value.as_tuple().exponent
    assert isinstance(exponent, int)  # a finite decimal
    return Decimal(1).scaleb(min(exponent, 0))


def recomputed_mj(read: GasRead) -> Decimal:
    """The read's MJ from its index, heating value and pressure factor, at the places the bill printed its MJ."""
    exact = (read.end - read.start) * read.heating_value * read.pressure_factor
    return exact.quantize(printed_unit(read.mj), rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class RecordChecks:
    """The P5 spec §6 checks of one record, and the total recomputed from its lines."""

    reads_recompute: bool
    usage_matches_reads: bool
    days_match: bool
    lines_recompute: bool
    totals_recompute: bool
    recomputed_total: Decimal

    def passed(self) -> bool:
        return all(
            (
                self.reads_recompute,
                self.usage_matches_reads,
                self.days_match,
                self.lines_recompute,
                self.totals_recompute,
            )
        )


def check_record(record: GasBillRecord, *, tolerance: Decimal, gst_rate: Decimal) -> RecordChecks:
    """Recompute `record` from its own reads and lines (P5 spec §6 step 4)."""
    reads_ok = all(abs(recomputed_mj(read) - read.mj) <= printed_unit(read.mj) for read in record.reads)
    usage = [line for line in record.lines if line.line == "usage"]
    read_mj = sum((read.mj for read in record.reads), Decimal(0))
    usage_mj = sum((line.quantity or Decimal(0) for line in usage), Decimal(0))
    places = [printed_unit(read.mj) for read in record.reads] + [
        printed_unit(line.quantity) for line in usage if line.quantity is not None
    ]
    usage_ok = abs(usage_mj - read_mj) <= max(places)
    supply_days = sum((line.quantity or Decimal(0) for line in record.lines if line.line == "supply"), Decimal(0))
    days_ok = supply_days == record.days
    lines_ok = all(
        abs(money(line.quantity * line.rate) - line.amount) <= tolerance
        for line in record.lines
        if line.quantity is not None and line.rate is not None
    )
    taxed = sum((line.amount for line in record.lines if not line.gst_free), Decimal(0))
    after_gst = sum((line.amount for line in record.lines if line.gst_free), Decimal(0))
    totals_ok = (
        abs(taxed - record.subtotal) <= tolerance
        and abs(money(record.subtotal * gst_rate) - record.gst) <= tolerance
        and abs(record.subtotal + record.gst + after_gst - record.total) <= tolerance
    )
    return RecordChecks(
        reads_recompute=reads_ok,
        usage_matches_reads=usage_ok,
        days_match=days_ok,
        lines_recompute=lines_ok,
        totals_recompute=totals_ok,
        recomputed_total=taxed + money(taxed * gst_rate) + after_gst,
    )


def overlapping(periods: Iterable[tuple[date, date]]) -> bool:
    """Whether any two inclusive date ranges share a day."""
    ordered = sorted(periods)
    return any(later[0] <= earlier[1] for earlier, later in pairwise(ordered))


# Periods (P5 spec §7)


@dataclass(frozen=True)
class BillUsage:
    """What the profile needs from one bill: its period, its MJ and the type of its last read."""

    period_start: date
    period_end: date
    mj: Decimal
    end_read_type: ReadType


@dataclass(frozen=True)
class UsagePeriod:
    """One profile period: one bill, or a bill ending on an estimate combined with the bills that follow on."""

    start: date
    end: date
    mj: Decimal
    bills: int
    estimated: bool

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    @property
    def mj_per_day(self) -> Decimal:
        return (self.mj / self.days).quantize(SIX_PLACES, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class Periods:
    """The combined periods in date order, the gaps between bills (inclusive date pairs), and whether bills overlap."""

    periods: tuple[UsagePeriod, ...]
    gaps: tuple[tuple[date, date], ...]
    overlap: bool


def combine(bills: Sequence[BillUsage]) -> Periods:
    """The usage periods of `bills` (P5 spec §7): a bill ending on an estimate joins the next one that follows on."""
    periods: list[UsagePeriod] = []
    gaps: list[tuple[date, date]] = []
    overlap = False
    current: UsagePeriod | None = None
    previous_end: date | None = None
    for bill in sorted(bills, key=lambda found: (found.period_start, found.period_end)):
        follows = previous_end is not None and bill.period_start == previous_end + ONE_DAY
        if previous_end is not None and bill.period_start <= previous_end:
            overlap = True
        elif previous_end is not None and not follows:
            gaps.append((previous_end + ONE_DAY, bill.period_start - ONE_DAY))
        joins = current is not None and current.estimated and bill.period_start == current.end + ONE_DAY and not overlap
        if current is not None and joins:
            current = UsagePeriod(
                start=current.start,
                end=bill.period_end,
                mj=current.mj + bill.mj,
                bills=current.bills + 1,
                estimated=bill.end_read_type == "estimate",
            )
        else:
            if current is not None:
                periods.append(current)
            current = UsagePeriod(
                start=bill.period_start,
                end=bill.period_end,
                mj=bill.mj,
                bills=1,
                estimated=bill.end_read_type == "estimate",
            )
        previous_end = bill.period_end if previous_end is None else max(previous_end, bill.period_end)
    if current is not None:
        periods.append(current)
    return Periods(periods=tuple(periods), gaps=tuple(gaps), overlap=overlap)


def reference(periods: Sequence[UsagePeriod], bills: int) -> tuple[UsagePeriod, ...]:
    """The newest periods, in date order, until they hold at least `bills` bills (a combined period counts whole)."""
    chosen: list[UsagePeriod] = []
    held = 0
    for period in reversed(periods):
        if held >= bills:
            break
        chosen.append(period)
        held += period.bills
    return tuple(reversed(chosen))


@dataclass(frozen=True)
class MonthUsage:
    month: str  # YYYY-MM
    days_covered: int
    mj: Decimal

    @property
    def mj_per_day(self) -> Decimal:
        return (self.mj / self.days_covered).quantize(SIX_PLACES, rounding=ROUND_HALF_UP)


def monthly(periods: Iterable[UsagePeriod]) -> tuple[MonthUsage, ...]:
    """Each period's MJ spread evenly over its days and summed per calendar month, in month order."""
    days: dict[str, int] = {}
    mj: dict[str, Decimal] = {}
    for period in periods:
        counts: dict[str, int] = {}
        day = period.start
        while day <= period.end:
            key = f"{day.year:04d}-{day.month:02d}"
            counts[key] = counts.get(key, 0) + 1
            day += ONE_DAY
        months = sorted(counts)
        given = Decimal(0)
        for index, key in enumerate(months):
            if index == len(months) - 1:
                share = period.mj - given
            else:
                share = (period.mj * counts[key] / period.days).quantize(SIX_PLACES, rounding=ROUND_HALF_UP)
                given += share
            days[key] = days.get(key, 0) + counts[key]
            mj[key] = mj.get(key, Decimal(0)) + share
    return tuple(MonthUsage(month=key, days_covered=days[key], mj=mj[key]) for key in sorted(days))
