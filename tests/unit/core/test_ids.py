"""ID constructors and parsers, and the compact timestamp formats (D1 §6.2, §6.3, §13)."""

import re
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta, timezone
from enum import StrEnum

import pytest
from hypothesis import given
from hypothesis import strategies as st

from energatron.core.canonical import JSONValue, content_hash, sha256_hex
from energatron.core.errors import Category, IdCollisionError
from energatron.core.ids import (
    DatasetIdParts,
    DecisionIdParts,
    FetchBatchIdParts,
    OfferSnapshotIdParts,
    RecIdParts,
    RunIdParts,
    check_dataset_id,
    compact_source,
    compact_utc,
    dataset_id,
    decision_id,
    fetch_batch_id,
    offer_snapshot_id,
    parse_dataset_id,
    parse_decision_id,
    parse_fetch_batch_id,
    parse_offer_snapshot_id,
    parse_rec_id,
    parse_run_id,
    rec_id,
    run_id,
)
from tests.unit.core._strategies import json_objects, json_values

# D1 §6.3's patterns, as written there.
DATASET = r"^[a-z][a-z0-9_]*-\d{8}T\d{6}-[0-9a-f]{8}$"
OFFER_SNAPSHOT = r"^[^~\s]+~\d{8}T\d{6}Z~[0-9a-f]{8}$"
FETCH_BATCH = r"^fetch-\d{8}T\d{6}Z$"
RUN = r"^run-\d{8}T\d{6}Z-[0-9a-f]{7}$"
REC = r"^REC-\d{8}-[0-9a-f]{8}$"
DECISION = r"^DEC-\d{8}-\d{2}$"

SHA256 = "3fa9c2e1" + "0" * 56
HEAD = "1a2b3c4" + "d" * 33
PLUS_10_30 = timezone(timedelta(hours=10, minutes=30))
LAST_UPDATED = datetime(2026, 10, 1, 9, 30, tzinfo=UTC)
PLAN_DATA: dict[str, JSONValue] = {"planId": "EXAMPLE_PLAN_A", "fees": [], "effectiveFrom": "2026-10-01"}


def matches(pattern: str, value: str) -> bool:
    return re.fullmatch(pattern, value) is not None


# Compact timestamps (D1 §6.2)


def test_compact_utc_converts_to_utc() -> None:
    assert compact_utc(datetime(2026, 10, 23, 19, 30, tzinfo=PLUS_10_30)) == "20261023T090000Z"
    assert compact_utc(datetime(2026, 10, 23, 9, 30, 0, 999_999, tzinfo=UTC)) == "20261023T093000Z"
    assert compact_utc(datetime(1, 1, 1, tzinfo=UTC)) == "00010101T000000Z"


def test_compact_utc_refuses_a_naive_datetime() -> None:
    with pytest.raises(ValueError):
        compact_utc(datetime(2026, 10, 23, 9, 30))


def test_compact_source_keeps_naive_and_converts_aware() -> None:
    assert compact_source(datetime(2026, 10, 23, 10, 3, 45)) == "20261023T100345"
    assert compact_source(datetime(2026, 10, 23, 10, 3, 45, tzinfo=PLUS_10_30)) == "20261022T233345"


# Fixed examples and validation (D1 §6.3)


class Source(StrEnum):
    SAPN_INTERVAL = "sapn_interval"


def test_dataset_id_example() -> None:
    expected = "sapn_interval-20261023T100345-3fa9c2e1"
    assert dataset_id("sapn_interval", datetime(2026, 10, 23, 10, 3, 45), SHA256) == expected
    assert dataset_id(Source.SAPN_INTERVAL, datetime(2026, 10, 23, 10, 3, 45), SHA256) == expected
    assert parse_dataset_id(expected) == DatasetIdParts(
        source="sapn_interval", source_ts=datetime(2026, 10, 23, 10, 3, 45), sha256_prefix="3fa9c2e1"
    )


@pytest.mark.parametrize(
    ("source", "sha256"),
    [
        ("Sapn", SHA256),
        ("sapn-interval", SHA256),
        ("", SHA256),
        ("9sapn", SHA256),
        ("sapn", SHA256.upper()),
        ("sapn", SHA256[:63]),
        ("sapn", "g" * 64),
    ],
)
def test_dataset_id_refuses_a_bad_source_or_sha256(source: str, sha256: str) -> None:
    with pytest.raises(ValueError):
        dataset_id(source, datetime(2026, 10, 23), sha256)


def test_offer_snapshot_id_example() -> None:
    expected = f"EXAMPLE_PLAN_A~20261001T093000Z~{content_hash(PLAN_DATA)[:8]}"
    assert offer_snapshot_id("EXAMPLE_PLAN_A", LAST_UPDATED.astimezone(PLUS_10_30), PLAN_DATA) == expected
    assert parse_offer_snapshot_id(expected) == OfferSnapshotIdParts(
        plan_id="EXAMPLE_PLAN_A", last_updated=LAST_UPDATED, content_hash_prefix=content_hash(PLAN_DATA)[:8]
    )


