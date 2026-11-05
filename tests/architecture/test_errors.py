"""The error model (D1 §7, §13): every code is defined once in `core/errors.py`, in exactly one category.

The expected table is D1 §7's, plus the codes and warnings D2 adds (D2 §1 change 7) and P1's (D1 §7, D3 §11).
A phase that adds codes adds them here too.
"""

import builtins
import re

import pytest
from pydantic import ValidationError

from energatron.core import errors
from energatron.core.errors import (
    EXIT_CODES,
    Category,
    EnergatronError,
    NotAWorkspaceError,
    ResultWarning,
    WarningCode,
)
from tests.architecture._support import import_all_modules

EXPECTED_TABLE: dict[Category, tuple[int, frozenset[str]]] = {
    Category.INTERNAL: (1, frozenset({"E_INTERNAL"})),
    Category.USAGE: (2, frozenset({"E_USAGE", "E_NOT_A_WORKSPACE", "E_CONFIRMATION_REQUIRED"})),
    Category.INPUT: (3, frozenset({"E_UNKNOWN_SOURCE", "E_AMBIGUOUS_SOURCE", "E_INVALID_INPUT"})),
    Category.STATE: (
        4,
        frozenset(
            {
                "E_LOCKED",
                "E_DIRTY",
                "E_ID_COLLISION",
                "E_BLOB_EXISTS",
                "E_CATALOG_CORRUPT",
                "E_SCHEMA_TOO_NEW",
                "E_FINGERPRINT_STALE",
                "E_PIPELINE_STALE",
                "E_PRIVATE_CONFIG_MISSING",
                "E_COMMIT_REJECTED",
                "E_BACKUP_NOT_CONFIGURED",  # D2
                "E_DENYLIST_STALE",  # D2
                "E_PIPELINE_NOT_SYNCED",  # D3
            }
        ),
    ),
    Category.PRIVACY: (5, frozenset({"E_FOREIGN_PREMISES", "E_DENYLIST_HIT"})),
    Category.EXTERNAL: (6, frozenset({"E_SUBPROCESS", "E_NETWORK", "E_STORAGE", "E_STAGE_FAILED"})),
    Category.CHECK: (7, frozenset({"E_CHECK_FAILED"})),
    Category.PARTIAL: (8, frozenset({"E_PARTIAL"})),
}
EXPECTED_WARNINGS = frozenset(
    {
        "W_PRIVATE_FIELDS_MISSING",  # D2
        "W_BACKUP_DATALESS",  # D2
        "W_FINGERPRINT_METADATA",  # D1 §11.3
        "W_BACKUP_PENDING",  # D1 §4.4, D3 §11
        "W_PREMISES_UNCHECKED",  # D3 §6
        "W_UNKNOWN_IDENTIFIER",  # D3 §6
        "W_MERGE_SAME_UPDATE",  # P2 spec §7.2, §12
        "W_METER_GAPS",  # P2 spec §8, §12
        "W_METER_UNVALIDATED",  # P2 spec §8, §12
        "W_TIMEBASE_UNCONFIRMED",  # P2 spec §9.4, §12
        "W_TIMEBASE_UNEXPLAINED",  # P2 spec §9.4, §12
        "W_USAGE_MIXED_WINDOWS",  # P3 spec §5.6, §6, §11
        "W_USAGE_PARTIAL_DAYS",  # P3 spec §8, §11
        "W_FETCH_FAILED",  # P4 spec §3.2, §12
        "W_OFFERS_MISSING_SNAPSHOT",  # P4 spec §8, §12
        "W_BACKTEST_OUTSIDE_TOLERANCE",  # P4 spec §10, §12
        "W_BACKTEST_NO_TARIFF",  # P4 spec §10, §12
        "W_GAS_GAP",  # P5 spec §7, §11
        "W_GAS_PARTIAL_YEAR",  # P5 spec §9.3, §11
    }
)


