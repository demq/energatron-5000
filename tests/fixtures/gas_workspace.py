"""The fixture gas zone (P5 spec §13): synthetic gas bill PDFs and their records, landed beside a raw zone that
`empty_raw_zone` or `build_raw_zone` made.

Three monthly bills follow on from each other: August ends on an actual read, September on an estimate (so the profile
combines it with October), October on an actual read. August's record was corrected: a first transcription with a
wrong total landed earlier and is superseded by the newer one. Every value is made up.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from energatron.core.models.catalog import SourceType
from tests.fixtures import make_gas as gas
from tests.fixtures.make_sources import make_pdf
from tests.fixtures.pipeline_workspace import land_blob


@dataclass(frozen=True)
class GasZone:
    pdfs: tuple[str, ...]  # dataset IDs, August to October
    records: tuple[str, ...]  # the current records' dataset IDs, August to October
    superseded: str


BILLS: tuple[tuple[date, int, int, tuple[int, ...], tuple[str, ...]], ...] = (
    (date(2026, 8, 1), 31, 1000, (60,), ("actual",)),
    (date(2026, 9, 1), 30, 1060, (20, 30), ("actual", "estimate")),
    (date(2026, 10, 1), 31, 1110, (45,), ("actual",)),
)


def bill_records(pdf_ids: tuple[str, ...]) -> list[dict[str, Any]]:
    """The three consistent records, one per PDF."""
    return [
        gas.record(
            period_start=start, days=days, start_index=index, volumes=volumes, read_types=kinds, pdf_dataset_id=pdf
        )
        for (start, days, index, volumes, kinds), pdf in zip(BILLS, pdf_ids, strict=True)
    ]


def build_gas_zone(root: Path) -> GasZone:
    pdfs = tuple(
        land_blob(
            root,
            SourceType.GAS_BILL,
            f"gas-{month}.pdf",
            make_pdf(creation_date=f"D:2026{month:02d}05120000+10'30'"),
        ).dataset_id
        for month in (9, 10, 11)
    )
    records = bill_records(pdfs)
    wrong = {**records[0], "total": "1.00"}
    superseded = land_blob(
        root,
        SourceType.GAS_BILL_RECORD,
        "gas-8-first.yaml",
        gas.record_bytes(wrong),
        landed_at=datetime(2026, 11, 1, tzinfo=UTC),
    ).dataset_id
    current = tuple(
        land_blob(
            root,
            SourceType.GAS_BILL_RECORD,
            f"gas-{position}.yaml",
            gas.record_bytes(record),
            landed_at=datetime(2026, 11, 2, tzinfo=UTC),
        ).dataset_id
        for position, record in enumerate(records)
    )
    return GasZone(pdfs=pdfs, records=current, superseded=superseded)
