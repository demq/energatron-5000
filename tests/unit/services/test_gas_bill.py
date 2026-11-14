"""`services.gas_bill` (P5 spec §3.3) and landing a record: `gas-bill new` writes the skeleton for a landed PDF, an
unfilled skeleton can't land and a filled record does. The workspace is `test_land`'s; the PDF and the record are
synthetic."""

import json
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from energatron.adapters.sources.bill_pdf import GAS_BILL
from energatron.adapters.sources.gas_bill_record import GAS_BILL_RECORD
from energatron.adapters.storage.jsonl_catalog import JsonlCatalog
from energatron.core.errors import ConfirmationRequiredError, InvalidInputError
from energatron.core.models.catalog import SourceType
from energatron.core.models.gas import GasBillRecord, skeleton
from energatron.core.models.workspace import WorkspacePaths
from energatron.services.context import ServiceContext
from energatron.services.gas_bill import (
    GasBillScaffoldResult,
    check_gas_bill_record,
    gas_bill_text,
    new_gas_bill_record,
    parse_record,
    record_filename,
    write_gas_bill_record,
)
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


# P9 spec §4: extracted_by in the skeleton, check, write and text


def test_the_skeleton_can_say_who_extracts_it(ctx: ServiceContext, tmp_path: Path) -> None:
    pdf_id = landed_pdf(ctx, tmp_path)
    result = new_gas_bill_record(ctx, pdf_id, extracted="agent:test-model")
    assert 'extracted_by: "agent:test-model"' in (ctx.paths.root / result.path).read_text()
    with pytest.raises(InvalidInputError) as caught:
        new_gas_bill_record(ctx, pdf_id, force=True, extracted="robot")
    assert caught.value.details == {"field": "extracted_by"}


def test_a_consistent_record_passes_every_check_and_writes_nothing(ctx: ServiceContext, tmp_path: Path) -> None:
    pdf_id = landed_pdf(ctx, tmp_path)
    result = check_gas_bill_record(ctx, gas.record_bytes(gas.record(pdf_dataset_id=pdf_id)))
    assert result.failed() == [] and all(result.checks.values()) and len(result.checks) == 5
    assert (result.extracted_by, result.path, result.written) == ("agent:test-model", None, False)
    assert result.recomputed_total == result.printed_total
    assert not (ctx.paths.root / "data/landing" / record_filename(pdf_id)).exists()


def test_a_wrong_total_or_read_fails_its_check(ctx: ServiceContext, tmp_path: Path) -> None:
    pdf_id = landed_pdf(ctx, tmp_path)
    document = gas.record(pdf_dataset_id=pdf_id)
    document["total"] = str(Decimal(document["total"]) + 1)
    assert check_gas_bill_record(ctx, gas.record_bytes(document)).failed() == ["totals_recompute"]
    document = gas.record(pdf_dataset_id=pdf_id)
    document["reads"][0]["mj"] = str(Decimal(document["reads"][0]["mj"]) + 5)
    assert "reads_recompute" in check_gas_bill_record(ctx, gas.record_bytes(document)).failed()


def test_json_on_stdin_is_a_record_too(ctx: ServiceContext, tmp_path: Path) -> None:
    pdf_id = landed_pdf(ctx, tmp_path)
    data = json.dumps(gas.record(pdf_dataset_id=pdf_id)).encode()
    assert check_gas_bill_record(ctx, data).failed() == []


@pytest.mark.parametrize(
    ("data", "field"),
    [
        (b"\xff", "record"),
        (b"- a list\n", "kind"),
        (b"kind: offer_manual\n", "kind"),
        (b"kind: gas_bill_record\nschema_version: 2\nissue_date: 2026-10-04\n", "record"),
    ],
)
def test_a_document_that_is_not_a_record(ctx: ServiceContext, data: bytes, field: str) -> None:
    with pytest.raises(InvalidInputError) as caught:
        check_gas_bill_record(ctx, data)
    assert caught.value.details == {"field": field}


