"""The synthetic fixtures (D3 §12): made-up identifiers with a valid checksum digit, a matching private config, and
the NEM12 generator (§12.1)."""

import ast
import json
import math
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from energatron.adapters.sources.cdr_plan import OFFER_CDR
from energatron.core.models.private import PrivateConfig
from energatron.core.privacy import nmi_checksum
from tests.fixtures import make_cdr, oracle
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
    make_retailer_usage_from_nem12,
    make_sapn_summary_csv,
    make_summary_from_nem12,
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


# --- `make_nem12`: day_values, day_quality, negative_on (P2 spec §13.2) ---------------------------------------------


def test_day_values_set_a_chosen_days_values_exactly_and_leave_other_days_alone() -> None:
    plain = records(make_nem12(Nem12Spec(start=date(2025, 1, 1), days=3, interval_minutes=30)))
    chosen = [f"{index / 100:.3f}" for index in range(48)]
    spec = Nem12Spec(start=date(2025, 1, 1), days=3, interval_minutes=30, day_values={date(2025, 1, 2): chosen})
    rows = records(make_nem12(spec))
    assert rows[2][2:50] == chosen
    assert [row for row in rows if row[1:2] != ["20250102"]] == [row for row in plain if row[1:2] != ["20250102"]]


def test_day_values_with_the_wrong_count_are_refused() -> None:
    spec = Nem12Spec(start=date(2025, 1, 1), days=1, interval_minutes=30, day_values={date(2025, 1, 1): ["1.000"]})
    with pytest.raises(ValueError, match="48"):
        make_nem12(spec)


def test_day_quality_sets_a_non_v_quality_method() -> None:
    spec = Nem12Spec(start=date(2025, 1, 1), days=2, interval_minutes=30, day_quality={date(2025, 1, 2): "E52"})
    qualities = [row[2 + 48] for row in records(make_nem12(spec)) if row[0] == "300"]
    assert qualities == ["A", "E52"]


def test_day_quality_cannot_be_v_or_sit_on_a_day_with_400s() -> None:
    day = date(2025, 1, 1)
    with pytest.raises(ValueError):
        make_nem12(Nem12Spec(start=day, days=1, day_quality={day: "V"}))
    ranges = {day: (QualityRange(1, 288, "A"),)}
    with pytest.raises(ValueError):
        make_nem12(Nem12Spec(start=day, days=1, v_days=ranges, day_quality={day: "E52"}))


def test_negative_on_makes_that_days_first_value_negative() -> None:
    spec = Nem12Spec(start=date(2025, 1, 1), days=2, negative_on=date(2025, 1, 2))
    rows = [row for row in records(make_nem12(spec)) if row[0] == "300"]
    assert not rows[0][2].startswith("-")
    assert rows[1][2].startswith("-") and not rows[1][3].startswith("-")


# --- the oracle (P2 spec §13.2): independent of core, checked here against hand-made cases --------------------------


def small_nem12() -> bytes:
    day = date(2025, 1, 1)
    values = ["0.100"] * 24 + ["0.200"] * 24
    return make_nem12(
        Nem12Spec(start=day, days=2, interval_minutes=30, day_values={day: values, day + timedelta(1): values})
    )


def test_the_oracle_sums_each_day_per_suffix() -> None:
    sums = oracle.day_sums(small_nem12())
    assert set(sums) == {("E1", date(2025, 1, 1)), ("E1", date(2025, 1, 2))}
    assert all(math.isclose(total, 7.2) for total in sums.values())
    assert math.isclose(oracle.period_sum(small_nem12(), date(2025, 1, 1), date(2025, 1, 2)), 14.4)
    assert oracle.period_sum(small_nem12(), date(2025, 1, 1), date(2025, 1, 1), suffixes=("B1",)) == 0.0


def test_the_oracle_lists_intervals_with_their_meter_start() -> None:
    items = oracle.intervals(small_nem12())
    assert len(items) == 96
    assert items[0].meter_start == datetime(2025, 1, 1, 0, 0) and items[0].minutes == 30
    assert items[47].meter_start == datetime(2025, 1, 1, 23, 30) and items[47].kwh == 0.2
    assert items[48].meter_start == datetime(2025, 1, 2, 0, 0)


