"""The `usage_report` stage (P3 spec §9, §10, §13.3): the nine usage marts turned into one HTML page.

Every value is synthetic. The marts come from `tests/fixtures/usage_marts.py` and are written as `usage_profile` writes
them; the page the stage leaves must equal what the pure renderer makes from the same marts.
"""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, Final

import pyarrow.parquet as pq
import pytest

from energatron.core.canonical import content_hash, sha256_hex
from energatron.core.errors import InvalidInputError
from energatron.core.models.provenance import StageCode, StageProvenance
from energatron.core.usage_report import UsageMarts, render
from energatron.services.files import write_json, write_parquet
from energatron.services.stages import usage_report
from energatron.services.stages.dvc_yaml import stage_entry
from energatron.services.stages.registry import STAGES, get_stage
from energatron.services.stages.runner import StageResult, prepare_stage, run_stage
from energatron.services.stages.tables import USAGE_TABLES
from tests.fixtures.params_text import params_v3_text
from tests.fixtures.usage_marts import Marts, report_marts

PROFILE: Final = Path("data") / "marts" / "usage_profile"
REPORT: Final = Path("data") / "reports" / "usage"
FILES: Final = ["_provenance.json", "index.html"]


def _code() -> StageCode:
    source = sha256_hex(b"usage_report")
    digest = content_hash({"source_sha256": source, "packages": {}, "python": "3.14.0"})
    return StageCode(stage_sha256=digest, source_sha256=source, python="3.14.0", packages={}, files=["__init__.py"])


def write_marts(root: Path, marts: Marts) -> Path:
    """Write the nine tables and `summary.json` as `usage_profile` does."""
    directory = root / PROFILE
    directory.mkdir(parents=True, exist_ok=True)
    for table in USAGE_TABLES:
        write_parquet(
            marts.tables[table.stem],
            directory / f"{table.stem}.parquet",
            kind=table.kind,
            schema_version=table.version,
            sort_by=table.sort,
            schema=table.schema,
        )
    write_json(directory / "summary.json", marts.summary.model_dump(mode="json"))
    return directory


def run_report(root: Path) -> StageResult:
    spec = get_stage("usage_report")
    prepared = prepare_stage(spec, root=root, params_file=root / "params.yaml")
    code = _code()
    return run_stage(
        spec, prepared.inputs, prepared.params, prepared.outputs, code=code, recorded_code=code.stage_sha256
    )


def make_root(tmp_path: Path, marts: Marts | None = None) -> Path:
    root = tmp_path / "workspace"
    root.mkdir(parents=True)
    (root / "params.yaml").write_text(params_v3_text(region=True), encoding="utf-8")
    write_marts(root, marts if marts is not None else report_marts())
    return root


@pytest.fixture(scope="module")
def marts() -> Marts:
    return report_marts()


def page(root: Path) -> str:
    return (root / REPORT / "index.html").read_text(encoding="utf-8")


def rewrite_metadata(path: Path, **changes: str) -> None:
    """Rewrite one Parquet file with other `energatron.*` metadata keys."""
    table = pq.read_table(path)
    metadata = dict(table.schema.metadata or {})
    metadata.update({f"energatron.{key}".encode(): value.encode() for key, value in changes.items()})
    pq.write_table(table.replace_schema_metadata(metadata), path)


# --- the spec ----------------------------------------------------------------------------------------------------


def test_spec_reads_the_mart_directory_alone_and_declares_no_params() -> None:
    spec = usage_report.SPEC
    assert spec.name == "usage_report"
    assert dict(spec.inputs) == {"profile": "data/marts/usage_profile"}
    assert spec.params == ()
    assert dict(spec.outputs) == {"report": "data/reports/usage"}


def test_it_is_registered_right_after_usage_profile() -> None:
    names = list(STAGES)
    assert names[names.index("usage_profile") + 1] == "usage_report"


def test_an_empty_params_tuple_reaches_the_dvc_entry_as_the_code_entry_alone() -> None:
    assert stage_entry(usage_report.SPEC) == {
        "cmd": "energatron stage usage_report",
        "deps": ["data/marts/usage_profile"],
        "params": [{".energatron/code_fingerprint.json": ["stages.usage_report.stage_sha256"]}],
        "outs": ["data/reports/usage"],
    }


