"""Gas documents (P5 spec §3): `gas_bill_record`, one bill transcribed from its landed PDF, and the record skeleton
that `energatron gas-bill new` writes.

A record holds no identifier: the MIRN, account, invoice and reference numbers stay in the PDF, which the record names
only by `pdf_dataset_id`, and an unknown key is refused, so a pasted number has nowhere to go. Every number is a quoted
decimal string, kept as a `Decimal` with the places it was written with (the MJ check compares at the printed
precision), and bounded to the places the stage tables hold: money 2, quantities 6, rates 10. Reads are listed oldest
first, so the last read is the period's closing one. Rates are dollars per unit, GST-exclusive; amounts are signed.
Pure: no I/O.
"""

import re
from collections.abc import Callable
from datetime import date
from decimal import Decimal
from typing import Annotated, ClassVar, Final, Literal, Self

from pydantic import AfterValidator, BeforeValidator, ConfigDict, Field, model_validator

from energatron.core.canonical import JSONValue
from energatron.core.errors import InvalidInputError
from energatron.core.models._common import DatasetId, FrozenModel, trimmed_text
from energatron.core.persisted import Persisted, migration

GAS_BILL_PREFIX: Final = "gas_bill-"
_DECIMAL: Final = re.compile(r"-?[0-9]+(?:\.[0-9]+)?")

type ReadType = Literal["actual", "estimate"]
type GasLineKind = Literal["usage", "supply", "discount", "credit", "fee", "other"]
type GasUnit = Literal["MJ", "day"]
_EXTRACTED_BY: Final = re.compile(r"manual|agent:[A-Za-z0-9][A-Za-z0-9._:/-]{0,79}")
UNKNOWN_AGENT: Final = "agent:unknown"
"""What a v1 record's `transcribed_by: agent` becomes: v1 didn't say which model (P9 spec §4.1)."""


def _decimal_text(value: object) -> Decimal:
    """A quoted decimal string (`"12.30"`, `"-4.5"`), as a `Decimal` that keeps its places; anything else is refused."""
    if isinstance(value, Decimal):
        return value
    if not isinstance(value, str) or _DECIMAL.fullmatch(value) is None:
        raise ValueError('a decimal written as a quoted string, such as "12.30"')
    return Decimal(value)


def _places(most: int) -> Callable[[Decimal], Decimal]:
    """At most `most` decimal places: the stages' tables hold money to 2, quantities to 6 and rates to 10 (D1 §12)."""

    def check(value: Decimal) -> Decimal:
        exponent = value.as_tuple().exponent
        if not isinstance(exponent, int) or -exponent > most:
            raise ValueError(f"at most {most} decimal places")
        return value

    return check


def _non_negative(value: Decimal) -> Decimal:
    if value < 0:
        raise ValueError("must not be negative")
    return value


def _positive(value: Decimal) -> Decimal:
    if value <= 0:
        raise ValueError("must be more than 0")
    return value


def extracted_by(value: str) -> str:
    """`manual`, or `agent:<model id>` (P9 spec §4.1); anything else is refused."""
    if _EXTRACTED_BY.fullmatch(value) is None:
        raise ValueError("manual, or agent:<model id>")
    return value


def _gas_bill_id(value: str) -> str:
    if not value.startswith(GAS_BILL_PREFIX):
        raise ValueError("a gas_bill dataset ID")
    return value


Money = Annotated[Decimal, BeforeValidator(_decimal_text), AfterValidator(_places(2))]
"""A signed dollar amount, to the cent."""
Rate = Annotated[Decimal, BeforeValidator(_decimal_text), AfterValidator(_places(10))]
"""A signed price per unit."""
NonNegative = Annotated[
    Decimal, BeforeValidator(_decimal_text), AfterValidator(_non_negative), AfterValidator(_places(6))
]
Positive = Annotated[Decimal, BeforeValidator(_decimal_text), AfterValidator(_positive), AfterValidator(_places(6))]


class GasRead(FrozenModel):
    """One meter read as the bill prints it: index in m³, heating value MJ/m³, pressure factor, MJ."""

    model_config = ConfigDict(serialize_by_alias=True, validate_by_name=True)  # merged with FrozenModel's

    read_date: Annotated[date, Field(alias="date")]
    type: ReadType
    start: NonNegative
    end: NonNegative
    heating_value: Positive
    pressure_factor: Positive
    mj: NonNegative

    @model_validator(mode="after")
    def _no_rollover(self) -> Self:
        if self.end < self.start:
            raise InvalidInputError("A read ends below its start (a meter rollover isn't supported).", field="reads")
        return self


