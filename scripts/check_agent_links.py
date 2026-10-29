#!/usr/bin/env python3
"""Check that a repo's agent instructions and skills load the same way in Claude Code and Codex (D2 §9).

Usage:  check_agent_links.py [--repo DIR] [--parent]
  --repo DIR  the repo to check; defaults to the git root of the current directory
  --parent    also run `<tool repo>/scripts/link_parent.py --check` (local hooks; CI has no parent folder)

Checks:
  1. CLAUDE.md is a regular UTF-8 file of at most 16 KiB with no line starting with `@` (no Claude-only imports).
  2. AGENTS.md is a symlink whose target is exactly `CLAUDE.md`.
  3. Every .claude/skills/<name>/SKILL.md has frontmatter whose `name` is <name> and whose `description` isn't
     empty, and .agents/skills/<name> is a symlink to exactly `../../.claude/skills/<name>`.
  4. Every entry in .agents/skills/ is such a symlink, to a skill that exists: no strays, no broken links.
  5. .claude/settings.json, if present, parses as JSON.
  6. With --parent: link_parent.py --check exits 0.

Prints one line per problem, then exits 1; exits 0 when clean. Stdlib only, so it runs before any venv exists.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

MAX_CLAUDE_MD = 16 * 1024
TOOL_REPO = Path(__file__).resolve().parents[1]


def frontmatter(text: str) -> dict[str, str] | None:
    """Top-level `key: value` pairs of a SKILL.md frontmatter, quotes stripped; None without a closed one."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    fields: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            return fields
        key, sep, value = line.partition(":")
        if sep and not line[:1].isspace():
            fields[key.strip()] = _unquote(value.strip())
    return None


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def check_claude_md(repo: Path) -> list[str]:
    path = repo / "CLAUDE.md"
    if path.is_symlink() or not path.is_file():
        return ["CLAUDE.md is missing or not a regular file"]
    data = path.read_bytes()
    problems: list[str] = []
    if len(data) > MAX_CLAUDE_MD:
        problems.append(f"CLAUDE.md is over {MAX_CLAUDE_MD // 1024} KiB")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return [*problems, "CLAUDE.md is not UTF-8"]
    for number, line in enumerate(text.split("\n"), start=1):
        if line.startswith("@"):
            problems.append(f"CLAUDE.md line {number} starts with @ (a Claude-only import)")
    return problems


def check_agents_md(repo: Path) -> list[str]:
    path = repo / "AGENTS.md"
    if not path.is_symlink() or os.readlink(path) != "CLAUDE.md":
        return ["AGENTS.md is not a symlink to exactly CLAUDE.md"]
    return []


def skills(repo: Path) -> list[str]:
    """The names of the directories under .claude/skills/ that hold a SKILL.md."""
    root = repo / ".claude" / "skills"
    if not root.is_dir():
        return []
    return sorted(entry.name for entry in root.iterdir() if (entry / "SKILL.md").is_file())


def check_skills(repo: Path) -> list[str]:
    problems: list[str] = []
    names = skills(repo)
    for name in names:
        skill_md = f".claude/skills/{name}/SKILL.md"
        try:
            fields = frontmatter((repo / skill_md).read_text(encoding="utf-8"))
        except UnicodeDecodeError:
            fields = None
        if fields is None:
            problems.append(f"{skill_md} has no frontmatter")
        else:
            if fields.get("name") != name:
                problems.append(f"{skill_md}: name is not {name}")
            if not fields.get("description"):
                problems.append(f"{skill_md}: description is empty")
        link = repo / ".agents" / "skills" / name
        if not link.is_symlink() or os.readlink(link) != f"../../.claude/skills/{name}":
            problems.append(f".agents/skills/{name} is not a symlink to exactly ../../.claude/skills/{name}")
    links = repo / ".agents" / "skills"
    if links.is_dir():
        for entry in sorted(links.iterdir()):
            if entry.name in names:
                continue  # checked above
            if not entry.is_symlink():
                problems.append(f".agents/skills/{entry.name} is a stray entry, not a skill link")
            elif not entry.exists():
                problems.append(f".agents/skills/{entry.name} is a broken link")
            else:
                problems.append(f".agents/skills/{entry.name} is a stray link to no skill in .claude/skills/")
    return problems


def check_settings(repo: Path) -> list[str]:
    path = repo / ".claude" / "settings.json"
    if not path.exists():
        return []
    try:
        json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return [".claude/settings.json does not parse as JSON"]
    return []


def check_parent() -> list[str]:
    script = TOOL_REPO / "scripts" / "link_parent.py"
    done = subprocess.run([sys.executable, str(script), "--check"], capture_output=True, text=True)
    if done.returncode != 0:
        return ["link_parent.py --check failed: run `python3 energatron-5000/scripts/link_parent.py` in the parent"]
    return []


def git_root() -> Path | None:
    try:
        done = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    except OSError:
        return None
    return Path(done.stdout.strip()) if done.returncode == 0 else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check a repo's agent instructions and skill links (D2 §9).")
    parser.add_argument("--repo", type=Path, help="the repo to check (default: the git root of the current folder)")
    parser.add_argument("--parent", action="store_true", help="also run link_parent.py --check")
    args = parser.parse_args(argv)
    repo = args.repo if args.repo is not None else git_root()
    if repo is None:
        print("check_agent_links: not in a git repo; pass --repo DIR", file=sys.stderr)
        return 1
    problems = check_claude_md(repo) + check_agents_md(repo) + check_skills(repo) + check_settings(repo)
    if args.parent:
        problems += check_parent()
    for problem in problems:
        print(f"check_agent_links: {problem}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
