"""`energatron gas-bill new <pdf_dataset_id>`: scaffold the `gas_bill_record` YAML for a landed gas bill PDF (P5 spec
§3.3).

Read-only on the raw zone and the catalog, so it takes no lock. It writes one file, the record skeleton, in the inbox
(`data/landing/`), named after the PDF's dataset ID; an existing file is kept unless `force` is given. The skeleton's
values are empty, so `land` refuses it until it is filled.
"""

from typing import ClassVar, Final, Literal

from energatron.core.errors import ConfirmationRequiredError, InvalidInputError
from energatron.core.models._common import DatasetId, RelativePosixPath
from energatron.core.models.catalog import SourceType
from energatron.core.models.gas import skeleton
from energatron.services.context import ServiceContext, ServiceResult
from energatron.services.files import atomic_write_text, make_dirs

LANDING: Final = "data/landing"


class GasBillScaffoldResult(ServiceResult):
    """`gas_bill_scaffold_result` v1: the PDF the skeleton is for and where it was written (workspace-relative)."""

    KIND: ClassVar[str] = "gas_bill_scaffold_result"
    SCHEMA_VERSION: ClassVar[int] = 1
    kind: Literal["gas_bill_scaffold_result"] = "gas_bill_scaffold_result"
    schema_version: Literal[1] = 1

    pdf_dataset_id: DatasetId
    path: RelativePosixPath


def record_filename(pdf_dataset_id: str) -> str:
    """The skeleton's file name in the inbox."""
    return f"gas_bill_record-{pdf_dataset_id}.yaml"


def new_gas_bill_record(ctx: ServiceContext, pdf_dataset_id: str, *, force: bool = False) -> GasBillScaffoldResult:
    """Write the record skeleton for the landed gas bill PDF `pdf_dataset_id`."""
    landed = {record.dataset_id for record in ctx.catalog.records(SourceType.GAS_BILL)}
    if pdf_dataset_id not in landed:
        raise InvalidInputError("No landed gas bill PDF has that dataset ID.", field="pdf_dataset_id")
    relative = f"{LANDING}/{record_filename(pdf_dataset_id)}"
    target = ctx.paths.root / relative
    if target.exists() and not force:
        raise ConfirmationRequiredError(
            "The record skeleton already exists in data/landing/: rerun with --force to overwrite it.", path=relative
        )
    make_dirs(target.parent)
    atomic_write_text(target, skeleton(pdf_dataset_id))
    return GasBillScaffoldResult(pdf_dataset_id=pdf_dataset_id, path=relative)
