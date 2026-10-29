"""A synthetic private config for the privacy tests. Every value here is made up.

`SYNTHETIC_VALUES` lists every personal value the config holds, so a test can check that none of them reaches a
repr, an error or other output.
"""

import copy

from energatron.core.canonical import JSONValue

# A made-up NMI: AEMO's checksum of "QAAAVZZZZZ" is 3 (see test_derive_denylist).
NMI = "QAAAVZZZZZ"
NMI_WITH_CHECKSUM = "QAAAVZZZZZ3"

SYNTHETIC_DOCUMENT: dict[str, JSONValue] = {
    "kind": "private_config",
    "schema_version": 1,
    "salt": "0123456789abcdef0123456789abcdef",
    "premises": {
        "nmi": NMI_WITH_CHECKSUM,
        "meter_serials": ["MSYNTHAB", "Q7X"],
        "street": "1  Synthetic\tStreet",
        "postcode": "0000",
        "network_tariff": "SYNTARIFF",
        "gas_mirn": "55512345678",
    },
    "accounts": {"electricity": ["ELECACCTSYN", "K9"], "gas": ["GASACCTSYN"]},
    "current_plans": {"electricity": "SYN-PLAN-A~20260101T000000Z~0123abcd", "gas": None},
}

# The values that must never show in output. The salt and the public codes aren't personal, but they shouldn't
# show either.
SYNTHETIC_VALUES: tuple[str, ...] = (
    NMI,
    "MSYNTHAB",
    "Q7X",
    "Synthetic",
    "0000",
    "SYNTARIFF",
    "55512345678",
    "ELECACCTSYN",
    "K9",
    "GASACCTSYN",
    "0123456789abcdef0123456789abcdef",
    "SYN-PLAN-A",
)


def synthetic_document() -> dict[str, JSONValue]:
    """A fresh deep copy of the synthetic document, safe to edit."""
    return copy.deepcopy(SYNTHETIC_DOCUMENT)
