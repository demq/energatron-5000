"""Gas bill records (P5 spec §3.3; P9 spec §4): scaffold one, check one, write a checked one, and read a bill's text.

- `new_gas_bill_record` writes the record skeleton for a landed gas bill PDF into the inbox (`data/landing/`), named
  after the PDF's dataset ID; an existing file is kept unless `force` is given. Its values are empty, so `land` refuses
  it until it is filled; `extracted_by` may be filled in.
- `check_gas_bill_record` parses a record (YAML or JSON), checks that its PDF is landed, and recomputes it from its
  own reads and lines with the `gas_bills` stage's tolerance and GST rate (P5 spec §6): "agent proposes, validators
  decide" (plan §14.5). Read-only.
- `write_gas_bill_record` runs the same checks and, only when every one passes, writes the record as canonical YAML
  into the inbox. Landing stays a separate `land`.
- `gas_bill_text` returns the text of a landed gas bill PDF, read by the extractor the caller passes (pypdf lives in
  the adapters).

A record that doesn't validate → `E_INVALID_INPUT` naming the field, never the value.
"""

from collections.abc import Callable
from datetime import date
from decimal import Decimal
from typing import ClassVar, Final, Literal, cast

import yaml

from energatron.core.canonical import JSONValue
from energatron.core.errors import ConfirmationRequiredError, InvalidInputError, SchemaTooNewError
from energatron.core.gas_bill import check_record
from energatron.core.models._common import DatasetId, RelativePosixPath
from energatron.core.models.catalog import CatalogRecord, SourceType
from energatron.core.models.gas import GasBillRecord, extracted_by, skeleton
from energatron.core.models.params import Params
from energatron.services.context import ServiceContext, ServiceResult
from energatron.services.files import atomic_write_text, make_dirs

LANDING: Final = "data/landing"
PARAMS: Final = "params.yaml"


class GasBillScaffoldResult(ServiceResult):
    """`gas_bill_scaffold_result` v1: the PDF the skeleton is for and where it was written (workspace-relative)."""

    KIND: ClassVar[str] = "gas_bill_scaffold_result"
    SCHEMA_VERSION: ClassVar[int] = 1
    kind: Literal["gas_bill_scaffold_result"] = "gas_bill_scaffold_result"
    schema_version: Literal[1] = 1

    pdf_dataset_id: DatasetId
    path: RelativePosixPath


class GasBillCheckResult(ServiceResult):
    """`gas_bill_check_result` v1 (P9 spec §4.2), for `gas-bill check` and `gas-bill write`."""

    KIND: ClassVar[str] = "gas_bill_check_result"
    SCHEMA_VERSION: ClassVar[int] = 1
    kind: Literal["gas_bill_check_result"] = "gas_bill_check_result"
    schema_version: Literal[1] = 1

    pdf_dataset_id: DatasetId
    extracted_by: str
    period_start: date
    period_end: date
    checks: dict[str, bool]
    recomputed_total: Decimal
    printed_total: Decimal
    path: RelativePosixPath | None
    written: bool
    dry_run: bool

    def failed(self) -> list[str]:
        return [name for name, passed in self.checks.items() if not passed]


class GasBillTextResult(ServiceResult):
    """`gas_bill_text_result` v1 (P9 spec §4.2): a landed gas bill PDF's text, pages joined by a form feed."""

    KIND: ClassVar[str] = "gas_bill_text_result"
    SCHEMA_VERSION: ClassVar[int] = 1
    kind: Literal["gas_bill_text_result"] = "gas_bill_text_result"
    schema_version: Literal[1] = 1

    pdf_dataset_id: DatasetId
    pages: int
    text: str


def record_filename(pdf_dataset_id: str) -> str:
    """The record's file name in the inbox."""
    return f"gas_bill_record-{pdf_dataset_id}.yaml"


def new_gas_bill_record(
    ctx: ServiceContext, pdf_dataset_id: str, *, force: bool = False, extracted: str | None = None
) -> GasBillScaffoldResult:
    """Write the record skeleton for the landed gas bill PDF `pdf_dataset_id`, with `extracted_by` filled when given."""
    if extracted is not None:
        try:
            extracted_by(extracted)
        except ValueError:
            raise InvalidInputError("extracted_by is manual, or agent:<model id>.", field="extracted_by") from None
    _require_landed_pdf(ctx, pdf_dataset_id)
    relative = f"{LANDING}/{record_filename(pdf_dataset_id)}"
    target = ctx.paths.root / relative
    if target.exists() and not force:
        raise ConfirmationRequiredError(
            "The record skeleton already exists in data/landing/: rerun with --force to overwrite it.", path=relative
        )
    make_dirs(target.parent)
    atomic_write_text(target, skeleton(pdf_dataset_id, extracted_by=extracted or ""))
    return GasBillScaffoldResult(pdf_dataset_id=pdf_dataset_id, path=relative)


def check_gas_bill_record(ctx: ServiceContext, data: bytes) -> GasBillCheckResult:
    """Parse and check the record in `data`; nothing is written."""
    record = parse_record(data)
    return _checked(ctx, record, path=None, written=False, dry_run=False)


