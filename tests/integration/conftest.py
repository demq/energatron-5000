"""Every integration test runs git and DVC in scratch folders, cut off from the machine's own configuration.

Global and system git and DVC configuration is replaced by empty scratch files, so a signing key, a global hook
or a DVC remote of the machine never reaches a test. Nothing here touches iCloud or the real workspace.
"""

import pytest


@pytest.fixture(autouse=True)
def isolated_tools(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path_factory.mktemp("tool-config")
    gitconfig = home / "gitconfig"
    gitconfig.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(gitconfig))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Scratch")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "scratch@example.invalid")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "Scratch")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "scratch@example.invalid")
    monkeypatch.setenv("DVC_NO_ANALYTICS", "1")
    for name in ("DVC_GLOBAL_CONFIG_DIR", "DVC_SYSTEM_CONFIG_DIR", "DVC_SITE_CACHE_DIR"):
        folder = home / name.lower()
        folder.mkdir()
        monkeypatch.setenv(name, str(folder))
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        monkeypatch.delenv(name, raising=False)
