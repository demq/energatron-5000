"""`scripts/privacy_guard.py` (D2 §7; P0.2.5's `denylist.txt` rules). Synthetic values only.

Each test builds a scratch git repo and a scratch denylist directory, written from a synthetic `PrivateConfig` by the
package's own `derive_denylist` and `render_generated`, so the guard and the package can't drift apart. The guard
runs as a subprocess, as the hooks run it, and every run's stdout and stderr is checked for denylist values (§7.6
case 15). The numbered comments are §7.6's cases.
"""

import ast
import importlib.util
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from energatron.core.canonical import JSONValue, sha256_hex
from energatron.core.errors import InvalidInputError
from energatron.core.models.private import PrivateConfig
from energatron.core.privacy import DenyEntry, derive_denylist, redact
from energatron.services.denylist import parse_manual_denylist, render_generated
from tests.unit.adapters._workspace import PRIVATE_TOML
from tests.unit.core._private import NMI, NMI_WITH_CHECKSUM, synthetic_document

pytestmark = pytest.mark.integration

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "privacy_guard.py"
MIB = 1024 * 1024
ALL_LABELS = ("account", "meter_serial", "mirn", "mirn_base", "nmi", "nmi_checksum", "postcode", "street")


def load_guard() -> ModuleType:
    spec = importlib.util.spec_from_file_location("privacy_guard", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["privacy_guard"] = module  # dataclasses look their module up here
    spec.loader.exec_module(module)
    return module


guard_module = load_guard()


# Scratch repos and denylists


def entries_of(document: dict[str, JSONValue]) -> tuple[DenyEntry, ...]:
    return derive_denylist(PrivateConfig.from_document(document))


DENYLIST = entries_of(synthetic_document())
VALUES = {entry.label: entry.value for entry in DENYLIST}
SERIAL = next(entry.value for entry in DENYLIST if entry.label == "meter_serial" and entry.match == "substring")
ACCOUNT = next(entry.value for entry in DENYLIST if entry.label == "account" and entry.match == "substring")
STREET = VALUES["street"]


@dataclass
class Ran:
    code: int
    out: str
    err: str

    @property
    def lines(self) -> list[str]:
        return self.err.splitlines()


@dataclass
class Repo:
    root: Path
    denylist_dir: Path
    entries: tuple[DenyEntry, ...]

    def git(self, *args: str) -> str:
        return git(self.root, *args)

    def write(self, relative: str, content: str | bytes = "synthetic\n") -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")
        return path

    def stage(self, relative: str, content: str | bytes = "synthetic\n") -> None:
        self.write(relative, content)
        self.git("add", "--", relative)

    def commit(self, message: str = "scratch") -> str:
        self.git("commit", "-q", "--allow-empty", "-m", message)
        return self.git("rev-parse", "HEAD").strip()

    def guard(self, *args: str, cwd: Path | None = None) -> Ran:
        return run_guard(cwd or self.root, *args, entries=self.entries)


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout


def run_guard(cwd: Path, *args: str, entries: tuple[DenyEntry, ...] = DENYLIST) -> Ran:
    """Run the guard; fail the test if its output holds any denylist value (§7.6 case 15)."""
    done = subprocess.run([sys.executable, str(SCRIPT), *args], cwd=cwd, capture_output=True, text=True)
    ran = Ran(done.returncode, done.stdout, done.stderr)
    assert_no_values(ran, entries)
    return ran


def assert_no_values(ran: Ran, entries: tuple[DenyEntry, ...]) -> None:
    for text in (ran.out, ran.err):
        assert redact(text, entries) == text, "the guard's output holds a denylist value"


def write_denylist(
    directory: Path,
    document: dict[str, JSONValue] | None = None,
    *,
    manual: str | bytes | None = None,
    config: bytes | None = PRIVATE_TOML.encode(),
) -> tuple[DenyEntry, ...]:
    """`config.toml`, `denylist.generated.txt` from `derive_denylist`, and `denylist.txt` when given."""
    directory.mkdir(parents=True, exist_ok=True)
    entries = entries_of(document or synthetic_document())
    source = config if config is not None else b"no config"
    if config is not None:
        (directory / "config.toml").write_bytes(config)
    (directory / "denylist.generated.txt").write_text(render_generated(entries, sha256_hex(source)), encoding="utf-8")
    manual_entries: tuple[DenyEntry, ...] = ()
    if manual is not None:
        data = manual if isinstance(manual, bytes) else manual.encode()
        (directory / "denylist.txt").write_bytes(data)
        try:
            manual_entries = parse_manual_denylist(data.decode("utf-8"))
        except InvalidInputError, UnicodeDecodeError:
            manual_entries = ()
    return entries + manual_entries


def make_repo(tmp_path: Path, document: dict[str, JSONValue] | None = None, *, manual: str | None = None) -> Repo:
    """A scratch tool repo whose `energatron.denylist` points at a sibling denylist directory."""
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "energatron.denylist", "../private")
    entries = write_denylist(tmp_path / "private", document, manual=manual)
    return Repo(root, tmp_path / "private", entries)


def make_workspace_repo(tmp_path: Path) -> Repo:
    """A scratch workspace repo with its denylist in `private/`, never staged."""
    root = tmp_path / "ws"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    entries = write_denylist(root / "private")
    return Repo(root, root / "private", entries)


@pytest.fixture
def repo(tmp_path: Path) -> Repo:
    return make_repo(tmp_path)


