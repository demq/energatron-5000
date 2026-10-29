"""`mask_filename()` (D3 §8.2): redact with the denylist, then hide every alphanumeric run with 4+ digits."""

import pytest

from energatron.core.models.private import PrivateConfig
from energatron.core.privacy import derive_denylist, mask_filename
from tests.fixtures.synthetic import SYNTHETIC_NMI, SYNTHETIC_NMI11, synthetic_private_config

DENYLIST = derive_denylist(synthetic_private_config())


@pytest.mark.parametrize(
    ("name", "masked"),
    [
        ("notes.txt", "notes.txt"),
        ("bill_2025.pdf", "bill_<n>.pdf"),
        ("123.csv", "123.csv"),
        ("a1b2c3d4.csv", "<n>.csv"),
        ("v12_x3456y.json", "v12_<n>.json"),
        ("20240101_20241231_20241231103045_SAPN_DETAILED.csv", "<n>_<n>_<n>_SAPN_DETAILED.csv"),
        ("Usage-Export 2025-10.csv", "Usage-Export <n>-10.csv"),
        ("", ""),
    ],
)
def test_runs_with_four_or_more_digits_become_n(name: str, masked: str) -> None:
    assert mask_filename(name, ()) == masked


def test_without_a_denylist_an_nmi_still_loses_its_digits() -> None:
    name = f"{SYNTHETIC_NMI11}_20240101_20241231_20241231103045_SAPN_DETAILED.csv"
    assert mask_filename(name, ()) == "<n>_<n>_<n>_<n>_SAPN_DETAILED.csv"


def test_the_denylist_is_applied_first() -> None:
    name = f"{SYNTHETIC_NMI11}_20240101_SAPN_DETAILED.csv"
    assert mask_filename(name, DENYLIST) == "<redacted>_<n>_SAPN_DETAILED.csv"
    assert mask_filename(f"x{SYNTHETIC_NMI.lower()}.csv", DENYLIST) == "x<redacted>.csv"


def test_a_denylisted_word_without_digits_is_redacted() -> None:
    private = PrivateConfig.from_document(
        {
            "kind": "private_config",
            "schema_version": 1,
            "salt": "0123456789abcdef0123456789abcdef",
            "premises": {"nmi": SYNTHETIC_NMI11, "meter_serials": ["SYNSERIALX"]},
        }
    )
    assert mask_filename("meter_SYNSERIALX.csv", derive_denylist(private)) == "meter_<redacted>.csv"
