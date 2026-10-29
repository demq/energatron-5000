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
