"""`energatron serve` as a real child (P10 spec §7, §8): one result line, pages and the API through the token, a clean
stop on SIGINT, and a workspace left as it was."""

import hashlib
import http.client
import json
import os
import signal
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from tests.unit.services._read_workspace import make_read_workspace

pytestmark = pytest.mark.integration


def snapshot(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def get(port: int, target: str, headers: dict[str, str]) -> tuple[int, dict[str, str], str]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
    try:
        connection.request("GET", target, headers=headers)
        response = connection.getresponse()
        return response.status, {key.lower(): value for key, value in response.getheaders()}, response.read().decode()
    finally:
        connection.close()


def test_serve_answers_through_its_token_and_stops_on_sigint(tmp_path: Path) -> None:
    paths, _ = make_read_workspace(tmp_path)
    before = snapshot(paths.root)
    env = {key: value for key, value in os.environ.items() if not key.startswith("ENERGATRON_")}
    child = subprocess.Popen(
        [sys.executable, "-m", "energatron", "serve", "--port", "0", "--json", "--workspace", str(paths.root)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=tmp_path, env=env,
    )  # fmt: skip
    try:
        assert child.stdout is not None
        first = child.stdout.readline()
        envelope = json.loads(first)
        assert envelope["ok"] and envelope["command"] == "serve"
        data = envelope["data"]
        assert data["kind"] == "serve_result" and data["host"] == "127.0.0.1" and not data["allow_documents"]
        url = urlsplit(data["url"])
        port = data["port"]
        assert url.port == port and url.query.startswith("token=")
        token = url.query.removeprefix("token=")

        status, _, body = get(port, "/api/v1/version", {"Authorization": f"Bearer {token}"})
        assert status == 200 and json.loads(body)["data"]["kind"] == "version_result"
        assert get(port, "/api/v1/version", {})[0] == 401
        assert get(port, "/api/v1/version", {"Host": "evil.example", "Authorization": f"Bearer {token}"})[0] == 400

        status, headers, _ = get(port, f"/?{url.query}", {})
        assert status == 303 and headers["location"] == "/"
        cookie = headers["set-cookie"].split(";", 1)[0]
        status, headers, body = get(port, "/usage", {"Cookie": cookie})
        assert status == 200 and body.startswith("<!doctype html>") and "server" not in headers
    finally:
        child.send_signal(signal.SIGINT)
        out, err = child.communicate(timeout=10)
    assert child.returncode == 0, err
    assert out == "", "stdout held more than the one result line"
    assert token not in err  # no access log
    assert snapshot(paths.root) == before


def test_sigterm_also_ends_it_with_exit_0(tmp_path: Path) -> None:
    paths, _ = make_read_workspace(tmp_path)
    env = {key: value for key, value in os.environ.items() if not key.startswith("ENERGATRON_")}
    child = subprocess.Popen(
        [sys.executable, "-m", "energatron", "serve", "--port", "0", "--workspace", str(paths.root)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=tmp_path, env=env,
    )  # fmt: skip
    assert child.stdout is not None
    assert child.stdout.readline().startswith("Serving ")
    child.send_signal(signal.SIGTERM)
    out, err = child.communicate(timeout=10)
    assert child.returncode == 0 and out == "", err
