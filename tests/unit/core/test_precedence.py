"""Dataset precedence within one source (P2 spec §3.3, D3 §3.3). Synthetic records only."""

import random
from datetime import UTC, datetime, timedelta, timezone

from energatron.core.canonical import sha256_hex
from energatron.core.ids import dataset_id
from energatron.core.models.catalog import CatalogRecord, SourceType, blob_key, precedence_order

SOURCE = SourceType.SAPN_INTERVAL
BASE = datetime(2025, 3, 1, 10, 0, 0)
MTIME = datetime(2025, 3, 2, 8, 0, 0, tzinfo=UTC)


def record(tag: str, source_ts: datetime, file_mtime: datetime | None = None) -> CatalogRecord:
    digest = sha256_hex(tag.encode())
    ident = dataset_id(SOURCE.value, source_ts, digest)
    return CatalogRecord.model_validate(
        {
            "dataset_id": ident,
            "source": SOURCE,
            "sha256": digest,
            "bytes": 10,
            "stored_path": blob_key(SOURCE, ident, ".csv"),
            "original_filename": f"{tag}.csv",
            "landed_at": datetime(2025, 3, 3, tzinfo=UTC),
            "file_mtime": file_mtime,
            "source_ts": source_ts,
            "coverage": None,
            "detector": {"name": SOURCE, "version": 1, "evidence": ("filename",)},
            "extra": {},
            "supersedes": None,
            "notes": "",
        }
    )


def test_an_empty_catalog_has_an_empty_order() -> None:
    assert precedence_order([]) == []


def test_a_newer_source_ts_comes_first() -> None:
    old, new = record("old", BASE), record("new", BASE + timedelta(days=1))
    assert precedence_order([old, new]) == [new.dataset_id, old.dataset_id]
    assert precedence_order([new, old]) == [new.dataset_id, old.dataset_id]


def test_an_equal_source_ts_falls_to_the_newer_file_mtime() -> None:
    old = record("old", BASE, MTIME)
    new = record("new", BASE, MTIME + timedelta(hours=1))
    assert precedence_order([new, old]) == [new.dataset_id, old.dataset_id]
    assert precedence_order([old, new]) == [new.dataset_id, old.dataset_id]


def test_a_null_file_mtime_ranks_below_every_non_null_one() -> None:
    none = record("none", BASE, None)
    early = record("early", BASE, datetime(1999, 1, 1, tzinfo=UTC))
    assert precedence_order([early, none]) == [early.dataset_id, none.dataset_id]
    assert precedence_order([none, early]) == [early.dataset_id, none.dataset_id]


def test_equal_source_ts_and_mtime_falls_to_the_later_catalog_position() -> None:
    first, second, third = (record(tag, BASE, MTIME) for tag in ("a", "b", "c"))
    assert precedence_order([first, second, third]) == [third.dataset_id, second.dataset_id, first.dataset_id]
    assert precedence_order([third, first, second]) == [second.dataset_id, first.dataset_id, third.dataset_id]


def test_two_null_mtimes_also_fall_to_the_later_position() -> None:
    first, second = record("a", BASE), record("b", BASE)
    assert precedence_order([first, second]) == [second.dataset_id, first.dataset_id]


def test_naive_and_aware_source_ts_order_through_compact_source() -> None:
    naive = record("naive", datetime(2025, 3, 1, 10, 0, 0))
    aware_later = record("aware", datetime(2025, 3, 1, 10, 0, 1, tzinfo=UTC))
    aware_earlier = record("early", datetime(2025, 3, 1, 9, 59, 59, tzinfo=UTC))
    assert precedence_order([naive, aware_later, aware_earlier]) == [
        aware_later.dataset_id,
        naive.dataset_id,
        aware_earlier.dataset_id,
    ]


def test_an_aware_source_ts_is_compared_in_utc() -> None:
    plus_ten = datetime(2025, 3, 1, 20, 0, 0, tzinfo=timezone(timedelta(hours=10)))
    utc = record("utc", datetime(2025, 3, 1, 9, 59, 59, tzinfo=UTC))
    shifted = record("shifted", plus_ten)  # 10:00:00 UTC
    assert precedence_order([shifted, utc]) == [shifted.dataset_id, utc.dataset_id]


def test_the_order_has_one_entry_per_record_whatever_the_input_order() -> None:
    records = [
        record("a", BASE),
        record("b", BASE + timedelta(days=1)),
        record("c", BASE, MTIME),
        record("d", BASE, MTIME + timedelta(minutes=1)),
        record("e", BASE + timedelta(days=1), MTIME),
    ]
    expected = precedence_order(records)
    assert sorted(expected) == sorted(item.dataset_id for item in records)
    shuffled = list(records)
    random.Random(7).shuffle(shuffled)
    assert precedence_order(shuffled) == expected  # these records all differ in source_ts or file_mtime
