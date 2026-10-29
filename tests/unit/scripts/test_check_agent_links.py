"""`scripts/check_agent_links.py` (D2 §9): each rule has a failing scratch tree and a passing one.

The script runs as a subprocess against scratch trees. `--parent` runs `<tool repo>/scripts/link_parent.py --check`,
so its tests run a copy of the script inside a scratch tool repo whose `link_parent.py` is a stand-in: the real
parent folder is never read.
"""

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "check_agent_links.py"
SKILL = "---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n"


@dataclass
class Ran:
    code: int
    out: str
    err: str

    @property
    def problems(self) -> list[str]:
        return [line for line in (self.out + self.err).splitlines() if line.startswith("check_agent_links: ")]


def run(script: Path, *args: str, cwd: Path | None = None) -> Ran:
    done = subprocess.run([sys.executable, str(script), *args], cwd=cwd, capture_output=True, text=True)
    return Ran(done.returncode, done.stdout, done.stderr)


def check(repo: Path, *args: str) -> Ran:
    return run(SCRIPT, "--repo", str(repo), *args)


def add_skill(repo: Path, name: str, *, description: str = "Does a thing.", link: bool = True) -> None:
    skill = repo / ".claude" / "skills" / name
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(SKILL.format(name=name, description=description), encoding="utf-8")
    if link:
        links = repo / ".agents" / "skills"
        links.mkdir(parents=True, exist_ok=True)
        os.symlink(f"../../.claude/skills/{name}", links / name)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A tree that passes every rule."""
    root = tmp_path / "repo"
    root.mkdir()
    (root / "CLAUDE.md").write_text("# repo\n\nRules first.\n", encoding="utf-8")
    os.symlink("CLAUDE.md", root / "AGENTS.md")
    add_skill(root, "next")
    add_skill(root, "done")
    (root / ".claude" / "settings.json").write_text(json.dumps({"permissions": {}}), encoding="utf-8")
    return root


def test_a_good_tree_passes(repo: Path) -> None:
    ran = check(repo)
    assert ran.code == 0, ran.problems
    assert ran.problems == []


# Rule 1: CLAUDE.md


def test_claude_md_missing(repo: Path) -> None:
    (repo / "CLAUDE.md").unlink()
    assert check(repo).code == 1


@pytest.mark.parametrize(
    "content",
    [b"x" * (16 * 1024 + 1), b"# repo\n\xff\xfe\n", b"# repo\n@docs/more.md\n"],
    ids=["too-big", "not-utf8", "import-line"],
)
def test_claude_md_content_rules(repo: Path, content: bytes) -> None:
    (repo / "CLAUDE.md").write_bytes(content)
    ran = check(repo)
    assert ran.code == 1
    assert any("CLAUDE.md" in problem for problem in ran.problems)


def test_claude_md_of_exactly_16_kib_with_an_inner_at_passes(repo: Path) -> None:
    text = "see @someone\n"
    (repo / "CLAUDE.md").write_text(text + "x" * (16 * 1024 - len(text)), encoding="utf-8")
    assert check(repo).code == 0


def test_claude_md_must_be_a_regular_file(repo: Path) -> None:
    (repo / "CLAUDE.md").rename(repo / "REAL.md")
    os.symlink("REAL.md", repo / "CLAUDE.md")
    assert check(repo).code == 1


# Rule 2: AGENTS.md


@pytest.mark.parametrize("kind", ["missing", "file", "other-target"])
def test_agents_md_must_link_to_exactly_claude_md(repo: Path, kind: str) -> None:
    agents = repo / "AGENTS.md"
    agents.unlink()
    if kind == "file":
        agents.write_text("# repo\n", encoding="utf-8")
    elif kind == "other-target":
        os.symlink("./CLAUDE.md", agents)
    ran = check(repo)
    assert ran.code == 1
    assert any("AGENTS.md" in problem for problem in ran.problems)


# Rule 3: skills and their links


@pytest.mark.parametrize(
    "skill_md",
    [
        SKILL.format(name="other", description="Does a thing."),
        SKILL.format(name="extra", description=""),
        "# extra\n\nno frontmatter\n",
        "---\nname: extra\ndescription: x\n",
    ],
    ids=["name-differs", "empty-description", "no-frontmatter", "unterminated"],
)
def test_skill_frontmatter_rules(repo: Path, skill_md: str) -> None:
    add_skill(repo, "extra")
    (repo / ".claude" / "skills" / "extra" / "SKILL.md").write_text(skill_md, encoding="utf-8")
    ran = check(repo)
    assert ran.code == 1
    assert any("extra" in problem for problem in ran.problems)


def test_a_quoted_name_and_description_pass(repo: Path) -> None:
    add_skill(repo, "extra")
    text = '---\nname: "extra"\ndescription: \'Does: a thing.\'\nargument-hint: "[id]"\n---\n'
    (repo / ".claude" / "skills" / "extra" / "SKILL.md").write_text(text, encoding="utf-8")
    assert check(repo).code == 0


def test_a_skill_without_its_link_fails(repo: Path) -> None:
    add_skill(repo, "extra", link=False)
    ran = check(repo)
    assert ran.code == 1
    assert any(".agents/skills/extra" in problem for problem in ran.problems)


@pytest.mark.parametrize(
    "target", ["../../.claude/skills/next/", "/abs/.claude/skills/extra", "../../.claude/skills/next"]
)
def test_a_skill_link_with_another_target_fails(repo: Path, target: str) -> None:
    add_skill(repo, "extra", link=False)
    os.symlink(target, repo / ".agents" / "skills" / "extra")
    assert check(repo).code == 1


# Rule 4: no strays, no broken links


def test_a_stray_link_fails(repo: Path) -> None:
    shutil.copytree(repo / ".claude" / "skills" / "next", repo / "elsewhere")
    os.symlink("../../elsewhere", repo / ".agents" / "skills" / "elsewhere")
    ran = check(repo)
    assert ran.code == 1
    assert any(".agents/skills/elsewhere" in problem for problem in ran.problems)


def test_a_broken_link_fails(repo: Path) -> None:
    os.symlink("../../.claude/skills/gone", repo / ".agents" / "skills" / "gone")
    ran = check(repo)
    assert ran.code == 1
    assert any(".agents/skills/gone" in problem for problem in ran.problems)


def test_an_entry_that_is_not_a_symlink_fails(repo: Path) -> None:
    (repo / ".agents" / "skills" / "copied").mkdir()
    assert check(repo).code == 1


# Rule 5: settings


def test_settings_that_are_not_json_fail(repo: Path) -> None:
    (repo / ".claude" / "settings.json").write_text("{nope", encoding="utf-8")
    ran = check(repo)
    assert ran.code == 1
    assert any("settings.json" in problem for problem in ran.problems)


def test_settings_may_be_absent(repo: Path) -> None:
    (repo / ".claude" / "settings.json").unlink()
    assert check(repo).code == 0


# Rule 6: --parent runs the tool repo's link_parent.py --check


@pytest.mark.parametrize(("exit_code", "expected"), [(0, 0), (1, 1)])
def test_parent_runs_link_parent_check(repo: Path, tmp_path: Path, exit_code: int, expected: int) -> None:
    tool = tmp_path / "tool"
    (tool / "scripts").mkdir(parents=True)
    script = tool / "scripts" / "check_agent_links.py"
    shutil.copy(SCRIPT, script)
    record = tmp_path / "args.txt"
    (tool / "scripts" / "link_parent.py").write_text(
        f"import sys\nopen({str(record)!r}, 'w').write(' '.join(sys.argv[1:]))\nsys.exit({exit_code})\n",
        encoding="utf-8",
    )
    ran = run(script, "--repo", str(repo), "--parent")
    assert ran.code == expected
    assert record.read_text() == "--check"
    assert check(repo).code == 0  # without --parent nothing runs it


# --repo defaults to the git root of the current directory


def test_repo_defaults_to_the_git_root(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    sub = repo / "sub"
    sub.mkdir()
    assert run(SCRIPT, cwd=sub).code == 0
    (repo / "AGENTS.md").unlink()
    assert run(SCRIPT, cwd=sub).code == 1


def test_outside_a_git_repo_without_repo_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    folder = tmp_path / "plain"
    folder.mkdir()
    ran = run(SCRIPT, cwd=folder)
    assert ran.code != 0
    assert "--repo" in ran.out + ran.err
