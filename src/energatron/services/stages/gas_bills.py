"""The `gas_bills` stage (P5 spec §6): every current gas bill record, checked against its own reads and lines.

Inputs: the landed records (`data/raw/gas_bill_record`, read whole) and the gas bill PDFs' catalog (only to check that
each record's PDF is landed; the PDFs aren't read). The newest landed record per PDF is current; older ones are
superseded corrections. Every check fails the stage: a mismatch is a transcription error the owner fixes by landing a
corrected record. Outputs: `bills.parquet`, `reads.parquet`, `lines.parquet` and `summary.json`. Workspace data: the
household's bills, never written to the tool repo. The retailer and plan name stay in the records.
"""

from decimal import Decimal
from pathlib import Path
from typing import Final, cast

import pandas as pd
import yaml

from energatron.core.canonical import JSONValue
from energatron.core.errors import InvalidInputError, SchemaTooNewError
from energatron.core.gas_bill import RecordChecks, check_record, overlapping, recomputed_mj
from energatron.core.models.catalog import CatalogRecord, SourceType
from energatron.core.models.gas import (
    GAS_BILL_KIND,
    GAS_BILL_SORT,
    GAS_BILL_VERSION,
    GAS_BILLS_FILENAME,
    GAS_LINE_KIND,
    GAS_LINE_SORT,
    GAS_LINE_VERSION,
    GAS_LINES_FILENAME,
    GAS_READ_KIND,
    GAS_READ_SORT,
    GAS_READ_VERSION,
    GAS_READS_FILENAME,
    GAS_SUMMARY_FILENAME,
    GasBillRecord,
    GasBillsSummary,
)
from energatron.core.models.params import GasBillsValidation
from energatron.services.files import write_json, write_parquet
from energatron.services.stages.raw_zone import catalog_records, read_raw_zone
from energatron.services.stages.runner import StageRun, StageSpec
from energatron.services.stages.tables import GAS_BILL_SCHEMA, GAS_LINE_SCHEMA, GAS_READ_SCHEMA

KNOWN_DETECTOR_VERSIONS: Final = frozenset({1})
CHECKS: Final = (
    "blobs_match_catalog",
    "detector_known",
    "records_valid",
    "pdf_landed",
    "reads_recompute",
    "usage_matches_reads",
    "days_match",
    "lines_recompute",
    "totals_recompute",
    "periods_disjoint",
)


def _run(run: StageRun) -> None:
    tolerance = GasBillsValidation.model_validate(run.params["validation.gas_bills"]).tolerance
    gst_rate = Decimal(str(run.params["pricing.gst_rate"]))
    zone = read_raw_zone(run.input("records"), SourceType.GAS_BILL_RECORD, KNOWN_DETECTOR_VERSIONS)
    pdfs = {record.dataset_id for record in catalog_records(run.input("pdfs"), SourceType.GAS_BILL)}

    parsed: list[tuple[CatalogRecord, GasBillRecord]] = []
    invalid: list[str] = []
    for catalog_record, data in zone.datasets:
        record = _parse(data)
        if record is None:
            invalid.append(catalog_record.dataset_id)
        else:
            parsed.append((catalog_record, record))
    current = _current(parsed)
    results = {ident: check_record(record, tolerance=tolerance, gst_rate=gst_rate) for ident, record in current}
    failing: dict[str, list[str]] = {name: [] for name in CHECKS}
    for ident, record in current:
        if record.pdf_dataset_id not in pdfs:
            failing["pdf_landed"].append(ident)
        for name in ("reads_recompute", "usage_matches_reads", "days_match", "lines_recompute", "totals_recompute"):
            if not getattr(results[ident], name):
                failing[name].append(ident)
    disjoint = not overlapping((record.period_start, record.period_end) for _, record in current)
    checks = {
        "blobs_match_catalog": not zone.mismatched,
        "detector_known": not zone.unknown_detector,
        "records_valid": not invalid,
        **{name: not failing[name] for name in CHECKS[3:-1]},
        "periods_disjoint": disjoint,
    }

    output = run.output("bills")
    _write_tables(output, current, results)
    periods = [(record.period_start, record.period_end) for _, record in current]
    summary = GasBillsSummary(
        records=len(parsed) + len(invalid),
        current=len(current),
        superseded=len(parsed) - len(current),
        first_period_start=min((start for start, _ in periods), default=None),
        last_period_end=max((end for _, end in periods), default=None),
        checks=checks,
    )
    write_json(output / GAS_SUMMARY_FILENAME, cast(JSONValue, summary.model_dump(mode="json")))
    run.check("blobs_match_catalog", checks["blobs_match_catalog"], mismatched=list(zone.mismatched))
    run.check("detector_known", checks["detector_known"], unknown=list(zone.unknown_detector))
    run.check("records_valid", checks["records_valid"], invalid=list(invalid))
    for name in CHECKS[3:-1]:
        run.check(name, checks[name], records=list(failing[name]))
    run.check("periods_disjoint", disjoint)


