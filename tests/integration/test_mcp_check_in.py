"""A monthly check-in through the MCP tools only (P9 spec §7.1, §8; plan §15's P9 acceptance), on the demo workspace.

A scripted client drives `energatron mcp` over pipes: status, the inbox, one more synthetic gas bill (the demo's
generators, the month after its last bill) landed and transcribed through the record tools, a run, then the read
tools. `fetch_offers` is left out: tests use no network, and the demo's frozen offers are already landed. The run is a
draft because the development checkout may be dirty (the tool children can't be told otherwise); in CI it is clean.
Every answer is checked for the demo's identifiers, raw file names, interval-table keys and `private/` paths.
"""

import json
import os
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

from energatron.core.workspace_template import SKILLS
from energatron.services import demo
from tests.integration.test_demo import clean_tool as clean_tool
from tests.integration.test_init_cli import init_args
from tests.integration.test_land_cli import envelope

pytestmark = pytest.mark.integration

FORBIDDEN = (
    demo.DEMO_NMI, demo.DEMO_SERIAL, demo.DEMO_ACCOUNT, "SAPN_DETAILED", "demo_usage_export", "demo_gas_bill_2025",
    "meter_start", "utc_start", "local_start", "private/",
)  # fmt: skip


class Client:
    """One `energatron mcp` child, one request at a time."""

    def __init__(self, root: Path) -> None:
        self.process = subprocess.Popen(
            [sys.executable, "-m", "energatron", "mcp", "--workspace", str(root)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, cwd=root,
        )  # fmt: skip
        self.next_id = 0
        self.answers: list[str] = []

    def send(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.next_id += 1
        message = {"jsonrpc": "2.0", "id": self.next_id, "method": method, "params": params or {}}
        assert self.process.stdin is not None and self.process.stdout is not None
        self.process.stdin.write(json.dumps(message) + "\n")
        self.process.stdin.flush()
        line = self.process.stdout.readline()
        self.answers.append(line)
        answer = json.loads(line)
        assert answer["id"] == self.next_id
        return answer

    def tool(self, name: str, ok: bool = True, **arguments: Any) -> dict[str, Any]:
        result = self.send("tools/call", {"name": name, "arguments": arguments})["result"]
        envelope = result["structuredContent"]
        assert result["isError"] is (not ok), envelope["error"]
        return envelope

    def close(self) -> int:
        assert self.process.stdin is not None
        self.process.stdin.close()
        return self.process.wait(timeout=60)


@pytest.mark.usefixtures("clean_tool")
def test_a_monthly_check_in_using_only_the_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    root = tmp_path / "demo"
    code, made = envelope(capsys, *init_args(root, "--demo"))
    assert code == 0, made["error"]
    before = made["data"]["demo"]["recommendation_id"]
    assert json.loads((root / ".mcp.json").read_text())["mcpServers"]["energatron"]["args"][-1] == "mcp"
    for name in SKILLS:
        assert (root / ".agents/skills" / name / "SKILL.md").is_file()
    periods = demo.gas_periods()
    start = periods[-1][1] + timedelta(days=1)
    end = date(start.year, start.month + 1, 1) - timedelta(days=1)
    (root / "data/landing").mkdir(exist_ok=True)
    (root / "data/landing/new_bill.pdf").write_bytes(demo.gas_bill_pdf(end + timedelta(days=4)))
    index = 1000 + sum(demo._GAS_VOLUMES[first.month] for first, _ in periods)  # pyright: ignore[reportPrivateUsage]

    client = Client(root)
    try:
        assert (
            client.send("initialize", {"protocolVersion": "2025-06-18"})["result"]["serverInfo"]["name"] == "energatron"
        )
        assert client.tool("status")["data"]["kind"] == "status_result"
        inbox = client.tool("list_inbox")["data"]
        assert (inbox["new"], [item["status"] for item in inbox["files"]]) == (1, ["new"])
        bill: dict[str, Any] = {"path": "data/landing/new_bill.pdf", "source": "gas_bill"}
        assert client.tool("land_file", **bill, dry_run=True)["data"]["dry_run"] is True
        (pdf_id,) = client.tool("land_file", **bill)["data"]["landed"]

        written = client.tool("new_gas_bill_record", pdf_dataset_id=pdf_id, extracted_by="agent:test-model")
        assert written["data"]["path"].endswith(".yaml")
        record = demo.gas_bill_record((start, end), index, pdf_id) | {"extracted_by": "agent:test-model"}
        wrong = client.tool("check_gas_bill_record", ok=False, record=record | {"total": "1.00"})
        assert wrong["error"]["code"] == "E_CHECK_FAILED" and wrong["error"]["details"]["failed"] == [
            "totals_recompute"
        ]
        assert all(client.tool("check_gas_bill_record", record=record)["data"]["checks"].values())
        saved = client.tool("write_gas_bill_record", record=record, force=True)["data"]
        assert saved["written"] is True
        assert client.tool("land_file", path=saved["path"])["data"]["landed"]

        ran = client.tool("run_pipeline", draft=True)["data"]
        assert ran["recommendation_id"]
        assert client.tool("offers_diff")["data"]["kind"] == "offer_diff_result"
        explained = client.tool("explain_recommendation")["data"]
        assert explained["recommendation_id"] == ran["recommendation_id"] and explained["run"]["draft"] is True
        assert explained["behind"]["datasets_not_in_run"] == 0
        assert client.tool("compare_offers", limit=3)["data"]["plans"]["electricity"]["shown"] <= 3
        usage = client.tool("usage_summary", months=2)["data"]
        assert usage["gas"]["months"][-1]["month"] == f"{start:%Y-%m}"
        newest = client.tool("list_datasets", source="gas_bill_record", limit=1)["data"]["datasets"][0]
        assert newest["extracted_by"] == "agent:test-model"
        assert client.tool("status")["data"]["last_run"]["run_id"] == ran["run_id"]
    finally:
        assert client.close() == 0
    assert before  # the demo's own run came first

    transcript = "".join(client.answers)
    for forbidden in FORBIDDEN:
        assert forbidden not in transcript, forbidden
    assert os.path.exists(root / "private/config.toml")  # it is there; no answer showed it
