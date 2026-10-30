"""The code fingerprint: file hashing, symlinks, the install fields, and `energatron fingerprint [--check]`
(D1 §11, §13).

The hashing tests use a synthetic package tree. The CLI tests use the real package's fingerprint, computed once and
handed to the command through `wiring.code_fingerprint`, so a file edited while the tests run can't change it.
"""

import json
import os
from collections.abc import Sequence
from pathlib import Path

import pytest

from energatron.adapters import wiring
from energatron.adapters.pipeline.fingerprint import install_fields, stage_code
from energatron.core.canonical import content_hash, sha256_hex, tree_hash
from energatron.core.errors import InternalError
from energatron.core.models.provenance import CodeFingerprint, StageCode
from energatron.services.stages.dvc_yaml import CODE_FINGERPRINT_PATH
from energatron.services.stages.registry import STAGES
from tests.unit.adapters._cli import FakeVcsState, run_cli
from tests.unit.adapters._workspace import make_workspace
from tests.unit.adapters.test_closure import NO_DISTRIBUTIONS, write

SHA = "0123456789abcdef0123456789abcdef01234567"


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    root = tmp_path / "pkg"
    write(root, "__init__.py", "")
    write(root, "sub/__init__.py", "")
    write(root, "sub/stage.py", "from pkg.sub import helper\n")
    write(root, "sub/helper.py", "x = 1\n")
    write(root, "data/a.yaml", "a: 1\n")
    write(root, "data/more/b.yaml", "b: 2\n")
    return root


def code(root: Path, package_data: Sequence[str] = ("data",)) -> StageCode:
    return stage_code("pkg.sub.stage", package_data, package_dir=root, distributions=NO_DISTRIBUTIONS)


def test_a_fixed_tree_has_a_stable_hash(tree: Path) -> None:
    first, second = code(tree), code(tree)
    assert first == second
    assert first.files == [
        "__init__.py",
        "data/a.yaml",
        "data/more/b.yaml",
        "sub/__init__.py",
        "sub/helper.py",
        "sub/stage.py",
    ]
    expected = tree_hash((path, sha256_hex((tree / path).read_bytes())) for path in first.files)
    assert first.source_sha256 == expected


def test_pycache_and_finder_files_are_ignored(tree: Path) -> None:
    before = code(tree)
    write(tree, "__pycache__/x.cpython-314.pyc", "junk")
    write(tree, "data/__pycache__/y.pyc", "junk")
    write(tree, "data/z.pyc", "junk")
    write(tree, "data/z.pyo", "junk")
    write(tree, "data/.DS_Store", "junk")
    assert code(tree) == before


def test_missing_package_data_is_an_internal_error(tree: Path) -> None:
    with pytest.raises(InternalError):
        code(tree, ("nothing/here.yaml",))


def test_a_symlinked_module_file_is_an_internal_error(tree: Path) -> None:
    target = write(tree.parent, "elsewhere/helper.py", "x = 1\n")
    (tree / "sub/helper.py").unlink()
    (tree / "sub/helper.py").symlink_to(target)
    with pytest.raises(InternalError):
        code(tree)


def test_a_symlink_under_declared_package_data_is_an_internal_error(tree: Path) -> None:
    target = write(tree.parent, "elsewhere/c.yaml", "c: 3\n")
    (tree / "data/more/c.yaml").symlink_to(target)
    with pytest.raises(InternalError):
        code(tree)


def test_a_symlinked_directory_under_package_data_is_an_internal_error(tree: Path) -> None:
    target = write(tree.parent, "elsewhere/dir/d.yaml", "d: 4\n").parent
    (tree / "data/linked").symlink_to(target, target_is_directory=True)
    with pytest.raises(InternalError):
        code(tree)


def test_a_symlinked_directory_higher_up_is_an_internal_error(tree: Path) -> None:
    real = tree.parent / "real_sub"
    (tree / "sub").rename(real)
    (tree / "sub").symlink_to(real, target_is_directory=True)
    with pytest.raises(InternalError):
        code(tree)


def test_a_symlinked_package_data_file_named_directly_is_an_internal_error(tree: Path) -> None:
    target = write(tree.parent, "elsewhere/e.yaml", "e: 5\n")
    (tree / "data/e.yaml").symlink_to(target)
    with pytest.raises(InternalError):
        code(tree, ("data/e.yaml",))


# The install fields (D1 §11.2's table)


class FakeGit:
    def __init__(self, status: str = "") -> None:
        self.status = status
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, *args: str) -> str:
        self.calls.append(args)
        if args[0] == "rev-parse":
            return SHA + "\n"
        assert args[0] == "status"
        return self.status


@pytest.mark.parametrize(
    ("status", "dirty"),
    [("", False), (" M src/energatron/core/ids.py\n", True)],
)
def test_an_editable_install_records_the_commit_and_whether_the_package_is_dirty(status: str, dirty: bool) -> None:
    git = FakeGit(status)
    doc = json.dumps({"url": "file:///x", "dir_info": {"editable": True}})
    assert install_fields(doc, git=git) == ("editable", SHA, dirty)
    assert git.calls == [("rev-parse", "HEAD"), ("status", "--porcelain", "--", ".")]