class GasLine(FrozenModel):
    """One printed line of this period's charges, GST-exclusive; `gst_free` lines are applied after GST."""

    line: GasLineKind
    label: Annotated[str, AfterValidator(trimmed_text("lines.label"))]
    quantity: NonNegative | None = None
    unit: GasUnit | None = None
    rate: Rate | None = None
    amount: Money
    gst_free: Annotated[bool, Field(strict=True)] = False

    @model_validator(mode="after")
    def _priced_lines_say_how(self) -> Self:
        needed: dict[str, GasUnit] = {"usage": "MJ", "supply": "day"}
        unit = needed.get(self.line)
        if unit is not None and (self.quantity is None or self.rate is None or self.unit != unit):
            raise InvalidInputError(f"A {self.line} line needs a quantity, a rate and the unit {unit}.", field="lines")
        if (self.quantity is None) != (self.rate is None):
            raise InvalidInputError(
                "A line with a quantity needs a rate, and one with a rate a quantity.", field="lines"
            )
        return self


class GasBillRecord(Persisted):
    """`gas_bill_record` v2: one gas bill transcribed from its PDF (P5 spec §3.2), saying who extracted it (P9 spec
    §4.1)."""

    KIND: ClassVar[str] = "gas_bill_record"
    SCHEMA_VERSION: ClassVar[int] = 2
    kind: Literal["gas_bill_record"] = "gas_bill_record"
    schema_version: Literal[2] = 2

    pdf_dataset_id: Annotated[DatasetId, AfterValidator(_gas_bill_id)]
    extracted_by: Annotated[str, Field(strict=True), AfterValidator(extracted_by)]
    retailer: Annotated[str, AfterValidator(trimmed_text("retailer"))]
    plan_name: Annotated[str, AfterValidator(trimmed_text("plan_name"))]
    issue_date: date
    period_start: date
    period_end: date
    reads: Annotated[tuple[GasRead, ...], Field(min_length=1)]
    lines: Annotated[tuple[GasLine, ...], Field(min_length=1)]
    subtotal: Money
    gst: Money
    total: Money

    @model_validator(mode="after")
    def _period_and_reads_are_ordered(self) -> Self:
        if self.period_start > self.period_end:
            raise InvalidInputError("The bill period starts after it ends.", field="period_start")
        dates = [read.read_date for read in self.reads]
        if dates != sorted(dates):
            raise InvalidInputError("The reads must be listed oldest first.", field="reads")
        return self

    @property
    def days(self) -> int:
        """The period's days, both ends included."""
        return (self.period_end - self.period_start).days + 1


SKELETON: Final = """\
# A gas bill transcribed from its PDF (P5 spec §3.2). Fill every "" value from the bill, then land this file:
#   uv run energatron land <this file> --dry-run --json, then without --dry-run.
# Rules: numbers and dates are quoted strings; rates are $ per unit without GST (c/MJ / 100); amounts are signed
# (credits negative). Transcribe only this period's charges: `total` includes GST, never a carried balance. A line
# applied after GST (a concession) gets `gst_free: true`. Never add an account, MIRN, invoice or reference number.
kind: gas_bill_record
schema_version: 2
pdf_dataset_id: {pdf_dataset_id}
extracted_by: "{extracted_by}"{pad}# manual | agent:<model id>
retailer: ""                # as printed
plan_name: ""               # as printed
issue_date: ""              # YYYY-MM-DD
period_start: ""            # YYYY-MM-DD, inclusive
period_end: ""              # YYYY-MM-DD, inclusive
reads:                      # one entry per printed read, in the bill's order
  - date: ""                # YYYY-MM-DD
    type: ""                # actual | estimate
    start: ""               # m³ index
    end: ""                 # m³ index
    heating_value: ""       # MJ/m³
    pressure_factor: ""
    mj: ""                  # as printed
lines:                      # this period's charges, GST-exclusive, as printed
  - line: usage
    label: ""
    quantity: ""            # MJ
    unit: MJ
    rate: ""                # $ per MJ
    amount: ""
  - line: supply
    label: ""
    quantity: ""            # days
    unit: day
    rate: ""                # $ per day
    amount: ""
subtotal: ""                # $ before GST
gst: ""
total: ""                   # $ including GST
"""


