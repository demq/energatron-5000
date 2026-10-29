"""The premises check (D3 §6). Synthetic identifiers only (D3 §12)."""

import json
from collections.abc import Mapping, Sequence

import pytest

from energatron.core.errors import (
    Category,
    EnergatronError,
    ForeignPremisesError,
    InternalError,
    InvalidInputError,
    ResultWarning,
)
from energatron.core.models.catalog import PUBLIC_SOURCES, SourceType
from energatron.core.models.private import PrivateConfig
from energatron.core.privacy import check_premises, nmi_checksum
from tests.fixtures.synthetic import (
    SYNTHETIC_ACCOUNT,
    SYNTHETIC_NMI,
    SYNTHETIC_NMI11,
    SYNTHETIC_SERIAL,
    synthetic_private_config,
)

PRIVATE = synthetic_private_config()
OTHER_NMI = "ZZTEST0002"
OTHER_SERIAL = "SYN0000009"
OTHER_ACCOUNT = "900000000009"
ALL_VALUES = (SYNTHETIC_NMI, SYNTHETIC_SERIAL, SYNTHETIC_ACCOUNT, OTHER_NMI, OTHER_SERIAL, OTHER_ACCOUNT)


def check(
    identifiers: Mapping[str, Sequence[str]],
    private: PrivateConfig = PRIVATE,
    source: SourceType = SourceType.SAPN_INTERVAL,
) -> list[ResultWarning]:
    return check_premises(source, identifiers, private)


def codes(warnings: list[ResultWarning]) -> list[tuple[str, object]]:
    return [(warning.code.value, warning.details.get("identifier")) for warning in warnings]


def assert_no_values(shown: str) -> None:
    for value in ALL_VALUES:
        assert value not in shown


def shown_error(error: EnergatronError) -> str:
    return f"{error.message} {json.dumps(error.details)} {error!r}"


# 1. Public sources


@pytest.mark.parametrize("source", sorted(PUBLIC_SOURCES))
def test_public_sources_are_not_checked(source: SourceType) -> None:
    assert check({"nmi": [OTHER_NMI]}, source=source) == []
    assert check({}, source=source) == []


# 2. The NMI


@pytest.mark.parametrize("nmi", [SYNTHETIC_NMI, SYNTHETIC_NMI11, SYNTHETIC_NMI.lower(), f"  {SYNTHETIC_NMI11}\t"])
def test_a_matching_nmi_confirms_the_premises(nmi: str) -> None:
    assert check({"nmi": [nmi], "meter_serial": [SYNTHETIC_SERIAL]}) == []


def test_premises_with_a_ten_character_nmi_match_an_eleven_character_one() -> None:
    private = synthetic_private_config(nmi=SYNTHETIC_NMI)
    assert check({"nmi": [SYNTHETIC_NMI11]}, private) == []


def test_a_wrong_checksum_digit_is_invalid_input() -> None:
    wrong = f"{SYNTHETIC_NMI}{(nmi_checksum(SYNTHETIC_NMI) + 1) % 10}"
    with pytest.raises(InvalidInputError) as caught:
        check({"nmi": [wrong]})
    assert caught.value.details == {"reason": "nmi_checksum", "identifier": "nmi"}
    assert_no_values(shown_error(caught.value))


def test_a_letter_as_checksum_is_invalid_input() -> None:
    with pytest.raises(InvalidInputError):
        check({"nmi": [f"{SYNTHETIC_NMI}X"]})


@pytest.mark.parametrize(
    "nmis",
    [[OTHER_NMI], [SYNTHETIC_NMI, OTHER_NMI], [f"{OTHER_NMI}{nmi_checksum(OTHER_NMI)}"], ["SHORT"]],
    ids=["other", "one-of-two", "other-with-checksum", "malformed"],
)
def test_any_other_nmi_is_foreign(nmis: list[str]) -> None:
    with pytest.raises(ForeignPremisesError) as caught:
        check({"nmi": nmis, "meter_serial": [SYNTHETIC_SERIAL]})
    assert caught.value.category is Category.PRIVACY
    assert caught.value.details == {"identifier": "nmi"}
    assert_no_values(shown_error(caught.value))


# 3. Unknown serials and accounts


def test_unknown_serials_and_accounts_warn_once_per_kind_when_the_nmi_matches() -> None:
    warnings = check(
        {
            "nmi": [SYNTHETIC_NMI],
            "meter_serial": [OTHER_SERIAL, "SYN0000008", SYNTHETIC_SERIAL],
            "account": [OTHER_ACCOUNT],
        }
    )
    assert codes(warnings) == [("W_UNKNOWN_IDENTIFIER", "meter_serial"), ("W_UNKNOWN_IDENTIFIER", "account")]
    for warning in warnings:
        assert "config.toml" in warning.message and "energatron denylist" in warning.message
        assert_no_values(warning.model_dump_json())


def test_known_values_match_case_insensitively_after_stripping() -> None:
    assert check({"nmi": [SYNTHETIC_NMI], "meter_serial": [f" {SYNTHETIC_SERIAL.lower()} "]}) == []


def test_a_gas_account_is_known_too() -> None:
    private = synthetic_private_config(electricity_accounts=(), gas_accounts=(OTHER_ACCOUNT,))
    assert check({"account": [OTHER_ACCOUNT]}, private) == []


# 4. No NMI in the file


def test_without_an_nmi_a_known_serial_or_account_confirms_the_premises() -> None:
    assert check({"meter_serial": [SYNTHETIC_SERIAL]}) == []
    assert check({"account": [SYNTHETIC_ACCOUNT]}) == []
    assert codes(check({"meter_serial": [SYNTHETIC_SERIAL], "account": [OTHER_ACCOUNT]})) == [
        ("W_UNKNOWN_IDENTIFIER", "account")
    ]


@pytest.mark.parametrize(
    ("identifiers", "kind"),
    [
        ({"meter_serial": [OTHER_SERIAL]}, "meter_serial"),
        ({"account": [OTHER_ACCOUNT]}, "account"),
        ({"meter_serial": [OTHER_SERIAL], "account": [OTHER_ACCOUNT]}, "meter_serial"),
    ],
)
def test_without_an_nmi_nothing_matching_a_known_list_is_foreign(identifiers: dict[str, list[str]], kind: str) -> None:
    with pytest.raises(ForeignPremisesError) as caught:
        check(identifiers)
    assert caught.value.details == {"identifier": kind}
    assert_no_values(shown_error(caught.value))


def test_without_an_nmi_or_identifiers_the_premises_is_unchecked() -> None:
    assert codes(check({})) == [("W_PREMISES_UNCHECKED", None)]
    assert codes(check({"nmi": [], "meter_serial": ["  "]})) == [("W_PREMISES_UNCHECKED", None)]


def test_values_of_a_kind_with_an_empty_private_list_leave_the_premises_unchecked() -> None:
    private = synthetic_private_config(meter_serials=())
    assert codes(check({"meter_serial": [OTHER_SERIAL]}, private)) == [
        ("W_UNKNOWN_IDENTIFIER", "meter_serial"),
        ("W_PREMISES_UNCHECKED", None),
    ]


def test_an_unknown_identifier_kind_is_a_bug() -> None:
    with pytest.raises(InternalError):
        check({"postcode": ["0000"]})
