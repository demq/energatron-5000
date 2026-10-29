"""Persisted documents (D1 §5, §13): kinds, versions, migrations and golden documents.

The first part checks the real package: every `Persisted` model has a unique `KIND`, every version above 1 has
a complete migration chain, and every kind's golden `tests/fixtures/schemas/<kind>/v1.json` upgrades and
validates. The second part checks the machinery on synthetic kinds, in a registry that each test gets empty and
that is restored afterwards.
"""

import json
from pathlib import Path
from typing import ClassVar, Literal

import pytest
from pydantic import ConfigDict, ValidationError

from energatron.core import persisted
from energatron.core.canonical import JSONValue, canonical_json
from energatron.core.errors import Category, InternalError, InvalidInputError, SchemaTooNewError
from energatron.core.persisted import (
    Migration,
    Persisted,
    migration,
    registered_kinds,
    registered_migrations,
    upgrade,
)
from tests.architecture._support import import_all_modules

SCHEMAS_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "schemas"


def package_models() -> list[type[Persisted]]:
    """Every `Persisted` subclass defined in the package."""
    import_all_modules()
    found: list[type[Persisted]] = []
    pending: list[type[Persisted]] = [Persisted]
    while pending:
        for subclass in pending.pop().__subclasses__():
            pending.append(subclass)
            if subclass.__module__.startswith("energatron."):
                found.append(subclass)
    return found


def package_kinds() -> dict[str, type[Persisted]]:
    """The registered kinds whose models are defined in the package."""
    import_all_modules()
    return {kind: model for kind, model in registered_kinds().items() if model.__module__.startswith("energatron.")}


# The real package


def test_every_model_is_registered_under_its_own_kind() -> None:
    concrete = [model for model in package_models() if "KIND" in vars(model)]
    kinds = [model.KIND for model in concrete]
    assert len(kinds) == len(set(kinds))
    assert {model.KIND: model for model in concrete} == package_kinds()


def test_every_version_above_one_has_a_complete_migration_chain() -> None:
    kinds = package_kinds()
    migrations = registered_migrations().items()
    steps = {step for step, function in migrations if function.__module__.startswith("energatron.")}
    assert steps == {(kind, version) for kind, model in kinds.items() for version in range(1, model.SCHEMA_VERSION)}


def test_golden_v1_documents_upgrade_and_validate() -> None:
    kinds = package_kinds()
    golden_dirs: set[str] = set()
    if SCHEMAS_DIR.is_dir():
        golden_dirs = {path.name for path in SCHEMAS_DIR.iterdir() if path.is_dir()}
    assert golden_dirs == set(kinds), "every kind needs a golden v1.json, and every golden directory a kind"
    for kind, model in sorted(kinds.items()):
        document = json.loads((SCHEMAS_DIR / kind / "v1.json").read_text(encoding="utf-8"))
        assert (document["kind"], document["schema_version"]) == (kind, 1)
        model.from_document(document)


# The machinery, on synthetic kinds


