"""The workspace template and its rendering (P8 spec §3)."""

import importlib.resources
import json
import tomllib

import pytest
import yaml

from energatron.adapters import wiring
from energatron.core.canonical import sha256_hex
from energatron.core.errors import InternalError
from energatron.core.workspace_template import (
    INTRODUCED,
    SKILLS,
    TEMPLATE_FILES,
    TEMPLATE_VERSION,
    TemplateSpec,
    TemplateValues,
    project_name,
    render,
    template_state,
    tool_source_line,
)

VALUES = TemplateValues(
    name="synthetic-ws",
    region="sa",
    tool_source=tool_source_line("path", "/synthetic/energatron-5000"),
    deny_root="~/synthetic-ws",
)


def sources() -> dict[str, str]:
    return wiring.template_sources()


def by_path() -> dict[str, bytes]:
    return {item.path: item.data for item in render(VALUES, sources())}


def test_the_package_holds_exactly_the_listed_templates() -> None:
    root = importlib.resources.files("energatron").joinpath("templates", "workspace")
    found: set[str] = set()
    pending = [(root, "")]
    while pending:
        folder, prefix = pending.pop()
        for item in folder.iterdir():
            if item.is_dir():
                pending.append((item, f"{prefix}{item.name}/"))
            else:
                found.add(f"{prefix}{item.name}")
    assert found == {spec.source for spec in TEMPLATE_FILES}
    assert all(name.endswith(".tmpl") for name in found)


def test_dot_parts_become_dot_files() -> None:
    assert TemplateSpec("dot-energatron/scripts/x.py.tmpl", managed=True).path == ".energatron/scripts/x.py"
    assert TemplateSpec("config/a.toml.tmpl", managed=True).path == "config/a.toml"


def test_rendered_files_parse_and_carry_the_values() -> None:
    files = by_path()
    pyproject = tomllib.loads(files["pyproject.toml"].decode())
    assert pyproject["project"]["name"] == "synthetic-ws"
    assert pyproject["tool"]["uv"]["sources"]["energatron-5000"] == {
        "path": "/synthetic/energatron-5000",
        "editable": True,
    }
    workspace = tomllib.loads(files[".energatron/workspace.toml"].decode())
    assert (workspace["kind"], workspace["region"]) == ("workspace_config", "sa")
    deny = json.loads(files[".claude/settings.json"])["permissions"]["deny"]
    assert "Read(~/synthetic-ws/private/**)" in deny
    hooks = yaml.safe_load(files[".pre-commit-config.yaml"])["repos"][0]["hooks"]
    assert any(".energatron/scripts/privacy_guard.py" in hook["entry"] for hook in hooks)
    assert all("{{" not in data.decode() for path, data in files.items() if not path.endswith(".py"))


def test_an_index_install_leaves_an_empty_sources_table() -> None:
    values = TemplateValues(name="w", region="sa", tool_source=tool_source_line("index"), deny_root="/w")
    data = next(item.data for item in render(values, sources()) if item.path == "pyproject.toml")
    assert tomllib.loads(data.decode())["tool"]["uv"]["sources"] == {}


def test_a_git_source_has_its_ref() -> None:
    line = tool_source_line("git", "https://example.invalid/energatron-5000", "abc123")
    assert tomllib.loads(f"[s]\n{line}\n")["s"]["energatron-5000"] == {
        "git": "https://example.invalid/energatron-5000",
        "rev": "abc123",
    }


def test_scripts_are_verbatim_and_executable() -> None:
    files = {item.path: item for item in render(VALUES, sources())}
    guard = files[".energatron/scripts/privacy_guard.py"]
    assert guard.data == sources()["dot-energatron/scripts/privacy_guard.py.tmpl"].encode()
    assert (guard.mode, guard.managed) == (0o755, True)
    assert files["AGENTS.md"].symlink_to == "CLAUDE.md"
    assert files["pyproject.toml"].managed is False and files[".gitignore"].managed is True


def test_unknown_placeholder_missing_template_and_unused_value_are_bugs() -> None:
    texts = sources()
    with pytest.raises(InternalError):
        render(VALUES, {**texts, "README.md.tmpl": "{{nope}}"})
    with pytest.raises(InternalError):
        render(VALUES, {key: value for key, value in texts.items() if key != "README.md.tmpl"})
    with pytest.raises(InternalError):
        render(VALUES, {**texts, "dot-claude/settings.json.tmpl": "{}"})  # deny_root used nowhere else


def test_template_state_hashes_the_managed_files_only() -> None:
    files = render(VALUES, sources())
    state = template_state(files)
    assert state.template_version == TEMPLATE_VERSION
    assert state.files == {item.path: sha256_hex(item.data) for item in files if item.managed}
    assert "pyproject.toml" not in state.files


@pytest.mark.parametrize(
    ("folder", "name"),
    [
        ("My Energy", "my-energy"),
        ("energatron-5000", "energatron-5000-workspace"),
        ("...", "energatron-workspace"),
        ("demo_ws.1", "demo_ws.1"),
    ],
)
def test_project_name(folder: str, name: str) -> None:
    assert project_name(folder) == name


def test_a_quote_in_a_tool_source_is_refused() -> None:
    with pytest.raises(InternalError):
        tool_source_line("path", '/a"b')


def test_version_2_adds_the_skills_their_links_and_the_mcp_registration() -> None:
    """P9 spec §7.2: four managed skills, a link to each from `.agents/skills/`, and `.mcp.json` as a seed file."""
    rendered = {item.path: item for item in render(VALUES, sources())}
    assert TEMPLATE_VERSION >= 2
    for name in SKILLS:
        skill = rendered[f".claude/skills/{name}/SKILL.md"]
        assert skill.managed and skill.data.decode().startswith(f"---\nname: {name}\n")
        link = rendered[f".agents/skills/{name}"]
        assert (link.symlink_to, link.managed) == (f"../../.claude/skills/{name}", False)
        assert INTRODUCED[link.path] == 2
    mcp = rendered[".mcp.json"]
    assert not mcp.managed and INTRODUCED[".mcp.json"] == 2
    assert json.loads(mcp.data)["mcpServers"]["energatron"] == {
        "command": "uv",
        "args": ["run", "--frozen", "energatron", "mcp"],
    }
    assert rendered["AGENTS.md"].symlink_to == "CLAUDE.md"
    assert "## Agents" in rendered["CLAUDE.md"].data.decode()


def test_version_3_lets_land_recognise_a_bill() -> None:
    """Bill PDF spec §3: `land` recognises a bill from its text, so the skills name a source only after
    `E_AMBIGUOUS_SOURCE`, never as a step every bill needs."""
    assert TEMPLATE_VERSION == 3
    for name in ("monthly-check-in", "transcribe-gas-bill"):
        text = " ".join(by_path()[f".claude/skills/{name}/SKILL.md"].decode().split())
        assert "--source gas_bill --dry-run" not in text and "needs `source" not in text, name
        assert "E_AMBIGUOUS_SOURCE" in text, name


def test_the_skills_name_no_agent_specific_tool() -> None:
    """Plan §14.5: skills name the energatron tools and commands, never a tool only one agent has."""
    for name in SKILLS:
        text = by_path()[f".claude/skills/{name}/SKILL.md"].decode()
        for word in ("Bash", "Read(", "Edit(", "TodoWrite", "mcp__"):
            assert word not in text, (name, word)
