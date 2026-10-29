"""`PrivateConfig` validation (D2 §6.3, §14): each rule, and no value in a repr or an error. Synthetic values only."""

from collections.abc import Callable

import pytest
from pydantic import ValidationError

from energatron.core.canonical import JSONValue, canonical_json
from energatron.core.errors import InvalidInputError
from energatron.core.models.private import PrivateConfig
from tests.unit.core._private import NMI, NMI_WITH_CHECKSUM, SYNTHETIC_VALUES, synthetic_document

type Edit = Callable[[dict[str, JSONValue]], object]


def premises(doc: dict[str, JSONValue]) -> dict[str, JSONValue]:
    section = doc["premises"]
    assert isinstance(section, dict)
    return section


def accounts(doc: dict[str, JSONValue]) -> dict[str, JSONValue]:
    section = doc["accounts"]
    assert isinstance(section, dict)
    return section


def plans(doc: dict[str, JSONValue]) -> dict[str, JSONValue]:
    section = doc["current_plans"]
    assert isinstance(section, dict)
    return section


def read(doc: dict[str, JSONValue]) -> PrivateConfig:
    return PrivateConfig.from_document(doc)


def assert_no_value_in(text: str) -> None:
    leaked = [index for index, value in enumerate(SYNTHETIC_VALUES) if value in text]
    assert leaked == [], f"synthetic values at these indexes leaked: {leaked}"


# Accepted documents


def test_the_full_synthetic_document_is_valid() -> None:
    config = read(synthetic_document())
    assert config.premises.nmi == NMI_WITH_CHECKSUM
    assert config.premises.meter_serials == ("MSYNTHAB", "Q7X")
    assert config.accounts.electricity == ("ELECACCTSYN", "K9")
    assert config.current_plans.gas is None


def test_only_salt_and_nmi_are_required() -> None:
    doc: dict[str, JSONValue] = {
        "kind": "private_config",
        "schema_version": 1,
        "salt": "f" * 32,
        "premises": {"nmi": NMI},
    }
    config = read(doc)
    assert config.premises.meter_serials == ()
    assert config.premises.street is None
    assert config.premises.postcode is None
    assert config.premises.network_tariff is None
    assert config.premises.gas_mirn is None
    assert (config.accounts.electricity, config.accounts.gas) == ((), ())
    assert (config.current_plans.electricity, config.current_plans.gas) == (None, None)
    assert "accounts" not in config.model_fields_set  # so a service can tell "missing" from "empty"


def test_the_nmi_is_upper_cased() -> None:
    doc = synthetic_document()
    premises(doc)["nmi"] = NMI_WITH_CHECKSUM.lower()
    assert read(doc).premises.nmi == NMI_WITH_CHECKSUM


def test_a_ten_character_nmi_needs_no_checksum() -> None:
    doc = synthetic_document()
    premises(doc)["nmi"] = NMI
    assert read(doc).premises.nmi == NMI


def test_the_model_is_frozen() -> None:
    config = read(synthetic_document())
    with pytest.raises(ValidationError):
        config.salt = "0" * 32  # pyright: ignore[reportAttributeAccessIssue]
    with pytest.raises(ValidationError):
        config.premises.nmi = NMI  # pyright: ignore[reportAttributeAccessIssue]


# Rejected documents: one case per rule. IDs name the rule, never the value.


def set_premise(field: str, value: JSONValue) -> Edit:
    return lambda doc: premises(doc).__setitem__(field, value)


def set_account(field: str, value: JSONValue) -> Edit:
    return lambda doc: accounts(doc).__setitem__(field, value)


def set_plan(field: str, value: JSONValue) -> Edit:
    return lambda doc: plans(doc).__setitem__(field, value)


def set_top(field: str, value: JSONValue) -> Edit:
    return lambda doc: doc.__setitem__(field, value)


def drop_premise(field: str) -> Edit:
    return lambda doc: premises(doc).pop(field)


