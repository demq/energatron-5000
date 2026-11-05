"""Offer rules (P4 spec §6): public facts CDR carries only as free text, applied after `map_cdr`.

A rule matches on the brand, a plan-ID or display-name pattern (a regular expression, matched whole) and a date range
of the snapshot's `lastUpdated`; at least one of brand, plan ID and display name is required. Its effect is one of
`exclude` (the offer becomes excluded with reason `rule:<id>:<reason>`), `approximate` (adds `rule:<id>:<reason>` to
the approximations) or `note`. Curated rules ship as package data; a workspace rule with the same `id` replaces one.
Rules apply in `id` order. Pure: the caller reads and parses the YAML files and passes each name with its document
(`None` for a file that isn't valid YAML).
"""

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Annotated, ClassVar, Final, Literal, Self

from pydantic import AfterValidator, ConfigDict, Field, model_validator

from energatron.core.canonical import JSONValue
from energatron.core.cdr_tariff import Excluded
from energatron.core.errors import InvalidInputError, SchemaTooNewError
from energatron.core.gas_tariff import GasTariff
from energatron.core.models._common import FrozenModel
from energatron.core.models.offers import SnapshotId
from energatron.core.persisted import Persisted
from energatron.core.tariff import Tariff

_ID: Final = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_REASON: Final = re.compile(r"[a-z0-9_]+(?::[A-Za-z0-9_]+)*")


def _rule_id(value: str) -> str:
    if _ID.fullmatch(value) is None:
        raise ValueError("id: kebab-case, lower-case words joined by '-'")
    return value


def _pattern(value: str) -> str:
    try:
        re.compile(value)
    except re.error:
        raise ValueError("a regular expression") from None
    return value


def _reason(value: str) -> str:
    if _REASON.fullmatch(value) is None:
        raise ValueError("a snake_case reason")
    return value


Pattern = Annotated[str, AfterValidator(_pattern)]
Reason = Annotated[str, AfterValidator(_reason)]


class RuleMatch(FrozenModel):
    """What a rule applies to; the file's date keys are `from` and `to` (both inclusive)."""

    model_config = ConfigDict(serialize_by_alias=True, validate_by_name=True)  # merged with FrozenModel's

    brand: str | None = None
    plan_id: Pattern | None = None
    display_name: Pattern | None = None
    start: Annotated[date | None, Field(alias="from")] = None
    end: Annotated[date | None, Field(alias="to")] = None

    @model_validator(mode="after")
    def _names_something(self) -> Self:
        if self.brand is None and self.plan_id is None and self.display_name is None:
            raise InvalidInputError("A rule must match on a brand, a plan ID or a display name.", field="match")
        if self.start is not None and self.end is not None and self.end < self.start:
            raise InvalidInputError("A rule's date range ends before it starts.", field="match")
        return self


class RuleEffect(FrozenModel):
    """Exactly one of `exclude`, `approximate` (fixed snake_case reasons) or `note` (free text)."""

    exclude: Reason | None = None
    approximate: Reason | None = None
    note: str | None = None

    @model_validator(mode="after")
    def _exactly_one(self) -> Self:
        if sum(value is not None for value in (self.exclude, self.approximate, self.note)) != 1:
            raise InvalidInputError("A rule has exactly one effect.", field="effect")
        return self


class OfferRule(Persisted):
    """`offer_rule` v1: one YAML file per rule (P4 spec §6)."""

    KIND: ClassVar[str] = "offer_rule"
    SCHEMA_VERSION: ClassVar[int] = 1
    kind: Literal["offer_rule"] = "offer_rule"
    schema_version: Literal[1] = 1

    id: Annotated[str, AfterValidator(_rule_id)]
    description: str
    match: RuleMatch
    effect: RuleEffect
    verified_on: date
    cites: tuple[SnapshotId, ...]

    def matches(self, *, brand: str, plan_id: str, display_name: str, last_updated: date) -> bool:
        found = self.match
        return (
            (found.brand is None or found.brand == brand)
            and (found.plan_id is None or re.fullmatch(found.plan_id, plan_id) is not None)
            and (found.display_name is None or re.fullmatch(found.display_name, display_name) is not None)
            and (found.start is None or last_updated >= found.start)
            and (found.end is None or last_updated <= found.end)
        )


type RuleFile = tuple[str, JSONValue | None]
"""A rule file's name and its parsed YAML document, `None` when it isn't valid YAML."""


def parse_rules(files: Iterable[RuleFile], *, where: str) -> dict[str, OfferRule]:
    """The rules in `files` by `id`. Any invalid file or a repeated `id` → `E_INVALID_INPUT` naming `where` and the
    file, never its content."""
    rules: dict[str, OfferRule] = {}
    for name, document in sorted(files, key=lambda found: found[0]):
        if document is None:
            raise InvalidInputError("An offer rule isn't valid YAML.", rules=where, file=name)
        if not isinstance(document, dict):
            raise InvalidInputError("An offer rule isn't a YAML mapping.", rules=where, file=name)
        try:
            rule = OfferRule.from_document(document)
        except InvalidInputError, SchemaTooNewError:
            raise InvalidInputError("An offer rule doesn't match offer_rule v1.", rules=where, file=name) from None
        if rule.id in rules:
            raise InvalidInputError("Two offer rules share an id.", rules=where, file=name, id=rule.id)
        rules[rule.id] = rule
    return rules


def load_rules(curated: Iterable[RuleFile], local: Iterable[RuleFile]) -> tuple[OfferRule, ...]:
    """The curated rules with the local ones over them (same `id` replaces), in `id` order."""
    merged = parse_rules(curated, where="curated") | parse_rules(local, where="local")
    return tuple(merged[key] for key in sorted(merged))


@dataclass(frozen=True)
class Ruled:
    """An offer after its rules: the mapped result and the ids of the rules that matched, in order. The rules apply to
    electricity and gas tariffs alike (P5 spec §8)."""

    result: Tariff | GasTariff | Excluded
    rules: tuple[str, ...]


def apply_rules(
    result: Tariff | GasTariff | Excluded,
    rules: Sequence[OfferRule],
    *,
    brand: str,
    plan_id: str,
    display_name: str,
    last_updated: date,
) -> Ruled:
    """Apply every matching rule in order. An excluded offer only gains exclusion reasons and notes."""
    applied: list[str] = []
    for rule in rules:
        if not rule.matches(brand=brand, plan_id=plan_id, display_name=display_name, last_updated=last_updated):
            continue
        applied.append(rule.id)
        effect = rule.effect
        if effect.exclude is not None:
            reason = f"rule:{rule.id}:{effect.exclude}"
            notes = result.notes
            reasons = (*result.reasons, reason) if isinstance(result, Excluded) else (reason,)
            result = Excluded(reasons=tuple(sorted(set(reasons))), notes=notes)
        elif effect.approximate is not None and not isinstance(result, Excluded):
            approximations = tuple(sorted({*result.approximations, f"rule:{rule.id}:{effect.approximate}"}))
            result = result.model_copy(update={"approximations": approximations, "status": "approximate"})
        elif effect.note is not None:
            note = f"rule:{rule.id}"
            if not isinstance(result, Excluded):
                result = result.model_copy(update={"notes": tuple(sorted({*result.notes, note}))})
            else:
                result = Excluded(reasons=result.reasons, notes=tuple(sorted({*result.notes, note})))
    return Ruled(result=result, rules=tuple(applied))
