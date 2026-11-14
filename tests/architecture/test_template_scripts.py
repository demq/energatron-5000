"""The workspace template's hook scripts are byte copies of `scripts/` (P8 spec §3.3).

A templated workspace's hooks run `.energatron/scripts/privacy_guard.py` and `check_agent_links.py`, which `init` and
`upgrade` write from package data. Change a script in `scripts/` and copy it into the template in the same change.
"""

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
TEMPLATE = REPO / "src" / "energatron" / "templates" / "workspace" / "dot-energatron" / "scripts"


@pytest.mark.parametrize("name", ["privacy_guard.py", "check_agent_links.py"])
def test_the_template_copy_equals_the_script(name: str) -> None:
    assert (TEMPLATE / f"{name}.tmpl").read_bytes() == (REPO / "scripts" / name).read_bytes(), (
        f"copy scripts/{name} to {TEMPLATE.relative_to(REPO)}/{name}.tmpl"
    )
