"""The synthetic fixtures (D3 §12): made-up identifiers with a valid checksum digit, a matching private config, and
the NEM12 generator (§12.1)."""

import json
from datetime import date, datetime, timedelta

import pytest

from energatron.core.models.private import PrivateConfig
from energatron.core.privacy import nmi_checksum
from tests.fixtures.make_nem12 import (
    Nem12Spec,
    QualityRange,
    dst_spec,
    make_nem12,
    nem12_header_dt,
    sa_dst_dates,
    sapn_filename,
)
from tests.fixtures.make_sources import (
    SummaryRow,
    make_cdr_plan_detail,
    make_pdf,
    make_retailer_txn_csv,
    make_retailer_usage_csv,
    make_sapn_summary_csv,
)
from tests.fixtures.synthetic import (
    SYNTHETIC_ACCOUNT,
    SYNTHETIC_NMI,
    SYNTHETIC_NMI11,
    SYNTHETIC_SERIAL,
    synthetic_private_config,
)


def test_the_synthetic_nmi_has_no_i_or_o_and_a_pinned_checksum_digit() -> None:
    assert SYNTHETIC_NMI == "ZZTEST0001"
    assert "I" not in SYNTHETIC_NMI and "O" not in SYNTHETIC_NMI
    assert nmi_checksum(SYNTHETIC_NMI) == 6
    assert SYNTHETIC_NMI11 == "ZZTEST00016"


def test_the_private_config_builder_matches_the_identifiers() -> None:
    private = synthetic_private_config()
    assert isinstance(private, PrivateConfig)
    assert private.premises.nmi == SYNTHETIC_NMI11
    assert private.premises.meter_serials == (SYNTHETIC_SERIAL,)
    assert private.accounts.electricity == (SYNTHETIC_ACCOUNT,)
    assert private.accounts.gas == ()


def test_the_builder_takes_overrides() -> None:
    private = synthetic_private_config(meter_serials=(), electricity_accounts=(), gas_accounts=("900000000002",))
    assert private.premises.meter_serials == ()
    assert private.accounts.gas == ("900000000002",)


# --- `make_nem12` (D3 §12.1) ----------------------------------------------------------------------------------------

START = date(2025, 1, 1)


def records(data: bytes) -> list[list[str]]:
    return [line.split(",") for line in data.decode("utf-8").split("\n") if line.strip()]


def test_the_same_spec_gives_the_same_bytes_and_another_seed_other_values() -> None:
    spec = Nem12Spec(start=START, days=3)
    assert make_nem12(spec) == make_nem12(Nem12Spec(start=START, days=3))
    assert make_nem12(spec) != make_nem12(Nem12Spec(start=START, days=3, seed=1))


def test_a_default_file_is_200_then_one_300_per_day_then_900() -> None:
    data = make_nem12(Nem12Spec(start=START, days=3))
    rows = records(data)
    assert [row[0] for row in rows] == ["200", "300", "300", "300", "900"]
    header = rows[0]
    assert len(header) == 10
    assert header[1] == SYNTHETIC_NMI and header[4] == "E1" and header[6] == SYNTHETIC_SERIAL
    assert header[7] == "KWH" and header[8] == "5"
    assert [row[1] for row in rows[1:4]] == ["20250101", "20250102", "20250103"]
    assert data.endswith(b"900\n")


