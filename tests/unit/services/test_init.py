"""`energatron init`'s checks, dry run and private config (P8 spec §4) with a recording set-up adapter.

The whole flow, with real git and DVC, is `tests/integration/test_init_cli.py`.
"""

import tomllib
from collections.abc import Sequence
from pathlib import Path

import pytest

from energatron.adapters import wiring
from energatron.core.canonical import JSONValue
from energatron.core.errors import InvalidInputError, SubprocessError, UsageError
from energatron.core.models.private import PrivateConfig
from energatron.core.models.workspace import WorkspacePaths
from energatron.core.workspace_template import tool_source_line
from energatron.services import init as init_service
from energatron.services.context import ServiceContext
from energatron.services.init import InitOptions, PrivateValues, deny_root, private_toml
from tests.fixtures.synthetic import SYNTHETIC_NMI11, SYNTHETIC_SALT, SYNTHETIC_SERIAL


class RecordingSetup:
    def __init__(self, missing: Sequence[str] = ()) -> None:
        self.missing = list(missing)
        self.calls: list[str] = []

    def missing_tools(self) -> list[str]:
        return self.missing

    def git_init(self, root: Path) -> None:
        self.calls.append("git_init")

    def ensure_git_identity(self, root: Path) -> bool:
        self.calls.append("git_identity")
        return False

    def uv_sync(self, root: Path) -> None:
        self.calls.append("uv_sync")

    def dvc_executable(self, root: Path) -> Path | None:
        return None

    def dvc_init(self, root: Path) -> None:
        self.calls.append("dvc_init")

    def dvc_remote(self, root: Path, remote: Path) -> None:
        self.calls.append("dvc_remote")

    def pre_commit_install(self, root: Path) -> None:
        self.calls.append("pre_commit")

    def unstaged_paths(self, root: Path) -> list[str]:
        return []

    def run_demo(self, root: Path) -> JSONValue:
        return {}


def no_context(paths: WorkspacePaths, dvc: Path | None) -> ServiceContext:
    raise AssertionError("no context is built")


def options(root: Path, **changes: object) -> InitOptions:
    base: dict[str, object] = {
        "root": root,
        "region": "sa",
        "regions": ["sa"],
        "tool_source": tool_source_line("path", "/synthetic/energatron-5000"),
        "tool_source_kind": "path",
        "home": root.parent,
        "sources": wiring.template_sources(),
    }
    return InitOptions(**{**base, **changes})  # type: ignore[arg-type]


def test_a_dry_run_writes_nothing_and_plans_the_steps(tmp_path: Path) -> None:
    setup = RecordingSetup()
    root = tmp_path / "ws"
    result = init_service.init(
        options(root, dry_run=True, private=PrivateValues(nmi=SYNTHETIC_NMI11)), setup=setup, open_context=no_context
    )
    assert not root.exists() and setup.calls == []
    planned = [step.name for step in result.steps if step.status == "planned"]
    assert planned == [
        "write_files",
        "git_init",
        "uv_sync",
        "dvc_init",
        "private_config",
        "denylist",
        "pipeline_sync",
        "pre_commit",
        "commit",
    ]
    assert result.root_name == "ws" and "pyproject.toml" in result.files and ".energatron/template.json" in result.files
    assert result.private_config is True and result.dry_run is True


def test_without_private_values_the_next_steps_say_how(tmp_path: Path) -> None:
    result = init_service.init(options(tmp_path / "ws", dry_run=True), setup=RecordingSetup(), open_context=no_context)
    assert result.private_config is False
    assert any("private.example.toml" in step for step in result.next_steps)
    assert any("remote" in step for step in result.next_steps)


@pytest.mark.parametrize("content", ["file", "folder"])
def test_a_target_that_is_not_an_empty_folder_is_refused(tmp_path: Path, content: str) -> None:
    root = tmp_path / "ws"
    if content == "file":
        root.write_text("x", encoding="utf-8")
    else:
        root.mkdir()
        (root / "keep.txt").write_text("x", encoding="utf-8")
    with pytest.raises(UsageError) as caught:
        init_service.init(options(root), setup=RecordingSetup(), open_context=no_context)
    assert caught.value.details["reason"] == "not_empty"


def test_an_empty_folder_is_accepted(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    result = init_service.init(options(root, dry_run=True), setup=RecordingSetup(), open_context=no_context)
    assert result.dry_run


def test_an_unknown_region_is_refused(tmp_path: Path) -> None:
    with pytest.raises(InvalidInputError) as caught:
        init_service.init(options(tmp_path / "ws", region="xx"), setup=RecordingSetup(), open_context=no_context)
    assert caught.value.details["field"] == "region"
    assert not (tmp_path / "ws").exists()


def test_a_path_with_a_quote_is_refused(tmp_path: Path) -> None:
    with pytest.raises(UsageError) as caught:
        init_service.init(options(tmp_path / 'w"s'), setup=RecordingSetup(), open_context=no_context)
    assert caught.value.details["reason"] == "path_characters"


def test_missing_tools_are_named(tmp_path: Path) -> None:
    with pytest.raises(SubprocessError) as caught:
        init_service.init(options(tmp_path / "ws"), setup=RecordingSetup(["uv"]), open_context=no_context)
    assert caught.value.details["tools"] == ["uv"]
    assert not (tmp_path / "ws").exists()


def test_private_toml_validates_and_quotes() -> None:
    text = private_toml(PrivateValues(nmi=SYNTHETIC_NMI11, meter_serials=(SYNTHETIC_SERIAL, 'a"b')), SYNTHETIC_SALT)
    document = tomllib.loads(text)
    assert document["premises"]["meter_serials"] == [SYNTHETIC_SERIAL, 'a"b']
    assert PrivateConfig.from_document(document).premises.nmi == SYNTHETIC_NMI11


def test_an_invalid_nmi_names_the_field_not_the_value() -> None:
    with pytest.raises(InvalidInputError) as caught:
        private_toml(PrivateValues(nmi="SECRET-NOT-AN-NMI"), SYNTHETIC_SALT)
    assert "SECRET" not in str(caught.value) and "SECRET" not in repr(caught.value.details)


def test_private_values_never_show_in_a_repr() -> None:
    assert SYNTHETIC_NMI11 not in repr(PrivateValues(nmi=SYNTHETIC_NMI11))


def test_deny_root_uses_tilde_under_home(tmp_path: Path) -> None:
    assert deny_root(tmp_path / "a" / "ws", tmp_path) == "~/a/ws"
    assert deny_root(Path("/elsewhere/ws"), tmp_path) == "/elsewhere/ws"