def test_an_invalid_record_names_the_field_not_the_value(ctx: ServiceContext, tmp_path: Path) -> None:
    pdf_id = landed_pdf(ctx, tmp_path)
    with pytest.raises(InvalidInputError) as caught:
        check_gas_bill_record(ctx, gas.record_bytes(gas.record(pdf_dataset_id=pdf_id, extracted_by="<secret-ish>")))
    assert "<secret-ish>" not in str(caught.value) + json.dumps(caught.value.details)


def test_a_record_for_a_pdf_that_is_not_landed(ctx: ServiceContext) -> None:
    with pytest.raises(InvalidInputError) as caught:
        check_gas_bill_record(ctx, gas.record_bytes(gas.record()))
    assert caught.value.details == {"field": "pdf_dataset_id"}


def test_write_writes_canonical_yaml_that_lands(ctx: ServiceContext, tmp_path: Path) -> None:
    pdf_id = landed_pdf(ctx, tmp_path)
    data = gas.record_bytes(gas.record(pdf_dataset_id=pdf_id))
    dry = write_gas_bill_record(ctx, data, dry_run=True)
    target = ctx.paths.root / "data/landing" / record_filename(pdf_id)
    assert (dry.written, dry.path, target.exists()) == (False, f"data/landing/{record_filename(pdf_id)}", False)
    result = write_gas_bill_record(ctx, data)
    assert result.written and target.read_text().startswith("kind: gas_bill_record\nschema_version: 2\n")
    assert GasBillRecord.from_document(yaml.safe_load(target.read_text())) == parse_record(data)
    with pytest.raises(ConfirmationRequiredError):
        write_gas_bill_record(ctx, data)
    assert write_gas_bill_record(ctx, data, force=True).written
    (landed,) = land(ctx, [target], source=None).landed
    record = next(item for item in ctx.catalog.records(SourceType.GAS_BILL_RECORD) if item.dataset_id == landed)
    assert record.extra["extracted_by"] == "agent:test-model" and record.detector.version == 2


def test_write_refuses_a_failing_record(ctx: ServiceContext, tmp_path: Path) -> None:
    pdf_id = landed_pdf(ctx, tmp_path)
    document = gas.record(pdf_dataset_id=pdf_id)
    document["gst"] = str(Decimal(document["gst"]) + 1)
    result = write_gas_bill_record(ctx, gas.record_bytes(document))
    assert result.failed() and not result.written
    assert not (ctx.paths.root / "data/landing" / record_filename(pdf_id)).exists()


def test_a_gst_free_line_round_trips(ctx: ServiceContext, tmp_path: Path) -> None:
    pdf_id = landed_pdf(ctx, tmp_path)
    concession = {"line": "credit", "label": "Concession", "amount": "-5.00", "gst_free": True}
    data = gas.record_bytes(gas.record(pdf_dataset_id=pdf_id, extra_lines=[concession]))
    assert write_gas_bill_record(ctx, data).written
    text = (ctx.paths.root / "data/landing" / record_filename(pdf_id)).read_text()
    assert '"gst_free": true' in text and parse_record(text.encode()).lines[-1].gst_free is True


def test_text_reads_a_landed_pdf_with_the_given_extractor(ctx: ServiceContext, tmp_path: Path) -> None:
    pdf_id = landed_pdf(ctx, tmp_path)
    seen: list[bytes] = []

    def extract(data: bytes) -> list[str]:
        seen.append(data)
        return ["page one", "page two"]

    result = gas_bill_text(ctx, pdf_id, extract)
    assert (result.pages, result.text) == (2, "page one\fpage two") and seen[0].startswith(b"%PDF")
    with pytest.raises(InvalidInputError):
        gas_bill_text(ctx, "gas_bill-20261101T000000-ffffffff", extract)
