"""The `parse_nem12` stage (P2 spec §7.1, §13.3): one Parquet per dataset plus `index.parquet`, and its checks.

Every value is synthetic. The expected numbers come from the generator specs and the independent oracle, never from
`read_nem12`.
"""

import json
import math
import shutil
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from energatron.core.canonical import JSONValue, canonical_json, content_hash, sha256_hex
from energatron.core.errors import CatalogCorruptError, CheckFailedError, InvalidInputError
from energatron.core.ids import dataset_id
from energatron.core.models.catalog import CatalogRecord, Detector, SourceType
from energatron.core.models.provenance import StageCode, StageProvenance
from energatron.core.nem12 import Nem12Read
from energatron.services.stages import parse_nem12
from energatron.services.stages.registry import get_stage
from energatron.services.stages.runner import prepare_stage, run_stage
from tests.fixtures import oracle
from tests.fixtures.make_nem12 import Nem12Spec, QualityRange, make_nem12, sapn_filename
from tests.fixtures.pipeline_workspace import (
    INTERVAL_MINUTES,
    Dataset,
    RawZone,
    build_raw_zone,
    empty_raw_zone,
    land_blob,
    make_record,
    total_kwh,
)
from tests.fixtures.synthetic import SYNTHETIC_ACCOUNT, SYNTHETIC_NMI, SYNTHETIC_NMI11, SYNTHETIC_SERIAL

CHECKS = [
    "blobs_match_catalog",
    "detector_known",
    "datasets_read",
    "values_per_day",
    "units",
    "v_days_covered",
    "non_negative",
    "catalog_agrees",
]
PER_DAY = 1440 // INTERVAL_MINUTES
STAGED_SCHEMA = pa.schema(
    [
        ("suffix", pa.string()),
        ("meter_date", pa.date32()),
        ("meter_start", pa.timestamp("us")),
        ("interval_minutes", pa.int16()),
        ("kwh", pa.float64()),
        ("quality", pa.string()),
        ("quality_flag", pa.string()),
        ("reason_code", pa.int16()),
        ("update_dt", pa.timestamp("us")),
        ("dataset_id", pa.string()),
    ]
)
INDEX_SCHEMA = pa.schema(
    [
        ("dataset_id", pa.string()),
        ("precedence", pa.int32()),
        ("is_current", pa.bool_()),
        ("read_ok", pa.bool_()),
        ("interval_minutes", pa.int16()),
        ("suffixes", pa.list_(pa.string())),
        ("days", pa.int32()),
        ("coverage_start", pa.date32()),
        ("coverage_end", pa.date32()),
    ]
)


def _code() -> StageCode:
    source = sha256_hex(b"parse_nem12")
    digest = content_hash({"source_sha256": source, "packages": {}, "python": "3.14.0"})
    return StageCode(stage_sha256=digest, source_sha256=source, python="3.14.0", packages={}, files=["__init__.py"])


CODE = _code()


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> RawZone:
    root = tmp_path_factory.mktemp("template")
    return build_raw_zone(root)


@pytest.fixture
def zone(template: RawZone, tmp_path: Path) -> RawZone:
    """A private copy of the fixture raw zone, with a `params.yaml`."""
    root = tmp_path / "workspace"
    shutil.copytree(template.root, root)
    (root / "params.yaml").write_text("kind: params\nschema_version: 1\n")
    return replace(template, root=root)


