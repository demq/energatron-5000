"""`energatron migrate` (P8 spec §7) in a workspace `init --no-sync` made, with real git.

Every real config kind is still at version 1, so the rewrite is proven on a synthetic kind registered here at version
2 with a migration from 1, through the service's `targets`.
"""

import tomllib
from pathlib import Path
from typing import ClassVar, Literal

import pytest
import yaml

from energatron.adapters import wiring
from energatron.core.canonical import JSONValue
from energatron.core.errors import InvalidInputError, SchemaTooNewError
from energatron.core.models.workspace import WorkspacePaths
from energatron.core.persisted import Persisted, migration
from energatron.services import migrate as migrate_service
from energatron.services.migrate import Target
from tests.fixtures.synthetic import SYNTHETIC_NMI11
from tests.integration._scratch import git
from tests.integration.test_init_cli import init_args
from tests.integration.test_land_cli import envelope

pytestmark = pytest.mark.integration


class SyntheticConfig(Persisted):
    KIND: ClassVar[str] = "synthetic_migrate_config"
    SCHEMA_VERSION: ClassVar[int] = 2
    kind: Literal["synthetic_migrate_config"] = "synthetic_migrate_config"
    schema_version: Literal[2] = 2

    name: str
    limits: dict[str, int]


@migration("synthetic_migrate_config", from_version=1)
def _v1_to_v2(doc: dict[str, JSONValue]) -> dict[str, JSONValue]:
    return {**{key: value for key, value in doc.items() if key != "limit"}, "schema_version": 2, "limits": {"a": 1}}


TOML_TARGET = Target("config/synthetic.toml", "synthetic_migrate_config")
YAML_TARGET = Target("config/synthetic.local", "synthetic_migrate_config", folder=True)


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> Path:
    for name in ("ENERGATRON_JSON", "ENERGATRON_WORKSPACE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    code, result = envelope(capsys, *init_args(tmp_path / "ws", "--nmi", SYNTHETIC_NMI11))
    assert code == 0, result["error"]
    monkeypatch.chdir(tmp_path / "ws")
    return tmp_path / "ws"


def test_a_current_workspace_has_nothing_to_rewrite(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    head = git(root, "rev-parse", "HEAD")
    code, result = envelope(capsys, "migrate", "--dry-run")
    assert code == 0, result["error"]
    code, result = envelope(capsys, "migrate")
    assert code == 0, result["error"]
    assert (result["data"]["files"], result["data"]["commit"]) == ([], None)
    assert git(root, "rev-parse", "HEAD") == head
    assert SYNTHETIC_NMI11 not in str(result)


def test_older_files_are_rewritten_and_committed(root: Path) -> None:
    (root / "config" / "synthetic.toml").write_text(
        'kind = "synthetic_migrate_config"\nschema_version = 1\nname = "a \\"quoted\\" name"\nlimit = 3\n',
        encoding="utf-8",
    )
    (root / "config" / "synthetic.local").mkdir()
    (root / "config" / "synthetic.local" / "one.yaml").write_text(
        "kind: synthetic_migrate_config\nschema_version: 1\nname: one\n", encoding="utf-8"
    )
    git(root, "add", "config")
    git(root, "commit", "-q", "-m", "older config")
    ctx = wiring.build_context(WorkspacePaths(root=root))

    dry = migrate_service.migrate(ctx, dry_run=True, targets=(TOML_TARGET, YAML_TARGET))
    assert [item.path for item in dry.files] == ["config/synthetic.toml", "config/synthetic.local/one.yaml"]
    assert "schema_version = 1" in (root / "config" / "synthetic.toml").read_text(encoding="utf-8")

    result = migrate_service.migrate(ctx, targets=(TOML_TARGET, YAML_TARGET))
    assert [(item.from_version, item.to_version) for item in result.files] == [(1, 2), (1, 2)]
    toml_doc = tomllib.loads((root / "config" / "synthetic.toml").read_text(encoding="utf-8"))
    assert toml_doc == {
        "kind": "synthetic_migrate_config",
        "schema_version": 2,
        "name": 'a "quoted" name',
        "limits": {"a": 1},
    }
    text = (root / "config" / "synthetic.local" / "one.yaml").read_text(encoding="utf-8")
    assert text.startswith("kind: synthetic_migrate_config\nschema_version: 2\n")
    assert yaml.safe_load(text)["limits"] == {"a": 1}
    assert git(root, "log", "-1", "--format=%s").strip() == "migrate: 2 files"
    assert git(root, "status", "--porcelain") == ""

    again = migrate_service.migrate(ctx, targets=(TOML_TARGET, YAML_TARGET))
    assert (again.files, again.commit) == ([], None)


def test_a_newer_or_foreign_file_stops_before_any_write(root: Path) -> None:
    ctx = wiring.build_context(WorkspacePaths(root=root))
    path = root / "config" / "synthetic.toml"
    path.write_text('kind = "synthetic_migrate_config"\nschema_version = 3\nname = "x"\n', encoding="utf-8")
    with pytest.raises(SchemaTooNewError):
        migrate_service.migrate(ctx, targets=(TOML_TARGET,))
    path.write_text('kind = "something_else"\nschema_version = 1\n', encoding="utf-8")
    with pytest.raises(InvalidInputError) as caught:
        migrate_service.migrate(ctx, targets=(TOML_TARGET,))
    assert caught.value.details["path"] == "config/synthetic.toml"
