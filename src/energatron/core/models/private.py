"""`PrivateConfig`, kind `private_config`: the shape of `private/config.toml` (D2 §6.3).

The values are personal. Every field is declared with `repr=False`, and validation errors never show input, so a
repr, a traceback or an error names fields but never values. `PrivateConfig.from_document` re-raises a validation
failure as `E_INVALID_INPUT`, naming the field and the rule. Validator messages never repeat the value.
"""

import re
from typing import Annotated, ClassVar, Literal

from pydantic import AfterValidator, Field

from energatron.core.ids import parse_offer_snapshot_id
from energatron.core.models._common import FrozenModel
from energatron.core.persisted import Persisted
from energatron.core.privacy import nmi_checksum

_NMI = re.compile(r"[A-Z0-9]{10}[0-9]?")
_SALT = re.compile(r"[0-9a-f]{32}")
_POSTCODE = re.compile(r"[0-9]{4}")
_MIRN = re.compile(r"[0-9]{10,11}")


def _nmi(value: str) -> str:
    nmi = value.upper()
    if _NMI.fullmatch(nmi) is None:
        raise ValueError("an NMI is 10 letters or digits, optionally followed by its checksum digit")
    if len(nmi) == 11 and int(nmi[10]) != nmi_checksum(nmi[:10]):
        raise ValueError("the NMI's last digit isn't its checksum")
    return nmi


def _salt(value: str) -> str:
    if _SALT.fullmatch(value) is None:
        raise ValueError("the salt is 32 lowercase hex characters")
    return value


def _postcode(value: str) -> str:
    if _POSTCODE.fullmatch(value) is None:
        raise ValueError("a postcode is 4 digits")
    return value


def _mirn(value: str) -> str:
    if _MIRN.fullmatch(value) is None:
        raise ValueError("a MIRN is 10 or 11 digits")
    return value


def _trimmed(value: str) -> str:
    if not value or value != value.strip():
        raise ValueError("must be non-empty, with no surrounding whitespace")
    return value


def _street(value: str) -> str:
    if not value.strip():
        raise ValueError("the street must not be blank")
    return value


def _plan(value: str) -> str:
    try:
        parse_offer_snapshot_id(value)
    except ValueError:
        raise ValueError("expected an offer snapshot ID") from None
    return value


Nmi = Annotated[str, AfterValidator(_nmi)]
Trimmed = Annotated[str, AfterValidator(_trimmed)]
OfferSnapshotId = Annotated[str, AfterValidator(_plan)]


class Premises(FrozenModel):
    """`[premises]`: the NMI is required; the rest is optional."""

    nmi: Nmi = Field(repr=False)  # upper-cased; 10 characters, or 11 with a matching checksum digit
    meter_serials: tuple[Trimmed, ...] = Field(default=(), repr=False)
    street: Annotated[str, AfterValidator(_street)] | None = Field(default=None, repr=False)
    postcode: Annotated[str, AfterValidator(_postcode)] | None = Field(default=None, repr=False)
    network_tariff: str | None = Field(default=None, repr=False)  # a public code; optional until P4
    gas_mirn: Annotated[str, AfterValidator(_mirn)] | None = Field(default=None, repr=False)  # optional until P5


class Accounts(FrozenModel):
    """`[accounts]`: retailer account or customer numbers, any number per fuel."""

    electricity: tuple[Trimmed, ...] = Field(default=(), repr=False)
    gas: tuple[Trimmed, ...] = Field(default=(), repr=False)


class CurrentPlans(FrozenModel):
    """`[current_plans]`: the offer snapshot IDs of the plans in force (public IDs)."""

    electricity: OfferSnapshotId | None = Field(default=None, repr=False)  # optional until P4
    gas: OfferSnapshotId | None = Field(default=None, repr=False)  # optional until P5


class PrivateConfig(Persisted):
    """`private/config.toml`. Only `salt` and `premises.nmi` are required.

    A section left out of the file is not in `model_fields_set`, so a service can tell "missing" from "empty".
    """

    KIND: ClassVar[str] = "private_config"
    SCHEMA_VERSION: ClassVar[int] = 1
    kind: Literal["private_config"] = "private_config"
    schema_version: Literal[1] = 1

    salt: Annotated[str, AfterValidator(_salt)] = Field(repr=False)
    premises: Premises = Field(repr=False)
    accounts: Accounts = Field(default_factory=Accounts, repr=False)
    current_plans: CurrentPlans = Field(default_factory=CurrentPlans, repr=False)
