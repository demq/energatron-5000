"""What `normalise_offers` and `normalise_gas_offers` share (P4 spec §8, P5 spec §8): one implementation, parametrised
by fuel.

Each stage reads three raw directories whole (`offer_cdr`, `offer_cdr_index`, `offer_manual`), the workspace's local
offer rules and the curated rules in package data. For each CDR base, the plans the latest fetch log selected (with
that base's list fetched without an error) are current; a stage keeps those of its fuel (the fetch log records each
selected plan's fuel; electricity takes every plan that isn't gas) and the manual offers holding its contract. Each
current plan's newest landed snapshot is mapped with the fuel's mapper, then the rules apply (`core.offer_rules`). Every
considered offer is one row of `offers.parquet`; the priced ones are in `tariffs.json`. Public offer data only. A
stage may not import another stage (D1 §11.1), so the two stage modules share this one.
"""

import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Final, Literal, cast

import pandas as pd
import yaml

from energatron.core.canonical import JSONValue, sha256_hex
from energatron.core.cdr_gas_tariff import map_cdr_gas
from energatron.core.cdr_tariff import Excluded, Household, Plan, map_cdr
from energatron.core.errors import InvalidInputError, SchemaTooNewError, WarningCode
from energatron.core.gas_tariff import GasTariff
from energatron.core.ids import offer_snapshot_id
from energatron.core.models.catalog import CATALOG_FILENAME, CatalogRecord, SourceType, parse_catalog, raw_key
from energatron.core.models.costs import (
    OFFER_STATUS_KIND,
    OFFER_STATUS_SORT,
    OFFER_STATUS_VERSION,
    OFFERS_FILENAME,
    OFFERS_SUMMARY_FILENAME,
    TARIFFS_FILENAME,
    GasOfferTariffs,
    OffersSummary,
    OfferStatus,
    OfferTariffs,
    RetailerCounts,
)
from energatron.core.models.offers import OfferFetchLog, OfferManual
from energatron.core.models.params import OffersParams
from energatron.core.offer_rules import OfferRule, RuleFile, apply_rules, load_rules
from energatron.core.tariff import Tariff
from energatron.services.files import write_json, write_parquet
from energatron.services.stages.runner import StageRun
from energatron.services.stages.tables import OFFER_STATUS_SCHEMA

type Fuel = Literal["ELECTRICITY", "GAS"]
type Priced = Tariff | GasTariff
type Mapper = Callable[[Mapping[str, JSONValue], Plan, Household, date], Priced | Excluded]


@dataclass(frozen=True)
class FuelConfig:
    """What differs between the two offer stages."""

    fuel: Fuel
    contract_key: str  # the CDR contract object: `electricityContract` or `gasContract`
    map: Mapper
    tariffs_document: Callable[[date | None, tuple[Priced, ...]], JSONValue]

    def takes(self, fuel_type: str) -> bool:
        """Whether a selected plan of `fuel_type` is this stage's (electricity takes every fuel but gas)."""
        return fuel_type == "GAS" if self.fuel == "GAS" else fuel_type != "GAS"


def _electricity_tariffs(as_of: date | None, tariffs: tuple[Priced, ...]) -> JSONValue:
    found = tuple(tariff for tariff in tariffs if isinstance(tariff, Tariff))
    return cast(JSONValue, OfferTariffs(as_of=as_of, tariffs=found).model_dump(mode="json"))


def _gas_tariffs(as_of: date | None, tariffs: tuple[Priced, ...]) -> JSONValue:
    found = tuple(tariff for tariff in tariffs if isinstance(tariff, GasTariff))
    return cast(JSONValue, GasOfferTariffs(as_of=as_of, tariffs=found).model_dump(mode="json"))