REJECTED: list[tuple[str, Edit, str]] = [
    ("nmi wrong checksum digit", set_premise("nmi", NMI + "4"), "premises.nmi"),
    ("nmi too short", set_premise("nmi", NMI[:9]), "premises.nmi"),
    ("nmi too long", set_premise("nmi", NMI_WITH_CHECKSUM + "0"), "premises.nmi"),
    ("nmi letter as checksum", set_premise("nmi", NMI + "A"), "premises.nmi"),
    ("nmi punctuation", set_premise("nmi", "QAAAV-ZZZZ"), "premises.nmi"),
    ("nmi missing", drop_premise("nmi"), "premises.nmi"),
    ("salt upper-case", set_top("salt", "0123456789ABCDEF0123456789ABCDEF"), "salt"),
    ("salt too short", set_top("salt", "0" * 31), "salt"),
    ("salt missing", lambda doc: doc.pop("salt"), "salt"),
    ("postcode three digits", set_premise("postcode", "000"), "premises.postcode"),
    ("postcode letters", set_premise("postcode", "SA00"), "premises.postcode"),
    ("mirn nine digits", set_premise("gas_mirn", "555123456"), "premises.gas_mirn"),
    ("mirn twelve digits", set_premise("gas_mirn", "555123456789"), "premises.gas_mirn"),
    ("mirn letters", set_premise("gas_mirn", "55512345ABC"), "premises.gas_mirn"),
    ("meter serial empty", set_premise("meter_serials", [""]), "premises.meter_serials.0"),
    ("meter serial padded", set_premise("meter_serials", ["MSYNTHAB "]), "premises.meter_serials.0"),
    ("account empty", set_account("electricity", ["ELECACCTSYN", ""]), "accounts.electricity.1"),
    ("account padded", set_account("gas", [" GASACCTSYN"]), "accounts.gas.0"),
    ("plan not a snapshot id", set_plan("electricity", "SYN-PLAN-A"), "current_plans.electricity"),
    ("plan with whitespace", set_plan("gas", "SYN PLAN~20260101T000000Z~0123abcd"), "current_plans.gas"),
    ("unknown premises field", set_premise("suburb", "Synthetic"), "premises.suburb"),
    ("unknown section", set_top("billing", {}), "billing"),
]


@pytest.mark.parametrize(("edit", "location"), [(e, loc) for _, e, loc in REJECTED], ids=[r[0] for r in REJECTED])
def test_each_rule_rejects_with_e_invalid_input_naming_the_field(edit: Edit, location: str) -> None:
    doc = synthetic_document()
    edit(doc)
    with pytest.raises(InvalidInputError) as caught:
        read(doc)
    error = caught.value
    assert error.code == "E_INVALID_INPUT"
    errors = error.details["errors"]
    assert isinstance(errors, list)
    assert location in [problem["loc"] for problem in errors if isinstance(problem, dict)]


@pytest.mark.parametrize(("edit", "location"), [(e, loc) for _, e, loc in REJECTED], ids=[r[0] for r in REJECTED])
def test_a_validation_error_never_holds_a_value(edit: Edit, location: str) -> None:
    doc = synthetic_document()
    edit(doc)
    with pytest.raises(InvalidInputError) as caught:
        read(doc)
    error = caught.value
    assert location in error.message
    assert_no_value_in(error.message)
    assert_no_value_in(canonical_json(error.details).decode("utf-8"))
    assert error.__cause__ is None
    # Validating directly, without from_document, raises pydantic's error: it must hide the values too.
    with pytest.raises(ValidationError) as direct:
        PrivateConfig.model_validate(doc)
    assert_no_value_in(str(direct.value))
    assert_no_value_in(repr(direct.value))
    problems = direct.value.errors(include_url=False, include_context=False, include_input=False)
    assert_no_value_in(repr(problems))


def test_the_wrong_checksum_message_names_the_rule_not_the_digit() -> None:
    doc = synthetic_document()
    premises(doc)["nmi"] = NMI + "4"
    with pytest.raises(ValidationError) as caught:
        PrivateConfig.model_validate(doc)
    text = str(caught.value)
    assert "checksum" in text
    assert NMI not in text


def test_repr_and_str_never_show_a_value() -> None:
    config = read(synthetic_document())
    for text in (repr(config), str(config), repr(config.premises), repr(config.accounts), repr(config.current_plans)):
        assert_no_value_in(text)
    # The values are still there for code that asks for them.
    assert config.premises.postcode == "0000"
