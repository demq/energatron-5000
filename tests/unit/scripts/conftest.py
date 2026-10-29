"""The script tests run git in scratch folders, cut off from the machine's own git configuration."""

import pytest


@pytest.fixture(autouse=True)
def isolated_git(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path_factory.mktemp("git-config")
    gitconfig = home / "gitconfig"
    gitconfig.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(gitconfig))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Scratch")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "scratch@example.invalid")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "Scratch")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "scratch@example.invalid")
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        monkeypatch.delenv(name, raising=False)
