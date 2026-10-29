"""Persisted documents: the `Persisted` base model, the kind registry and migrations (D1 §5).

Every document energatron writes as one of its own models contains `kind` and `schema_version`. A model
subclasses `Persisted` and declares both twice, as class variables and as `Literal` fields, so that a document of
another kind or version fails validation:

    class WorkspaceConfig(Persisted):
        KIND: ClassVar[str] = "workspace_config"
        SCHEMA_VERSION: ClassVar[int] = 1
        kind: Literal["workspace_config"] = "workspace_config"
        schema_version: Literal[1] = 1
        ...

Defining the class registers its kind, and the two declarations must agree. Any change to a persisted model bumps
its `SCHEMA_VERSION` and adds a `@migration` from the previous version. A document is read with
`Model.from_document(doc)`, which upgrades an older version in memory, then validates.
"""

import copy
import re
from collections.abc import Callable, Mapping
from types import MappingProxyType
from typing import Any, ClassVar, Literal, Self, get_args, get_origin

from pydantic import BaseModel, ConfigDict, ValidationError

from energatron.core.canonical import JSONValue
from energatron.core.errors import InternalError, InvalidInputError, SchemaTooNewError

type Migration = Callable[[dict[str, JSONValue]], dict[str, JSONValue]]
"""A pure function from a document at version n to the same document at version n + 1."""

_SNAKE_CASE = re.compile(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*")
_KINDS: dict[str, type[Persisted]] = {}
_MIGRATIONS: dict[tuple[str, int], Migration] = {}


class Persisted(BaseModel):
    """Base of every model energatron persists: frozen, and an unknown field is an error.

    Validation errors never show input values (`hide_input_in_errors`): documents such as catalog records hold
    original filenames, which can start with the NMI. NaN and infinities are refused (`allow_inf_nan=False`):
    `canonical_json` can't write them (D1 §6.1), so they fail on read as `E_INVALID_INPUT`.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True, allow_inf_nan=False)

    KIND: ClassVar[str]
    SCHEMA_VERSION: ClassVar[int]

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: Any) -> None:
        super().__pydantic_init_subclass__(**kwargs)
        if "KIND" in vars(cls):  # a base class without a kind of its own isn't registered
            _register(cls)

    @classmethod
    def from_document(cls, doc: Mapping[str, JSONValue]) -> Self:
        """Read a document of this kind: upgrade it to the current version, then validate it.

        Raises `InvalidInputError` for a document of another kind, or one that fails validation; the error names
        the fields and the kind of problem, never a value. Raises `SchemaTooNewError` for a version newer than
        this model's.
        """
        if doc.get("kind") != cls.KIND:
            raise InvalidInputError(f"Expected a {cls.KIND} document.", kind=cls.KIND)
        upgraded = upgrade(doc)
        try:
            return cls.model_validate(upgraded)
        except ValidationError as error:
            problems = [
                {"loc": ".".join(str(part) for part in problem["loc"]), "type": problem["type"]}
                for problem in error.errors(include_url=False, include_context=False, include_input=False)
            ]
            fields = ", ".join(f"{problem['loc']} ({problem['type']})" for problem in problems)
            raise InvalidInputError(
                f"The {cls.KIND} document is invalid: {fields}.", kind=cls.KIND, errors=problems
            ) from None  # pydantic's own error would repeat the values


def migration(kind: str, *, from_version: int) -> Callable[[Migration], Migration]:
    """Register the decorated function as the step that upgrades `kind` documents from `from_version`.

    The step is a pure function that returns the document at the next version, for example:

        @migration("catalog_record", from_version=1)
        def _catalog_record_v1_to_v2(doc: dict[str, JSONValue]) -> dict[str, JSONValue]:
            ...  # returns the document with schema_version == 2
    """
    if type(from_version) is not int or from_version < 1:
        raise TypeError("from_version must be an int of at least 1")

    def register(step: Migration) -> Migration:
        if (kind, from_version) in _MIGRATIONS:
            raise TypeError(f"kind {kind!r} already has a migration from version {from_version}")
        _MIGRATIONS[kind, from_version] = step
        return step

    return register


def upgrade(doc: Mapping[str, JSONValue]) -> dict[str, JSONValue]:
    """Bring a document up to its kind's current version, in memory.

    A current document comes back as a copy. An older one runs through the migration chain, on a deep copy, so
    the input is never changed. Raises `InvalidInputError` for a document without a known kind or a valid
    version, and `SchemaTooNewError` for a version newer than this energatron's.
    """
    kind = doc.get("kind")
    if not isinstance(kind, str) or kind not in _KINDS:
        raise InvalidInputError("The document has no kind that energatron knows.")
    version = doc.get("schema_version")
    if type(version) is not int or version < 1:
        raise InvalidInputError(f"The {kind} document has no valid schema_version.", kind=kind)
    current = _KINDS[kind].SCHEMA_VERSION
    if version > current:
        raise SchemaTooNewError(
            f"The {kind} document was written by a newer energatron; upgrade the tool.",
            kind=kind,
            schema_version=version,
            supported_version=current,
        )
    upgraded = dict(doc) if version == current else copy.deepcopy(dict(doc))
    while version < current:
        step = _MIGRATIONS.get((kind, version))
        if step is None:
            raise InternalError(f"No migration for {kind} documents from version {version}.", kind=kind)
        upgraded = step(upgraded)
        version += 1
        if upgraded.get("kind") != kind or upgraded.get("schema_version") != version:
            raise InternalError(f"The {kind} migration to version {version} returned another kind or version.")
    return upgraded


def registered_kinds() -> Mapping[str, type[Persisted]]:
    """Every registered kind with its model, as a read-only view."""
    return MappingProxyType(_KINDS)


def registered_migrations() -> Mapping[tuple[str, int], Migration]:
    """Every registered migration step, keyed by `(kind, from_version)`, as a read-only view."""
    return MappingProxyType(_MIGRATIONS)


def _register(model: type[Persisted]) -> None:
    name = model.__qualname__
    kind, version = model.KIND, model.SCHEMA_VERSION
    if _SNAKE_CASE.fullmatch(kind) is None:
        raise TypeError(f"{name}.KIND must be snake_case")
    if type(version) is not int or version < 1:
        raise TypeError(f"{name}.SCHEMA_VERSION must be an int of at least 1")
    _check_literal_field(model, "kind", kind)
    _check_literal_field(model, "schema_version", version)
    if model.model_config.get("frozen") is not True or model.model_config.get("extra") != "forbid":
        raise TypeError(f"{name} must stay frozen and forbid extra fields")
    if kind in _KINDS:
        raise TypeError(f"kind {kind!r} is already registered by {_KINDS[kind].__qualname__}")
    _KINDS[kind] = model


def _check_literal_field(model: type[Persisted], field: str, value: str | int) -> None:
    """The field must be declared `<field>: Literal[<value>] = <value>`, so it agrees with the class variable."""
    info = model.model_fields.get(field)
    if (
        info is None
        or get_origin(info.annotation) is not Literal
        or get_args(info.annotation) != (value,)
        or type(info.default) is not type(value)
        or info.default != value
    ):
        raise TypeError(f"{model.__qualname__}.{field} must be declared as `{field}: Literal[{value!r}] = {value!r}`")
