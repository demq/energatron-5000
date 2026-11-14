"""The workspace template (P8 spec §3): which files `energatron init` writes, and rendering them. Pure: the adapter
reads the template texts from package data and passes them in.

Template files live under `templates/workspace/` as `<name>.tmpl`; a path part `dot-<x>` is written as `.<x>`.
**Seed** files are written once and belong to the user afterwards; **managed** files belong to the tool, and `energatron
upgrade` rewrites them while the user hasn't edited them (§6). Placeholders are `{{name}}`, `{{region}}`,
`{{tool_source}}` and `{{deny_root}}`; verbatim files (the hook scripts, copies of `scripts/`) are never substituted.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import ClassVar, Final, Literal

from pydantic import Field

from energatron.core.canonical import sha256_hex
from energatron.core.errors import InternalError
from energatron.core.persisted import Persisted

TEMPLATE_VERSION: Final = 1
"""Bumped with any change to the template files or this module's list (P8 spec §3.2)."""

AGENTS_LINK: Final = "AGENTS.md"
TEMPLATE_STATE_PATH: Final = ".energatron/template.json"


@dataclass(frozen=True)
class TemplateSpec:
    """One template file: its package-data name, how it is written, and who owns it."""

    source: str  # relative to templates/workspace/, ending in .tmpl
    managed: bool
    verbatim: bool = False
    mode: int = 0o644

    @property
    def path(self) -> str:
        """The workspace path it is written to."""
        parts = self.source.removesuffix(".tmpl").split("/")
        return "/".join("." + part.removeprefix("dot-") if part.startswith("dot-") else part for part in parts)


TEMPLATE_FILES: Final = (
    TemplateSpec("pyproject.toml.tmpl", managed=False),
    TemplateSpec("dot-python-version.tmpl", managed=True),
    TemplateSpec("dot-gitignore.tmpl", managed=True),
    TemplateSpec("dot-dvcignore.tmpl", managed=True),
    TemplateSpec("dot-pre-commit-config.yaml.tmpl", managed=True),
    TemplateSpec("dot-energatron/scripts/privacy_guard.py.tmpl", managed=True, verbatim=True, mode=0o755),
    TemplateSpec("dot-energatron/scripts/check_agent_links.py.tmpl", managed=True, verbatim=True, mode=0o755),
    TemplateSpec("dot-energatron/workspace.toml.tmpl", managed=False),
    TemplateSpec("config/private.example.toml.tmpl", managed=True),
    TemplateSpec("CLAUDE.md.tmpl", managed=False),
    TemplateSpec("dot-claude/settings.json.tmpl", managed=False),
    TemplateSpec("README.md.tmpl", managed=False),
)
"""Every template file, in the order `init` writes them. `test_workspace_template` checks the package holds exactly
these."""

_PLACEHOLDER: Final = re.compile(r"\{\{([a-z_]+)\}\}")
_NAME_CHARS: Final = re.compile(r"[^a-z0-9._-]+")
_TOOL_DISTRIBUTION: Final = "energatron-5000"


@dataclass(frozen=True)
class TemplateValues:
    """What a render fills in. `tool_source` is the `[tool.uv.sources]` line, or "" for none (an index install)."""

    name: str
    region: str
    tool_source: str
    deny_root: str

    def as_mapping(self) -> dict[str, str]:
        return {
            "name": self.name,
            "region": self.region,
            "tool_source": self.tool_source,
            "deny_root": self.deny_root,
        }


@dataclass(frozen=True)
class TemplateFile:
    """One file to write: its workspace path, bytes, mode and owner. `symlink_to` makes it a symlink instead."""

    path: str
    data: bytes
    managed: bool
    mode: int = 0o644
    symlink_to: str | None = None


def project_name(folder: str) -> str:
    """A valid project name for the workspace's `pyproject.toml` from its folder name (PEP 508's characters).

    Never the tool's own distribution name, which would make the workspace depend on itself."""
    name = _NAME_CHARS.sub("-", folder.lower()).strip("-._")
    if not name:
        return "energatron-workspace"
    return f"{name}-workspace" if name == _TOOL_DISTRIBUTION else name


def tool_source_line(kind: Literal["path", "git", "index"], value: str | None = None, ref: str | None = None) -> str:
    """The `[tool.uv.sources]` line for the tool: an editable folder, a git URL at a ref, or none."""
    match kind:
        case "path":
            return f'{_TOOL_DISTRIBUTION} = {{ path = "{_toml_string(value)}", editable = true }}'
        case "git":
            rev = f', rev = "{_toml_string(ref)}"' if ref else ""
            return f'{_TOOL_DISTRIBUTION} = {{ git = "{_toml_string(value)}"{rev} }}'
        case "index":
            return ""


def _toml_string(value: str | None) -> str:
    if not value or any(char in value for char in '"\\\n\r'):
        raise InternalError("A tool source can't be written into pyproject.toml.")
    return value


def render(values: TemplateValues, sources: Mapping[str, str]) -> list[TemplateFile]:
    """Every template file rendered, plus the `AGENTS.md` symlink, in `TEMPLATE_FILES` order.

    `sources` maps each `TemplateSpec.source` to its text. A missing source, an unknown placeholder, or a value no
    template uses is a bug → `E_INTERNAL`.
    """
    mapping = values.as_mapping()
    used: set[str] = set()
    found: list[TemplateFile] = []

    def substitute(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in mapping:
            raise InternalError("A workspace template names an unknown placeholder.", placeholder=key)
        used.add(key)
        return mapping[key]

    for spec in TEMPLATE_FILES:
        text = sources.get(spec.source)
        if text is None:
            raise InternalError("A workspace template file is missing from the package.", template=spec.source)
        rendered = text if spec.verbatim else _PLACEHOLDER.sub(substitute, text)
        found.append(TemplateFile(spec.path, rendered.encode("utf-8"), spec.managed, spec.mode))
    unused = sorted(set(mapping) - used)
    if unused:
        raise InternalError("A workspace template value is never used.", placeholders=unused)
    found.append(TemplateFile(AGENTS_LINK, b"", managed=False, symlink_to="CLAUDE.md"))
    return found


class TemplateState(Persisted):
    """`.energatron/template.json` (P8 spec §3.4): the template version and the sha256 of each managed file as
    written, so `upgrade` can tell a file the user edited from one it may rewrite."""

    KIND: ClassVar[str] = "template_state"
    SCHEMA_VERSION: ClassVar[int] = 1
    kind: Literal["template_state"] = "template_state"
    schema_version: Literal[1] = 1

    template_version: int = Field(ge=1, strict=True)
    files: dict[str, str]


def template_state(files: list[TemplateFile]) -> TemplateState:
    """The state recording `files`' managed members."""
    return TemplateState(
        template_version=TEMPLATE_VERSION,
        files={item.path: sha256_hex(item.data) for item in files if item.managed},
    )