@pytest.mark.parametrize("minutes", [5, 15, 30])
def test_each_300_has_2_plus_values_plus_5_fields(minutes: int) -> None:
    rows = records(make_nem12(Nem12Spec(start=START, days=2, interval_minutes=minutes)))
    for row in rows:
        if row[0] == "300":
            assert len(row) == 2 + 1440 // minutes + 5
            values = [float(value) for value in row[2 : 2 + 1440 // minutes]]
            assert all(value >= 0 for value in values)
            assert all(len(text.split(".")[1]) == 3 for text in row[2 : 2 + 1440 // minutes])
            assert row[-5] == "A" and row[-4] == "" and row[-3] == ""
            assert len(row[-2]) == 14


def test_the_default_update_time_is_the_next_day_at_0500() -> None:
    rows = records(make_nem12(Nem12Spec(start=START, days=1)))
    assert rows[1][-2] == "20250102050000"


def test_repeated_200s_come_with_a_blank_line_before_each_repeat() -> None:
    data = make_nem12(Nem12Spec(start=START, days=5, repeat_200_every=2)).decode("utf-8")
    lines = data.split("\n")
    header_lines = [index for index, line in enumerate(lines) if line.startswith("200,")]
    assert len(header_lines) == 3
    assert len({lines[index] for index in header_lines}) == 1  # identical headers
    assert all(lines[index - 1] == "" for index in header_lines[1:])
    no_blank = make_nem12(Nem12Spec(start=START, days=5, repeat_200_every=2, blank_line_before_repeat=False))
    assert b"\n\n" not in no_blank


def test_v_days_get_their_400s_and_quality_v() -> None:
    v_day = START + timedelta(days=1)
    ranges = (QualityRange(1, 10, "A"), QualityRange(11, 288, "E52", "51"))
    rows = records(make_nem12(Nem12Spec(start=START, days=3, v_days={v_day: ranges})))
    kinds = [row[0] for row in rows]
    assert kinds == ["200", "300", "300", "400", "400", "300", "900"]
    assert rows[2][-5] == "V"
    assert rows[3][:4] == ["400", "1", "10", "A"] and len(rows[3]) == 6
    assert rows[4][:5] == ["400", "11", "288", "E52", "51"]


def test_options_header_suffixes_line_endings_and_no_trailer() -> None:
    spec = Nem12Spec(start=START, days=2, header_100=True, suffixes=("E1", "B1"), line_ending="\r\n", trailer_900=False)
    data = make_nem12(spec)
    assert b"\r\n" in data and b"\n" not in data.replace(b"\r\n", b"")
    rows = [line.split(",") for line in data.decode("utf-8").split("\r\n") if line]
    assert rows[0][:2] == ["100", "NEM12"] and rows[0][2] == nem12_header_dt(spec).strftime("%Y%m%d%H%M")
    assert [row[4] for row in rows if row[0] == "200"] == ["E1", "B1"]
    assert rows[-1][0] == "300"


def test_the_header_time_comes_after_every_update_time() -> None:
    spec = Nem12Spec(start=START, days=2, header_100=True)
    assert nem12_header_dt(spec) > datetime(2025, 1, 3, 5, 0, 0)
    assert nem12_header_dt(spec).second == 0


def test_a_custom_update_time() -> None:
    spec = Nem12Spec(start=START, days=1, update_dt=lambda day: datetime(day.year, day.month, day.day, 23, 59, 58))
    assert records(make_nem12(spec))[1][-2] == "20250101235958"


def test_sapn_filename_adds_the_checksum_digit() -> None:
    name = sapn_filename(
        "DETAILED", SYNTHETIC_NMI, (date(2024, 10, 23), date(2026, 10, 23)), datetime(2026, 10, 23, 10, 3, 45)
    )
    assert name == f"{SYNTHETIC_NMI11}_20241023_20261023_20261023100345_SAPN_DETAILED.csv"
    summary = sapn_filename(
        "SUMMARY", SYNTHETIC_NMI, (date(2025, 1, 1), date(2025, 2, 1)), datetime(2025, 2, 1), ext="pdf"
    )
    assert summary.endswith("_20250201000000_SAPN_SUMMARY.pdf")


def test_sa_dst_dates_2025() -> None:
    assert sa_dst_dates(2025) == (date(2025, 4, 6), date(2025, 10, 5))


def test_dst_spec_covers_both_changes_three_days_either_side() -> None:
    spec = dst_spec(2025, interval_minutes=30)
    assert spec.start == date(2025, 4, 3)
    assert spec.start + timedelta(days=spec.days - 1) == date(2025, 10, 8)
    assert spec.interval_minutes == 30
    rows = records(make_nem12(spec))
    assert all(len(row) == 2 + 48 + 5 for row in rows if row[0] == "300")  # NEM12 days are always 24 h


# --- `make_sources` (D3 §12.2) --------------------------------------------------------------------------------------


def test_every_source_generator_is_deterministic() -> None:
    rows = (SummaryRow(date(2025, 1, 1), date(2025, 1, 31)),)
    entries = (("Payment - Synthetic", datetime(2025, 1, 2), "1.00"),)
    makers = (
        lambda: make_sapn_summary_csv(rows),
        lambda: make_retailer_usage_csv(3, blank_last=True),
        lambda: make_retailer_txn_csv(entries),
        lambda: make_pdf(creation_date="D:2025", xmp="2025-01-01", info_in_objstm=True, xmp_compressed=True),
        lambda: make_cdr_plan_detail(pretty=True),
    )
    for make in makers:
        assert make() == make()


def test_the_summary_csv_is_11_fields_crlf_without_a_header() -> None:
    data = make_sapn_summary_csv((SummaryRow(date(2025, 1, 1), date(2025, 1, 31)),))
    assert data.endswith(b"\r\n") and data.count(b"\n") == 1
    fields = data.decode().strip().split(",")
    assert len(fields) == 11
    assert fields[:3] == [SYNTHETIC_NMI11, SYNTHETIC_SERIAL, "kWh"]
    assert fields[4:6] == ["01/01/2025", "31/01/2025"] and fields[10] == "kW"


def test_the_usage_export_has_one_register_column_per_label_and_a_blank_newest_row() -> None:
    lines = make_retailer_usage_csv(2, blank_last=True).decode().split("\r\n")
    assert lines[0] == (
        f"Account Code,Read Datetime,E1 ({SYNTHETIC_SERIAL} - Off Peak),E1 ({SYNTHETIC_SERIAL} - Peak),"
        f"E1 ({SYNTHETIC_SERIAL} - Shoulder),Total"
    )
    assert lines[1].startswith(f"{SYNTHETIC_ACCOUNT},2025-01-01T00:00:00,")
    assert lines[2] == f"{SYNTHETIC_ACCOUNT},2025-02-01T00:00:00,,,,"
    assert lines[3] == ""


def test_the_txn_export() -> None:
    data = make_retailer_txn_csv((("Payment - Synthetic", datetime(2025, 1, 2), "-1.00"),))
    assert data == b"Description,Date,Amount\r\nPayment - Synthetic,2025-01-02T00:00:00,-1.00\r\n"


def test_make_pdf_shapes() -> None:
    plain = make_pdf(creation_date="D:20250101")
    assert plain.startswith(b"%PDF-1.4") and plain.endswith(b"%%EOF\n")
    assert b"/CreationDate (D:20250101)" in plain and b"/Encrypt" not in plain
    assert b"/CreationDate <443A3230323530313031>" in make_pdf(creation_date="D:20250101", date_encoding="hex")
    assert b"(\xfe\xff\x00D" in make_pdf(creation_date="D:20250101", date_encoding="utf16")
    objstm = make_pdf(creation_date="D:20250101", info_in_objstm=True)
    assert objstm.startswith(b"%PDF-1.5") and b"/Type /ObjStm" in objstm and b"/Type /XRef" in objstm
    assert b"/CreationDate" not in objstm
    assert b"<xmp:CreateDate>2025-01-01</xmp:CreateDate>" in make_pdf(creation_date=None, xmp="2025-01-01")
    assert b"CreateDate" not in make_pdf(creation_date=None, xmp="2025-01-01", xmp_compressed=True)
    assert b"/Encrypt 9 0 R" in make_pdf(creation_date=None, encrypted=True)
    assert not make_pdf(creation_date=None, truncated=True).rstrip().endswith(b"%%EOF")


def test_make_pdf_xref_offsets_point_at_their_objects() -> None:
    data = make_pdf(creation_date="D:20250101", xmp="2025-01-01")
    xref = data[data.rindex(b"xref\n") :].split(b"\n")
    entries = [line for line in xref[3:] if line.endswith(b" n ")]
    for number, line in enumerate(entries, start=1):
        offset = int(line.split()[0])
        assert data[offset:].startswith(f"{number} 0 obj".encode())
    startxref = int(data.split(b"startxref\n")[1].split(b"\n")[0])
    assert data[startxref:].startswith(b"xref")


def test_the_cdr_plan_detail_has_data_links_and_meta_and_pretty_changes_only_whitespace() -> None:
    compact = make_cdr_plan_detail(plan_id="SYNPLAN1", last_updated="2025-01-01T00:00:00Z", fuel_type="GAS")
    document = json.loads(compact)
    assert set(document) == {"data", "links", "meta"}
    assert document["data"]["planId"] == "SYNPLAN1" and document["data"]["fuelType"] == "GAS"
    pretty = make_cdr_plan_detail(plan_id="SYNPLAN1", last_updated="2025-01-01T00:00:00Z", fuel_type="GAS", pretty=True)
    assert pretty != compact and json.loads(pretty) == document
