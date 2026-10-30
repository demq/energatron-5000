"""What P1 assumes about DVC 3.67.1 (D3 §14), checked in scratch repos. D1 §11.5's checks are in `test_dvc_runner.py`.

If one of these fails after a DVC upgrade, stop and ask the owner; don't work around it (D3 §14).
"""

import json
import os
import stat
import time
from pathlib import Path

import pytest

from energatron.core.models.catalog import SourceType
from tests.integration._scratch import Scratch, dvc, git, make_scratch, run

pytestmark = pytest.mark.integration

STAGE = """\
stages:
  index:
    cmd: python3 -c "import os, shutil; os.makedirs('out', exist_ok=True); shutil.copy('{catalog}', 'out/copy')"
    deps:
    - {catalog}
    outs:
    - out
"""


def raw_dirs(scratch: Scratch) -> list[str]:
    """One `data/raw/<source>/` per source type, each with an empty catalog and an empty `<YYYY>/` directory."""
    relative = []
    for source in sorted(SourceType):
        directory = scratch.root / "data" / "raw" / source.value
        (directory / "2025").mkdir(parents=True)
        (directory / "_catalog.jsonl").write_bytes(b"")
        relative.append(f"data/raw/{source.value}")
    return relative


def landed_blob(scratch: Scratch) -> Path:
    """A blob as `FsBlobStore` leaves it: mode 0444."""
    blob = scratch.write("data/raw/sapn_interval/2026/synthetic.csv", "synthetic,blob\n")
    blob.chmod(0o444)
    return blob


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_1_a_stage_can_depend_on_a_catalog_inside_a_tracked_raw_directory(tmp_path: Path) -> None:
    scratch = make_scratch(tmp_path, remote=False)
    dirs = raw_dirs(scratch)
    dvc(scratch.root, "add", "--quiet", *dirs)
    catalog = "data/raw/sapn_interval/_catalog.jsonl"
    scratch.write("dvc.yaml", STAGE.format(catalog=catalog))
    scratch.commit_all()
    dvc(scratch.root, "repro", "--quiet")
    assert json.loads(dvc(scratch.root, "status", "--json")) == {}

    with (scratch.root / catalog).open("a", encoding="utf-8") as handle:
        handle.write('{"synthetic": 1}\n')
    status = json.loads(dvc(scratch.root, "status", "--json"))
    assert status["index"] == [{"changed deps": {catalog: "modified"}}]


def test_2_adding_all_ten_unchanged_raw_directories_again_rewrites_no_pointer(tmp_path: Path) -> None:
    """Passes, confirmed by the owner on 2026-10-30: DVC 3.67.1 writes every pointer file again with the same bytes,
    so only its mtime changes. Git sees no change and no commit follows, which is what `land` relies on (D3 §7.3)."""
    scratch = make_scratch(tmp_path, remote=False)
    dirs = raw_dirs(scratch)
    landed_blob(scratch)
    dvc(scratch.root, "add", "--quiet", *dirs)
    scratch.commit_all()
    pointers = sorted(scratch.root.glob("data/raw/*.dvc"))
    assert len(pointers) == len(SourceType) == 10
    contents = {path: path.read_bytes() for path in pointers}
    time.sleep(1)
    dvc(scratch.root, "add", "--quiet", *dirs)
    assert {path: path.read_bytes() for path in pointers} == contents
    assert git(scratch.root, "status", "--porcelain") == ""
    assert git(scratch.root, "diff", "--quiet") == ""


def test_3_the_mode_of_a_landed_blob_after_add_and_after_a_fresh_clone(tmp_path: Path) -> None:
    """Recorded, not required (D3 §14 check 3): `dvc add` keeps 0444; a checkout into a fresh clone gives a
    writable file. So 0444 only guards against an accidental edit at landing; the catalog's `sha256` and DVC's
    hashes stay the integrity record. Empty `<YYYY>/` directories don't come back in a clone either."""
    scratch = make_scratch(tmp_path)
    dirs = raw_dirs(scratch)
    blob = landed_blob(scratch)
    dvc(scratch.root, "add", "--quiet", *dirs)
    assert mode(blob) == 0o444
    scratch.commit_all()
    dvc(scratch.root, "push", "--quiet")

    clone = tmp_path / "clone"
    git(tmp_path, "clone", "--quiet", str(scratch.root), str(clone))
    dvc(clone, "remote", "add", "--local", "-d", "backup", str(scratch.remote_dir))
    dvc(clone, "pull", "--quiet")
    copy = clone / blob.relative_to(scratch.root)
    assert copy.read_bytes() == blob.read_bytes()
    assert mode(copy) & stat.S_IWUSR, "DVC now leaves checked-out blobs read-only: update D3 §14's record"
    assert not (clone / "data" / "raw" / "sapn_interval" / "2025").exists()


def test_4_adding_a_directory_with_read_only_files_and_empty_year_directories_succeeds(tmp_path: Path) -> None:
    scratch = make_scratch(tmp_path, remote=False)
    dirs = raw_dirs(scratch)
    blob = landed_blob(scratch)
    dvc(scratch.root, "add", "--quiet", *dirs)
    assert (scratch.root / "data" / "raw" / "sapn_interval.dvc").is_file()
    assert mode(blob) == 0o444
    assert json.loads(dvc(scratch.root, "status", "--json")) == {}
    assert os.access(scratch.root / "data" / "raw" / "sapn_interval" / "2025", os.F_OK)
    assert run(scratch.root, "git", "check-ignore", "-q", "data/raw/sapn_interval") == ""