@pytest.mark.parametrize("plan_id", ["", "PLAN~A", "PLAN A", "PLAN\tA", "PLAN\u00a0A", "PLAN\nA"])
def test_offer_snapshot_id_refuses_a_tilde_or_whitespace_in_the_plan_id(plan_id: str) -> None:
    with pytest.raises(ValueError):
        offer_snapshot_id(plan_id, LAST_UPDATED, PLAN_DATA)


def test_offer_snapshot_id_needs_an_aware_last_updated() -> None:
    with pytest.raises(ValueError):
        offer_snapshot_id("EXAMPLE_PLAN_A", datetime(2026, 10, 1, 9, 30), PLAN_DATA)


def test_fetch_batch_id_example() -> None:
    assert fetch_batch_id(datetime(2026, 10, 23, 9, 30, tzinfo=UTC)) == "fetch-20261023T093000Z"
    assert parse_fetch_batch_id("fetch-20261023T093000Z") == FetchBatchIdParts(
        fetched_at=datetime(2026, 10, 23, 9, 30, tzinfo=UTC)
    )


def test_run_id_example() -> None:
    assert run_id(datetime(2026, 10, 23, 9, 30, tzinfo=UTC), HEAD) == "run-20261023T093000Z-1a2b3c4"
    assert run_id(datetime(2026, 10, 23, 9, 30, tzinfo=UTC), "ab" * 32) == "run-20261023T093000Z-abababa"
    assert parse_run_id("run-20261023T093000Z-1a2b3c4") == RunIdParts(
        started_at=datetime(2026, 10, 23, 9, 30, tzinfo=UTC), head_prefix="1a2b3c4"
    )


@pytest.mark.parametrize("head", ["1a2b3c4", HEAD.upper(), HEAD[:39], HEAD + "0", "z" * 40])
def test_run_id_needs_a_full_commit_sha(head: str) -> None:
    with pytest.raises(ValueError):
        run_id(datetime(2026, 10, 23, 9, 30, tzinfo=UTC), head)


def test_rec_id_example() -> None:
    payload: JSONValue = {"ranking": ["EXAMPLE_PLAN_A"], "params_sha256": SHA256}
    expected = f"REC-20261023-{content_hash(payload)[:8]}"
    assert rec_id(date(2026, 10, 23), payload) == expected
    assert parse_rec_id(expected) == RecIdParts(as_of=date(2026, 10, 23), payload_hash_prefix=content_hash(payload)[:8])


def test_decision_id_counts_the_decisions_already_made_that_day() -> None:
    assert decision_id(date(2026, 10, 23), []) == "DEC-20261023-01"
    existing = ["DEC-20261022-01", "DEC-20261022-02", "DEC-20261023-01"]
    assert decision_id(date(2026, 10, 23), existing) == "DEC-20261023-02"
    assert parse_decision_id("DEC-20261023-02") == DecisionIdParts(day=date(2026, 10, 23), number=2)


def test_decision_id_never_returns_an_existing_id() -> None:
    with pytest.raises(IdCollisionError):
        decision_id(date(2026, 10, 23), ["DEC-20261023-02"])


def test_decision_id_allows_ninety_nine_a_day() -> None:
    existing = [f"DEC-20261023-{n:02d}" for n in range(1, 99)]
    assert decision_id(date(2026, 10, 23), existing) == "DEC-20261023-99"
    with pytest.raises(ValueError):
        decision_id(date(2026, 10, 23), [*existing, "DEC-20261023-99"])


def test_date_ids_refuse_a_datetime() -> None:
    with pytest.raises(TypeError):
        rec_id(datetime(2026, 10, 23, tzinfo=UTC), {})
    with pytest.raises(TypeError):
        decision_id(datetime(2026, 10, 23, tzinfo=UTC), [])


@pytest.mark.parametrize(
    ("parse", "value"),
    [
        (parse_dataset_id, "sapn_interval-20261323T100345-3fa9c2e1"),
        (parse_dataset_id, "sapn_interval-20261023T100345-3FA9C2E1"),
        (parse_dataset_id, "sapn_interval-20261023T100345-3fa9c2e1\n"),
        (parse_dataset_id, "sapn_interval-\u0662\u0660\u0662\u0666\u0661\u0660\u0662\u0663T100345-3fa9c2e1"),
        (parse_dataset_id, "<NMI>_20261023_interval.csv"),
        (parse_offer_snapshot_id, "PLAN A~20261001T093000Z~3fa9c2e1"),
        (parse_offer_snapshot_id, "EXAMPLE_PLAN_A~20261001T093000~3fa9c2e1"),
        (parse_fetch_batch_id, "fetch-20261023T246000Z"),
        (parse_run_id, "run-20261023T093000Z-1a2b3c"),
        (parse_rec_id, "REC-20260230-3fa9c2e1"),
        (parse_decision_id, "DEC-20261023-00"),
        (parse_decision_id, "DEC-20261023-1"),
    ],
)
def test_parsers_refuse_malformed_ids_without_echoing_them(parse: Callable[[str], object], value: str) -> None:
    with pytest.raises(ValueError) as caught:
        parse(value)
    assert value.strip() not in str(caught.value)