def test_prepare_stage_accepts_an_empty_params_tuple(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    prepared = prepare_stage(usage_report.SPEC, root=root, params_file=root / "params.yaml")
    assert dict(prepared.params) == {}


# --- a passing run -----------------------------------------------------------------------------------------------


def test_a_run_writes_the_page_and_its_provenance_and_nothing_else(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    found = run_report(root)
    assert sorted(path.name for path in (root / REPORT).iterdir()) == FILES
    assert found.warnings == []
    assert found.checks == []


def test_the_page_equals_what_the_renderer_makes_from_the_same_marts(tmp_path: Path, marts: Marts) -> None:
    root = make_root(tmp_path, marts)
    run_report(root)
    assert page(root) == render(UsageMarts(marts.tables, marts.summary))


def test_the_same_marts_twice_give_identical_bytes(tmp_path: Path, marts: Marts) -> None:
    first, second = make_root(tmp_path / "a", marts), make_root(tmp_path / "b", marts)
    run_report(first)
    run_report(second)
    assert (first / REPORT / "index.html").read_bytes() == (second / REPORT / "index.html").read_bytes()


def test_the_provenance_names_the_one_input_and_records_no_params(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    run_report(root)
    provenance = StageProvenance.from_document(json.loads((root / REPORT / "_provenance.json").read_text()))
    assert provenance.stage == "usage_report"
    assert [record.name for record in provenance.inputs] == ["profile"]
    assert dict(provenance.params.values) == {}
    assert [file.path for file in provenance.files] == ["index.html"]


def test_it_reads_only_the_profile_directory(tmp_path: Path, marts: Marts) -> None:
    """Called with the profile directory and no `params.yaml` at all, the stage still runs."""
    root = tmp_path / "workspace"
    root.mkdir()
    directory = write_marts(root, marts)
    spec = usage_report.SPEC
    code = _code()
    run_stage(
        spec,
        {"profile": directory},
        {},
        {"report": root / REPORT},
        code=code,
        recorded_code=code.stage_sha256,
    )
    assert page(root) == render(UsageMarts(marts.tables, marts.summary))
    assert sorted(path.name for path in root.iterdir()) == ["data"]


def test_a_summary_with_no_register_gives_the_empty_page(tmp_path: Path, marts: Marts) -> None:
    empty = Marts(
        {stem: frame.iloc[0:0] for stem, frame in marts.tables.items()},
        marts.summary.model_copy(update={"suffixes": {}}),
    )
    root = make_root(tmp_path, empty)
    run_report(root)
    assert "nothing to show" in page(root)


# --- a mart directory it must refuse -----------------------------------------------------------------------------


def refused(root: Path) -> InvalidInputError:
    with pytest.raises(InvalidInputError) as caught:
        run_report(root)
    assert caught.value.details["input"] == "profile"
    assert not (root / REPORT).exists()
    return caught.value


@pytest.mark.parametrize("table", [table.stem for table in USAGE_TABLES])
def test_a_table_of_another_kind_is_refused_naming_its_file(tmp_path: Path, table: str) -> None:
    root = make_root(tmp_path)
    rewrite_metadata(root / PROFILE / f"{table}.parquet", kind="usage_other")
    assert refused(root).details["file"] == f"{table}.parquet"


@pytest.mark.parametrize("table", [table.stem for table in USAGE_TABLES])
def test_a_table_of_another_version_is_refused_naming_its_file(tmp_path: Path, table: str) -> None:
    root = make_root(tmp_path)
    rewrite_metadata(root / PROFILE / f"{table}.parquet", schema_version="2")
    assert refused(root).details["file"] == f"{table}.parquet"


def test_a_table_with_other_columns_is_refused(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    path = root / PROFILE / "daily.parquet"
    table = pq.read_table(path)
    pq.write_table(table.drop_columns(["kwh"]).replace_schema_metadata(table.schema.metadata), path)
    assert refused(root).details["file"] == "daily.parquet"


def test_a_missing_table_is_refused(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    (root / PROFILE / "heatmap.parquet").unlink()
    assert refused(root).details["file"] == "heatmap.parquet"


def test_a_file_that_is_not_parquet_is_refused(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    (root / PROFILE / "monthly.parquet").write_bytes(b"not parquet")
    assert refused(root).details["file"] == "monthly.parquet"


def test_a_missing_summary_is_refused(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    (root / PROFILE / "summary.json").unlink()
    assert refused(root).details["file"] == "summary.json"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda doc: {**doc, "kind": "usage_other"},
        lambda doc: {**doc, "schema_version": 2},
        lambda doc: {**doc, "schema_version": 0},
        lambda doc: {key: value for key, value in doc.items() if key != "suffixes"},
        lambda doc: [doc],
    ],
    ids=["other kind", "newer version", "older version", "missing field", "not an object"],
)
def test_a_summary_of_another_kind_or_version_or_shape_is_refused(tmp_path: Path, mutate: Callable[[Any], Any]) -> None:
    root = make_root(tmp_path)
    path = root / PROFILE / "summary.json"
    doc = json.loads(path.read_text())
    path.write_text(json.dumps(mutate(doc)), encoding="utf-8")
    assert refused(root).details["file"] == "summary.json"


def test_a_summary_that_is_not_json_is_refused(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    (root / PROFILE / "summary.json").write_bytes(b"\xff\xfe not json")
    assert refused(root).details["file"] == "summary.json"
