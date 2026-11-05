"""`services.gas_bill` (P5 spec §3.3) and landing a record: `gas-bill new` writes the skeleton for a landed PDF, an
unfilled skeleton can't land and a filled record does. The workspace is `test_land`'s; the PDF and the record are
synthetic."""

from pathlib import Path

import pytest

from energatron.adapters.sources.bill_pdf import GAS_BILL
from energatron.adapters.sources.gas_bill_record import GAS_BILL_RECORD
from energatron.adapters.storage.jsonl_catalog import JsonlCatalog
from energatron.core.errors import ConfirmationRequiredError, InvalidInputError
from energatron.core.models.catalog import SourceType
from energatron.core.models.gas import skeleton
from energatron.core.models.workspace import WorkspacePaths
from energatron.services.context import ServiceContext
from energatron.services.gas_bill import GasBillScaffoldResult, new_gas_bill_record
from energatron.services.land import land
from tests.fixtures import make_gas as gas
from tests.fixtures.make_sources import make_pdf
from tests.fixtures.synthetic import synthetic_private_config
from tests.unit.services.test_context import FixedClock, build
from tests.unit.services.test_land import LandVcs, SpyBlobs
from tests.unit.services.test_land import paths as paths
from tests.unit.services.test_land import vcs as vcs


@pytest.fixture
def ctx(paths: WorkspacePaths, vcs: LandVcs) -> ServiceContext:
    return build(
        paths=paths,
        private=synthetic_private_config(),
        catalog=JsonlCatalog(paths),
        blobs=SpyBlobs(paths),
        parsers=(GAS_BILL, GAS_BILL_RECORD),
        vcs=vcs,
        clock=FixedClock(),
    )


def landed_pdf(ctx: ServiceContext, tmp_path: Path) -> str:
    pdf = tmp_path / "inbox" / "bill.pdf"
    pdf.parent.mkdir()
    pdf.write_bytes(make_pdf(creation_date="D:20261103120000+10'30'"))
    (dataset,) = land(ctx, [pdf], source=SourceType.GAS_BILL).landed
    return dataset


def test_the_skeleton_is_written_to_the_inbox_for_a_landed_pdf(ctx: ServiceContext, tmp_path: Path) -> None:
    pdf_id = landed_pdf(ctx, tmp_path)
    result = new_gas_bill_record(ctx, pdf_id)
    assert result.path == f"data/landing/gas_bill_record-{pdf_id}.yaml"
    assert (ctx.paths.root / result.path).read_text(encoding="utf-8") == skeleton(pdf_id)
    assert GasBillScaffoldResult.from_document(result.model_dump(mode="json")) == result


def test_an_id_that_is_not_a_landed_gas_bill_is_invalid_input(ctx: ServiceContext) -> None:
    with pytest.raises(InvalidInputError) as caught:
        new_gas_bill_record(ctx, gas.PDF_ID)
    assert caught.value.details["field"] == "pdf_dataset_id"
    assert not any((ctx.paths.root / "data" / "landing").glob("gas_bill_record-*"))


def test_an_existing_skeleton_is_kept_unless_forced(ctx: ServiceContext, tmp_path: Path) -> None:
    pdf_id = landed_pdf(ctx, tmp_path)
    target = ctx.paths.root / new_gas_bill_record(ctx, pdf_id).path
    target.write_text("half filled\n", encoding="utf-8")
    with pytest.raises(ConfirmationRequiredError):
        new_gas_bill_record(ctx, pdf_id)
    assert target.read_text(encoding="utf-8") == "half filled\n"
    new_gas_bill_record(ctx, pdf_id, force=True)
    assert target.read_text(encoding="utf-8") == skeleton(pdf_id)


def test_an_unfilled_skeleton_is_rejected_and_a_filled_record_lands(ctx: ServiceContext, tmp_path: Path) -> None:
    pdf_id = landed_pdf(ctx, tmp_path)
    unfilled = ctx.paths.root / new_gas_bill_record(ctx, pdf_id).path
    rejected = land(ctx, [unfilled])
    assert [(item.status, item.error and item.error.details.get("reason")) for item in rejected.items] == [
        ("rejected", "invalid_gas_bill_record")
    ]
    filled = tmp_path / "inbox" / "record.yaml"
    filled.write_bytes(gas.record_bytes(gas.record(pdf_dataset_id=pdf_id)))
    (record_id,) = land(ctx, [filled]).landed
    (record,) = ctx.catalog.records(SourceType.GAS_BILL_RECORD)
    assert record.dataset_id == record_id
    assert record.extra["pdf_dataset_id"] == pdf_id
