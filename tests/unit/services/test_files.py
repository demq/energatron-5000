"""`services.files`: atomic writes and the JSON and JSONL formats (D1 §12)."""

import json
import os
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from energatron.services import files
from energatron.services.files import (
    atomic_write_bytes,
    atomic_write_text,
    dump_json,
    make_dirs,
    read_json,
    read_jsonl,
    write_json,
    write_jsonl,
    write_parquet,
)


def test_atomic_write_bytes_writes_and_leaves_no_temporary_file(tmp_path: Path) -> None:
    target = tmp_path / "out.bin"
    atomic_write_bytes(target, b"\x00one")
    atomic_write_bytes(target, b"two")
    assert target.read_bytes() == b"two"
    assert [path.name for path in tmp_path.iterdir()] == ["out.bin"]


def test_a_failed_write_keeps_the_old_file_and_removes_the_temporary_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "out.txt"
    target.write_text("old\n", encoding="utf-8")

    def failing_replace(source: object, destination: object) -> None:
        raise OSError("injected")

    monkeypatch.setattr(files.os, "replace", failing_replace)
    with pytest.raises(OSError, match="injected"):
        atomic_write_text(target, "new\n")
    assert target.read_text(encoding="utf-8") == "old\n"
    assert [path.name for path in tmp_path.iterdir()] == ["out.txt"]


def test_text_is_utf8_with_lf_line_endings(tmp_path: Path) -> None:
    target = tmp_path / "t.txt"
    atomic_write_text(target, "é\nline\n")
    assert target.read_bytes() == "é\nline\n".encode()


def test_atomic_write_keeps_a_file_mode_readable(tmp_path: Path) -> None:
    target = tmp_path / "t.txt"
    atomic_write_text(target, "x\n")
    assert os.stat(target).st_mode & 0o600 == 0o600


def test_json_documents_have_sorted_keys_indent_2_and_a_trailing_newline(tmp_path: Path) -> None:
    value = {"b": [1, 2], "a": {"z": "é", "y": None}}
    assert dump_json(value) == '{\n  "a": {\n    "y": null,\n    "z": "é"\n  },\n  "b": [\n    1,\n    2\n  ]\n}\n'
    target = tmp_path / "doc.json"
    write_json(target, value)
    assert target.read_text(encoding="utf-8") == dump_json(value)
    assert read_json(target) == value


def test_json_refuses_nan(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        write_json(tmp_path / "doc.json", {"x": float("nan")})
    assert not (tmp_path / "doc.json").exists()


def test_read_json_raises_value_error_on_bad_json(tmp_path: Path) -> None:
    target = tmp_path / "bad.json"
    target.write_text("{oops", encoding="utf-8")
    with pytest.raises(ValueError):
        read_json(target)


def test_jsonl_lines_are_compact_canonical_json(tmp_path: Path) -> None:
    target = tmp_path / "rows.jsonl"
    rows = [{"b": 1, "a": "é"}, {"c": [1.5, None]}]
    write_jsonl(target, rows)
    assert target.read_text(encoding="utf-8") == '{"a":"é","b":1}\n{"c":[1.5,null]}\n'
    assert read_jsonl(target) == rows
    write_jsonl(target, [])
    assert target.read_text(encoding="utf-8") == ""
    assert read_jsonl(target) == []


def test_read_jsonl_rejects_a_bad_line(tmp_path: Path) -> None:
    target = tmp_path / "rows.jsonl"
    target.write_text('{"a":1}\nnot json\n', encoding="utf-8")
    with pytest.raises(ValueError):
        read_jsonl(target)
    assert json.loads(target.read_text(encoding="utf-8").splitlines()[0]) == {"a": 1}


def test_make_dirs_creates_missing_directories_and_fsyncs_each_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    synced: list[Path] = []
    monkeypatch.setattr(files, "fsync_directory", synced.append)
    (tmp_path / "a").mkdir()
    files.make_dirs(tmp_path / "a" / "b" / "c")
    assert (tmp_path / "a" / "b" / "c").is_dir()
    assert synced == [tmp_path / "a", tmp_path / "a" / "b"]


def test_make_dirs_of_an_existing_directory_does_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    synced: list[Path] = []
    monkeypatch.setattr(files, "fsync_directory", synced.append)
    make_dirs(tmp_path)
    assert synced == []


# Parquet (D1 §5, §9.4)

_SCHEMA = pa.schema([("key", pa.string()), ("value", pa.int64())])


def _frame() -> pd.DataFrame:
    return pd.DataFrame({"key": ["b", "c", "a"], "value": [2, 3, 1]})


def test_write_parquet_sorts_rows_and_records_kind_and_version(tmp_path: Path) -> None:
    target = tmp_path / "table.parquet"
    write_parquet(_frame(), target, kind="synthetic_table", schema_version=3, sort_by=["key"], schema=_SCHEMA)
    table = pq.read_table(target)
    assert table.column("key").to_pylist() == ["a", "b", "c"]
    assert table.column("value").to_pylist() == [1, 2, 3]
    metadata = table.schema.metadata or {}
    assert metadata == {b"energatron.kind": b"synthetic_table", b"energatron.schema_version": b"3"}
    assert pq.ParquetFile(target).metadata.row_group(0).column(0).compression == "ZSTD"
    assert [path.name for path in tmp_path.iterdir()] == ["table.parquet"]


def test_write_parquet_is_byte_identical_on_a_rewrite(tmp_path: Path) -> None:
    first, second = tmp_path / "one.parquet", tmp_path / "two.parquet"
    write_parquet(_frame(), first, kind="synthetic_table", schema_version=1, sort_by=["key"], schema=_SCHEMA)
    write_parquet(
        _frame().iloc[::-1], second, kind="synthetic_table", schema_version=1, sort_by=["key"], schema=_SCHEMA
    )
    assert first.read_bytes() == second.read_bytes()


def test_write_parquet_writes_an_empty_table_with_its_schema(tmp_path: Path) -> None:
    target = tmp_path / "empty.parquet"
    empty = pd.DataFrame({"key": pd.Series([], dtype=object), "value": pd.Series([], dtype=object)})
    write_parquet(empty, target, kind="synthetic_table", schema_version=1, sort_by=["key"], schema=_SCHEMA)
    table = pq.read_table(target)
    assert table.num_rows == 0
    assert table.schema.remove_metadata() == _SCHEMA


def test_parquet_rows_reads_the_row_count_from_metadata(tmp_path: Path) -> None:
    target = tmp_path / "table.parquet"
    write_parquet(_frame(), target, kind="synthetic_table", schema_version=1, sort_by=["key"], schema=_SCHEMA)
    assert files.parquet_rows(target) == 3
