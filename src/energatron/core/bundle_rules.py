"""Bundle rules (P6 spec §5.1): dual-fuel deals that CDR data doesn't express in a form the engine can price.

A rule names a brand and, optionally, a plan-ID pattern per fuel (a regular expression matched whole); it applies to a
pair of an electricity and a gas tariff of that brand. Its effect is an annual credit or a percentage off the pair's
combined annual cost, GST-inclusive as annual totals are. Curated rules ship as package data (`bundle_rules/`); a
workspace rule (`config/bundle_rules.local/`) with the same `id` replaces one. Pure: the caller reads and parses the
YAML files and passes each name with its document (`None` for a file that isn't valid YAML).
"""

import re
from collections.abc import Iterable
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, ClassVar, Final, Literal, Self

from pydantic import AfterValidator, model_validator

from energatron.core.canonical import JSONValue
from energatron.core.errors import InvalidInputError, SchemaTooNewError
from energatron.core.models._common import FrozenModel
from energatron.core.models.offers import SnapshotId
from energatron.core.persisted import Persisted

_ID: Final = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


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


def _brand(value: str) -> str:
    if not value.strip():
        raise ValueError("brand: not blank")
    return value


def _credit(value: Decimal) -> Decimal:
    if not value.is_finite() or value <= 0:
        raise ValueError("effect.annual_credit: a decimal above 0")
    return value


def _percent(value: Decimal) -> Decimal:
    if not value.is_finite() or not Decimal(0) < value < Decimal(100):
        raise ValueError("effect.percent_off: a decimal above 0 and below 100")
    return value


Pattern = Annotated[str, AfterValidator(_pattern)]


class BundleEffect(FrozenModel):
    """Exactly one of `annual_credit` (dollars a year) and `percent_off` (of the pair's combined annual cost)."""

    annual_credit: Annotated[Decimal, AfterValidator(_credit)] | None = None
    percent_off: Annotated[Decimal, AfterValidator(_percent)] | None = None

    @model_validator(mode="after")
    def _exactly_one(self) -> Self:
        if (self.annual_credit is None) == (self.percent_off is None):
            raise InvalidInputError("A bundle rule has exactly one effect.", field="effect")
        return self

    def adjustment(self, base: Decimal) -> Decimal:
        """The (negative) change this effect makes to a pair whose combined annual cost is `base`, in cents."""
        if self.annual_credit is not None:
            return -self.annual_credit.quantize(Decimal("0.01"))
        assert self.percent_off is not None
        return -(base * self.percent_off / 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


class BundleRule(Persisted):
    """`bundle_rule` v1: one YAML file per rule (P6 spec §5.1)."""

    KIND: ClassVar[str] = "bundle_rule"
    SCHEMA_VERSION: ClassVar[int] = 1
    kind: Literal["bundle_rule"] = "bundle_rule"
    schema_version: Literal[1] = 1

    id: Annotated[str, AfterValidator(_rule_id)]
    description: str
    brand: Annotated[str, AfterValidator(_brand)]
    electricity_plan: Pattern | None = None
    gas_plan: Pattern | None = None
    effect: BundleEffect
    verified_on: date
    cites: tuple[SnapshotId, ...]

    def matches(self, *, elec_brand: str, elec_plan: str, gas_brand: str, gas_plan: str) -> bool:
        """Both tariffs are of this rule's brand (case-insensitively) and match its plan patterns (null matches any)."""
        brand = self.brand.casefold()
        return (
            elec_brand.casefold() == brand
            and gas_brand.casefold() == brand
            and (self.electricity_plan is None or re.fullmatch(self.electricity_plan, elec_plan) is not None)
            and (self.gas_plan is None or re.fullmatch(self.gas_plan, gas_plan) is not None)
        )


type BundleRuleFile = tuple[str, JSONValue | None]
"""A rule file's name and its parsed YAML document, `None` when it isn't valid YAML."""


def parse_bundle_rules(files: Iterable[BundleRuleFile], *, where: str) -> dict[str, BundleRule]:
    """The rules in `files` by `id`. An invalid file or a repeated `id` → `E_INVALID_INPUT` naming `where` and the
    file, never its content."""
    rules: dict[str, BundleRule] = {}
    for name, document in sorted(files, key=lambda found: found[0]):
        if not isinstance(document, dict):
            raise InvalidInputError("A bundle rule isn't a YAML mapping.", rules=where, file=name)
        try:
            rule = BundleRule.from_document(document)
        except InvalidInputError, SchemaTooNewError:
            raise InvalidInputError("A bundle rule doesn't match bundle_rule v1.", rules=where, file=name) from None
        if rule.id in rules:
            raise InvalidInputError("Two bundle rules share an id.", rules=where, file=name, id=rule.id)
        rules[rule.id] = rule
    return rules


def load_bundle_rules(curated: Iterable[BundleRuleFile], local: Iterable[BundleRuleFile]) -> tuple[BundleRule, ...]:
    """The curated rules with the local ones over them (same `id` replaces), in `id` order."""
    merged = parse_bundle_rules(curated, where="curated") | parse_bundle_rules(local, where="local")
    return tuple(merged[key] for key in sorted(merged))