PARAM_STAGE = """\
stages:
  checked:
    cmd: python3 -c "import os; os.makedirs('out', exist_ok=True); open('out/done', 'w').write('x')"
    params:
    - validation.timebase
    - meter.local_tz
    outs:
    - out
"""
PARAMS = """\
meter:
  local_tz: Test/Zone
validation:
  timebase:
    windows:
      Alpha: ["08:00-20:00"]
      Beta: ["20:00-08:00"]
    min_fit_periods: 3
"""


def test_5_a_dict_valued_param_key_is_tracked_as_a_whole_subtree(tmp_path: Path) -> None:
    """P2 spec §11: a stage declaring `validation.timebase` goes stale when any key under it changes, and `status`
    names the declared key as one modified value. (`prepare_stage` passing the subtree whole is checked in
    `test_run_stage.py`.)"""
    scratch = make_scratch(tmp_path, remote=False)
    scratch.write("dvc.yaml", PARAM_STAGE)
    scratch.write("params.yaml", PARAMS)
    scratch.commit_all()
    dvc(scratch.root, "repro", "--quiet")
    assert json.loads(dvc(scratch.root, "status", "--json")) == {}

    scratch.write("params.yaml", PARAMS.replace("min_fit_periods: 3", "min_fit_periods: 4"))
    status = json.loads(dvc(scratch.root, "status", "--json"))
    assert status["checked"] == [{"changed deps": {"params.yaml": {"validation.timebase": "modified"}}}]

    scratch.write("params.yaml", PARAMS.replace("08:00-20:00", "07:00-20:00"))
    status = json.loads(dvc(scratch.root, "status", "--json"))
    assert status["checked"] == [{"changed deps": {"params.yaml": {"validation.timebase": "modified"}}}]

    scratch.write("params.yaml", PARAMS.replace("Test/Zone", "Other/Zone"))
    status = json.loads(dvc(scratch.root, "status", "--json"))
    assert status["checked"] == [{"changed deps": {"params.yaml": {"meter.local_tz": "modified"}}}]


DIRECTORY_STAGE = """\
stages:
  parse:
    cmd: python3 -c "import os, pathlib; os.makedirs('out', exist_ok=True);
      n = len(list(pathlib.Path('{raw}').rglob('*'))); pathlib.Path('out/count').write_text(str(n))"
    deps:
    - {raw}
    outs:
    - out
"""


def test_6_a_stage_can_depend_on_a_whole_tracked_raw_directory(tmp_path: Path) -> None:
    """P2 spec §11: `parse_nem12` declares `data/raw/sapn_interval`, the directory that `data/raw/sapn_interval.dvc`
    tracks. DVC accepts it as a dependency, and the stage goes stale when a dataset lands: a new blob (read-only, as
    `FsBlobStore` leaves it, in a new year directory) and a catalog line together. Landing again, by `dvc add`, and
    running the stage brings the pipeline back to a clean state."""
    scratch = make_scratch(tmp_path, remote=False)
    dirs = raw_dirs(scratch)
    landed_blob(scratch)
    raw = "data/raw/sapn_interval"
    dvc(scratch.root, "add", "--quiet", *dirs)
    scratch.write("dvc.yaml", DIRECTORY_STAGE.format(raw=raw))
    scratch.commit_all()
    dvc(scratch.root, "repro", "--quiet")
    assert json.loads(dvc(scratch.root, "status", "--json")) == {}

    new_blob = scratch.write(f"{raw}/2027/synthetic_second.csv", "synthetic,second\n")
    new_blob.chmod(0o444)
    with (scratch.root / raw / "_catalog.jsonl").open("a", encoding="utf-8") as handle:
        handle.write('{"synthetic": 2}\n')
    status = json.loads(dvc(scratch.root, "status", "--json"))
    assert status["parse"] == [{"changed deps": {raw: "modified"}}]

    dvc(scratch.root, "add", "--quiet", *dirs)  # `land` adds every raw directory again
    assert json.loads(dvc(scratch.root, "status", "--json"))["parse"] == [{"changed deps": {raw: "modified"}}]
    dvc(scratch.root, "repro", "--quiet")
    assert json.loads(dvc(scratch.root, "status", "--json")) == {}
    assert (scratch.root / "out" / "count").read_text() == "6"  # the stage ran again: 3 directories, 2 blobs, 1 catalog


def test_7_a_dataset_that_lands_in_another_source_leaves_the_stage_up_to_date(tmp_path: Path) -> None:
    scratch = make_scratch(tmp_path, remote=False)
    dirs = raw_dirs(scratch)
    dvc(scratch.root, "add", "--quiet", *dirs)
    raw = "data/raw/sapn_interval"
    scratch.write("dvc.yaml", DIRECTORY_STAGE.format(raw=raw))
    scratch.commit_all()
    dvc(scratch.root, "repro", "--quiet")
    scratch.write("data/raw/sapn_summary/2026/synthetic.csv", "synthetic,other\n")
    with (scratch.root / "data/raw/sapn_summary/_catalog.jsonl").open("a", encoding="utf-8") as handle:
        handle.write('{"synthetic": 3}\n')
    status = json.loads(dvc(scratch.root, "status", "--json"))
    assert "parse" not in status