ELECTRICITY: Final = FuelConfig(
    fuel="ELECTRICITY",
    contract_key="electricityContract",
    map=lambda data, plan, household, as_of: map_cdr(data, plan, household, as_of=as_of),
    tariffs_document=_electricity_tariffs,
)
GAS: Final = FuelConfig(
    fuel="GAS",
    contract_key="gasContract",
    map=lambda data, plan, household, as_of: map_cdr_gas(data, plan, household, as_of=as_of),
    tariffs_document=_gas_tariffs,
)

KNOWN_DETECTOR_VERSIONS: Final = {
    SourceType.OFFER_CDR: frozenset({1}),
    SourceType.OFFER_CDR_INDEX: frozenset({1}),
    SourceType.OFFER_MANUAL: frozenset({1}),
}
RULES_PACKAGE_DATA: Final = "offer_rules"
MANUAL_RETAILER: Final = "manual"


@dataclass
class _Findings:
    mismatched: list[str] = field(default_factory=lambda: list[str]())
    unknown_detector: list[str] = field(default_factory=lambda: list[str]())
    not_read: dict[str, str] = field(default_factory=lambda: dict[str, str]())
    snapshot_mismatch: list[str] = field(default_factory=lambda: list[str]())
    rules_error: dict[str, JSONValue] | None = None


@dataclass(frozen=True)
class _Row:
    """One considered offer."""

    snapshot: str
    plan_id: str
    retailer: str
    brand: str
    display_name: str
    source: str
    pricing_model: str
    result: Priced | Excluded
    rules: tuple[str, ...]

    @property
    def status(self) -> OfferStatus:
        return "excluded" if isinstance(self.result, Excluded) else self.result.status


def normalise(run: StageRun, config: FuelConfig) -> None:
    """The body of `normalise_offers` (electricity) or `normalise_gas_offers` (gas)."""
    offers = OffersParams.model_validate(run.params["offers"])
    gst_rate = Decimal(str(run.params["pricing.gst_rate"]))
    household = Household(unmet_eligibility=frozenset(offers.unmet_eligibility), controlled_load=offers.controlled_load)
    findings = _Findings()
    cdr = _datasets(run.input("cdr"), SourceType.OFFER_CDR, findings)
    logs = _logs(_datasets(run.input("index"), SourceType.OFFER_CDR_INDEX, findings), findings)
    manual = _datasets(run.input("manual"), SourceType.OFFER_MANUAL, findings)
    rules = _rules(run, findings)

    latest = _latest_logs(logs)
    current = sorted(
        {
            (base, plan.plan_id)
            for base, log in latest.items()
            for plan in log.selected
            if plan.base == base and config.takes(plan.fuel_type)
        }
    )
    as_of = _as_of(latest, manual)
    snapshots = _newest_snapshots(cdr, findings)
    rows: list[_Row] = []
    missing = 0
    for base, plan_id in current:
        found = snapshots.get(plan_id)
        if found is None:
            missing += 1
            continue
        record, data = found
        assert as_of is not None  # a fetch log exists, so the date does
        rows.append(_cdr_row(config, record, data, base, household, rules, as_of))
    manual_of_fuel = 0
    for record, data in manual:
        row = _manual_row(config, record, data, household, rules, gst_rate, findings)
        if row == "other_fuel":
            continue
        manual_of_fuel += 1
        if row is not None:
            rows.append(row)
    if missing:
        run.warn(
            WarningCode.W_OFFERS_MISSING_SNAPSHOT,
            "Some plans the latest fetch log lists have no landed snapshot.",
            plans=missing,
        )
    tariffs = sorted(
        (row.result for row in rows if not isinstance(row.result, Excluded)), key=lambda t: t.offer_snapshot_id
    )
    output = run.output("offers")
    write_parquet(
        _frame(rows),
        output / OFFERS_FILENAME,
        kind=OFFER_STATUS_KIND,
        schema_version=OFFER_STATUS_VERSION,
        sort_by=OFFER_STATUS_SORT,
        schema=OFFER_STATUS_SCHEMA,
    )
    write_json(output / TARIFFS_FILENAME, config.tariffs_document(as_of, tuple(tariffs)))

    checks = {
        "blobs_match_catalog": not findings.mismatched,
        "detector_known": not findings.unknown_detector,
        "snapshot_ids_recompute": not findings.snapshot_mismatch,
        "rules_valid": findings.rules_error is None,
        "every_offer_accounted": _accounted(rows, len(current) - missing, manual_of_fuel, findings),
        "tariffs_partition": _partition_holds(tariffs),
    }
    summary = _summary(
        rows, as_of, latest, current_plans=len(current), missing=missing, manual=manual_of_fuel, checks=checks
    )
    write_json(output / OFFERS_SUMMARY_FILENAME, cast(JSONValue, summary.model_dump(mode="json")))
    run.check("blobs_match_catalog", checks["blobs_match_catalog"], mismatched=list(findings.mismatched))
    run.check("detector_known", checks["detector_known"], unknown=list(findings.unknown_detector))
    run.check("snapshot_ids_recompute", checks["snapshot_ids_recompute"], mismatched=list(findings.snapshot_mismatch))
    run.check("rules_valid", checks["rules_valid"], **(findings.rules_error or {}))
    run.check(
        "every_offer_accounted", checks["every_offer_accounted"], rows=len(rows), not_read=dict(findings.not_read)
    )
    run.check("tariffs_partition", checks["tariffs_partition"], tariffs=len(tariffs))


