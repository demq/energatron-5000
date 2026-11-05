"""A fixture workspace's offer zone (P4 spec §14): landed CDR plans, fetch logs, a manual offer and the local rules
directory, as `fetch-offers` and `land` would have left them. Every plan, brand and price is made up.

`build_offer_zone(root)` lands, through `pipeline_workspace.land_blob` (the real parsers' `inspect`):
- base `alpha`: a TOU plan (an older and a newer snapshot), a single-rate plan, and a controlled-load variant that
  `normalise_offers` excludes; base `beta-energy`: one single-rate plan of brand `beta`
- two fetch logs: the older also lists `GONE@EME`, which the newer no longer does, so it isn't current
- one `offer_manual` TOU offer written with GST
- gas (P5): base `alpha` has two current gas plans (one season with daily blocks; a winter and a summer season), which
  `normalise_offers` ignores and `normalise_gas_offers` prices, and one gas `offer_manual` written without GST
- `config/offer_rules.local/README.md` and no local rule
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from energatron.core.models.catalog import SourceType
from tests.fixtures import make_cdr as cdr
from tests.fixtures.pipeline_workspace import Dataset, land_blob

TOU_ID: Final = "TEST-TOU@EME"
FLAT_ID: Final = "TEST-FLAT@EME"
CL_ID: Final = "TEST-CL@EME"
BETA_ID: Final = "TEST-B@EME"
GONE_ID: Final = "GONE@EME"
MANUAL_ID: Final = "MANUAL-TEST-TOU"
CURRENT_CDR: Final = (BETA_ID, CL_ID, FLAT_ID, TOU_ID)
GAS_ID: Final = "TEST-GAS@EME"
GAS_SEASONS_ID: Final = "TEST-GAS-SEASONS@EME"
GAS_MANUAL_ID: Final = "MANUAL-TEST-GAS"
CURRENT_GAS: Final = (GAS_SEASONS_ID, GAS_ID)


def gas_plan() -> dict[str, Any]:
    return cdr.gas_plan(
        GAS_ID, discounts=[cdr.discount("GUARANTEED", "percentOfBill", category=None, rate_or_amount="0.10")]
    )


def gas_seasons_plan() -> dict[str, Any]:
    winter = cdr.gas_period(supply="0.90", start="06-01", end="09-30")
    summer = cdr.gas_period(supply="0.70", start="10-01", end="05-31")
    return cdr.gas_plan(GAS_SEASONS_ID, periods=[winter, summer])


def tou_plan(*, last_updated: str = "2026-10-01T00:00:00Z", peak: str = "0.40") -> dict[str, Any]:
    return cdr.plan(
        TOU_ID,
        last_updated=last_updated,
        periods=[cdr.tou_period(cdr.standard_tou(peak=peak), supply="1.00")],
        discounts=[cdr.discount("CONDITIONAL", "percentOfBill", category="PAY_ON_TIME", rate_or_amount="0.05")],
    )


def flat_plan() -> dict[str, Any]:
    blocks = [cdr.rate("0.25", volume=10), cdr.rate("0.35")]
    return cdr.plan(
        FLAT_ID,
        pricing_model="SINGLE_RATE",
        periods=[cdr.single_period(blocks, supply="0.90", period="P1D")],
        fees=[cdr.fee("MEMBERSHIP", "ANNUAL", "60.00")],
    )


def cl_plan() -> dict[str, Any]:
    return cdr.plan(CL_ID, pricing_model="TIME_OF_USE_CONT_LOAD")


def beta_plan() -> dict[str, Any]:
    return cdr.plan(BETA_ID, brand="beta", pricing_model="SINGLE_RATE", periods=[cdr.single_period([cdr.rate("0.30")])])


@dataclass(frozen=True)
class OfferZone:
    root: Path
    plans: dict[str, Dataset]  # the newest snapshot of each landed plan
    logs: tuple[Dataset, Dataset]  # older, newer
    manual: Dataset
    gas_plans: dict[str, Dataset]
    gas_manual: Dataset


def _selected(plan_id: str, base: str, data: dict[str, Any]) -> dict[str, Any]:
    return cdr.selected(plan_id, base=base, last_updated=data["lastUpdated"], fuel_type=data["fuelType"])


def build_offer_zone(root: Path) -> OfferZone:
    """Land the offer zone under `root`, beside a raw zone `build_raw_zone` or `empty_raw_zone` made."""
    tou_old = tou_plan(last_updated="2026-09-01T00:00:00Z", peak="0.45")
    tou, flat, cl, beta = tou_plan(), flat_plan(), cl_plan(), beta_plan()
    gas, gas_seasons = gas_plan(), gas_seasons_plan()
    gone = cdr.plan(GONE_ID, last_updated="2026-08-01T00:00:00Z")
    land_blob(root, SourceType.OFFER_CDR, f"alpha__{TOU_ID}.json", cdr.detail_body(tou_old))
    land_blob(root, SourceType.OFFER_CDR, f"alpha__{GONE_ID}.json", cdr.detail_body(gone))
    plans = {
        plan["planId"]: land_blob(root, SourceType.OFFER_CDR, f"{base}__{plan['planId']}.json", cdr.detail_body(plan))
        for base, plan in (("alpha", tou), ("alpha", flat), ("alpha", cl), ("beta-energy", beta))
    }
    gas_plans = {
        plan["planId"]: land_blob(root, SourceType.OFFER_CDR, f"alpha__{plan['planId']}.json", cdr.detail_body(plan))
        for plan in (gas, gas_seasons)
    }
    older = cdr.fetch_log(
        batch="fetch-20260901T000000Z",
        fetched_at="2026-09-01T00:00:00Z",
        retailers=[("alpha", [tou_old, gone])],
        selected=[_selected(TOU_ID, "alpha", tou_old), _selected(GONE_ID, "alpha", gone)],
    )
    newer = cdr.fetch_log(
        batch="fetch-20261101T000000Z",
        fetched_at="2026-11-01T00:00:00Z",
        retailers=[("alpha", [tou, flat, cl, gas, gas_seasons]), ("beta-energy", [beta])],
        selected=[
            _selected(TOU_ID, "alpha", tou),
            _selected(FLAT_ID, "alpha", flat),
            _selected(CL_ID, "alpha", cl),
            _selected(GAS_ID, "alpha", gas),
            _selected(GAS_SEASONS_ID, "alpha", gas_seasons),
            _selected(BETA_ID, "beta-energy", beta),
        ],
        gas_distributor=cdr.GAS_DISTRIBUTOR,
    )
    logs = (
        land_blob(root, SourceType.OFFER_CDR_INDEX, "fetch-log-1.json", cdr.fetch_log_body(older)),
        land_blob(root, SourceType.OFFER_CDR_INDEX, "fetch-log-2.json", cdr.fetch_log_body(newer)),
    )
    contract = cdr.plan(pricing_model="TIME_OF_USE", periods=[cdr.tou_period(supply="1.10")])["electricityContract"]
    manual_text = cdr.offer_manual_text(MANUAL_ID, prices_include_gst=True, contract=contract)
    manual = land_blob(root, SourceType.OFFER_MANUAL, "manual.yaml", manual_text.encode("utf-8"))
    gas_contract = cdr.gas_plan(periods=[cdr.gas_period(supply="0.85")])["gasContract"]
    gas_text = cdr.offer_manual_text(GAS_MANUAL_ID, gas_contract=gas_contract)
    gas_manual = land_blob(root, SourceType.OFFER_MANUAL, "manual-gas.yaml", gas_text.encode("utf-8"))
    rules = root / "config" / "offer_rules.local"
    rules.mkdir(parents=True, exist_ok=True)
    (rules / "README.md").write_text("Local offer rules (none).\n", encoding="utf-8")
    return OfferZone(root=root, plans=plans, logs=logs, manual=manual, gas_plans=gas_plans, gas_manual=gas_manual)