def hit_labels(ran: Ran) -> set[str]:
    return {line.rsplit("(", 1)[1].rstrip(")") for line in ran.lines if "denylist hit" in line}


# Case 1: each derived label blocks a staged file containing it


@pytest.mark.parametrize("label", ALL_LABELS)
def test_each_derived_label_blocks_a_staged_file(repo: Repo, label: str) -> None:
    value = next(entry.value for entry in DENYLIST if entry.label == label)
    repo.stage("notes/plain.txt", f"line one\nsee {value} here\n")
    ran = repo.guard("--mode", "tool", "--staged")
    assert ran.code == 1
    assert label in hit_labels(ran)
    assert f"privacy_guard: notes/plain.txt:2: denylist hit ({label})" in ran.lines


@pytest.mark.parametrize("text", [NMI, NMI.lower(), NMI_WITH_CHECKSUM, NMI_WITH_CHECKSUM.lower()])
def test_the_nmi_is_blocked_with_and_without_its_checksum_in_any_case(repo: Repo, text: str) -> None:
    repo.stage("a.txt", f"meter {text}\n")
    ran = repo.guard("--mode", "tool", "--staged")
    assert ran.code == 1
    expected = {"nmi", "nmi_checksum"} if len(text) == 11 else {"nmi"}
    assert hit_labels(ran) == expected


def test_a_clean_staged_file_passes_and_the_summary_is_last(repo: Repo) -> None:
    repo.stage("src/x.py", "print('synthetic')\n")
    ran = repo.guard("--mode", "tool", "--staged")
    assert ran.code == 0, ran.err
    assert ran.lines[-1].startswith("privacy_guard: ")
    assert "0 violations" in ran.lines[-1]


def test_manual_entries_block_with_their_line_label(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, manual="# a comment\n\nsecret#value\nword:zq9\n")
    repo.stage("a.txt", "x secret#VALUE y\nzq9 alone\n")
    ran = repo.guard("--mode", "tool", "--staged")
    assert ran.code == 1
    assert {"manual:3", "manual:4"} <= hit_labels(ran)


# Case 2: a hit only in a path is blocked, and the printed path is redacted


def test_a_hit_only_in_a_path_is_blocked_and_redacted(repo: Repo) -> None:
    repo.stage(f"notes/{NMI_WITH_CHECKSUM}_meter.md", "nothing personal\n")
    ran = repo.guard("--mode", "tool", "--staged")
    assert ran.code == 1
    assert "privacy_guard: notes/<redacted>_meter.md: denylist hit in path (nmi)" in ran.lines
    assert "privacy_guard: notes/<redacted>_meter.md: denylist hit in path (nmi_checksum)" in ran.lines


# Case 3: the commit message


def test_commit_msg_blocks_the_meter_serial(repo: Repo) -> None:
    message = repo.write(".git/COMMIT_EDITMSG", f"Fix the reader\n\nfor meter {SERIAL.lower()}\n")
    ran = repo.guard("--mode", "tool", "--commit-msg", str(message))
    assert ran.code == 1
    assert "privacy_guard: commit message:3: denylist hit (meter_serial)" in ran.lines


def test_commit_msg_passes_a_clean_message_and_checks_nothing_else(repo: Repo) -> None:
    repo.stage("x.csv", f"{NMI}\n")  # staged content is the pre-commit hook's job
    message = repo.write(".git/COMMIT_EDITMSG", "Add the reader\n")
    assert repo.guard("--mode", "tool", "--commit-msg", str(message)).code == 0


# Case 4: a partially staged file is checked as it will be committed


def test_a_hit_only_in_the_unstaged_part_passes(repo: Repo) -> None:
    repo.stage("a.txt", "clean\n")
    repo.write("a.txt", f"clean\n{NMI}\n")
    assert repo.guard("--mode", "tool", "--staged").code == 0


def test_a_hit_only_in_the_staged_part_fails(repo: Repo) -> None:
    repo.stage("a.txt", f"clean\n{NMI}\n")
    repo.write("a.txt", "clean\n")
    assert repo.guard("--mode", "tool", "--staged").code == 1


# Case 5: the postcode is a `word` entry


@pytest.fixture
def postcode_repo(tmp_path: Path) -> Repo:
    document = synthetic_document()
    premises = document["premises"]
    assert isinstance(premises, dict)
    premises["postcode"] = "5000"  # the number in the repo's name
    return make_repo(tmp_path, document)


def test_a_postcode_standing_alone_fails(postcode_repo: Repo) -> None:
    postcode_repo.stage("a.txt", "Adelaide SA 5000\n")
    ran = postcode_repo.guard("--mode", "tool", "--staged")
    assert ran.code == 1
    assert hit_labels(ran) == {"postcode"}


@pytest.mark.parametrize("text", ["see energatron-5000/TASKS.md\n", "a run of 150001 digits\n", "id 5000x\n"])
def test_the_repo_name_and_longer_digit_runs_pass(postcode_repo: Repo, text: str) -> None:
    postcode_repo.stage("a.txt", text)
    assert postcode_repo.guard("--mode", "tool", "--staged").code == 0


# Case 6: whitespace and case don't hide a street