# Raw datasets


def _datasets(raw: Path, source: SourceType, findings: _Findings) -> list[tuple[CatalogRecord, bytes]]:
    """Each catalogued dataset whose blob matches its record and whose detector is known, in catalog order."""
    try:
        catalog = (raw / CATALOG_FILENAME).read_bytes()
    except OSError:
        raise InvalidInputError("A raw directory has no readable catalog.", input=source.value) from None
    found: list[tuple[CatalogRecord, bytes]] = []
    for record in parse_catalog(catalog, source):
        data = _blob(raw, record)
        if data is None or sha256_hex(data) != record.sha256:
            findings.mismatched.append(record.dataset_id)
            findings.not_read[record.dataset_id] = "blob_mismatch"
            continue
        if record.detector.name != source or record.detector.version not in KNOWN_DETECTOR_VERSIONS[source]:
            findings.unknown_detector.append(record.dataset_id)
            findings.not_read[record.dataset_id] = "detector_unknown"
            continue
        found.append((record, data))
    return found


def _blob(raw: Path, record: CatalogRecord) -> bytes | None:
    relative = record.stored_path.removeprefix(raw_key(record.source))
    try:
        return raw.joinpath(*relative.split("/")).read_bytes()
    except OSError:
        return None


def _logs(datasets: Iterable[tuple[CatalogRecord, bytes]], findings: _Findings) -> list[OfferFetchLog]:
    logs: list[OfferFetchLog] = []
    for record, data in datasets:
        try:
            logs.append(OfferFetchLog.from_document(json.loads(data)))
        except ValueError, InvalidInputError, SchemaTooNewError:
            findings.not_read[record.dataset_id] = "invalid_fetch_log"
    return logs


def _latest_logs(logs: Iterable[OfferFetchLog]) -> dict[str, OfferFetchLog]:
    """For each base, the newest log whose list of that base was fetched without an error."""
    latest: dict[str, OfferFetchLog] = {}
    for log in sorted(logs, key=lambda found: (found.fetched_at, found.fetch_batch_id)):
        for retailer in log.retailers:
            if retailer.error is None:
                latest[retailer.base] = log
    return latest


def _as_of(latest: dict[str, OfferFetchLog], manual: list[tuple[CatalogRecord, bytes]]) -> date | None:
    if latest:
        return max(log.fetched_at for log in latest.values()).date()
    if manual:
        return max(record.source_ts for record, _ in manual).date()
    return None