def test_the_oracle_local_times_in_winter_and_summer() -> None:
    winter, summer = datetime(2025, 7, 1, 10, 0), datetime(2025, 1, 1, 10, 0)
    assert oracle.local_time(winter, "nem_aest") == datetime(2025, 7, 1, 9, 30)
    assert oracle.local_time(winter, "local_wall") == winter
    assert oracle.local_time(winter, "local_standard") == winter
    assert oracle.local_time(summer, "nem_aest") == datetime(2025, 1, 1, 10, 30)
    assert oracle.local_time(summer, "local_wall") == summer
    assert oracle.local_time(summer, "local_standard") == datetime(2025, 1, 1, 11, 0)


def test_the_oracle_switches_at_the_daylight_saving_instants() -> None:
    april, october = sa_dst_dates(2025)  # 2025-04-06, 2025-10-05
    end = datetime(april.year, april.month, april.day)
    start = datetime(october.year, october.month, october.day)
    # nem_aest: daylight time ends at 02:00 standard = 16:30 UTC the day before = 02:30 on the meter clock
    assert oracle.local_time(end + timedelta(hours=2, minutes=25), "nem_aest") == end + timedelta(hours=2, minutes=55)
    assert oracle.local_time(end + timedelta(hours=2, minutes=30), "nem_aest") == end + timedelta(hours=2)
    # daylight time starts at 02:00 standard = 16:30 UTC the day before = 02:30 on the meter clock
    assert oracle.local_time(start + timedelta(hours=2, minutes=25), "nem_aest") == start + timedelta(
        hours=1, minutes=55
    )
    assert oracle.local_time(start + timedelta(hours=2, minutes=30), "nem_aest") == start + timedelta(hours=3)
    assert oracle.local_time(start + timedelta(hours=1, minutes=55), "local_standard") == start + timedelta(
        hours=1, minutes=55
    )
    assert oracle.local_time(start + timedelta(hours=2), "local_standard") == start + timedelta(hours=3)


def test_the_oracle_utc_times_and_its_refusal_of_local_wall() -> None:
    meter = datetime(2025, 7, 1, 10, 0)
    assert oracle.utc_time(meter, "nem_aest") == datetime(2025, 7, 1, 0, 0)
    assert oracle.utc_time(meter, "local_standard") == datetime(2025, 7, 1, 0, 30)
    with pytest.raises(ValueError):
        oracle.utc_time(meter, "local_wall")
    with pytest.raises(ValueError):
        oracle.local_time(meter, "elsewhere")


WINDOWS = {"Off Peak": ["01:00-06:00", "10:00-15:00"], "Peak": ["06:00-10:00"], "Shoulder": ["15:00-01:00"]}


@pytest.mark.parametrize(
    ("minute", "label"),
    [(0, "Shoulder"), (55, "Shoulder"), (60, "Off Peak"), (355, "Off Peak"), (360, "Peak"), (595, "Peak"),
     (600, "Off Peak"), (895, "Off Peak"), (900, "Shoulder"), (1439, "Shoulder")],
)  # fmt: skip
def test_the_oracle_places_a_minute_in_a_window_including_one_that_wraps_midnight(minute: int, label: str) -> None:
    assert oracle.window_label(minute, WINDOWS) == label


def test_the_oracle_tou_sums_follow_the_basis() -> None:
    day = date(2025, 7, 1)
    values = [f"{index / 10:.3f}" for index in range(48)]  # winter day, 30-minute intervals
    nem12 = make_nem12(Nem12Spec(start=day, days=1, interval_minutes=30, day_values={day: values}))
    items = oracle.intervals(nem12)
    wall = oracle.tou_sums(items, WINDOWS, "local_wall")
    nem = oracle.tou_sums(items, WINDOWS, "nem_aest")
    assert math.isclose(math.fsum(wall.values()), math.fsum(item.kwh for item in items))
    assert math.isclose(wall["Peak"], math.fsum(float(v) for v in values[12:20]))  # 06:00-10:00
    assert math.isclose(nem["Peak"], math.fsum(float(v) for v in values[13:21]))  # shifted by 30 minutes
    assert wall != nem