def _parse(data: bytes) -> GasBillRecord | None:
    try:
        document = yaml.safe_load(data.decode("utf-8"))
        return GasBillRecord.from_document(document) if isinstance(document, dict) else None
    except UnicodeDecodeError, yaml.YAMLError, InvalidInputError, SchemaTooNewError:
        return None


def _current(parsed: list[tuple[CatalogRecord, GasBillRecord]]) -> list[tuple[str, GasBillRecord]]:
    """Per PDF, the newest landed record (by `landed_at`, then dataset ID), in period order."""
    newest: dict[str, tuple[CatalogRecord, GasBillRecord]] = {}
    for catalog_record, record in parsed:
        held = newest.get(record.pdf_dataset_id)
        key = (catalog_record.landed_at, catalog_record.dataset_id)
        if held is None or key > (held[0].landed_at, held[0].dataset_id):
            newest[record.pdf_dataset_id] = (catalog_record, record)
    ordered = sorted(newest.values(), key=lambda found: (found[1].period_start, found[0].dataset_id))
    return [(catalog_record.dataset_id, record) for catalog_record, record in ordered]


def _write_tables(output: Path, current: list[tuple[str, GasBillRecord]], results: dict[str, RecordChecks]) -> None:
    bills: list[dict[str, object]] = []
    reads: list[dict[str, object]] = []
    lines: list[dict[str, object]] = []
    for ident, record in current:
        bills.append(
            {
                "record_dataset_id": ident,
                "pdf_dataset_id": record.pdf_dataset_id,
                "issue_date": record.issue_date,
                "period_start": record.period_start,
                "period_end": record.period_end,
                "days": record.days,
                "mj": sum((read.mj for read in record.reads), Decimal(0)),
                "end_read_type": record.reads[-1].type,
                "estimated_reads": sum(read.type == "estimate" for read in record.reads),
                "subtotal": record.subtotal,
                "gst": record.gst,
                "total": record.total,
                "recomputed_total": results[ident].recomputed_total,
            }
        )
        reads.extend(
            {
                "record_dataset_id": ident,
                "position": position,
                "read_date": read.read_date,
                "read_type": read.type,
                "start": read.start,
                "end": read.end,
                "heating_value": read.heating_value,
                "pressure_factor": read.pressure_factor,
                "mj": read.mj,
                "recomputed_mj": recomputed_mj(read),
            }
            for position, read in enumerate(record.reads, start=1)
        )
        lines.extend(
            {
                "record_dataset_id": ident,
                "position": position,
                "line": line.line,
                "label": line.label,
                "quantity": line.quantity,
                "unit": line.unit,
                "rate": line.rate,
                "amount": line.amount,
                "gst_free": line.gst_free,
            }
            for position, line in enumerate(record.lines, start=1)
        )
    for rows, schema, name, kind, version, sort in (
        (bills, GAS_BILL_SCHEMA, GAS_BILLS_FILENAME, GAS_BILL_KIND, GAS_BILL_VERSION, GAS_BILL_SORT),
        (reads, GAS_READ_SCHEMA, GAS_READS_FILENAME, GAS_READ_KIND, GAS_READ_VERSION, GAS_READ_SORT),
        (lines, GAS_LINE_SCHEMA, GAS_LINES_FILENAME, GAS_LINE_KIND, GAS_LINE_VERSION, GAS_LINE_SORT),
    ):
        write_parquet(
            pd.DataFrame(rows, columns=list(schema.names)),
            output / name,
            kind=kind,
            schema_version=version,
            sort_by=sort,
            schema=schema,
        )


SPEC: Final = StageSpec(
    name="gas_bills",
    inputs={"records": "data/raw/gas_bill_record", "pdfs": "data/raw/gas_bill"},
    params=("validation.gas_bills", "pricing.gst_rate"),
    outputs={"bills": "data/interim/gas_bills"},
    impl=_run,
)