def _newest_snapshots(
    cdr: Iterable[tuple[CatalogRecord, bytes]], findings: _Findings
) -> dict[str, tuple[CatalogRecord, dict[str, JSONValue]]]:
    """Each plan's newest snapshot (by `lastUpdated`, then `landed_at`) whose recorded snapshot ID recomputes."""
    newest: dict[str, tuple[CatalogRecord, dict[str, JSONValue]]] = {}
    for record, blob in cdr:
        try:
            body = json.loads(blob)
            data = cast(dict[str, JSONValue], body["data"])
            plan_id = cast(str, data["planId"])
            snapshot = offer_snapshot_id(plan_id, datetime.fromisoformat(cast(str, data["lastUpdated"]).upper()), data)
        except KeyError, TypeError, ValueError, RecursionError:
            findings.not_read[record.dataset_id] = "unreadable_plan"
            continue
        if snapshot != record.extra.get("offer_snapshot_id"):
            findings.snapshot_mismatch.append(record.dataset_id)
            continue
        key = (record.source_ts, record.landed_at)
        held = newest.get(plan_id)
        if held is None or key > (held[0].source_ts, held[0].landed_at):
            newest[plan_id] = (record, data)
    return newest


# Mapping


def _cdr_row(
    config: FuelConfig,
    record: CatalogRecord,
    data: dict[str, JSONValue],
    base: str,
    household: Household,
    rules: tuple[OfferRule, ...],
    as_of: date,
) -> _Row:
    snapshot = cast(str, record.extra["offer_snapshot_id"])
    mapped = config.map(data, Plan(offer_snapshot_id=snapshot, retailer=base), household, as_of)
    return _ruled(config, snapshot, data, base, "cdr", mapped, rules, record.source_ts.date())


def _manual_row(
    config: FuelConfig,
    record: CatalogRecord,
    blob: bytes,
    household: Household,
    rules: tuple[OfferRule, ...],
    gst_rate: Decimal,
    findings: _Findings,
) -> _Row | Literal["other_fuel"] | None:
    """The manual offer's row; `other_fuel` when it holds the other fuel's contract; `None` when it can't be read."""
    try:
        manual = OfferManual.from_document(yaml.safe_load(blob.decode("utf-8")))
        snapshot = offer_snapshot_id(manual.plan_id, manual.published, yaml.safe_load(blob.decode("utf-8")))
    except ValueError, yaml.YAMLError, InvalidInputError, SchemaTooNewError, RecursionError:
        findings.not_read[record.dataset_id] = "invalid_offer_manual"
        return None
    if manual.fuel != config.fuel:
        return "other_fuel"
    if snapshot != record.extra.get("offer_snapshot_id"):
        findings.snapshot_mismatch.append(record.dataset_id)
        return None
    plan = Plan(
        offer_snapshot_id=snapshot,
        retailer=MANUAL_RETAILER,
        source="manual",
        prices_include_gst=manual.prices_include_gst,
        gst_rate=gst_rate,
    )
    in_force = manual.effective_from or manual.published.date()  # a manual offer is priced over its own dates
    data = manual.as_cdr_data()
    mapped = config.map(data, plan, household, in_force)
    return _ruled(config, snapshot, data, MANUAL_RETAILER, "manual", mapped, rules, manual.published.date())


def _ruled(
    config: FuelConfig,
    snapshot: str,
    data: dict[str, JSONValue],
    retailer: str,
    source: str,
    mapped: Priced | Excluded,
    rules: tuple[OfferRule, ...],
    last_updated: date,
) -> _Row:
    brand = _text(data.get("brand")).strip()
    display_name = _text(data.get("displayName"))
    plan_id = _text(data.get("planId"))
    ruled = apply_rules(
        mapped, rules, brand=brand, plan_id=plan_id, display_name=display_name, last_updated=last_updated
    )
    contract = data.get(config.contract_key)
    pricing_model = _text(contract.get("pricingModel")) if isinstance(contract, dict) else ""
    return _Row(
        snapshot=snapshot,
        plan_id=plan_id,
        retailer=retailer,
        brand=brand,
        display_name=display_name,
        source=source,
        pricing_model=pricing_model,
        result=ruled.result,
        rules=ruled.rules,
    )


