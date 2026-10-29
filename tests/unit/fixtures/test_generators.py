"""The synthetic identifiers (D3 §12): made-up values, a valid checksum digit and a matching private config."""

from energatron.core.models.private import PrivateConfig
from energatron.core.privacy import nmi_checksum
from tests.fixtures.synthetic import (
    SYNTHETIC_ACCOUNT,
    SYNTHETIC_NMI,
    SYNTHETIC_NMI11,
    SYNTHETIC_SERIAL,
    synthetic_private_config,
)


def test_the_synthetic_nmi_has_no_i_or_o_and_a_pinned_checksum_digit() -> None:
    assert SYNTHETIC_NMI == "ZZTEST0001"
    assert "I" not in SYNTHETIC_NMI and "O" not in SYNTHETIC_NMI
    assert nmi_checksum(SYNTHETIC_NMI) == 6
    assert SYNTHETIC_NMI11 == "ZZTEST00016"


def test_the_private_config_builder_matches_the_identifiers() -> None:
    private = synthetic_private_config()
    assert isinstance(private, PrivateConfig)
    assert private.premises.nmi == SYNTHETIC_NMI11
    assert private.premises.meter_serials == (SYNTHETIC_SERIAL,)
    assert private.accounts.electricity == (SYNTHETIC_ACCOUNT,)
    assert private.accounts.gas == ()


def test_the_builder_takes_overrides() -> None:
    private = synthetic_private_config(meter_serials=(), electricity_accounts=(), gas_accounts=("900000000002",))
    assert private.premises.meter_serials == ()
    assert private.accounts.gas == ("900000000002",)
