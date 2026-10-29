"""The tool repo's `.gitignore` keeps every cache the standard checks write out of P0.4.1's audit (D2 §5.3, §5.4).

The audit is `git ls-files -co --exclude-standard`; here it runs in a scratch repo holding a copy of the
`.gitignore`, never in the tool repo itself (it isn't a git repo until P0.4.1).
"""

import shutil
from pathlib import Path

import pytest

from tests.integration._scratch import git

pytestmark = pytest.mark.integration

REPO = Path(__file__).resolve().parents[2]
CACHES = [".venv", "__pycache__", ".pytest_cache", ".ruff_cache", ".hypothesis", ".import_linter_cache"]


def test_the_check_caches_are_ignored(tmp_path: Path) -> None:
    shutil.copyfile(REPO / ".gitignore", tmp_path / ".gitignore")
    git(tmp_path, "init", "-q", "-b", "main")
    for cache in CACHES:
        (tmp_path / cache).mkdir()
        (tmp_path / cache / "entry").write_text("cache\n", encoding="utf-8")
    assert git(tmp_path, "ls-files", "-co", "--exclude-standard").split() == [".gitignore"]
