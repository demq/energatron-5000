"""The P1 persisted models: repro log entries, provenance, the code fingerprint, params and the region profile
(D1 §4.5, §10, §11, §11.4; D3 §10.2, §10.3). Synthetic values only."""

from datetime import datetime
from typing import Any

import pytest

from energatron.core.canonical import JSONValue, content_hash, sha256_hex
from energatron.core.errors import InvalidInputError
from energatron.core.ids import dataset_id
from energatron.core.models.catalog import SourceType
from energatron.core.models.pipeline import ReproLogEntry
from energatron.core.models.provenance import CodeFingerprint, StageCode, StageProvenance, stale_stages
from energatron.core.models.region import RegionProfile

TREE = sha256_hex(b"tree")
COMMIT = "1a2b3c4" + "d" * 33
DATASET = dataset_id("sapn_interval", datetime(2026, 10, 23, 10, 3, 45), TREE)


def code_doc(source: str = TREE, **changes: Any) -> dict[str, Any]:
    packages = {"pydantic": "2.0.0", "pyyaml": "6.0"}
    python = "3.14.0"
    doc: dict[str, Any] = {
        "stage_sha256": content_hash({"source_sha256": source, "packages": packages, "python": python}),
        "source_sha256": source,
        "python": python,
        "packages": packages,
        "files": ["__init__.py", "core/__init__.py", "services/stages/catalog_index.py"],
    }
    return doc | changes


def code(source: str = TREE) -> StageCode:
    return StageCode.model_validate(code_doc(source))


# ReproLogEntry (D1 §4.5)


def test_repro_log_entries() -> None:
    start = ReproLogEntry(stage="catalog_index", event="start", exit_code=None)
    assert start.model_dump(mode="json") == {
        "kind": "repro_log_entry",
        "schema_version": 1,
        "stage": "catalog_index",
        "event": "start",
        "exit_code": None,
    }
    assert ReproLogEntry(stage="catalog_index", event="end", exit_code=7).exit_code == 7


@pytest.mark.parametrize(
    "doc",
    [
        {"stage": "x", "event": "start", "exit_code": 0},
        {"stage": "x", "event": "end", "exit_code": None},
        {"stage": "x", "event": "middle", "exit_code": None},
        {"stage": "", "event": "start", "exit_code": None},
        {"stage": "x", "event": "end", "exit_code": -1},
        {"stage": "x", "event": "end", "exit_code": 256},
    ],
)
def test_repro_log_entry_rules(doc: dict[str, JSONValue]) -> None:
    with pytest.raises(InvalidInputError):
        ReproLogEntry.from_document({"kind": "repro_log_entry", "schema_version": 1, **doc})


# StageCode (D1 §11.1)


def test_a_stage_code_entry_round_trips() -> None:
    assert code().model_dump(mode="json") == code_doc()


