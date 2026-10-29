"""Scratch workspaces for the adapter and CLI tests: plain folders under the test's `tmp_path`, never a real one.

The private config is synthetic (`tests/unit/core/_private.py`): every value in it is made up.
"""

from pathlib import Path

from energatron.core.models.workspace import WorkspacePaths

WORKSPACE_TOML = 'kind = "workspace_config"\nschema_version = 1\nregion = "sa"\n'

# The synthetic private config of `tests/unit/core/_private.py`, as TOML (no `current_plans.gas`: TOML has no null).
PRIVATE_TOML = """\
kind = "private_config"
schema_version = 1
salt = "0123456789abcdef0123456789abcdef"

[premises]
nmi = "QAAAVZZZZZ3"
meter_serials = ["MSYNTHAB", "Q7X"]
street = "1  Synthetic\\tStreet"
postcode = "0000"
network_tariff = "SYNTARIFF"
gas_mirn = "55512345678"

[accounts]
electricity = ["ELECACCTSYN", "K9"]
gas = ["GASACCTSYN"]

[current_plans]
electricity = "SYN-PLAN-A~20260101T000000Z~0123abcd"
"""

# Only the required fields: `street`, `postcode`, `meter_serials` and `accounts` are missing.
MINIMAL_PRIVATE_TOML = """\
kind = "private_config"
schema_version = 1
salt = "0123456789abcdef0123456789abcdef"

[premises]
nmi = "QAAAVZZZZZ"
"""


def write(root: Path, relative: str, text: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def make_workspace(
    tmp_path: Path, *, private: str | None = PRIVATE_TOML, local: str | None = None, name: str = "ws"
) -> WorkspacePaths:
    """A folder marked as a workspace, with `private/config.toml` and `.energatron/local.toml` when given."""
    root = tmp_path / name
    write(root, ".energatron/workspace.toml", WORKSPACE_TOML)
    if private is not None:
        write(root, "private/config.toml", private)
    if local is not None:
        write(root, ".energatron/local.toml", local)
    return WorkspacePaths(root=root.resolve())


def local_toml(bundle_dir: str) -> str:
    return f'kind = "local_config"\nschema_version = 1\n\n[backup]\nbundle_dir = "{bundle_dir}"\n'
