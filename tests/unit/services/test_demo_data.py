"""The demo's generated files (P8 spec §5.2) read back through the real parsers, and agree with each other."""

from decimal import Decimal

import yaml

from energatron.adapters import wiring
from energatron.adapters.sources.registry import PARSERS
from energatron.core.models.catalog import SourceType
from energatron.core.privacy import nmi_checksum
from energatron.services import demo


def parser(source: SourceType):  # type: ignore[no-untyped-def]
    return next(item for item in PARSERS if item.source == source)


def test_the_meter_file_is_a_sapn_download_covering_the_demo_months() -> None:
    days = demo.meter_days()
    data = demo.nem12(days)
    info = parser(SourceType.SAPN_INTERVAL).inspect(demo.nem12_filename(), data)
    assert info.coverage is not None
    assert (info.coverage.start, info.coverage.end) == (demo.FIRST_DAY, demo.LAST_DAY)
    assert demo.nem12(days) == data  # deterministic
    assert demo.DEMO_NMI + str(nmi_checksum(demo.DEMO_NMI)) == demo.DEMO_NMI11


def test_the_usage_export_totals_the_meter_data() -> None:
    days = demo.meter_days()
    export = demo.usage_export(days, wiring.load_region_profile("sa"))
    parser(SourceType.RETAILER_USAGE).inspect("demo_usage_export.csv", export)
    rows = [line.split(",") for line in export.decode().splitlines()[1:]]
    exported = sum((Decimal(row[-1]) for row in rows), Decimal(0))
    last_read = rows[-1][1][:10]
    metered = sum(
        (Decimal(f"{value:.3f}") for day, values in days.items() if day.isoformat() <= last_read for value in values),
        Decimal(0),
    )
    assert exported == metered
    for row in rows:
        assert sum((Decimal(cell) for cell in row[2:-1]), Decimal(0)) == Decimal(row[-1])


def test_gas_bills_land_and_their_records_add_up() -> None:
    periods = demo.gas_periods()
    assert len(periods) == 12 and periods[0][0].month == 11
    pdf = demo.gas_bill_pdf(periods[0][1])
    parser(SourceType.GAS_BILL).inspect("bill.pdf", pdf)
    record = demo.gas_bill_record(periods[0], 1000, "gas_bill-20251204T120000-0123abcd")
    text = demo.record_yaml(record)
    assert text.startswith(b"kind: gas_bill_record\nschema_version: 2\n")
    assert yaml.safe_load(text) == record
    parser(SourceType.GAS_BILL_RECORD).inspect("gas_bill_record-202511.yaml", text)
    lines = sum((Decimal(line["amount"]) for line in record["lines"]), Decimal(0))
    assert Decimal(record["subtotal"]) == lines
    assert Decimal(record["total"]) == Decimal(record["subtotal"]) + Decimal(record["gst"])