@pytest.mark.parametrize("separator", [" ", "   ", "\t", "\n", " \r\n "])
def test_a_street_with_other_spacing_and_case_is_blocked(repo: Repo, separator: str) -> None:
    text = separator.join(part.upper() for part in STREET.split())
    repo.stage("a.txt", f"lives at {text}.\n")
    ran = repo.guard("--mode", "tool", "--staged")
    assert ran.code == 1
    assert hit_labels(ran) == {"street"}


# Case 7: setup errors


def test_a_stale_denylist_is_a_setup_error(repo: Repo) -> None:
    (repo.denylist_dir / "config.toml").write_bytes(PRIVATE_TOML.encode() + b"# edited\n")
    repo.stage("a.txt")
    ran = repo.guard("--mode", "tool", "--staged")
    assert ran.code == 2
    assert "stale" in ran.err
    assert "uv run energatron denylist" in ran.err


def test_a_missing_generated_file_is_a_setup_error(repo: Repo) -> None:
    (repo.denylist_dir / "denylist.generated.txt").unlink()
    repo.stage("a.txt")
    assert repo.guard("--mode", "tool", "--staged").code == 2


def test_without_config_toml_the_generated_file_is_used_as_it_is(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    (repo.denylist_dir / "config.toml").unlink()
    repo.stage("a.txt", f"{NMI}\n")
    assert repo.guard("--mode", "tool", "--staged").code == 1


def test_tool_mode_with_the_setting_unset_is_a_setup_error(repo: Repo) -> None:
    repo.git("config", "--unset", "energatron.denylist")
    repo.stage("a.txt")
    ran = repo.guard("--mode", "tool", "--staged")
    assert ran.code == 2
    assert "git config energatron.denylist" in ran.err
    assert "none" in ran.err


def test_tool_mode_with_none_runs_without_a_denylist(repo: Repo) -> None:
    repo.git("config", "energatron.denylist", "none")
    repo.stage("a.txt", f"{NMI}\n")
    assert repo.guard("--mode", "tool", "--staged").code == 0
    repo.stage("b.csv", "x\n")
    assert repo.guard("--mode", "tool", "--staged").code == 1  # the path rules still run


def test_the_setting_is_relative_to_the_repo_root(repo: Repo) -> None:
    repo.stage("a.txt", f"{NMI}\n")
    sub = repo.root / "src"
    sub.mkdir()
    assert repo.guard("--mode", "tool", "--staged", cwd=sub).code == 1


def test_denylist_dir_overrides_the_setting(repo: Repo, tmp_path: Path) -> None:
    repo.git("config", "--unset", "energatron.denylist")
    repo.stage("a.txt", f"{NMI}\n")
    assert repo.guard("--mode", "tool", "--staged", "--denylist-dir", str(repo.denylist_dir)).code == 1
    assert repo.guard("--mode", "tool", "--staged", "--no-denylist").code == 0


# Case 8 and P0.2.5's `denylist.txt` rules


def test_a_short_substring_entry_is_a_setup_error_named_by_its_line(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, manual="# entries\nabcd\n")
    repo.stage("a.txt")
    ran = repo.guard("--mode", "tool", "--staged")
    assert ran.code == 2
    assert "line 2" in ran.err
    assert "abcd" not in ran.err


def test_the_same_entry_with_word_is_accepted(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, manual="word:abcd\n")
    repo.stage("a.txt", "xabcdy and abcd-x\n")
    assert repo.guard("--mode", "tool", "--staged").code == 0
    repo.stage("b.txt", "the ABCD here\n")
    ran = repo.guard("--mode", "tool", "--staged")
    assert ran.code == 1
    assert hit_labels(ran) == {"manual:1"}


@pytest.mark.parametrize(
    ("manual", "line"),
    [
        ("word:\n", 1),
        ("ok entry\nword:   \n", 2),
        ("ab  c\n", 1),  # 4 characters once whitespace is normalised
        ("one\x0ctwo\u2028x\rabc\n", None),  # one line: a form feed, U+2028 and a lone \r stay inside it
    ],
)
def test_manual_lines_refused_or_kept(tmp_path: Path, manual: str, line: int | None) -> None:
    repo = make_repo(tmp_path, manual=manual)
    repo.stage("a.txt")
    ran = repo.guard("--mode", "tool", "--staged")
    if line is None:
        assert ran.code == 0, ran.err
    else:
        assert ran.code == 2
        assert f"line {line}" in ran.err


def test_a_manual_file_that_is_not_utf8_is_a_setup_error(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    (repo.denylist_dir / "denylist.txt").write_bytes(b"caf\xe9 entry\n")
    repo.stage("a.txt")
    assert repo.guard("--mode", "tool", "--staged").code == 2


def test_manual_line_numbers_count_only_newlines(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, manual="first\x0centry\r\n\u2028second entry\n")
    repo.stage("a.txt", "the SECOND ENTRY\n")
    ran = repo.guard("--mode", "tool", "--staged")
    assert ran.code == 1
    assert hit_labels(ran) == {"manual:2"}


manual_text = st.text(alphabet=st.sampled_from(list("ab#w:ord \t\r\n\x0c\x85\u2028\u00a0")), max_size=40)


@settings(max_examples=300, deadline=None)
@given(manual_text)
def test_manual_parsing_matches_the_package(text: str) -> None:
    try:
        expected: object = [(entry.label, entry.value, entry.match) for entry in parse_manual_denylist(text)]
    except InvalidInputError as error:
        expected = ("error", error.details["line"])
    try:
        actual: object = [(entry.label, entry.value, entry.match) for entry in guard_module.parse_manual(text)]
    except guard_module.SetupError as error:
        actual = ("error", error.line)
    assert actual == expected


def test_the_generated_file_round_trips() -> None:
    text = render_generated(DENYLIST, "ab" * 32)
    source, entries = guard_module.parse_generated(text)
    assert source == "ab" * 32
    assert [(entry.label, entry.value, entry.match) for entry in entries] == [
        (entry.label, entry.value, entry.match) for entry in DENYLIST
    ]
    assert text.split("\n", 1)[0] == guard_module.GENERATED_HEADER


match_text = st.text(alphabet=st.sampled_from(list("aAb_- \t\n9xé")), max_size=24)
entry_values = st.text(alphabet=st.sampled_from(list("aAb_- 9é")), min_size=1, max_size=6).filter(
    lambda value: bool(value.split())
)


@settings(max_examples=300, deadline=None)
@given(entry_values, st.sampled_from(["substring", "word"]), match_text)
def test_matching_and_redaction_match_the_package(value: str, match: str, text: str) -> None:
    entry = DenyEntry("manual:1", value, match)  # type: ignore[arg-type]
    matcher = guard_module.Matcher([guard_module.Entry("manual:1", value, match)])
    assert matcher.redact(text) == redact(text, [entry])
    assert bool(matcher.hits(text)) == (redact(text, [entry]) != text)


# Case 9: data file types


@pytest.mark.parametrize(
    ("path", "code"),
    [
        ("x.csv", 1),
        ("notes/report.PDF", 1),
        ("src/energatron/x.parquet", 1),
        ("tests/fixtures/x.csv", 0),
        ("tests/fixtures/schemas/thing/v1.json", 0),
        (".claude/settings.json", 0),
        ("config/x.json", 1),  # allowed in the workspace only
        ("src/x.py", 0),
    ],
)
def test_data_file_types_in_the_tool_repo(repo: Repo, path: str, code: int) -> None:
    repo.stage(path, "{}\n")
    ran = repo.guard("--mode", "tool", "--staged")
    assert ran.code == code, ran.err
    if code:
        assert "data file type" in ran.err


# Owner decision 2026-10-29 (P0.2.5 review finding 5, a D2 §7.4 deviation): compressed, archive and office formats,
# whose content the denylist scan can't see into, are data file types too.
CONTAINER_SUFFIXES = (".gz", ".bz2", ".xz", ".zst", ".zip", ".7z", ".tar", ".tgz", ".docx", ".xls", ".xlsm", ".ods")
CONTAINER_SUFFIXES += (".odt",)


@pytest.mark.parametrize("suffix", CONTAINER_SUFFIXES)
def test_container_types_are_data_file_types(tmp_path: Path, suffix: str) -> None:
    repo = make_repo(tmp_path)
    for path in (f"notes/export{suffix}", f"notes/export.txt{suffix.upper()}"):
        repo.stage(path, b"\x1f\x8b synthetic container")
        ran = repo.guard("--mode", "tool", "--staged")
        assert ran.code == 1, path
        assert any(line.startswith(f"privacy_guard: {path}: data file type") for line in ran.lines)
        repo.git("rm", "-q", "--cached", "--", path)
    repo.stage(f"tests/fixtures/export{suffix}", b"synthetic")
    assert repo.guard("--mode", "tool", "--staged").code == 0


@pytest.mark.parametrize("suffix", CONTAINER_SUFFIXES)
def test_container_types_in_the_workspace(tmp_path: Path, suffix: str) -> None:
    repo = make_workspace_repo(tmp_path)
    repo.stage(f"config/x{suffix}", b"synthetic")
    assert repo.guard("--mode", "workspace", "--staged").code == 0
    repo.stage(f"notes/x{suffix}", b"synthetic")
    assert repo.guard("--mode", "workspace", "--staged").code == 1


@pytest.mark.parametrize(
    ("path", "code"),
    [("config/x.json", 0), (".energatron/code_fingerprint.json", 0), (".claude/settings.json", 0), ("x.csv", 1)],
)
def test_data_file_types_in_the_workspace(tmp_path: Path, path: str, code: int) -> None:
    repo = make_workspace_repo(tmp_path)
    repo.stage(path, "{}\n")
    assert repo.guard("--mode", "workspace", "--staged").code == code


# Case 10: tool-mode path rules


@pytest.mark.parametrize(
    ("path", "code"),
    [
        ("x.dvc", 1),
        ("dvc.lock", 1),
        (".dvc/config", 1),
        ("dvc.yaml", 1),
        ("tests/fixtures/golden/dvc.yaml", 0),
        ("analysis.ipynb", 1),
        ("data/x.txt", 1),
        ("private/x.txt", 1),
        ("legacy/x.txt", 1),
        ("src/data/x.txt", 0),
    ],
)
def test_tool_mode_path_rules(repo: Repo, path: str, code: int) -> None:
    repo.stage(path, "x: 1\n")
    assert repo.guard("--mode", "tool", "--staged").code == code


# Case 11: workspace-mode path rules


@pytest.mark.parametrize(
    ("path", "code"),
    [
        ("data/raw/x.csv", 1),
        ("data/raw/notes.txt", 1),
        ("data/raw/sapn_interval.dvc", 0),
        ("data/raw/.gitignore", 0),
        ("private.dvc", 0),
        ("private/config.toml", 1),
        (".energatron/local.toml", 1),
        (".energatron/lock", 1),
        (".energatron/tmp/a/b.txt", 1),
        (".energatron/workspace.toml", 0),
        ("legacy/x", 1),
        (".venv/x", 1),
        ("dvc.yaml", 0),
    ],
)
def test_workspace_mode_path_rules(tmp_path: Path, path: str, code: int) -> None:
    repo = make_workspace_repo(tmp_path)
    repo.stage(path, "x: 1\n")
    assert repo.guard("--mode", "workspace", "--staged", "--no-denylist").code == code


# Case 12: notebook outputs (workspace)


def notebook(*, outputs: list[JSONValue], count: int | None) -> str:
    cell: dict[str, JSONValue] = {"cell_type": "code", "source": [], "outputs": outputs, "execution_count": count}
    return json.dumps({"cells": [{"cell_type": "markdown", "source": []}, cell], "nbformat": 4})


@pytest.mark.parametrize(
    ("content", "code"),
    [
        (notebook(outputs=[{"output_type": "stream", "text": ["x"]}], count=None), 1),
        (notebook(outputs=[], count=3), 1),
        (notebook(outputs=[], count=None), 0),
        ("not json", 1),
    ],
)
def test_notebook_outputs(tmp_path: Path, content: str, code: int) -> None:
    repo = make_workspace_repo(tmp_path)
    repo.stage("notebooks/a.ipynb", content)
    ran = repo.guard("--mode", "workspace", "--staged")
    assert ran.code == code, ran.err


# Case 13: file size


def test_a_file_over_5_mib_fails(repo: Repo) -> None:
    repo.stage("big.txt", b"0" * (6 * MIB))
    ran = repo.guard("--mode", "tool", "--staged")
    assert ran.code == 1
    assert "privacy_guard: big.txt: file over 5 MiB" in ran.lines


def test_a_file_of_exactly_5_mib_passes(repo: Repo) -> None:
    repo.stage("big.txt", b"0" * (5 * MIB))
    assert repo.guard("--mode", "tool", "--staged").code == 0


# Case 14: history


def test_history_finds_a_hit_that_a_later_commit_removed(repo: Repo) -> None:
    repo.stage("a.txt", f"{ACCOUNT}\n")
    first = repo.commit("add notes")
    repo.git("rm", "-q", "a.txt")
    repo.commit("remove notes")
    assert repo.guard("--mode", "tool", "--tracked").code == 0
    ran = repo.guard("--mode", "tool", "--history")
    assert ran.code == 1
    assert f"privacy_guard: commit {first[:7]}: denylist hit in patch (account)" in ran.lines
    assert "2 commits" in ran.lines[-1]


def test_history_checks_messages_and_changed_paths(repo: Repo) -> None:
    repo.stage("x.csv", "a,b\n")
    first = repo.commit(f"add data near {STREET}")
    repo.git("rm", "-q", "x.csv")
    repo.commit("tidy")
    ran = repo.guard("--mode", "tool", "--history")
    assert ran.code == 1
    assert f"privacy_guard: commit {first[:7]}: denylist hit in message (street)" in ran.lines
    assert any(line.startswith(f"privacy_guard: commit {first[:7]}: x.csv: data file type") for line in ran.lines)


def test_history_of_an_empty_repo_is_clean(repo: Repo) -> None:
    assert repo.guard("--mode", "tool", "--history").code == 0


def test_history_redacts_paths(repo: Repo) -> None:
    repo.stage(f"{NMI}.md", "clean\n")
    first = repo.commit("add")
    ran = repo.guard("--mode", "tool", "--history")
    assert ran.code == 1
    assert f"privacy_guard: commit {first[:7]}: <redacted>.md: denylist hit in path (nmi)" in ran.lines


# P0.2.5 review finding 1: `--history` scans each added or changed path's whole new blob, so a match that spans
# lines (which the patch's `+` prefixes break up) is found, as `--staged` finds it.


@pytest.mark.parametrize("separator", ["\n", " \r\n ", "\n\n"])
def test_history_finds_a_street_split_across_lines(repo: Repo, separator: str) -> None:
    text = separator.join(STREET.split())
    repo.stage("notes/a.txt", f"intro\nlives at {text}.\n")
    assert repo.guard("--mode", "tool", "--staged").code == 1
    first = repo.commit("add notes")
    repo.stage("notes/a.txt", "intro\n")
    repo.commit("tidy")
    ran = repo.guard("--mode", "tool", "--history")
    assert ran.code == 1
    assert f"privacy_guard: commit {first[:7]}: notes/a.txt:2: denylist hit (street)" in ran.lines


def test_history_scans_the_new_blob_of_a_changed_file(repo: Repo) -> None:
    """The split is completed by a later commit whose patch shows only one half of it."""
    parts = STREET.split()
    repo.stage("a.txt", f"{' '.join(parts[:-1])}\nzzz\n")
    repo.commit("one half")
    repo.stage("a.txt", f"{' '.join(parts[:-1])}\n{parts[-1]}\n")
    second = repo.commit("the other half")
    ran = repo.guard("--mode", "tool", "--history")
    assert ran.code == 1
    assert f"privacy_guard: commit {second[:7]}: a.txt:1: denylist hit (street)" in ran.lines


# P0.2.5 review finding 4: `--history` also checks ref names and annotated tags


def test_history_checks_branch_and_tag_names(repo: Repo) -> None:
    repo.stage("a.txt", "clean\n")
    repo.commit("add")
    repo.git("branch", f"feature/{NMI}")
    repo.git("tag", f"v1-{ACCOUNT}")
    ran = repo.guard("--mode", "tool", "--history")
    assert ran.code == 1
    assert "privacy_guard: ref refs/heads/feature/<redacted>: denylist hit in ref name (nmi)" in ran.lines
    assert "privacy_guard: ref refs/tags/v1-<redacted>: denylist hit in ref name (account)" in ran.lines
    assert ran.lines[-1].endswith("of 1 commit and 3 refs")


def test_history_checks_annotated_tag_messages_down_a_tag_chain(repo: Repo) -> None:
    repo.stage("a.txt", "clean\n")
    repo.commit("add")
    repo.git("tag", "-a", "inner", "-m", f"release\n\nnear {STREET}")
    repo.git("tag", "-a", "outer", "-m", f"wraps it\n\nfor {SERIAL}", "inner")
    repo.git("tag", "-d", "inner")  # the inner tag object is now reachable only through `outer`
    ran = repo.guard("--mode", "tool", "--history")
    assert ran.code == 1
    assert "privacy_guard: ref refs/tags/outer: denylist hit in annotated tag (meter_serial)" in ran.lines
    assert "privacy_guard: ref refs/tags/outer: denylist hit in annotated tag (street)" in ran.lines


def test_history_with_clean_refs_and_tags_passes(repo: Repo, monkeypatch: pytest.MonkeyPatch) -> None:
    # The whole tag object is searched, its tagger line too. Under UTC that line ends in `+0000`, which is the
    # synthetic postcode as a word, so the tag gets a fixed time with a non-zero offset.
    monkeypatch.setenv("GIT_COMMITTER_DATE", "2026-01-01T12:00:00+1030")
    repo.stage("a.txt", "clean\n")
    repo.commit("add")
    repo.git("tag", "-a", "v1", "-m", "release")
    repo.git("branch", "topic")
    ran = repo.guard("--mode", "tool", "--history")
    assert ran.code == 0, ran.err
    assert ran.lines[-1] == "privacy_guard: 0 violations in 0 of 1 commit and 3 refs"


# P0.2.5 review finding 3 (stricter than D2 §7.4): content holding NUL bytes is also searched with them removed, so an
# identifier in UTF-16 text (Excel "Unicode text", PowerShell exports) is found.


@pytest.mark.parametrize("encoding", ["utf-16", "utf-16-le", "utf-16-be"])
def test_utf16_content_is_searched_without_its_nuls(repo: Repo, encoding: str) -> None:
    content = f"header\nmeter {NMI} and {STREET}\n".encode(encoding)
    repo.stage("export.txt", content)
    for source in ("--staged", "--tracked"):
        ran = repo.guard("--mode", "tool", source)
        assert ran.code == 1, source
        assert "privacy_guard: export.txt:2: denylist hit (nmi)" in ran.lines
        assert "street" in hit_labels(ran)
    ran = repo.guard("--mode", "tool", "export.txt")
    assert ran.code == 1
    assert "privacy_guard: export.txt:2: denylist hit (nmi)" in ran.lines
    first = repo.commit("add export")
    repo.git("rm", "-q", "export.txt")
    repo.commit("remove export")
    ran = repo.guard("--mode", "tool", "--history")
    assert ran.code == 1
    assert f"privacy_guard: commit {first[:7]}: export.txt:2: denylist hit (nmi)" in ran.lines


def test_utf16_content_in_a_commit_message_file_is_searched(repo: Repo) -> None:
    message = repo.write(".git/COMMIT_EDITMSG", f"Fix\n\nfor {SERIAL}\n".encode("utf-16-le"))
    ran = repo.guard("--mode", "tool", "--commit-msg", str(message))
    assert ran.code == 1
    assert "privacy_guard: commit message:3: denylist hit (meter_serial)" in ran.lines


def test_clean_utf16_content_passes(repo: Repo) -> None:
    repo.stage("export.txt", "header\nnothing personal\n".encode("utf-16"))
    assert repo.guard("--mode", "tool", "--staged").code == 0


# Case 15: no output holds a denylist value


def test_no_output_holds_a_denylist_value(repo: Repo) -> None:
    """`run_guard` checks every run; this one puts every value everywhere at once."""
    everything = "\n".join(entry.value for entry in DENYLIST)
    repo.stage(f"{NMI_WITH_CHECKSUM}/{SERIAL}_{ACCOUNT}.txt", everything)
    repo.commit(everything)
    repo.stage("b.txt", everything)
    message = repo.write(".git/COMMIT_EDITMSG", everything)
    for source in (["--staged"], ["--tracked"], ["--history"], ["--commit-msg", str(message)]):
        ran = repo.guard("--mode", "tool", "--figures", *source)
        assert ran.code == 1
        assert hit_labels(ran) >= {"nmi", "street", "account"}


# Case 16: --figures


def test_figures_prints_counts_and_keeps_the_exit_code(repo: Repo) -> None:
    repo.stage("notes.md", "used 12.5 kWh and 1,234 MWh\ncost $ 17 and $9\npostcode-like 4321 and 7777 in 2026\n")
    ran = repo.guard("--mode", "tool", "--staged", "--figures")
    assert ran.code == 0, ran.err
    assert "privacy_guard: figures: notes.md: energy 2, money 2, four-digit 2" in ran.out.splitlines()
    for figure in ("12.5", "1,234", "17", "4321", "7777"):
        assert figure not in ran.out + ran.err
    repo.stage("x.csv", "1 kWh\n")
    assert repo.guard("--mode", "tool", "--staged", "--figures").code == 1


def test_figures_skips_years_and_longer_digit_runs(repo: Repo) -> None:
    repo.stage("notes.md", "1900 2099 12345 1899 2100\n")
    ran = repo.guard("--mode", "tool", "--staged", "--figures")
    assert "privacy_guard: figures: notes.md: energy 0, money 0, four-digit 2" in ran.out.splitlines()


figure_text = st.text(alphabet=st.sampled_from(list("0123,. \tkWhMJm3³$x")), max_size=40)


@settings(max_examples=500, deadline=None)
@given(figure_text)
def test_figure_counts_match_the_spec_patterns(text: str) -> None:
    import re

    energy = sum(1 for _ in re.finditer(guard_module.ENERGY_PATTERN, text))
    money = sum(1 for _ in re.finditer(r"\$\s*\d", text))
    four = sum(1 for found in re.finditer(r"(?<!\d)\d{4}(?!\d)", text) if not 1900 <= int(found.group()) <= 2099)
    assert guard_module.figure_counts(text) == (energy, money, four)


def test_figures_on_a_long_digit_run_stay_fast(repo: Repo) -> None:
    repo.stage("big.txt", b"1," * (2 * MIB) + b"5 kWh\n")
    ran = repo.guard("--mode", "tool", "--staged", "--figures")
    assert ran.code == 0, ran.err
    assert "privacy_guard: figures: big.txt: energy 1, money 0, four-digit 0" in ran.out.splitlines()


# Case 17: symlinks are checked by their target text


def test_a_symlink_whose_target_holds_a_value_fails(repo: Repo, tmp_path: Path) -> None:
    os.symlink(f"../elsewhere/{NMI}.txt", repo.root / "link")
    repo.git("add", "link")
    ran = repo.guard("--mode", "tool", "--staged")
    assert ran.code == 1
    assert "privacy_guard: link:1: denylist hit (nmi)" in ran.lines


def test_the_file_a_symlink_points_to_is_not_read(repo: Repo, tmp_path: Path) -> None:
    (tmp_path / "secret.txt").write_text(f"{NMI}\n", encoding="utf-8")
    os.symlink("../secret.txt", repo.root / "link")
    repo.git("add", "link")
    assert repo.guard("--mode", "tool", "--staged").code == 0
    assert run_guard(repo.root, "--mode", "tool", "link").code == 0


# Case 18: gitlinks


@pytest.mark.parametrize("mode", ["tool", "workspace"])
def test_a_staged_gitlink_fails(tmp_path: Path, mode: str) -> None:
    repo = make_repo(tmp_path) if mode == "tool" else make_workspace_repo(tmp_path)
    repo.git("update-index", "--add", "--cacheinfo", f"160000,{'1' * 40},vendor/sub")
    for source in ("--staged", "--tracked"):
        ran = repo.guard("--mode", mode, source)
        assert ran.code == 1
        assert "privacy_guard: vendor/sub: gitlink (submodule)" in ran.lines


# P0.2.5 review finding 2: git's submodule-ignoring settings don't hide a gitlink


GITMODULES_IGNORE_ALL = '[submodule "sub"]\n\tpath = vendor/sub\n\turl = ./sub\n\tignore = all\n'


@pytest.mark.parametrize("setting", ["diff.ignoreSubmodules", ".gitmodules"])
@pytest.mark.parametrize("mode", ["tool", "workspace"])
def test_a_gitlink_is_reported_when_git_ignores_submodules(tmp_path: Path, mode: str, setting: str) -> None:
    repo = make_repo(tmp_path) if mode == "tool" else make_workspace_repo(tmp_path)
    if setting == "diff.ignoreSubmodules":
        repo.git("config", "diff.ignoreSubmodules", "all")
    else:
        repo.stage(".gitmodules", GITMODULES_IGNORE_ALL)
        repo.commit("add .gitmodules")
    repo.git("update-index", "--add", "--cacheinfo", f"160000,{'1' * 40},vendor/sub")
    ran = repo.guard("--mode", mode, "--staged")
    assert ran.code == 1
    assert "privacy_guard: vendor/sub: gitlink (submodule)" in ran.lines
    second = repo.commit("add a gitlink")
    ran = repo.guard("--mode", mode, "--history")
    assert ran.code == 1
    assert f"privacy_guard: commit {second[:7]}: vendor/sub: gitlink (submodule)" in ran.lines


# Case 19: --tracked checks every path in the index


def test_tracked_finds_a_hit_in_an_unchanged_committed_file(repo: Repo) -> None:
    repo.stage("old.txt", f"{NMI}\n")
    repo.commit()
    repo.stage("new.txt", "clean\n")
    assert repo.guard("--mode", "tool", "--staged").code == 0
    ran = repo.guard("--mode", "tool", "--tracked")
    assert ran.code == 1
    assert "privacy_guard: old.txt:1: denylist hit (nmi)" in ran.lines


# Case 20: a malformed generated line


@pytest.mark.parametrize(
    "bad_line",
    [f"nmi\tbogus\t{NMI}", f"just text {NMI}", f"nmi\tsubstring\t{NMI}\textra", "\tsubstring\tvalue", "nmi\tword\t  "],
)
def test_a_malformed_generated_line_is_a_setup_error_without_echo(repo: Repo, bad_line: str) -> None:
    path = repo.denylist_dir / "denylist.generated.txt"
    lines = path.read_text(encoding="utf-8").split("\n")
    lines.insert(3, bad_line)
    path.write_text("\n".join(lines), encoding="utf-8")
    repo.stage("a.txt")
    ran = repo.guard("--mode", "tool", "--staged")
    assert ran.code == 2
    assert "line 4" in ran.err
    assert "bogus" not in ran.err
    assert "just text" not in ran.err


@pytest.mark.parametrize("damage", ["header", "source", "utf8"])
def test_a_damaged_generated_header_is_a_setup_error(repo: Repo, damage: str) -> None:
    path = repo.denylist_dir / "denylist.generated.txt"
    data = path.read_bytes()
    if damage == "header":
        data = data.replace(b"v1", b"v9", 1)
    elif damage == "source":
        data = data.replace(b"# source-sha256: ", b"# source: ", 1)
    else:
        data += b"nmi\tsubstring\t\xff\xfe\n"
    path.write_bytes(data)
    repo.stage("a.txt")
    assert repo.guard("--mode", "tool", "--staged").code == 2


# Path scans (PATH...), which must work before the tool repo is a git repo


@pytest.fixture
def plain(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A plain folder: no git repo here, and git doesn't look above it (`GIT_CEILING_DIRECTORIES`)."""
    folder = tmp_path / "plain"
    folder.mkdir()
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    return folder


def test_path_scans_work_outside_a_git_repo(plain: Path, tmp_path: Path) -> None:
    write_denylist(tmp_path / "private")
    (plain / "notes.md").write_text(f"{ACCOUNT}\n", encoding="utf-8")
    (plain / "clean.md").write_text("clean\n", encoding="utf-8")
    (plain / "x.csv").write_text("a\n", encoding="utf-8")
    ran = run_guard(plain, "--mode", "tool", "--denylist-dir", str(tmp_path / "private"), "notes.md", "clean.md")
    assert ran.code == 1
    assert "privacy_guard: notes.md:1: denylist hit (account)" in ran.lines
    assert run_guard(plain, "--mode", "tool", "--no-denylist", "clean.md").code == 0
    assert run_guard(plain, "--mode", "tool", "--no-denylist", "x.csv").code == 1


def test_a_path_scan_without_git_needs_a_denylist_choice(plain: Path) -> None:
    (plain / "clean.md").write_text("clean\n", encoding="utf-8")
    ran = run_guard(plain, "--mode", "tool", "clean.md")
    assert ran.code == 2
    assert "--no-denylist" in ran.err


def test_a_workspace_path_scan_uses_private_by_default(plain: Path) -> None:
    write_denylist(plain / "private")
    (plain / "notes.md").write_text(f"{NMI}\n", encoding="utf-8")
    assert run_guard(plain, "--mode", "workspace", "notes.md").code == 1


def test_path_scans_use_paths_relative_to_the_repo(repo: Repo) -> None:
    repo.write("src/data.csv", "a\n")
    ran = repo.guard("--mode", "tool", "data.csv", cwd=repo.root / "src")
    assert ran.code == 1
    assert any(line.startswith("privacy_guard: src/data.csv: data file type") for line in ran.lines)


def test_path_scans_of_missing_files_and_folders_are_setup_errors(plain: Path) -> None:
    (plain / "folder").mkdir()
    assert run_guard(plain, "--mode", "tool", "--no-denylist", "missing.md").code == 2
    assert run_guard(plain, "--mode", "tool", "--no-denylist", "folder").code == 2


def test_path_scans_apply_the_figures(plain: Path) -> None:
    (plain / "notes.md").write_text("$5 and 3 kWh\n", encoding="utf-8")
    ran = run_guard(plain, "--mode", "tool", "--no-denylist", "--figures", "notes.md")
    assert ran.code == 0
    assert "privacy_guard: figures: notes.md: energy 1, money 1, four-digit 0" in ran.out.splitlines()


# Arguments


@pytest.mark.parametrize(
    "args",
    [
        ["--mode", "tool"],
        ["--mode", "tool", "--staged", "--tracked"],
        ["--mode", "tool", "--staged", f"{NMI}.csv"],
        ["--mode", "nope", "--staged"],
        ["--staged"],
        ["--mode", "tool", "--staged", "--no-denylist", "--denylist-dir", NMI],
        ["--mode", "tool", "--staged", f"--{NMI}"],
        ["--mode", "tool", "--commit-msg"],
    ],
)
def test_bad_arguments_exit_2_without_echoing_them(repo: Repo, args: list[str]) -> None:
    ran = repo.guard(*args)
    assert ran.code == 2
    assert NMI.lower() not in ran.err.lower()


def test_git_failure_is_a_setup_error(plain: Path) -> None:
    assert run_guard(plain, "--mode", "tool", "--no-denylist", "--staged").code == 2


# The script itself


@pytest.mark.parametrize("name", ["privacy_guard.py", "check_agent_links.py"])
def test_the_script_imports_only_the_standard_library(name: str) -> None:
    tree = ast.parse((SCRIPT.parent / name).read_text(encoding="utf-8"))
    imported = {
        alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names
    } | {
        node.module.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0
    }
    assert imported <= set(sys.stdlib_module_names) | {"__future__"}
    assert "energatron" not in imported