@pytest.mark.parametrize(
    ("doc", "expected"),
    [
        ({"url": "file:///x", "dir_info": {}}, ("local_dir", None, False)),
        ({"url": "file:///x", "dir_info": {"editable": False}}, ("local_dir", None, False)),
        ({"url": "https://x", "vcs_info": {"vcs": "git", "commit_id": SHA}}, ("vcs", SHA, False)),
        ({"url": "https://x/e.whl", "archive_info": {}}, ("archive", None, False)),
    ],
)
def test_the_other_direct_url_cases_map_as_the_table_says(
    doc: dict[str, object], expected: tuple[str, str | None, bool]
) -> None:
    def no_git(*args: str) -> str:
        raise AssertionError("only an editable install runs git")

    assert install_fields(json.dumps(doc), git=no_git) == expected


def test_no_direct_url_file_means_an_index_install() -> None:
    assert install_fields(None, git=FakeGit()) == ("index", None, False)


@pytest.mark.parametrize(
    "text",
    [
        "{not json",
        "[]",
        json.dumps({"url": "x"}),
        json.dumps({"url": "x", "dir_info": {}, "archive_info": {}}),
        json.dumps({"url": "x", "vcs_info": {"vcs": "git"}}),
        json.dumps({"url": "x", "vcs_info": {"vcs": "git", "commit_id": "not-a-sha"}}),
    ],
)
def test_anything_else_is_an_internal_error(text: str) -> None:
    with pytest.raises(InternalError):
        install_fields(text, git=FakeGit())


# energatron fingerprint [--check]


@pytest.fixture(scope="module")
def live() -> CodeFingerprint:
    return wiring.code_fingerprint()


@pytest.fixture
def workspace(tmp_path: Path, live: CodeFingerprint, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(wiring, "code_fingerprint", lambda: live)
    return make_workspace(tmp_path).root


def recorded_path(root: Path) -> Path:
    return root / CODE_FINGERPRINT_PATH


def record(root: Path, fingerprint: CodeFingerprint) -> None:
    path = recorded_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(fingerprint.model_dump(mode="json"), sort_keys=True, indent=2) + "\n")


def test_the_live_fingerprint_has_an_entry_per_registered_stage(live: CodeFingerprint) -> None:
    assert list(live.stages) == list(STAGES)
    for spec in STAGES.values():
        files = live.stages[spec.name].files
        assert f"{spec.impl.__module__.removeprefix('energatron.').replace('.', '/')}.py" in files
        assert "services/stages/registry.py" not in files