@pytest.fixture
def empty(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    empty_raw_zone(root)
    (root / "params.yaml").write_text("kind: params\nschema_version: 1\n")
    return root


def run_parse(root: Path, **overrides: Path) -> None:
    spec = get_stage("parse_nem12")
    prepared = prepare_stage(spec, root=root, params_file=root / "params.yaml", input_overrides=overrides)
    run_stage(spec, prepared.inputs, prepared.params, prepared.outputs, code=CODE, recorded_code=CODE.stage_sha256)


def output(root: Path) -> Path:
    return root / "data" / "staged" / "elec_interval"


def index(root: Path) -> pa.Table:
    return pq.read_table(output(root) / "index.parquet")


def staged(root: Path, dataset: Dataset | str) -> pa.Table:
    ident = dataset if isinstance(dataset, str) else dataset.dataset_id
    return pq.read_table(output(root) / f"{ident}.parquet")


def checks(root: Path) -> dict[str, tuple[str, dict[str, JSONValue]]]:
    provenance = StageProvenance.from_document(json.loads((output(root) / "_provenance.json").read_text()))
    return {check.name: (check.status, dict(check.details)) for check in provenance.checks}


def failing(root: Path) -> dict[str, dict[str, JSONValue]]:
    with pytest.raises(CheckFailedError):
        run_parse(root)
    return {name: details for name, (status, details) in checks(root).items() if status == "fail"}


def edit_record(root: Path, dataset: Dataset, change: Callable[[dict[str, JSONValue]], None]) -> None:
    """Rewrite one catalog line, as a hand edit or an older tool would leave it."""
    catalog = root / "data" / "raw" / dataset.source.value / "_catalog.jsonl"
    out: list[bytes] = []
    for line in catalog.read_bytes().splitlines():
        doc = json.loads(line)
        if doc["dataset_id"] == dataset.dataset_id:
            change(doc)
        out.append(canonical_json(doc) + b"\n")
    catalog.write_bytes(b"".join(out))


def land_spec(root: Path, spec: Nem12Spec, generated: datetime, **kw: object) -> Dataset:
    window = (spec.start, spec.start + timedelta(days=spec.days - 1))
    name = sapn_filename("DETAILED", SYNTHETIC_NMI, window, generated)
    return land_blob(root, SourceType.SAPN_INTERVAL, name, make_nem12(spec), **kw)  # type: ignore[arg-type]


def small_spec(**changes: object) -> Nem12Spec:
    return replace(Nem12Spec(start=date(2025, 6, 1), days=3, interval_minutes=INTERVAL_MINUTES), **changes)  # type: ignore[arg-type]


def blob_of(root: Path, dataset: Dataset) -> Path:
    return root / dataset.stored_path


def values(table: pa.Table, name: str) -> list[object]:
    return table.column(name).to_pylist()


def on_day(table: pa.Table, day: date) -> pa.Table:
    """The rows of one meter date."""
    mask = [meter_date == day for meter_date in values(table, "meter_date")]
    return table.filter(pa.array(mask, type=pa.bool_()))


def test_spec_reads_the_raw_directory_and_writes_the_staged_zone() -> None:
    spec = parse_nem12.SPEC
    assert spec.name == "parse_nem12"
    assert dict(spec.inputs) == {"raw": "data/raw/sapn_interval"}
    assert spec.params == ()
    assert dict(spec.outputs) == {"staged": "data/staged/elec_interval"}


def test_it_is_registered_after_catalog_index() -> None:
    assert get_stage("parse_nem12") is parse_nem12.SPEC


# --- the fixture zone ------------------------------------------------------------------------------------------------


def test_it_writes_one_parquet_per_dataset_and_the_index(zone: RawZone) -> None:
    run_parse(zone.root)
    names = sorted(path.name for path in output(zone.root).iterdir())
    expected = sorted(
        ["_provenance.json", "index.parquet", *(f"{dataset.dataset_id}.parquet" for dataset in zone.interval)]
    )
    assert names == expected


def test_every_check_passes_on_the_fixture_zone(zone: RawZone) -> None:
    run_parse(zone.root)
    results = checks(zone.root)
    assert list(results) == CHECKS
    assert {name: status for name, (status, _) in results.items()} == dict.fromkeys(CHECKS, "pass")


def test_a_staged_table_has_the_columns_types_and_metadata(zone: RawZone) -> None:
    run_parse(zone.root)
    table = staged(zone.root, zone.interval[0])
    assert table.schema.remove_metadata().equals(STAGED_SCHEMA)
    assert table.schema.metadata == {b"energatron.kind": b"elec_interval_staged", b"energatron.schema_version": b"1"}


def test_the_index_has_the_columns_types_and_metadata(zone: RawZone) -> None:
    run_parse(zone.root)
    table = index(zone.root)
    assert table.schema.remove_metadata().equals(INDEX_SCHEMA)
    assert table.schema.metadata == {b"energatron.kind": b"elec_interval_index", b"energatron.schema_version": b"1"}


@pytest.mark.parametrize("position", [0, 1])
def test_a_staged_table_holds_every_interval_of_its_file(zone: RawZone, position: int) -> None:
    run_parse(zone.root)
    dataset, spec = zone.interval[position], (zone.specs.a, zone.specs.b)[position]
    table = staged(zone.root, dataset)
    assert table.num_rows == spec.days * PER_DAY
    assert set(values(table, "dataset_id")) == {dataset.dataset_id}
    assert set(values(table, "suffix")) == {"E1"}
    assert set(values(table, "interval_minutes")) == {INTERVAL_MINUTES}
    keys = list(zip(values(table, "suffix"), values(table, "meter_start"), strict=True))
    assert keys == sorted(keys)
    assert len(set(keys)) == table.num_rows
    assert values(table, "meter_start")[0] == datetime.combine(spec.start, datetime.min.time())
    last = spec.start + timedelta(days=spec.days - 1)
    assert values(table, "meter_start")[-1] == datetime.combine(last, datetime.min.time()) + timedelta(
        minutes=1440 - INTERVAL_MINUTES
    )
    text = make_nem12(spec)
    assert math.isclose(math.fsum(values(table, "kwh")), total_kwh(text), rel_tol=1e-12)  # type: ignore[arg-type]


def test_the_values_of_each_day_equal_the_oracle(zone: RawZone) -> None:
    run_parse(zone.root)
    table = staged(zone.root, zone.interval[0])
    expected = oracle.day_sums(make_nem12(zone.specs.a))
    per_day: dict[date, list[float]] = {}
    for day, kwh in zip(values(table, "meter_date"), values(table, "kwh"), strict=True):
        per_day.setdefault(day, []).append(kwh)  # type: ignore[arg-type]
    assert {day: math.fsum(kwh) for day, kwh in per_day.items()} == {
        day: total for (suffix, day), total in expected.items() if suffix == "E1"
    }


def test_quality_and_reason_follow_the_day_and_its_400_ranges(zone: RawZone) -> None:
    run_parse(zone.root)
    a, b = staged(zone.root, zone.interval[0]), staged(zone.root, zone.interval[1])

    def rows_of(table: pa.Table, day: date) -> list[tuple[object, object, object]]:
        picked = on_day(table, day)
        return list(
            zip(values(picked, "quality"), values(picked, "quality_flag"), values(picked, "reason_code"), strict=True)
        )

    assert set(rows_of(a, zone.specs.v_day)) == {("S14", "S", 79)}
    assert set(rows_of(b, zone.specs.e_day)) == {("E52", "E", None)}
    ordinary = zone.specs.a_only[0]
    assert set(rows_of(a, ordinary)) == {("A", "A", None)}


def test_update_dt_is_the_days_update_time(zone: RawZone) -> None:
    run_parse(zone.root)
    b = staged(zone.root, zone.interval[1])
    picked = on_day(b, zone.specs.conflict_day)
    expected = datetime.combine(zone.specs.conflict_day + timedelta(days=11), datetime.min.time()) + timedelta(hours=5)
    assert set(values(picked, "update_dt")) == {expected}


def test_the_index_ranks_the_newer_file_first_and_marks_both_current(zone: RawZone) -> None:
    run_parse(zone.root)
    table = index(zone.root)
    a, b = zone.interval
    assert values(table, "dataset_id") == [b.dataset_id, a.dataset_id]
    assert values(table, "precedence") == [1, 2]
    assert values(table, "is_current") == [True, True]
    assert values(table, "read_ok") == [True, True]
    assert values(table, "interval_minutes") == [INTERVAL_MINUTES] * 2
    assert values(table, "suffixes") == [["E1"], ["E1"]]
    assert values(table, "days") == [zone.specs.b.days, zone.specs.a.days]
    b_last = zone.specs.b.start + timedelta(days=zone.specs.b.days - 1)
    a_last = zone.specs.a.start + timedelta(days=zone.specs.a.days - 1)
    assert values(table, "coverage_start") == [zone.specs.b.start, zone.specs.a.start]
    assert values(table, "coverage_end") == [b_last, a_last]


def test_provenance_names_the_raw_directory_and_its_datasets(zone: RawZone) -> None:
    run_parse(zone.root)
    provenance = StageProvenance.from_document(json.loads((output(zone.root) / "_provenance.json").read_text()))
    (record,) = provenance.inputs
    assert (record.name, record.type, record.path) == ("raw", "dir", "data/raw/sapn_interval")
    assert record.dataset_ids == sorted(dataset.dataset_id for dataset in zone.interval)


def test_running_twice_writes_the_same_bytes(zone: RawZone) -> None:
    run_parse(zone.root)
    first = {path.name: path.read_bytes() for path in output(zone.root).iterdir()}
    run_parse(zone.root)
    assert {path.name: path.read_bytes() for path in output(zone.root).iterdir()} == first


def test_the_input_can_be_overridden(zone: RawZone, tmp_path: Path) -> None:
    other = tmp_path / "elsewhere"
    shutil.copytree(zone.root / "data" / "raw" / "sapn_interval", other)
    shutil.rmtree(zone.root / "data" / "raw" / "sapn_interval")  # the default location is gone
    run_parse(zone.root, raw=other)
    assert staged(zone.root, zone.interval[0]).num_rows == zone.specs.a.days * PER_DAY


# --- no identifier in any output -------------------------------------------------------------------------------------


def test_no_nmi_serial_or_account_reaches_any_output(zone: RawZone) -> None:
    run_parse(zone.root)
    secrets = [SYNTHETIC_NMI, SYNTHETIC_NMI11, SYNTHETIC_SERIAL, SYNTHETIC_ACCOUNT]
    for path in output(zone.root).rglob("*"):
        if not path.is_file():
            continue
        data = path.read_bytes()
        assert not any(secret.encode() in data for secret in secrets), path.name
        if path.suffix == ".parquet":
            table = pq.read_table(path)
            assert not any(name in table.column_names for name in ("nmi", "serial", "meter_serial", "account"))
            for column in table.column_names:
                if pa.types.is_string(table.schema.field(column).type):
                    cells = {str(cell) for cell in table.column(column).to_pylist()}
                    assert not any(secret in cell for cell in cells for secret in secrets)


# --- the empty catalog -----------------------------------------------------------------------------------------------


def test_an_empty_catalog_gives_an_empty_index_and_every_check_passes(empty: Path) -> None:
    run_parse(empty)
    table = index(empty)
    assert table.num_rows == 0
    assert table.schema.remove_metadata().equals(INDEX_SCHEMA)
    assert [path.name for path in output(empty).iterdir() if path.suffix == ".parquet"] == ["index.parquet"]
    assert {status for status, _ in checks(empty).values()} == {"pass"}
    assert list(checks(empty)) == CHECKS


def test_a_missing_catalog_file_is_invalid_input(empty: Path) -> None:
    (empty / "data" / "raw" / "sapn_interval" / "_catalog.jsonl").unlink()
    with pytest.raises(InvalidInputError):
        run_parse(empty)


def test_a_corrupt_catalog_line_fails_the_stage(empty: Path) -> None:
    (empty / "data" / "raw" / "sapn_interval" / "_catalog.jsonl").write_bytes(b"not json\n")
    with pytest.raises(CatalogCorruptError):
        run_parse(empty)


# --- precedence and is_current ---------------------------------------------------------------------------------------


def test_a_superseded_dataset_is_read_but_not_current(empty: Path) -> None:
    old = land_spec(empty, small_spec(seed=1), datetime(2025, 7, 1, 9, 0, 0))
    new = land_spec(empty, small_spec(seed=2), datetime(2025, 7, 2, 9, 0, 0), supersedes=old.dataset_id)
    run_parse(empty)
    table = index(empty)
    assert values(table, "dataset_id") == [new.dataset_id, old.dataset_id]
    assert values(table, "is_current") == [True, False]
    assert values(table, "read_ok") == [True, True]
    assert staged(empty, old).num_rows == 3 * PER_DAY  # still read: the merge decides what to use


def test_equal_source_times_rank_by_file_mtime_then_catalog_position(empty: Path) -> None:
    stamp = datetime(2025, 7, 1, 9, 0, 0)
    first = land_spec(empty, small_spec(seed=1), stamp, file_mtime=datetime(2025, 7, 1, 10, 0, tzinfo=UTC))
    second = land_spec(empty, small_spec(seed=2), stamp, file_mtime=datetime(2025, 7, 1, 11, 0, tzinfo=UTC))
    third = land_spec(empty, small_spec(seed=3), stamp, file_mtime=datetime(2025, 7, 1, 10, 0, tzinfo=UTC))
    run_parse(empty)
    assert values(index(empty), "dataset_id") == [second.dataset_id, third.dataset_id, first.dataset_id]
    assert values(index(empty), "precedence") == [1, 2, 3]


# --- each check passes and fails -------------------------------------------------------------------------------------


def test_blobs_match_catalog_fails_for_a_tampered_blob(zone: RawZone) -> None:
    target = zone.interval[0]
    blob_of(zone.root, target).write_bytes(blob_of(zone.root, target).read_bytes() + b"\n")
    failed = failing(zone.root)
    assert target.dataset_id in json.dumps(failed["blobs_match_catalog"])
    assert zone.interval[1].dataset_id not in json.dumps(failed["blobs_match_catalog"])
    assert "datasets_read" in failed  # a dataset that doesn't match its record is not read
    assert not (output(zone.root) / f"{target.dataset_id}.parquet").exists()
    assert (output(zone.root) / f"{zone.interval[1].dataset_id}.parquet").exists()
    table = index(zone.root)
    rows = dict(zip(values(table, "dataset_id"), values(table, "read_ok"), strict=True))
    assert rows == {target.dataset_id: False, zone.interval[1].dataset_id: True}


def test_blobs_match_catalog_fails_for_a_missing_blob(zone: RawZone) -> None:
    target = zone.interval[1]
    blob_of(zone.root, target).unlink()
    failed = failing(zone.root)
    assert target.dataset_id in json.dumps(failed["blobs_match_catalog"])


def test_detector_known_fails_for_a_detector_version_the_stage_doesnt_know(zone: RawZone) -> None:
    target = zone.interval[0]
    edit_record(zone.root, target, lambda doc: doc["detector"].update({"version": 2}))  # type: ignore[union-attr]
    failed = failing(zone.root)
    assert target.dataset_id in json.dumps(failed["detector_known"])
    assert "datasets_read" in failed
    assert not (output(zone.root) / f"{target.dataset_id}.parquet").exists()


def test_a_detector_of_another_source_is_refused_at_the_catalog(zone: RawZone) -> None:
    """`CatalogRecord` itself requires `detector.name` to equal `source`, so a doctored line never reaches the stage's
    own `detector.name` guard (next test): the catalog reader refuses it."""
    edit_record(zone.root, zone.interval[0], lambda doc: doc["detector"].update({"name": "sapn_summary"}))  # type: ignore[union-attr]
    with pytest.raises(CatalogCorruptError):
        run_parse(zone.root)


def test_detector_known_fails_for_a_detector_of_another_source(zone: RawZone, monkeypatch: pytest.MonkeyPatch) -> None:
    """The stage's own guard on `detector.name`, reached by handing it a record the catalog reader would refuse."""
    target = zone.interval[0]
    original = parse_nem12.parse_catalog

    def doctored(data: bytes, source: SourceType) -> list[CatalogRecord]:
        records = original(data, source)
        other = Detector(name=SourceType.SAPN_SUMMARY, version=1, evidence=("filename", "content"))
        return [r.model_copy(update={"detector": other}) if r.dataset_id == target.dataset_id else r for r in records]

    monkeypatch.setattr(parse_nem12, "parse_catalog", doctored)
    failed = failing(zone.root)
    assert failed["detector_known"] == {"unknown": [target.dataset_id]}
    assert failed["datasets_read"]["not_read"] == {target.dataset_id: "detector_unknown"}  # type: ignore[index]
    assert not (output(zone.root) / f"{target.dataset_id}.parquet").exists()


def test_datasets_read_fails_for_a_value_that_isnt_a_number_and_names_the_reason(empty: Path) -> None:
    good = land_spec(empty, small_spec(seed=1), datetime(2025, 7, 1, 9, 0, 0))
    spec = small_spec(seed=2, day_values={date(2025, 6, 2): ["abc", *["0.100"] * (PER_DAY - 1)]})
    bad = land_spec(empty, spec, datetime(2025, 7, 2, 9, 0, 0))
    failed = failing(empty)
    assert failed["datasets_read"] == {"not_read": {bad.dataset_id: "bad_value"}}
    assert set(failed) == {"datasets_read"}
    assert (output(empty) / f"{good.dataset_id}.parquet").exists()
    assert not (output(empty) / f"{bad.dataset_id}.parquet").exists()
    rows = dict(zip(values(index(empty), "dataset_id"), values(index(empty), "read_ok"), strict=True))
    assert rows == {good.dataset_id: True, bad.dataset_id: False}


def test_values_per_day_fails_when_a_day_lacks_intervals(zone: RawZone, monkeypatch: pytest.MonkeyPatch) -> None:
    original = parse_nem12.read_nem12

    def short(data: bytes) -> Nem12Read:
        read = original(data)
        return replace(read, table=read.table.iloc[1:].reset_index(drop=True))

    monkeypatch.setattr(parse_nem12, "read_nem12", short)
    failed = failing(zone.root)
    assert set(failed) == {"values_per_day"}
    assert {zone.interval[0].dataset_id, zone.interval[1].dataset_id} <= set(failed["values_per_day"]["faulty"])  # type: ignore[arg-type]


def test_units_fails_when_a_reader_refuses_the_unit(zone: RawZone, monkeypatch: pytest.MonkeyPatch) -> None:
    original = parse_nem12.read_nem12
    refused = zone.interval[0].dataset_id

    def read(data: bytes) -> Nem12Read:
        if sha256_hex(data) == zone.interval[0].sha256:
            raise InvalidInputError("A 200 record's unit isn't KWH.", reason="bad_unit")
        return original(data)

    monkeypatch.setattr(parse_nem12, "read_nem12", read)
    failed = failing(zone.root)
    assert failed["units"] == {"bad_unit": [refused]}
    assert failed["datasets_read"] == {"not_read": {refused: "bad_unit"}}


def test_units_fails_for_a_real_file_in_another_unit(empty: Path) -> None:
    """`inspect` refuses such a file at landing, so the record is built by hand: the blob is edited, and the record's
    hash, ID, path and size are recomputed to match it (a file edited before it was catalogued)."""
    name = sapn_filename("DETAILED", SYNTHETIC_NMI, (date(2025, 6, 1), date(2025, 6, 3)), datetime(2025, 7, 1, 9, 0, 0))
    good = make_nem12(small_spec())
    edited = good.replace(b",KWH,", b",MWH,")
    assert edited != good
    record = make_record(SourceType.SAPN_INTERVAL, name, good).model_dump(mode="json")
    digest = sha256_hex(edited)
    ident = dataset_id("sapn_interval", datetime.fromisoformat(str(record["source_ts"])), digest)
    record.update(
        sha256=digest, dataset_id=ident, bytes=len(edited), stored_path=f"data/raw/sapn_interval/2025/{ident}.csv"
    )
    CatalogRecord.from_document(record)  # the doctored record is itself valid
    blob = empty / str(record["stored_path"])
    blob.parent.mkdir(parents=True, exist_ok=True)
    blob.write_bytes(edited)
    (empty / "data" / "raw" / "sapn_interval" / "_catalog.jsonl").write_bytes(canonical_json(record) + b"\n")
    failed = failing(empty)
    assert failed["units"] == {"bad_unit": [ident]}
    assert failed["datasets_read"] == {"not_read": {ident: "bad_unit"}}
    assert set(failed) == {"units", "datasets_read"}


def test_v_days_covered_fails_when_a_v_day_has_uncovered_intervals(empty: Path) -> None:
    day = date(2025, 6, 2)
    spec = small_spec(v_days={day: (QualityRange(1, PER_DAY // 2, "S14"),)})
    bad = land_spec(empty, spec, datetime(2025, 7, 1, 9, 0, 0))
    failed = failing(empty)
    assert failed == {"v_days_covered": {"faults": {bad.dataset_id: PER_DAY - PER_DAY // 2}}}
    assert staged(empty, bad).num_rows == 3 * PER_DAY  # the table is still written


def test_v_days_covered_fails_for_an_interval_covered_twice(empty: Path) -> None:
    day = date(2025, 6, 2)
    ranges = (QualityRange(1, PER_DAY, "S14"), QualityRange(1, 2, "F17"))
    bad = land_spec(empty, small_spec(v_days={day: ranges}), datetime(2025, 7, 1, 9, 0, 0))
    failed = failing(empty)
    assert failed == {"v_days_covered": {"faults": {bad.dataset_id: 2}}}


def test_non_negative_fails_for_a_negative_value(empty: Path) -> None:
    bad = land_spec(empty, small_spec(negative_on=date(2025, 6, 2)), datetime(2025, 7, 1, 9, 0, 0))
    failed = failing(empty)
    assert failed == {"non_negative": {"negative": {bad.dataset_id: 1}}}
    assert min(values(staged(empty, bad), "kwh")) < 0  # type: ignore[type-var]


def test_catalog_agrees_fails_when_the_record_disagrees_with_the_file(zone: RawZone) -> None:
    target = zone.interval[1]
    edit_record(zone.root, target, lambda doc: doc["extra"].update({"days": 5}))  # type: ignore[union-attr]
    failed = failing(zone.root)
    assert failed == {"catalog_agrees": {"disagree": [target.dataset_id]}}


@pytest.mark.parametrize(
    "change",
    [
        lambda doc: doc["extra"].update({"days_v": 9}),  # type: ignore[union-attr]
        lambda doc: doc["extra"].update({"interval_minutes": 15}),  # type: ignore[union-attr]
        lambda doc: doc["coverage"].update({"end": "2030-01-01"}),  # type: ignore[union-attr]
        lambda doc: doc.update({"coverage": None}),
    ],
)
def test_catalog_agrees_compares_days_v_interval_and_coverage(
    zone: RawZone, change: Callable[[dict[str, JSONValue]], None]
) -> None:
    target = zone.interval[0]
    edit_record(zone.root, target, change)
    assert failing(zone.root) == {"catalog_agrees": {"disagree": [target.dataset_id]}}


def test_a_failed_check_leaves_the_outputs_in_place(zone: RawZone) -> None:
    blob_of(zone.root, zone.interval[0]).write_bytes(b"tampered")
    failing(zone.root)
    assert (output(zone.root) / "index.parquet").is_file()
    assert (output(zone.root) / "_provenance.json").is_file()