def error_classes() -> list[type[EnergatronError]]:
    """Every `EnergatronError` subclass defined anywhere in the package."""
    import_all_modules()
    found: list[type[EnergatronError]] = []
    pending = [EnergatronError]
    while pending:
        for subclass in pending.pop().__subclasses__():
            pending.append(subclass)
            if subclass.__module__.startswith("energatron."):
                found.append(subclass)
    return found


def test_every_error_is_defined_in_core_errors() -> None:
    assert {cls.__module__ for cls in error_classes()} == {"energatron.core.errors"}


def test_codes_are_unique_and_well_formed() -> None:
    codes = [cls.code for cls in error_classes()]
    assert len(codes) == len(set(codes))
    assert [code for code in codes if not re.fullmatch(r"E_[A-Z_]+", code)] == []


def test_each_code_has_one_category() -> None:
    assert [cls.code for cls in error_classes() if not isinstance(cls.category, Category)] == []


def test_class_names_follow_their_codes() -> None:
    def class_name(code: str) -> str:
        return "".join(part.capitalize() for part in code.removeprefix("E_").split("_")) + "Error"

    assert [cls.__name__ for cls in error_classes() if cls.__name__ != class_name(cls.code)] == []


def test_exit_codes_and_categories_match_the_table() -> None:
    assert dict(EXIT_CODES) == {category: exit_code for category, (exit_code, _) in EXPECTED_TABLE.items()}
    by_category: dict[Category, set[str]] = {category: set() for category in Category}
    for cls in error_classes():
        by_category[cls.category].add(cls.code)
    assert by_category == {category: set(codes) for category, (_, codes) in EXPECTED_TABLE.items()}


def test_an_error_carries_its_message_details_and_exit_code() -> None:
    error = NotAWorkspaceError("No energatron workspace here or above.", searched="current directory")
    assert str(error) == error.message == "No energatron workspace here or above."
    assert error.details == {"searched": "current directory"}
    assert (error.code, error.category, error.exit_code) == ("E_NOT_A_WORKSPACE", Category.USAGE, 2)


def test_the_base_error_cannot_be_raised_without_a_code() -> None:
    with pytest.raises(TypeError):
        EnergatronError("No code.")


def test_warning_codes_are_unique_and_well_formed() -> None:
    assert {code.value for code in WarningCode} == EXPECTED_WARNINGS
    assert all(code.name == code.value and re.fullmatch(r"W_[A-Z_]+", code.value) for code in WarningCode)


def test_a_result_warning_dumps_code_message_and_details() -> None:
    warning = ResultWarning(code=WarningCode.W_BACKUP_DATALESS, message="2 files are dataless.", details={"count": 2})
    assert warning.model_dump(mode="json") == {
        "code": "W_BACKUP_DATALESS",
        "message": "2 files are dataless.",
        "details": {"count": 2},
    }
    assert ResultWarning(code=WarningCode.W_PRIVATE_FIELDS_MISSING, message="m").details == {}


def test_a_result_warning_takes_only_known_codes_and_is_frozen() -> None:
    with pytest.raises(ValidationError):
        ResultWarning.model_validate({"code": "W_NOT_DEFINED", "message": "m"})
    with pytest.raises(ValidationError):
        ResultWarning.model_validate({"code": "W_BACKUP_DATALESS", "message": "m", "extra": 1})
    warning = ResultWarning(code=WarningCode.W_BACKUP_DATALESS, message="m")
    with pytest.raises(ValidationError):
        warning.message = "changed"


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_a_result_warning_refuses_nan_and_infinity_in_its_details(bad: float) -> None:
    with pytest.raises(ValidationError):
        ResultWarning.model_validate({"code": "W_BACKUP_DATALESS", "message": "m", "details": {"share": bad}})


def test_the_warning_model_does_not_shadow_the_builtin() -> None:
    assert getattr(errors, "Warning", builtins.Warning) is builtins.Warning
