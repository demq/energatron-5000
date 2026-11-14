"""`energatron serve` on the command line (P10 spec §7): the host and port rules, the one result, the browser."""

import socket
from pathlib import Path
from typing import Any

import pytest

from energatron.adapters.http import server
from tests.unit.adapters._cli import FakeVcsState, run_cli
from tests.unit.adapters._workspace import make_workspace


@pytest.fixture
def ran_server(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Replace uvicorn's loop: record what `run` was given, and close the socket."""
    calls: list[dict[str, Any]] = []

    def fake_run(paths: Any, sock: socket.socket, access: Any, **kwargs: Any) -> None:
        calls.append({"paths": paths, "port": sock.getsockname()[1], "access": access, **kwargs})
        sock.close()

    monkeypatch.setattr(server, "run", fake_run)
    return calls


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.168.1.10", "example.org"])
def test_only_loopback_hosts(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, fake_vcs: FakeVcsState, ran_server: list[Any], host: str
) -> None:
    paths = make_workspace(tmp_path)
    ran = run_cli(capsys, "serve", "--host", host, "--port", "0", "--json", "--workspace", str(paths.root))
    error = ran.envelope()["error"]
    assert ran.code == 2 and error["code"] == "E_USAGE" and error["details"]["parameter"] == "--host"
    assert not ran_server


def test_a_busy_port_is_a_usage_error(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, fake_vcs: FakeVcsState, ran_server: list[Any]
) -> None:
    paths = make_workspace(tmp_path)
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        port = busy.getsockname()[1]
        ran = run_cli(capsys, "serve", "--port", str(port), "--json", "--workspace", str(paths.root))
    error = ran.envelope()["error"]
    assert ran.code == 2 and error["details"]["reason"] == "port_in_use"
    assert not ran_server


def test_outside_a_workspace_exit_2_with_one_envelope(
    capsys: pytest.CaptureFixture[str], ran_server: list[Any]
) -> None:
    ran = run_cli(capsys, "serve", "--port", "0", "--json")
    assert ran.code == 2 and ran.envelope()["error"]["code"] == "E_NOT_A_WORKSPACE"
    assert not ran_server


def test_it_prints_one_result_then_serves(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, fake_vcs: FakeVcsState, ran_server: list[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    opened: list[str] = []
    monkeypatch.setattr("webbrowser.open", opened.append)
    paths = make_workspace(tmp_path)
    ran = run_cli(capsys, "serve", "--port", "0", "--open", "--allow-documents", "--json", "--workspace",
                  str(paths.root))  # fmt: skip
    assert ran.code == 0
    data = ran.envelope()["data"]
    (call,) = ran_server
    token = call["access"].token
    assert len(token) >= 40 and data["url"] == f"http://127.0.0.1:{call['port']}/?token={token}"
    assert data["port"] == call["port"] and data["allow_documents"] and call["allow_documents"]
    assert opened == [data["url"]]


def test_human_output_names_the_address(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, fake_vcs: FakeVcsState, ran_server: list[Any]
) -> None:
    paths = make_workspace(tmp_path)
    ran = run_cli(capsys, "serve", "--host", "localhost", "--port", "0", "--workspace", str(paths.root))
    assert ran.code == 0 and ran.out.startswith("Serving ") and "http://localhost:" in ran.out and "?token=" in ran.out


def test_each_launch_has_its_own_token(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, fake_vcs: FakeVcsState, ran_server: list[Any]
) -> None:
    paths = make_workspace(tmp_path)
    for _ in range(2):
        run_cli(capsys, "serve", "--port", "0", "--json", "--workspace", str(paths.root))
    assert ran_server[0]["access"].token != ran_server[1]["access"].token


def test_the_cli_starts_without_the_web_stack() -> None:
    """A workspace whose lock predates fastapi and uvicorn must still run every other command (P10.3.1's fix)."""
    import subprocess
    import sys

    code = (
        "import sys; import energatron.adapters.cli.app; "
        "print(sorted(m for m in ('fastapi', 'starlette', 'uvicorn', 'energatron.adapters.http') if m in sys.modules))"
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert done.stdout.strip() == "[]"


def test_serve_without_the_web_stack_says_what_to_do(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    fake_vcs: FakeVcsState,
    monkeypatch: pytest.MonkeyPatch,
    ran_server: list[Any],
) -> None:
    import sys

    monkeypatch.setitem(sys.modules, "energatron.adapters.http.server", None)  # importing it now fails
    monkeypatch.delattr("energatron.adapters.http.server", raising=False)
    paths = make_workspace(tmp_path)
    ran = run_cli(capsys, "serve", "--port", "0", "--json", "--workspace", str(paths.root))
    error = ran.envelope()["error"]
    assert ran.code == 2 and error["code"] == "E_USAGE"
    assert error["details"]["reason"] == "web_dependencies_missing" and "uv lock" in error["details"]["hint"]