def test_the_summary_from_nem12_carries_the_oracles_period_totals_in_field_7() -> None:
    nem12 = make_nem12(Nem12Spec(start=date(2025, 1, 1), days=6, interval_minutes=30, suffixes=("E1", "B1")))
    periods = [(date(2025, 1, 1), date(2025, 1, 3)), (date(2025, 1, 4), date(2025, 1, 6))]
    rows = make_summary_from_nem12(nem12, periods=periods).decode().split("\r\n")[:-1]
    assert len(rows) == 2
    for row, (start, end) in zip(rows, periods, strict=True):
        fields = row.split(",")
        assert fields[4:6] == [f"{start:%d/%m/%Y}", f"{end:%d/%m/%Y}"]
        expected = oracle.period_sum(nem12, start, end, suffixes=("E1",))
        assert float(fields[6]) == pytest.approx(expected, abs=0.0006)  # the B1 register isn't counted
        assert expected < oracle.period_sum(nem12, start, end)


def test_the_usage_from_nem12_follows_the_basis_and_reconciles_with_the_total() -> None:
    nem12 = make_nem12(dst_spec(2025, interval_minutes=30))
    reads = [datetime(2025, 4, 3), datetime(2025, 4, 10), datetime(2025, 10, 1), datetime(2025, 10, 8)]
    labels = ("Off Peak", "Peak", "Shoulder")
    texts = {
        basis: make_retailer_usage_from_nem12(nem12, basis=basis, windows=WINDOWS, reads=reads, labels=labels)
        for basis in oracle.BASES
    }
    assert len(set(texts.values())) == 3  # the three bases give three different exports
    rows = [line.split(",") for line in texts["nem_aest"].decode().split("\r\n")[1:-1]]
    assert [row[1] for row in rows] == [f"{read:%Y-%m-%dT%H:%M:%S}" for read in reads]
    for previous, row in zip(reads, rows[1:], strict=False):
        read = datetime.fromisoformat(row[1])
        total = oracle.period_sum(nem12, previous.date() + timedelta(days=1), read.date())
        assert float(row[-1]) == pytest.approx(total, abs=0.0002)
        assert sum(float(value) for value in row[2:-1]) == pytest.approx(total, abs=0.0004)


def test_the_usage_from_nem12_can_leave_the_newest_row_blank() -> None:
    nem12 = make_nem12(Nem12Spec(start=date(2025, 1, 1), days=3, interval_minutes=30))
    reads = [datetime(2025, 1, 1), datetime(2025, 1, 3)]
    data = make_retailer_usage_from_nem12(
        nem12,
        basis="local_wall",
        windows=WINDOWS,
        reads=reads,
        labels=("Off Peak", "Peak", "Shoulder"),
        blank_last=True,
    )
    assert data.decode().split("\r\n")[2] == f"{SYNTHETIC_ACCOUNT},2025-01-03T00:00:00,,,,"


# --- the oracle stays independent of the code it checks (P2 spec §13.2) ---------------------------------------------

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


def _imported_modules(path: Path) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None and node.level == 0:
            found.add(node.module)
            found.update(f"{node.module}.{alias.name}" for alias in node.names)
    return found


def test_the_oracle_and_its_fixture_imports_never_import_energatron() -> None:
    seen: set[Path] = set()
    pending = [FIXTURES / "oracle.py"]
    while pending:
        path = pending.pop()
        if path in seen:
            continue
        seen.add(path)
        for module in _imported_modules(path):
            assert module.split(".")[0] != "energatron", f"{path.name} imports {module}"
            candidate = FIXTURES.parent.parent / Path(*module.split(".")).with_suffix(".py")
            if module.startswith("tests.fixtures.") and candidate.exists():
                pending.append(candidate)
    assert FIXTURES / "oracle.py" in seen


# make_cdr (P4 spec §14)


def test_a_generated_plan_detail_is_landable_as_offer_cdr() -> None:
    body = make_cdr.detail_body(make_cdr.plan("GEN1@EME"))
    assert OFFER_CDR.detect("alpha__GEN1@EME.json", body) is not None
    info = OFFER_CDR.inspect("alpha__GEN1@EME.json", body)
    assert info.extra["plan_id"] == "GEN1@EME"


def test_a_generated_list_page_holds_headers_but_no_contract() -> None:
    page = json.loads(
        make_cdr.list_body([make_cdr.plan("GEN1@EME"), make_cdr.plan("GEN2@EME")], next_url="https://cdr.test/p2")
    )
    assert [item["planId"] for item in page["data"]["plans"]] == ["GEN1@EME", "GEN2@EME"]
    assert all("electricityContract" not in item for item in page["data"]["plans"])
    assert page["links"]["next"] == "https://cdr.test/p2"