@pytest.mark.parametrize(
    "changes",
    [
        {"stage_sha256": sha256_hex(b"other")},
        {"python": "3.14.1"},
        {"source_sha256": "nothex"},
        {"files": ["b.py", "a.py"]},
        {"files": ["a.py", "a.py"]},
        {"files": ["/abs.py"]},
        {"files": ["../x.py"]},
    ],
    ids=["hash-mismatch", "python-changed-without-hash", "bad-tree", "unsorted", "duplicate", "absolute", "dotdot"],
)
def test_stage_code_rules(changes: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        StageCode.model_validate(code_doc(**changes))


# CodeFingerprint (D1 §11, §11.2)


def fingerprint_doc(**changes: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "kind": "code_fingerprint",
        "schema_version": 1,
        "energatron_version": "0.1.0",
        "install": "editable",
        "tool_commit": COMMIT,
        "tool_dirty": False,
        "stages": {"catalog_index": code_doc()},
    }
    return doc | changes


def test_a_fingerprint_round_trips() -> None:
    fingerprint = CodeFingerprint.from_document(fingerprint_doc())
    assert fingerprint.model_dump(mode="json") == fingerprint_doc()
    assert CodeFingerprint.from_document(fingerprint_doc(tool_commit=None, tool_dirty=True)).tool_dirty
    assert CodeFingerprint.from_document(fingerprint_doc(install="vcs")).tool_commit == COMMIT
    for install in ("local_dir", "archive", "index"):
        CodeFingerprint.from_document(fingerprint_doc(install=install, tool_commit=None))


@pytest.mark.parametrize(
    "changes",
    [
        {"install": "wheel"},
        {"install": "index"},  # a commit only for editable and vcs installs
        {"install": "vcs", "tool_dirty": True, "tool_commit": COMMIT},
        {"install": "archive", "tool_commit": None, "tool_dirty": True},
        {"tool_commit": "abc"},
        {"stages": {"Bad-Name": code_doc()}},
    ],
)
def test_fingerprint_rules(changes: dict[str, Any]) -> None:
    with pytest.raises(InvalidInputError):
        CodeFingerprint.from_document(fingerprint_doc(**changes))


# stale_stages (D1 §11.4)


def fingerprint(**stages: StageCode) -> CodeFingerprint:
    return CodeFingerprint.from_document(
        fingerprint_doc(stages={name: entry.model_dump(mode="json") for name, entry in stages.items()})
    )


def test_stale_stages_lists_differing_missing_and_extra_stages_sorted() -> None:
    other = sha256_hex(b"other")
    live = fingerprint(b=code(), a=code(), c=code(), d=code())
    recorded = fingerprint(a=code(), c=code(other), d=code(), e=code())
    assert stale_stages(live, recorded) == ["b", "c", "e"]
    assert stale_stages(live, live) == []


def test_without_a_recorded_fingerprint_every_live_stage_is_stale() -> None:
    assert stale_stages(fingerprint(b=code(), a=code()), None) == ["a", "b"]


def test_only_stage_sha256_counts() -> None:
    live = CodeFingerprint.from_document(fingerprint_doc())
    recorded = CodeFingerprint.from_document(fingerprint_doc(install="index", tool_commit=None, energatron_version="9"))
    assert stale_stages(live, recorded) == []


# StageProvenance (D1 §10)


def provenance_doc(**changes: Any) -> dict[str, Any]:
    values: dict[str, JSONValue] = {"meter.timebase": "nem_aest"}
    doc: dict[str, Any] = {
        "kind": "stage_provenance",
        "schema_version": 1,
        "stage": "catalog_index",
        "output": "catalog",
        "code": code_doc(),
        "inputs": [
            {
                "name": "catalog.sapn_interval",
                "type": "file",
                "path": "data/raw/sapn_interval/_catalog.jsonl",
                "sha256": TREE,
                "dataset_ids": [DATASET],
            },
            {
                "name": "private_config",
                "type": "file",
                "path": "private/config.toml",
                "sha256": TREE,
                "dataset_ids": [],
            },
        ],
        "params": {"values": values, "sha256": content_hash(values)},
        "files": [
            {"path": "datasets.parquet", "sha256": TREE, "bytes": 10, "rows": 1},
            {"path": "notes/a.txt", "sha256": TREE, "bytes": 0, "rows": None},
        ],
        "checks": [{"name": "key_unique", "status": "pass", "details": {}}],
    }
    return doc | changes


def test_a_provenance_round_trips() -> None:
    provenance = StageProvenance.from_document(provenance_doc())
    assert provenance.model_dump(mode="json") == provenance_doc()
    empty: dict[str, JSONValue] = {}
    StageProvenance.from_document(provenance_doc(params={"values": empty, "sha256": content_hash(empty)}, checks=[]))


def with_input(**changes: Any) -> dict[str, Any]:
    first = provenance_doc()["inputs"][0] | changes
    return provenance_doc(inputs=[first])


@pytest.mark.parametrize(
    "doc",
    [
        provenance_doc(params={"values": {"a": 1}, "sha256": content_hash({"a": 2})}),
        with_input(type="glob"),
        with_input(path="/abs/path"),
        with_input(path="data/../x"),
        with_input(dataset_ids=[DATASET, DATASET]),
        with_input(dataset_ids=["not-an-id"]),
        provenance_doc(inputs=[provenance_doc()["inputs"][0], provenance_doc()["inputs"][0]]),
        provenance_doc(
            files=[
                {"path": "b", "sha256": TREE, "bytes": 1, "rows": None},
                {"path": "a", "sha256": TREE, "bytes": 1, "rows": None},
            ]
        ),
        provenance_doc(files=[{"path": "_provenance.json", "sha256": TREE, "bytes": 1, "rows": None}]),
        provenance_doc(files=[{"path": "a", "sha256": TREE, "bytes": -1, "rows": None}]),
        provenance_doc(checks=[{"name": "x", "status": "warn", "details": {}}]),
        provenance_doc(stage="Bad Name"),
        provenance_doc(landed_at="2026-10-23T00:00:00Z"),
    ],
    ids=[
        "params-hash",
        "input-type",
        "absolute-path",
        "dotdot-path",
        "duplicate-ids",
        "bad-id",
        "duplicate-input",
        "unsorted-files",
        "provenance-listed",
        "negative-bytes",
        "check-status",
        "stage-name",
        "run-specific-field",
    ],
)
def test_provenance_rules(doc: dict[str, Any]) -> None:
    with pytest.raises(InvalidInputError):
        StageProvenance.from_document(doc)


# RegionProfile (D3 §10.3)


def test_the_sa_region_profile_shape() -> None:
    parsers = ["elec_bill", "gas_bill", "offer_cdr", "retailer_txn", "retailer_usage", "sapn_interval", "sapn_summary"]
    profile = RegionProfile.from_document(
        {
            "kind": "region_profile",
            "schema_version": 2,
            "name": "sa",
            "local_tz": None,
            "tou_windows": None,
            "parsers": parsers,
        }
    )
    assert profile.parsers == tuple(SourceType(parser) for parser in parsers)


@pytest.mark.parametrize(
    "changes",
    [{"name": "SA"}, {"name": ""}, {"parsers": ["sapn_interval", "sapn_interval"]}, {"parsers": ["nem13"]}],
)
def test_region_profile_rules(changes: dict[str, JSONValue]) -> None:
    doc: dict[str, JSONValue] = {
        "kind": "region_profile",
        "schema_version": 2,
        "name": "sa",
        "local_tz": None,
        "tou_windows": None,
        "parsers": [],
    }
    with pytest.raises(InvalidInputError):
        RegionProfile.from_document(doc | changes)
