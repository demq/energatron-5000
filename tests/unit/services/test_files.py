"""`services.files`: atomic writes and the JSON and JSONL formats (D1 §12)."""

import json
import os
from pathlib import Path

import pytest

from energatron.services import files
from energatron.services.files import (
    atomic_write_bytes,
    atomic_write_text,
    dump_json,
    read_json,
    read_jsonl,
    write_json,
    write_jsonl,
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