def skeleton(pdf_dataset_id: str, *, extracted_by: str = "") -> str:
    """The record skeleton for the PDF `pdf_dataset_id`: every value empty, so it can't land until it is filled.
    `extracted_by`, when given, is filled in (the caller validates it)."""
    pad = " " * max(1, 26 - len(f'extracted_by: "{extracted_by}"'))
    return SKELETON.format(pdf_dataset_id=pdf_dataset_id, extracted_by=extracted_by, pad=pad)


@migration("gas_bill_record", from_version=1)
def _gas_bill_record_v1_to_v2(doc: dict[str, JSONValue]) -> dict[str, JSONValue]:
    """`transcribed_by` becomes `extracted_by`: `owner` → `manual`, `agent` → `agent:unknown` (P9 spec §4.1). Any other
    value is carried over as it is, and v2's validation refuses it."""
    upgraded = {key: value for key, value in doc.items() if key != "transcribed_by"}
    who = doc.get("transcribed_by")
    mapped = {"owner": "manual", "agent": UNKNOWN_AGENT}
    upgraded["extracted_by"] = mapped.get(who, who) if isinstance(who, str) else who
    upgraded["schema_version"] = 2
    return upgraded


# The gas tables (P5 spec §6, §7): kinds and columns; their pyarrow schemas live in `services/stages/tables.py`.

_Count = Annotated[int, Field(ge=0)]

GAS_BILL_KIND: Final = "gas_bill"
GAS_BILL_VERSION: Final = 1
GAS_BILL_SORT: Final = ("period_start", "record_dataset_id")
GAS_READ_KIND: Final = "gas_bill_read"
GAS_READ_VERSION: Final = 1
GAS_READ_SORT: Final = ("record_dataset_id", "position")
GAS_LINE_KIND: Final = "gas_bill_line"
GAS_LINE_VERSION: Final = 1
GAS_LINE_SORT: Final = ("record_dataset_id", "position")
GAS_BILLS_FILENAME: Final = "bills.parquet"
GAS_READS_FILENAME: Final = "reads.parquet"
GAS_LINES_FILENAME: Final = "lines.parquet"
GAS_PERIOD_KIND: Final = "gas_period"
GAS_PERIOD_VERSION: Final = 1
GAS_PERIOD_SORT: Final = ("period_start",)
GAS_MONTH_KIND: Final = "gas_month"
GAS_MONTH_VERSION: Final = 1
GAS_MONTH_SORT: Final = ("month",)
GAS_PERIODS_FILENAME: Final = "periods.parquet"
GAS_MONTHLY_FILENAME: Final = "monthly.parquet"
GAS_SUMMARY_FILENAME: Final = "summary.json"


class GasBillsSummary(Persisted):
    """`gas_bills_summary` v1 (P5 spec §6): how many records were read, kept and superseded, the dates, the checks."""

    KIND: ClassVar[str] = "gas_bills_summary"
    SCHEMA_VERSION: ClassVar[int] = 1
    kind: Literal["gas_bills_summary"] = "gas_bills_summary"
    schema_version: Literal[1] = 1

    records: _Count
    current: _Count
    superseded: _Count
    first_period_start: date | None
    last_period_end: date | None
    checks: dict[str, bool]


class GasProfileSummary(Persisted):
    """`gas_profile_summary` v1 (P5 spec §7): coverage, gaps, estimates and the reference year."""

    KIND: ClassVar[str] = "gas_profile_summary"
    SCHEMA_VERSION: ClassVar[int] = 1
    kind: Literal["gas_profile_summary"] = "gas_profile_summary"
    schema_version: Literal[1] = 1

    periods: _Count
    coverage_start: date | None
    coverage_end: date | None
    gaps: tuple[tuple[date, date], ...]
    estimated_periods: _Count
    reference_start: date | None
    reference_end: date | None
    reference_days: _Count
    reference_bills: _Count
    reference_mj: Decimal
    partial_year: bool
    checks: dict[str, bool]
