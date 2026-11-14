"""`energatron diagnose` (P8 spec §8): structure only, in a workspace with a landed synthetic file.

The report must hold none of the private values, the landed file's name or its dataset ID, and must still come out
when a check fails (here: a stale denylist).
"""

import json
from datetime import datetime
from pathlib import Path

import pytest

from tests.fixtures.make_nem12 import Nem12Spec, make_nem12, sapn_filename
from tests.fixtures.synthetic import SYNTHETIC_NMI, SYNTHETIC_NMI11, SYNTHETIC_SERIAL
from tests.integration.test_init_cli import init_args
from tests.integration.test_land_cli import WINDOW, envelope

pytestmark = pytest.mark.integration


def test_the_report_holds_structure_not_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for name in ("ENERGATRON_JSON", "ENERGATRON_WORKSPACE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    root = tmp_path / "ws"
    code, made = envelope(capsys, *init_args(root, "--nmi", SYNTHETIC_NMI11, "--meter-serial", SYNTHETIC_SERIAL))
    assert code == 0, made["error"]
    monkeypatch.chdir(root)
    name = sapn_filename("DETAILED", SYNTHETIC_NMI, WINDOW, datetime(2025, 1, 4, 10))
    (root / "data" / "landing" / name).write_bytes(make_nem12(Nem12Spec(start=WINDOW[0], days=3)))
    code, landed = envelope(capsys, "land", "data/landing/")
    assert code == 0, landed["error"]
    dataset_id = landed["data"]["landed"][0]

    code, report = envelope(capsys, "diagnose", "--redacted")
    assert code == 0, report["error"]
    data = report["data"]
    assert data["workspace"]["catalog_records"]["sapn_interval"] == 1
    assert data["workspace"]["template_version"] == 1 and data["workspace"]["private_config"] is True
    assert {item["path"]: item["current"] for item in data["workspace"]["configs"]}["params.yaml"] is True
    assert data["tool"]["git"] and data["problems"] == []
    text = json.dumps(report)
    for secret in (SYNTHETIC_NMI, SYNTHETIC_SERIAL, name, dataset_id, str(tmp_path)):
        assert secret not in text

    config = root / "private" / "config.toml"
    config.write_text(config.read_text(encoding="utf-8") + 'postcode = "0000"\n', encoding="utf-8")
    code, stale = envelope(capsys, "diagnose")
    assert code == 0, stale["error"]
    assert {"check": "denylist", "code": "E_DENYLIST_STALE"} in stale["data"]["problems"]
    assert "0000" not in json.dumps(stale["data"]["workspace"])