def test_fingerprint_writes_the_file_sorted_and_only_when_it_changed(
    capsys: pytest.CaptureFixture[str], workspace: Path, fake_vcs: FakeVcsState, live: CodeFingerprint
) -> None:
    ran = run_cli(capsys, "fingerprint", "--json", "--workspace", str(workspace))
    assert ran.code == 0, ran.out
    data = ran.envelope()["data"]
    assert data["kind"] == "fingerprint_result"
    assert data["written"] is True
    assert data["changed_stages"] == sorted(STAGES)
    text = recorded_path(workspace).read_text(encoding="utf-8")
    assert text == json.dumps(live.model_dump(mode="json"), sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    assert CodeFingerprint.from_document(json.loads(text)) == live

    stamp = os.stat(recorded_path(workspace)).st_mtime_ns
    again = run_cli(capsys, "fingerprint", "--json", "--workspace", str(workspace)).envelope()["data"]
    assert again["written"] is False
    assert again["changed_stages"] == []
    assert os.stat(recorded_path(workspace)).st_mtime_ns == stamp


def test_fingerprint_dry_run_writes_nothing(
    capsys: pytest.CaptureFixture[str], workspace: Path, fake_vcs: FakeVcsState
) -> None:
    ran = run_cli(capsys, "fingerprint", "--dry-run", "--json", "--workspace", str(workspace))
    assert ran.code == 0
    assert ran.envelope()["data"]["written"] is False
    assert not recorded_path(workspace).exists()


def test_check_passes_on_a_current_file(
    capsys: pytest.CaptureFixture[str], workspace: Path, fake_vcs: FakeVcsState, live: CodeFingerprint
) -> None:
    record(workspace, live)
    ran = run_cli(capsys, "fingerprint", "--check", "--json", "--workspace", str(workspace))
    assert ran.code == 0
    envelope = ran.envelope()
    assert envelope["warnings"] == []
    assert envelope["data"]["written"] is False


def test_check_fails_on_a_stale_stage_entry_and_writes_nothing(
    capsys: pytest.CaptureFixture[str], workspace: Path, fake_vcs: FakeVcsState, live: CodeFingerprint
) -> None:
    name = next(iter(live.stages))
    entry = live.stages[name]
    packages = {**entry.packages, "made-up": "0"}
    stale = entry.model_copy(
        update={
            "packages": packages,
            "stage_sha256": content_hash(
                {"source_sha256": entry.source_sha256, "packages": packages, "python": entry.python}
            ),
        }
    )
    record(workspace, live.model_copy(update={"stages": {**live.stages, name: stale}}))
    before = recorded_path(workspace).read_bytes()
    ran = run_cli(capsys, "fingerprint", "--check", "--json", "--workspace", str(workspace))
    assert ran.code == 4
    error = ran.envelope()["error"]
    assert error["code"] == "E_FINGERPRINT_STALE"
    assert error["details"]["stages"] == [name]
    assert recorded_path(workspace).read_bytes() == before


def test_check_fails_on_a_missing_or_extra_stage(
    capsys: pytest.CaptureFixture[str], workspace: Path, fake_vcs: FakeVcsState, live: CodeFingerprint
) -> None:
    name = next(iter(live.stages))
    record(workspace, live.model_copy(update={"stages": {"zz_gone": live.stages[name]}}))
    ran = run_cli(capsys, "fingerprint", "--check", "--json", "--workspace", str(workspace))
    assert ran.code == 4
    assert ran.envelope()["error"]["details"]["stages"] == sorted([*live.stages, "zz_gone"])


def test_check_fails_when_the_file_is_missing(
    capsys: pytest.CaptureFixture[str], workspace: Path, fake_vcs: FakeVcsState, live: CodeFingerprint
) -> None:
    ran = run_cli(capsys, "fingerprint", "--check", "--json", "--workspace", str(workspace))
    assert ran.code == 4
    assert ran.envelope()["error"]["details"]["stages"] == sorted(live.stages)


def test_check_only_warns_on_a_changed_tool_commit(
    capsys: pytest.CaptureFixture[str], workspace: Path, fake_vcs: FakeVcsState, live: CodeFingerprint
) -> None:
    other = "f" * 40 if live.tool_commit != "f" * 40 else "e" * 40
    record(workspace, live.model_copy(update={"install": "editable", "tool_commit": other}))
    ran = run_cli(capsys, "fingerprint", "--check", "--json", "--workspace", str(workspace))
    assert ran.code == 0
    envelope = ran.envelope()
    assert [warning["code"] for warning in envelope["warnings"]] == ["W_FINGERPRINT_METADATA"]
    assert "tool_commit" in envelope["warnings"][0]["details"]["fields"]


def test_an_invalid_recorded_file_is_invalid_input(
    capsys: pytest.CaptureFixture[str], workspace: Path, fake_vcs: FakeVcsState
) -> None:
    write(workspace, CODE_FINGERPRINT_PATH, "{not json")
    ran = run_cli(capsys, "fingerprint", "--check", "--json", "--workspace", str(workspace))
    assert ran.code == 3
    assert ran.envelope()["error"]["code"] == "E_INVALID_INPUT"


CONFLICTED = "<<<<<<< HEAD\n{}\n=======\n{}\n>>>>>>> other\n"


@pytest.mark.parametrize("text", [CONFLICTED, "[]\n", '{"kind": "code_fingerprint", "schema_version": 1}\n'])
def test_fingerprint_overwrites_an_invalid_recorded_file(
    capsys: pytest.CaptureFixture[str], workspace: Path, fake_vcs: FakeVcsState, live: CodeFingerprint, text: str
) -> None:
    write(workspace, CODE_FINGERPRINT_PATH, text)
    ran = run_cli(capsys, "fingerprint", "--json", "--workspace", str(workspace))
    assert ran.code == 0, ran.out
    data = ran.envelope()["data"]
    assert data["written"] is True
    assert data["changed_stages"] == sorted(live.stages)
    assert data["changed_fields"] == []
    recorded = CodeFingerprint.from_document(json.loads(recorded_path(workspace).read_text(encoding="utf-8")))
    assert recorded == live


def test_fingerprint_dry_run_reports_it_would_replace_an_invalid_file(
    capsys: pytest.CaptureFixture[str], workspace: Path, fake_vcs: FakeVcsState, live: CodeFingerprint
) -> None:
    write(workspace, CODE_FINGERPRINT_PATH, CONFLICTED)
    ran = run_cli(capsys, "fingerprint", "--dry-run", "--json", "--workspace", str(workspace))
    assert ran.code == 0, ran.out
    data = ran.envelope()["data"]
    assert data["written"] is False
    assert data["changed_stages"] == sorted(live.stages)
    assert recorded_path(workspace).read_text(encoding="utf-8") == CONFLICTED


def test_human_output_names_the_result(
    capsys: pytest.CaptureFixture[str], workspace: Path, fake_vcs: FakeVcsState
) -> None:
    ran = run_cli(capsys, "fingerprint", "--workspace", str(workspace))
    assert ran.code == 0
    assert ran.out.startswith("Code fingerprint written")