@pytest.fixture
def registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty kind and migration registry for one test; monkeypatch restores the real one afterwards."""
    monkeypatch.setattr(persisted, "_KINDS", {})
    monkeypatch.setattr(persisted, "_MIGRATIONS", {})


def add_size(doc: dict[str, JSONValue]) -> dict[str, JSONValue]:
    return {**doc, "schema_version": 2, "size": 0}


def add_colour(doc: dict[str, JSONValue]) -> dict[str, JSONValue]:
    return {**doc, "schema_version": 3, "colour": "grey"}


def skip_a_version(doc: dict[str, JSONValue]) -> dict[str, JSONValue]:
    return {**doc, "schema_version": 3}


def change_the_kind(doc: dict[str, JSONValue]) -> dict[str, JSONValue]:
    return {**doc, "kind": "gadget", "schema_version": 2}


def keep_the_version(doc: dict[str, JSONValue]) -> dict[str, JSONValue]:
    return dict(doc)


def define_widget_v1() -> type[Persisted]:
    class Widget(Persisted):
        KIND: ClassVar[str] = "widget"
        SCHEMA_VERSION: ClassVar[int] = 1
        kind: Literal["widget"] = "widget"
        schema_version: Literal[1] = 1
        name: str

    return Widget


def define_widget_v3(steps: tuple[Migration | None, Migration | None] = (add_size, add_colour)) -> type[Persisted]:
    """Kind `widget` at version 3, with its two migration steps; None leaves a step out."""

    class Widget(Persisted):
        KIND: ClassVar[str] = "widget"
        SCHEMA_VERSION: ClassVar[int] = 3
        kind: Literal["widget"] = "widget"
        schema_version: Literal[3] = 3
        name: str
        size: int
        colour: str

    for from_version, step in enumerate(steps, start=1):
        if step is not None:
            migration("widget", from_version=from_version)(step)
    return Widget


@pytest.mark.usefixtures("registry")
class TestRegistration:
    def test_defining_a_model_registers_its_kind(self) -> None:
        model = define_widget_v1()
        assert registered_kinds() == {"widget": model}

    def test_a_kind_can_be_registered_only_once(self) -> None:
        define_widget_v1()
        with pytest.raises(TypeError, match="widget"):
            define_widget_v1()

    def test_a_base_without_a_kind_is_not_registered(self) -> None:
        class Base(Persisted):
            pass

        assert issubclass(Base, Persisted)
        assert registered_kinds() == {}

    def test_kind_must_match_the_kind_field(self) -> None:
        with pytest.raises(TypeError, match="kind"):

            class WrongDefault(Persisted):
                KIND: ClassVar[str] = "widget"
                SCHEMA_VERSION: ClassVar[int] = 1
                kind: Literal["widget", "gadget"] = "gadget"
                schema_version: Literal[1] = 1

        with pytest.raises(TypeError, match="kind"):

            class NotLiteral(Persisted):
                KIND: ClassVar[str] = "widget"
                SCHEMA_VERSION: ClassVar[int] = 1
                kind: str = "widget"
                schema_version: Literal[1] = 1

        with pytest.raises(TypeError, match="kind"):

            class NoKindField(Persisted):
                KIND: ClassVar[str] = "widget"
                SCHEMA_VERSION: ClassVar[int] = 1
                schema_version: Literal[1] = 1

        assert registered_kinds() == {}

    def test_schema_version_must_match_the_schema_version_field(self) -> None:
        with pytest.raises(TypeError, match="schema_version"):

            class WrongVersion(Persisted):
                KIND: ClassVar[str] = "widget"
                SCHEMA_VERSION: ClassVar[int] = 2
                kind: Literal["widget"] = "widget"
                schema_version: Literal[1] = 1

        with pytest.raises(TypeError, match="schema_version"):

            class NoVersionField(Persisted):
                KIND: ClassVar[str] = "widget"
                SCHEMA_VERSION: ClassVar[int] = 1
                kind: Literal["widget"] = "widget"

        assert registered_kinds() == {}

    def test_kind_is_snake_case(self) -> None:
        with pytest.raises(TypeError, match="snake_case"):

            class Capitalised(Persisted):
                KIND: ClassVar[str] = "Widget"
                SCHEMA_VERSION: ClassVar[int] = 1
                kind: Literal["Widget"] = "Widget"
                schema_version: Literal[1] = 1

        with pytest.raises(TypeError, match="snake_case"):

            class Hyphenated(Persisted):
                KIND: ClassVar[str] = "widget-set"
                SCHEMA_VERSION: ClassVar[int] = 1
                kind: Literal["widget-set"] = "widget-set"
                schema_version: Literal[1] = 1

    def test_schema_version_starts_at_one(self) -> None:
        with pytest.raises(TypeError, match="SCHEMA_VERSION"):

            class VersionZero(Persisted):
                KIND: ClassVar[str] = "widget"
                SCHEMA_VERSION: ClassVar[int] = 0
                kind: Literal["widget"] = "widget"
                schema_version: Literal[0] = 0

    def test_models_stay_frozen_and_forbid_extra_fields(self) -> None:
        with pytest.raises(TypeError, match="frozen"):

            class Mutable(Persisted):
                model_config = ConfigDict(frozen=False)
                KIND: ClassVar[str] = "mutable"
                SCHEMA_VERSION: ClassVar[int] = 1
                kind: Literal["mutable"] = "mutable"
                schema_version: Literal[1] = 1

        with pytest.raises(TypeError, match="extra"):

            class Open(Persisted):
                model_config = ConfigDict(extra="allow")
                KIND: ClassVar[str] = "open"
                SCHEMA_VERSION: ClassVar[int] = 1
                kind: Literal["open"] = "open"
                schema_version: Literal[1] = 1

    def test_a_migration_step_is_registered_once_from_a_positive_version(self) -> None:
        define_widget_v3()
        assert set(registered_migrations()) == {("widget", 1), ("widget", 2)}
        with pytest.raises(TypeError, match="widget"):
            migration("widget", from_version=1)(add_size)
        with pytest.raises(TypeError, match="from_version"):
            migration("widget", from_version=0)(add_size)


@pytest.mark.usefixtures("registry")
class TestUpgrade:
    def test_a_current_document_comes_back_as_an_equal_copy(self) -> None:
        define_widget_v1()
        document: dict[str, JSONValue] = {"kind": "widget", "schema_version": 1, "name": "a"}
        result = upgrade(document)
        assert result == document
        assert result is not document

    def test_an_older_document_runs_the_chain_and_the_input_is_untouched(self) -> None:
        define_widget_v3()
        document: dict[str, JSONValue] = {"kind": "widget", "schema_version": 1, "name": "a", "tags": ["x"]}
        before = json.loads(json.dumps(document))
        assert upgrade(document) == {
            "kind": "widget",
            "schema_version": 3,
            "name": "a",
            "tags": ["x"],
            "size": 0,
            "colour": "grey",
        }
        assert document == before

    def test_a_newer_document_is_refused(self) -> None:
        define_widget_v1()
        with pytest.raises(SchemaTooNewError) as caught:
            upgrade({"kind": "widget", "schema_version": 2, "name": "a"})
        assert (caught.value.code, caught.value.category) == ("E_SCHEMA_TOO_NEW", Category.STATE)
        assert caught.value.details == {"kind": "widget", "schema_version": 2, "supported_version": 1}

    @pytest.mark.parametrize(
        "document",
        [
            {},
            {"schema_version": 1},
            {"kind": 7, "schema_version": 1},
            {"kind": "gadget", "schema_version": 1},
            {"kind": "widget"},
            {"kind": "widget", "schema_version": 0},
            {"kind": "widget", "schema_version": "1"},
            {"kind": "widget", "schema_version": True},
            {"kind": "widget", "schema_version": 1.0},
        ],
    )
    def test_a_document_needs_a_known_kind_and_a_valid_version(self, document: dict[str, JSONValue]) -> None:
        define_widget_v1()
        with pytest.raises(InvalidInputError):
            upgrade(document)

    def test_a_missing_step_is_a_bug(self) -> None:
        define_widget_v3((add_size, None))
        with pytest.raises(InternalError):
            upgrade({"kind": "widget", "schema_version": 1, "name": "a"})

    @pytest.mark.parametrize("bad_step", [skip_a_version, change_the_kind, keep_the_version])
    def test_a_step_must_advance_one_version_and_keep_the_kind(self, bad_step: Migration) -> None:
        define_widget_v3((bad_step, add_colour))
        with pytest.raises(InternalError):
            upgrade({"kind": "widget", "schema_version": 1, "name": "a"})


@pytest.mark.usefixtures("registry")
class TestFromDocument:
    def test_validates_the_upgraded_document(self) -> None:
        model = define_widget_v3()
        widget = model.from_document({"kind": "widget", "schema_version": 1, "name": "a"})
        assert widget.model_dump(mode="json") == {
            "kind": "widget",
            "schema_version": 3,
            "name": "a",
            "size": 0,
            "colour": "grey",
        }

    def test_refuses_a_document_of_another_kind(self) -> None:
        model = define_widget_v1()

        class Gadget(Persisted):
            KIND: ClassVar[str] = "gadget"
            SCHEMA_VERSION: ClassVar[int] = 1
            kind: Literal["gadget"] = "gadget"
            schema_version: Literal[1] = 1

        with pytest.raises(InvalidInputError):
            model.from_document(Gadget().model_dump(mode="json"))

    def test_extra_fields_are_refused(self) -> None:
        model = define_widget_v1()
        with pytest.raises(InvalidInputError) as caught:
            model.from_document({"kind": "widget", "schema_version": 1, "name": "a", "size": 1})
        assert caught.value.details["errors"] == [{"loc": "size", "type": "extra_forbidden"}]

    def test_a_validation_error_names_fields_never_values(self) -> None:
        secret = "SYNTHETIC-SECRET-41"

        class Account(Persisted):
            KIND: ClassVar[str] = "account"
            SCHEMA_VERSION: ClassVar[int] = 1
            kind: Literal["account"] = "account"
            schema_version: Literal[1] = 1
            number: int
            labels: list[int]

        with pytest.raises(InvalidInputError) as caught:
            Account.from_document({"kind": "account", "schema_version": 1, "number": secret, "labels": [1, secret]})
        error = caught.value
        assert error.details == {
            "kind": "account",
            "errors": [{"loc": "number", "type": "int_parsing"}, {"loc": "labels.1", "type": "int_parsing"}],
        }
        assert secret not in str(error)
        assert secret.encode() not in canonical_json(error.details)
        assert error.__cause__ is None
        assert error.__suppress_context__

    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
    def test_nan_and_infinity_are_refused_on_read(self, bad: float) -> None:
        """`canonical_json` can't write them back (D1 §6.1), so reading refuses them, nested JSON included."""

        class Reading(Persisted):
            KIND: ClassVar[str] = "reading"
            SCHEMA_VERSION: ClassVar[int] = 1
            kind: Literal["reading"] = "reading"
            schema_version: Literal[1] = 1
            ratio: float
            extra: JSONValue

        with pytest.raises(InvalidInputError) as caught:
            Reading.from_document({"kind": "reading", "schema_version": 1, "ratio": bad, "extra": 1})
        assert caught.value.details["errors"] == [{"loc": "ratio", "type": "finite_number"}]
        with pytest.raises(InvalidInputError):
            Reading.from_document({"kind": "reading", "schema_version": 1, "ratio": 1.0, "extra": {"a": [bad]}})

    def test_models_are_frozen_and_dump_kind_and_version(self) -> None:
        model = define_widget_v1()
        widget = model.from_document({"kind": "widget", "schema_version": 1, "name": "a"})
        with pytest.raises(ValidationError):
            widget.name = "b"  # pyright: ignore[reportAttributeAccessIssue]
        assert model.model_validate({"name": "a"}).model_dump(mode="json") == {
            "kind": "widget",
            "schema_version": 1,
            "name": "a",
        }
