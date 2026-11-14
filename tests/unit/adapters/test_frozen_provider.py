"""The demo's frozen offer provider (P8 spec §5.3): fixed bodies, no network, unknown names → `E_NETWORK`."""

import json
from datetime import UTC, datetime

import pytest

from energatron.adapters import wiring
from energatron.adapters.offers.frozen import FrozenProvider
from energatron.core.errors import NetworkError
from energatron.services import demo

NOW = datetime(2026, 11, 14, 1, 2, 3, tzinfo=UTC)


class FixedClock:
    def now(self) -> datetime:
        return NOW


def provider() -> FrozenProvider:
    return wiring.demo_provider(FixedClock(), wiring.load_region_profile("sa"))


def test_the_list_holds_every_demo_plan_and_details_match() -> None:
    region = wiring.load_region_profile("sa")
    plans = demo.demo_plans(region)
    [page] = provider().list_plans(demo.DEMO_BASE)
    listed = json.loads(page.body)["data"]["plans"]
    assert [item["planId"] for item in listed] == [plan["planId"] for plan in plans]
    assert (page.status, page.fetched_at, page.url) == (200, NOW, "demo://demo/plans")
    for plan in plans:
        detail = provider().get_plan(demo.DEMO_BASE, plan["planId"])
        assert json.loads(detail.body)["data"] == plan
        assert detail.request_headers["x-v"] == "3"
    distributors = {plan["geography"]["distributors"][0] for plan in plans}
    assert distributors == {region.distributor, region.gas_distributor}


def test_unknown_bases_and_plans_are_network_errors() -> None:
    with pytest.raises(NetworkError):
        provider().list_plans("elsewhere")
    with pytest.raises(NetworkError):
        provider().get_plan(demo.DEMO_BASE, "NOPE@DEMO")
