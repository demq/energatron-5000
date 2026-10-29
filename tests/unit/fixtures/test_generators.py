"""The synthetic fixtures (D3 §12): made-up identifiers with a valid checksum digit, a matching private config, and
the NEM12 generator (§12.1)."""

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