def _text(value: JSONValue | None) -> str:
    return value if isinstance(value, str) else ""


def _rules(run: StageRun, findings: _Findings) -> tuple[OfferRule, ...]:
    try:
        return load_rules(_rule_files(run.package_data(RULES_PACKAGE_DATA)), _rule_files(run.input("rules")))
    except InvalidInputError as error:
        findings.rules_error = dict(error.details)
        return ()


def _rule_files(directory: Path) -> list[RuleFile]:
    """Each `*.yaml` file directly in `directory`, by name, with its parsed document (`None` when not YAML)."""
    files: list[RuleFile] = []
    for path in sorted(directory.glob("*.yaml")):
        try:
            files.append((path.name, cast(JSONValue, yaml.safe_load(path.read_text(encoding="utf-8")))))
        except UnicodeDecodeError, yaml.YAMLError:
            files.append((path.name, None))
    return files


# Outputs


def _frame(rows: list[_Row]) -> pd.DataFrame:
    records: list[dict[str, object]] = []
    for row in rows:
        result = row.result
        records.append(
            {
                "offer_snapshot_id": row.snapshot,
                "plan_id": row.plan_id,
                "retailer": row.retailer,
                "brand": row.brand,
                "display_name": row.display_name,
                "source": row.source,
                "pricing_model": row.pricing_model,
                "status": row.status,
                "reasons": list(result.reasons) if isinstance(result, Excluded) else [],
                "approximations": [] if isinstance(result, Excluded) else list(result.approximations),
                "notes": list(result.notes),
                "rules": list(row.rules),
            }
        )
    return pd.DataFrame(records, columns=list(OFFER_STATUS_SCHEMA.names))


def _accounted(rows: list[_Row], current: int, manual: int, findings: _Findings) -> bool:
    manual_not_read = sum(reason == "invalid_offer_manual" for reason in findings.not_read.values())
    excluded_have_reasons = all(row.result.reasons for row in rows if isinstance(row.result, Excluded))
    unique = len({row.snapshot for row in rows}) == len(rows)
    return len(rows) == current + manual - manual_not_read and excluded_have_reasons and unique


def _partition_holds(tariffs: Iterable[Priced]) -> bool:
    try:
        for tariff in tariffs:
            type(tariff).model_validate(tariff.model_dump())
    except ValueError, InvalidInputError:
        return False
    return True


def _summary(
    rows: list[_Row],
    as_of: date | None,
    latest: dict[str, OfferFetchLog],
    *,
    current_plans: int,
    missing: int,
    manual: int,
    checks: dict[str, bool],
) -> OffersSummary:
    statuses: dict[OfferStatus, int] = {"normalised": 0, "approximate": 0, "excluded": 0}
    reasons: dict[str, int] = {}
    retailers: dict[str, dict[str, int]] = {}
    for row in rows:
        statuses[row.status] += 1
        counts = retailers.setdefault(row.retailer, {"normalised": 0, "approximate": 0, "excluded": 0})
        counts[row.status] += 1
        if isinstance(row.result, Excluded):
            for reason in row.result.reasons:
                reasons[reason] = reasons.get(reason, 0) + 1
    return OffersSummary(
        as_of=as_of,
        statuses=statuses,
        reasons=dict(sorted(reasons.items())),
        retailers={base: RetailerCounts(**counts) for base, counts in sorted(retailers.items())},
        current_plans=current_plans,
        missing_snapshot=missing,
        manual=manual,
        batches={base: log.fetch_batch_id for base, log in sorted(latest.items())},
        checks=checks,
    )