# The collision rule (D1 §6.3)


def test_check_dataset_id_free_landed_or_colliding() -> None:
    new_id = dataset_id("sapn_interval", datetime(2026, 10, 23, 10, 3, 45), SHA256)
    assert check_dataset_id(new_id, SHA256, None) is False
    assert check_dataset_id(new_id, SHA256, SHA256) is True
    other = "3fa9c2e1" + "f" * 56
    with pytest.raises(IdCollisionError) as caught:
        check_dataset_id(new_id, other, SHA256)
    error = caught.value
    assert (error.code, error.category) == ("E_ID_COLLISION", Category.STATE)
    assert error.details == {"dataset_id": new_id}


# Properties: every constructor's output matches its pattern and round-trips through its parser


offsets = st.timedeltas(min_value=timedelta(hours=-23, minutes=-59), max_value=timedelta(hours=23, minutes=59))
zones = st.just(UTC) | offsets.map(timezone)
naive_datetimes = st.datetimes(min_value=datetime(1900, 1, 1), max_value=datetime(2200, 12, 31))
aware_datetimes = st.datetimes(min_value=datetime(1900, 1, 1), max_value=datetime(2200, 12, 31), timezones=zones)
dates = st.dates(min_value=date(1900, 1, 1), max_value=date(2200, 12, 31))
sources = st.from_regex(r"[a-z][a-z0-9_]{0,15}", fullmatch=True)
sha256s = st.binary(max_size=16).map(sha256_hex)
heads = st.from_regex(r"[0-9a-f]{40}|[0-9a-f]{64}", fullmatch=True)
plan_ids = st.text(st.characters(codec="utf-8", exclude_characters="~"), min_size=1, max_size=16).filter(
    lambda text: not any(character.isspace() for character in text)
)


def in_utc_to_the_second(moment: datetime) -> datetime:
    return moment.astimezone(UTC).replace(microsecond=0)


@given(source=sources, source_ts=naive_datetimes | aware_datetimes, sha256=sha256s)
def test_dataset_ids_round_trip(source: str, source_ts: datetime, sha256: str) -> None:
    value = dataset_id(source, source_ts, sha256)
    assert matches(DATASET, value)
    expected_ts = source_ts if source_ts.tzinfo is None else in_utc_to_the_second(source_ts).replace(tzinfo=None)
    assert parse_dataset_id(value) == DatasetIdParts(source, expected_ts.replace(microsecond=0), sha256[:8])


@given(plan_id=plan_ids, last_updated=aware_datetimes, data=json_objects)
def test_offer_snapshot_ids_round_trip(plan_id: str, last_updated: datetime, data: dict[str, JSONValue]) -> None:
    value = offer_snapshot_id(plan_id, last_updated, data)
    assert matches(OFFER_SNAPSHOT, value)
    assert parse_offer_snapshot_id(value) == OfferSnapshotIdParts(
        plan_id, in_utc_to_the_second(last_updated), content_hash(data)[:8]
    )


@given(now=aware_datetimes)
def test_fetch_batch_ids_round_trip(now: datetime) -> None:
    value = fetch_batch_id(now)
    assert matches(FETCH_BATCH, value)
    assert parse_fetch_batch_id(value) == FetchBatchIdParts(in_utc_to_the_second(now))


@given(now=aware_datetimes, head=heads)
def test_run_ids_round_trip(now: datetime, head: str) -> None:
    value = run_id(now, head)
    assert matches(RUN, value)
    assert parse_run_id(value) == RunIdParts(in_utc_to_the_second(now), head[:7])


@given(as_of=dates, payload=json_values)
def test_rec_ids_round_trip(as_of: date, payload: JSONValue) -> None:
    value = rec_id(as_of, payload)
    assert matches(REC, value)
    assert parse_rec_id(value) == RecIdParts(as_of, content_hash(payload)[:8])


@given(day=dates, made_that_day=st.integers(min_value=0, max_value=98), made_the_day_before=st.integers(0, 3))
def test_decision_ids_round_trip(day: date, made_that_day: int, made_the_day_before: int) -> None:
    before = day - timedelta(days=1)
    existing = [f"DEC-{day:%Y%m%d}-{n:02d}" for n in range(1, made_that_day + 1)]
    existing += [f"DEC-{before:%Y%m%d}-{n:02d}" for n in range(1, made_the_day_before + 1)]
    value = decision_id(day, existing)
    assert matches(DECISION, value)
    assert parse_decision_id(value) == DecisionIdParts(day, made_that_day + 1)