def write_gas_bill_record(
    ctx: ServiceContext, data: bytes, *, force: bool = False, dry_run: bool = False
) -> GasBillCheckResult:
    """Check the record in `data` and, when every check passes, write it to the inbox (unless `dry_run`)."""
    record = parse_record(data)
    relative = f"{LANDING}/{record_filename(record.pdf_dataset_id)}"
    result = _checked(ctx, record, path=relative, written=False, dry_run=dry_run)
    if result.failed() or dry_run:
        return result
    target = ctx.paths.root / relative
    if target.exists() and not force:
        raise ConfirmationRequiredError(
            "A record for that PDF already exists in data/landing/: rerun with --force to overwrite it.", path=relative
        )
    make_dirs(target.parent)
    atomic_write_text(target, record_yaml(record))
    return result.model_copy(update={"written": True})


def gas_bill_text(ctx: ServiceContext, pdf_dataset_id: str, extract: Callable[[bytes], list[str]]) -> GasBillTextResult:
    """The text of the landed gas bill PDF `pdf_dataset_id`, as `extract` reads it."""
    record = _require_landed_pdf(ctx, pdf_dataset_id)
    pages = extract(ctx.blobs.get(record.stored_path))
    return GasBillTextResult(pdf_dataset_id=pdf_dataset_id, pages=len(pages), text="\f".join(pages))


def parse_record(data: bytes) -> GasBillRecord:
    """The `gas_bill_record` in `data` (YAML, so JSON too), upgraded to the current version."""
    try:
        document = yaml.safe_load(data.decode("utf-8").removeprefix("﻿"))
    except UnicodeDecodeError, yaml.YAMLError:
        raise InvalidInputError("The record isn't UTF-8 YAML or JSON.", field="record") from None
    if not isinstance(document, dict) or document.get("kind") != GasBillRecord.KIND:
        raise InvalidInputError("The document isn't a gas_bill_record.", field="kind")
    if _holds_a_date(document):
        raise InvalidInputError("A date is written unquoted: quote every date.", field="record")
    try:
        return GasBillRecord.from_document(cast(dict[str, JSONValue], document))
    except SchemaTooNewError:
        raise
    except InvalidInputError as exc:
        field = exc.details.get("field")
        raise InvalidInputError(
            "The record doesn't validate.", field=field if isinstance(field, str) else "record"
        ) from None


def record_yaml(record: GasBillRecord) -> str:
    """The record as YAML: `kind` and `schema_version` first as plain lines (how `land` recognises it), every string
    double-quoted so dates and amounts stay strings, booleans plain, empty values left out."""
    document = record.model_dump(mode="json", by_alias=True, exclude_none=True)
    rest = {key: value for key, value in document.items() if key not in ("kind", "schema_version")}
    head = f"kind: {record.kind}\nschema_version: {record.schema_version}\n"
    return head + yaml.dump(rest, Dumper=_QuotedStrings, sort_keys=False, allow_unicode=True)


def _checked(
    ctx: ServiceContext, record: GasBillRecord, *, path: str | None, written: bool, dry_run: bool
) -> GasBillCheckResult:
    _require_landed_pdf(ctx, record.pdf_dataset_id)
    params = _params(ctx)
    checks = check_record(record, tolerance=params.validation.gas_bills.tolerance, gst_rate=params.pricing.gst_rate)
    return GasBillCheckResult(
        pdf_dataset_id=record.pdf_dataset_id,
        extracted_by=record.extracted_by,
        period_start=record.period_start,
        period_end=record.period_end,
        checks={
            "reads_recompute": checks.reads_recompute,
            "usage_matches_reads": checks.usage_matches_reads,
            "days_match": checks.days_match,
            "lines_recompute": checks.lines_recompute,
            "totals_recompute": checks.totals_recompute,
        },
        recomputed_total=checks.recomputed_total,
        printed_total=record.total,
        path=path,
        written=written,
        dry_run=dry_run,
    )


def _require_landed_pdf(ctx: ServiceContext, pdf_dataset_id: str) -> CatalogRecord:
    found = next(
        (record for record in ctx.catalog.records(SourceType.GAS_BILL) if record.dataset_id == pdf_dataset_id), None
    )
    if found is None:
        raise InvalidInputError("No landed gas bill PDF has that dataset ID.", field="pdf_dataset_id")
    return found


def _params(ctx: ServiceContext) -> Params:
    try:
        document = yaml.safe_load((ctx.paths.root / PARAMS).read_text(encoding="utf-8"))
    except OSError, UnicodeDecodeError, yaml.YAMLError:
        raise InvalidInputError("params.yaml can't be read as YAML.", file=PARAMS) from None
    if not isinstance(document, dict):
        raise InvalidInputError("params.yaml isn't a mapping.", file=PARAMS)
    return Params.from_document(cast(dict[str, JSONValue], document))


def _holds_a_date(value: object) -> bool:
    """Whether YAML read an unquoted date anywhere in `value` (a date must stay a quoted string)."""
    if isinstance(value, date):
        return True
    if isinstance(value, dict):
        return any(_holds_a_date(item) for item in cast(dict[object, object], value).values())
    if isinstance(value, list):
        return any(_holds_a_date(item) for item in cast(list[object], value))
    return False


class _QuotedStrings(yaml.SafeDumper):
    """Writes every string double-quoted."""


def _quoted(dumper: yaml.SafeDumper, value: str) -> yaml.ScalarNode:
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style='"')


_QuotedStrings.add_representer(str, _quoted)
