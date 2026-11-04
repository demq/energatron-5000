# P4 offers and electricity engine spec

- **Created:** 2026-11-04
- **Status:** Approved 2026-11-04 (owner, `/done`, given by the owner's standing instruction to review and accept each
  step), with the recommendations for Q1–Q8 as written. Its §15 edits are in the plan as rev 12 and in D1 as rev 5
- **Deliverable:** P4.1.1 in `TASKS.md`
- **Builds on:** [`2026-10-23_data-pipeline-plan.md`](../2026-10/2026-10-23_data-pipeline-plan.md) rev 11 ("the plan"),
  [`2026-10-23_core-architecture-spec.md`](../2026-10/2026-10-23_core-architecture-spec.md) rev 4 ("D1", binding for all
  code), [`2026-10-23_p1-landing-catalog-spec.md`](../2026-10/2026-10-23_p1-landing-catalog-spec.md) ("D3"), the P2 spec
  ([`2026-10-30_p2-meter-pipeline-spec.md`](../2026-10/2026-10-30_p2-meter-pipeline-spec.md)) and the P3 spec
  ([`2026-11-03_p3-usage-profile-spec.md`](2026-11-03_p3-usage-profile-spec.md))
- **For:** the agent or person implementing P4; `/done` of P4.1.1 rewrites P4's sub-tasks from §2
- **Privacy note:** contains no identifiers or usage figures, and nothing added to it may. Facts about the workspace
  (§1) are structure only. Facts about retail offers are public (owner decision, 2026-11-03). Examples use placeholders
  and synthetic values.

---

## 1. What this spec fixes

P4 brings retail electricity offers into the workspace and prices them against the household's metered usage. It builds
a CDR client and the `fetch-offers` command, two new landable sources (`offer_cdr_index` fetch logs and `offer_manual`
YAML), a normalised tariff model, curated and local offer rules, a cost engine, and three stages: `normalise_offers`,
`cost_elec` and `backtest`; plus a read-only `offers diff` command. "Must" means required. D1 and the earlier phase
specs stay binding; §15 lists every change this spec makes to them.

**What P4 builds**
- core: `core/tariff.py` (the `Tariff` model, partition checks, the GST convention), `core/cdr_tariff.py` (CDR contract
  → `Tariff` or exclusion reasons), `core/offer_rules.py` (rule model and matching), `core/engine.py` (the cost engine),
  `core/retailer_txn.py` (the transactions reader), `core/models/offers.py` gains the fetch log and `offer_manual`
  models, `core/models/costs.py` (table kinds)
- adapters: `adapters/offers/cdr.py` (`CdrProvider`, stdlib `urllib`), parsers `adapters/sources/cdr_index.py` and
  `adapters/sources/offer_manual.py`, the CLI commands `fetch-offers` and `offers diff`
- services: `services/fetch_offers.py`, `services/offer_diff.py`, stages `normalise_offers`, `cost_elec`, `backtest`
- package data: `offer_rules/` (curated rules, §6)
- `Params` v4 and `RegionProfile` v3 (§4)
- no new third-party dependency

**Facts about the live CDR API** (probed 2026-11-04; public data)
- Plan list: `GET https://cdr.energymadeeasy.gov.au/<base>/cds-au/v1/energy/plans?effective=CURRENT&fuelType=ALL&page-size=1000`,
  header `x-v: 1`. The body is `{data: {plans: [...]}, links: {self, next?}, meta: {totalRecords, totalPages}}`. Large
  retailers need several pages (`links.next`). An unknown or empty base returns HTTP 200 with zero plans.
- Plan detail: `GET …/energy/plans/<planId>`, header `x-v: 3`; `data.electricityContract` holds the tariff.
- **The base URI is not the brand.** The AER's base-URI list maps several brands to one base; for example RAA Energy is
  served from base `energy-locals`, under which no current SAPN residential plan was listed on 2026-11-04. List items
  carry their own `brand`.
- Of the 93 base URIs on the AER's list (January 2026), 27 returned SAPN residential electricity plans, about 640 in
  total.
- Structure seen in 609 of those details (23 bases):
  - `pricingModel`: `SINGLE_RATE`, `SINGLE_RATE_CONT_LOAD`, `TIME_OF_USE`, `TIME_OF_USE_CONT_LOAD` only.
  - `timeZone`: mostly `LOCAL`, some `AEST`, some absent (the standard says absent means `AEST`).
  - one `tariffPeriod` each, covering `07-01`→`06-30` or `01-01`→`12-31`; `dailySupplyChargeType` always `SINGLE`.
  - TOU windows always every day of the week, end times always `HH:59` (inclusive end minute).
  - blocks: per-day (`P1D`) blocks in single and TOU rates (for example a free first block); some blocks with period
    `P1Y` or absent (which the standard defaults to `P1Y`), whose descriptions read as daily allowances.
  - demand charges in a few plans, two charges with identical days and window whose season is only in free text, end
    times both `HH:59` and `HH:00`.
  - discounts: `percentOfBill` (conditional `PAY_ON_TIME`/`DIRECT_DEBIT`, guaranteed), `fixedAmount` (conditional
    `OTHER`: referral and behaviour credits); fees: event fees (`FIXED`), `MEMBERSHIP` with `ANNUAL` term, `PAPER_BILL`.
  - eligibility: `EXISTING_SOLAR`, `EXISTING_BATTERY`, `EXISTING_SMART_METER`, `OTHER` (free text).
  - Some retailers publish wholesale pass-through plans whose rates are illustrative (the `variation` text says so).
- The CDR energy standard (v1.36) says supply charges, usage unit prices and demand amounts are **exclusive of GST**. It
  is silent on fees and fixed discount amounts; published fee descriptions say "incl. GST" and an annual membership
  amount equals twelve monthly GST-inclusive fees.
- Energy Made Easy's Basic Plan Information Document (BPID) page states "All prices listed are inclusive of GST" and is
  rendered in the browser from the same ex-GST numbers.

**Facts about the workspace** (structure only)
- One consumption register, 5-minute data, a confirmed `nem_aest` time basis; P3's reference window is 12 full months.
- `retailer_usage`: two landed datasets of monthly reads (P2 spec §9.3). `retailer_txn`: one landed export whose rows are
  `Invoice - …` (positive amounts, GST-inclusive totals) and `Payment - …` (negative) with datetimes; no billing period.
- No `elec_bill` PDF is landed. The retailer of record publishes no current plan in CDR, so the tariff that applied to
  past bills can only come from an `offer_manual` YAML (§5.3) the owner writes from a bill or a quote.

---

## 2. Order of work

`/done` of P4.1.1 turns the rows into sub-tasks, replacing the provisional list.

| # | Sub-task | Builds | Sections |
|---|---|---|---|
| 1 | P4.2.1 Foundations | `Params` v4 and its migration; `RegionProfile` v3 and `regions/sa.yaml` v3; `pipeline sync` fills the offer fields and creates `config/offer_rules.local/`; the P4 codes and warnings; golden schemas | §4, §12 |
| 2 | P4.2.2 Tariff model and CDR mapping | `core/tariff.py`, `core/cdr_tariff.py`; `tests/fixtures/make_cdr.py` | §5.1, §5.2, §7 |
| 3 | P4.2.3 Offer sources and rules | fetch log and `offer_manual` models and parsers; `core/offer_rules.py`; curated `offer_rules/` | §3.3, §5.3, §6 |
| 4 | P4.2.4 CDR client and `fetch-offers` | `adapters/offers/cdr.py`, `services/fetch_offers.py`, the command | §3 |
| 5 | P4.2.5 `normalise_offers` | the stage, golden `dvc.yaml`, fixture workspace | §8 |
| 6 | P4.2.6 Engine | `core/engine.py`, the oracle additions, property tests | §7 |
| 7 | P4.2.7 `cost_elec` | the stage, `core/models/costs.py` | §9 |
| 8 | P4.2.8 `backtest` | `core/retailer_txn.py`, the stage | §10 |
| 9 | P4.2.9 Offer diff and the end-to-end test | `services/offer_diff.py`, `offers diff`; `test_p4_pipeline` ending with `verify` | §11, §14 |
| 10 | P4.3.1 Acceptance | in the real workspace | §13 |
| 11 | P4.3.2 Owner: historical tariff and backup | the owner writes the `offer_manual` for the tariff in force, then backs up | §13 |

`Params` v4 sits in a module every stage imports, so P4.2.1 makes every stage's code entry stale until `fingerprint` is
re-run in a workspace (as in P2 and P3). Each stage sub-task updates the golden `dvc.yaml` and the fixture workspace for
the stage it adds.

---

## 3. Fetching offers

### 3.1 `CdrProvider` (`adapters/offers/cdr.py`)

Implements D1 §4.6's `OfferProvider` unchanged, with `name = "cdr"`. `ServiceContext.offers` holds it under `"cdr"`.
- `list_plans(base)` requests the list URL of §1 and follows `links.next` while present, at most 50 pages (more →
  `E_NETWORK`, `reason: too_many_pages`). One `Fetched` per page.
- `get_plan(base, plan_id)` requests the detail URL; `plan_id` is percent-encoded as a path segment.
- Request headers: `x-v` (`1` list, `3` detail), `accept: application/json`, `user-agent: energatron/<version>`. Nothing
  else: no cookies, no contact details.
- `Fetched.response_headers` keeps this allow-list only, lower-cased: `content-type`, `date`, `etag`, `last-modified`,
  `x-v`, `x-fapi-interaction-id`. `Fetched` itself is unchanged.
- Timeout 30 s per request. No retries in P4; requests are sequential.
- A transport failure, a timeout, a non-JSON body, or a status ≥ 400 → `E_NETWORK` with `url`, `status` (or `null`)
  and `reason` in `details`. The provider never filters or interprets.
- `fetched_at` comes from `ctx.clock` passed to the provider at construction, so tests fix it.

### 3.2 `energatron fetch-offers` (`services/fetch_offers.py`)

`fetch_offers(ctx, *, retailers=None, all_details=False, dry_run=False) -> FetchOffersResult`. Writes, so it holds the
workspace lock (D1 §8.6 table) and needs `private/config.toml` (`E_PRIVATE_CONFIG_MISSING` otherwise; the postcode
filter reads it). Options: `--retailer <base>` (repeatable; default `offers.retailers`), `--all` (re-fetch every detail),
`--dry-run`, `--json`.

1. Preconditions: as `land`'s (D3 §7.1: pipeline synced, raw zone and pipeline files clean). `params.yaml` is read
   through `Params`; `offers.retailers` empty and no `--retailer` → `E_USAGE`.
2. `batch = fetch_batch_id(now)`. For each base, in the given order: `list_plans`. A failure records the error for that
   base and moves on.
3. **Local filter** of each list item, in this order; the first failing rule names why it was left out (counted per
   reason in the log): `fuel` (`fuelType` in `offers.fuel_types`; `DUAL` waits for P6), `customer_type`,
   `distributor` (`offers.distributor` in `geography.distributors`), `postcode` (when the private config has a postcode
   and the item lists `includedPostcodes` without it, or `excludedPostcodes` with it; with no postcode the rule passes
   and the result says `postcode_applied: false`), `effective` (`effectiveFrom` ≤ now < `effectiveTo` when given).
4. **Selected** items: their details are fetched unless the newest landed `offer_cdr` record for that `planId` has the
   same `lastUpdated` (then `unchanged`, no request), or `--all` is given. A failing detail is recorded and skipped.
5. Write a staging directory `data/landing/.fetch-<batch>/`: each detail body verbatim as `<base>__<planId>.json`
   (characters outside `[A-Za-z0-9@._-]` in either part become `_`), and one `offer_fetch_log` document (§3.3) as
   `fetch-log.json`.
6. Call `land(ctx, [staging dir])` with no `--source`: the two parsers detect their files (§3.3). One `land: <ids>`
   commit holds the new datasets. Re-fetched details whose snapshot already exists are `land`'s no-ops (D3 §5.7).
7. Remove the staging directory after `land` returns without a whole-command error; otherwise keep it and name it in the
   error's `details`.
8. Result `fetch_offers_result` v1: `fetch_batch_id`, per base `{listed, selected, fetched, unchanged, failed,
   left_out: {reason: n}, error}`, `postcode_applied`, the `land` result's dataset IDs and commit. Exit: 0; 8
   (`E_PARTIAL`) when any base or detail failed but something landed; the single error when nothing could be fetched.
   `W_BACKUP_PENDING` as for `land`.
- `--dry-run` lists and filters but fetches no detail and writes nothing.

### 3.3 The fetch log: `offer_cdr_index`

`offer_fetch_log` v1 (`core/models/offers.py`), one per batch:
```
kind, schema_version, fetch_batch_id, fetched_at (UTC, the batch start), filter {fuel_types, customer_type,
distributor, postcode_applied}, retailers: [{base, pages: [Fetched with body as body_b64], error}],
selected: [{base, plan_id, last_updated, action: fetched|unchanged|failed, sha256|null, offer_snapshot_id|null}]
```
- List bodies are kept verbatim as base64 (`body_b64`), so the log never contains a literal `"planId"` key and the
  `offer_cdr` parser (D3 §5.7, unchanged) can't detect it. Field names in the log are snake_case for the same reason.
- No postcode, no identifier: only `postcode_applied`.
- Parser `cdr_index` (`adapters/sources/cdr_index.py`, version 1): detects content starting with `{` whose head (the
  64 KiB `land` passes) matches `"kind"\s*:\s*"offer_fetch_log"`; the writer's sorted keys put `kind` before the page
  bodies; `inspect` validates
  the model; `source_ts` = `fetched_at`; `extra`: `fetch_batch_id`, `retailers` (bases), `selected` count. Public source
  (D3 §6, already in `PUBLIC_SOURCES`). `regions/sa.yaml` lists it.
- The latest log per base defines which plans are **current** (§8 step 2): a plan that disappears from the retailer's
  list stops being priced even though its snapshots stay in the raw zone.

---

## 4. Configuration

### 4.1 `params` v4 (`core/models/params.py`)

New blocks (v3 content unchanged):
```yaml
offers:
  retailers: [globird, agl, …]      # CDR base URIs; copied from the region profile by pipeline sync when null
  distributor: SA Power Networks    # copied from the region profile when null
  customer_type: RESIDENTIAL
  fuel_types: [ELECTRICITY]         # P5 adds GAS
  unmet_eligibility: [EXISTING_SOLAR, EXISTING_BATTERY]  # CDR eligibility types the household does not meet
  controlled_load: false            # does the household have a controlled-load register?
pricing:
  gst_rate: "0.10"                  # a decimal string, read as Decimal
  assume_conditions_met: [PAY_ON_TIME, DIRECT_DEBIT]
  demand_minutes: 30                # the demand measurement block
backtest:
  tariffs: []                       # [{offer: <offer snapshot id>, from: "YYYY-MM-DD", to: "YYYY-MM-DD" | null}]
validation:
  backtest: {tolerance_pct: 1.0, invoice_window_days: 14}
```
- `offers.retailers` and `offers.distributor` join `REGION_FIELDS` (null until `pipeline sync`; a stage reading them
  `null` → `E_INVALID_INPUT`, as in P2).
- `unmet_eligibility` values are CDR eligibility enum names; unknown names are refused. Household facts, not identifiers.
- `backtest.tariffs`: snapshot IDs as D1 §6 (validated by `parse_offer_snapshot_id`); `from` ≤ `to`; ranges are meter
  dates, inclusive, and must not overlap (`E_INVALID_INPUT`). Dates are quoted strings (a DVC lesson from P3).
- Migration v3 → v4: inserts the blocks with the defaults above and `null` for the two region fields.

### 4.2 `RegionProfile` v3 and `regions/sa.yaml`

Adds `distributor: str` and `cdr_retailers: tuple[str, ...]` (unique, each `[a-z0-9-]+`). `sa.yaml` v3 lists
`SA Power Networks` and the 27 base URIs of §1 plus `energy-locals` (the owner's retailer of record, listed so a return
to CDR is noticed). Migration v2 → v3 inserts `null` for both; `build_context()` refuses a profile with either unset.

### 4.3 `pipeline sync`

Fills `offers.retailers` and `offers.distributor` from the profile when `null`; `--check` reports a `null` as drift
(owner edits are kept, as for the windows). It also creates `config/offer_rules.local/` with a `README.md` saying what
belongs there (§6), so the stage dependency exists; the directory is git-tracked config, never DVC.

---

## 5. Tariffs

### 5.1 The model (`core/tariff.py`)

`Tariff` (kind `tariff` v1, frozen, `extra="forbid"`): every money amount is a `Decimal`, **GST-exclusive** except where
noted.
```
offer_snapshot_id, plan_id, brand, retailer (base), display_name, source: cdr|manual, customer_type, distributors,
pricing_model, effective_from: date|None, effective_to: date|None, clock: LOCAL|AEST,
seasons: [Season{start: "MM-DD", end: "MM-DD", daily_supply, energy: [EnergyCharge], demand: [DemandCharge]}],
discounts: [Discount{kind: guaranteed|conditional|other, category, method: percent_of_bill|percent_of_use|fixed,
          rate|amount, one_off: bool}],
fees: [Fee{type, term, amount_inc_gst}],    # only periodic fees (§7.6)
status: normalised|approximate, approximations: [str], notes: [str]
EnergyCharge{label (PEAK, OFF_PEAK, SHOULDER, SHOULDER1, SHOULDER2, or ALL), windows: [Window{days: frozenset[MON..SUN],
          start_minute, end_minute}] (half-open, end may wrap past midnight), block_period: P1D|P1M, blocks:
          [Block{volume_kwh: Decimal|None, unit_price}]}
DemandCharge{days, start_minute, end_minute, amount_per_kw, measurement: DAY|MONTH, charge: DAY|MONTH, min_kw, max_kw}
```
- **Partition rules**, checked by `core/tariff.py` and failing construction: seasons cover each of the 366 `MM-DD`
  values exactly once (ranges inclusive and may wrap the year end); within each season the energy charges' windows cover
  each `(weekday, minute)` exactly once (a single-rate tariff has one charge `ALL` with one all-week window); blocks
  have a volume on every block but the last, volumes > 0.
- **Windows**: CDR end times are inclusive minutes (`16:00`–`22:59` = minutes 960–1379), converted to half-open
  `[960, 1380)`. An end of `HH:00` is read the same way (inclusive), which the partition check then accepts or rejects.
- **GST convention** (§7.8, verified at acceptance): `CDR_RATES_INCLUDE_GST = False`, `CDR_FEES_INCLUDE_GST = True`, as
  two named constants with one test each.

### 5.2 CDR mapping (`core/cdr_tariff.py`)

`map_cdr(data, *, retailer, snapshot_id, offers_params) -> Tariff | Excluded` is pure. `Excluded{reasons: [str],
notes}`. Every input feature lands in exactly one of: supported, an approximation (`approximations`, status
`approximate`, still ranked) or an exclusion reason. The reasons and approximations are fixed strings:

| Feature | Treatment |
|---|---|
| `pricingModel` `*_CONT_LOAD` and `offers.controlled_load` false | excluded `controlled_load_plan` |
| `pricingModel` `FLEXIBLE*`, `QUOTA` | excluded `pricing_model:<name>` |
| `eligibility` type in `offers.unmet_eligibility` | excluded `eligibility:<type>` |
| `eligibility` `OTHER` (free text) | approximation `eligibility_unverified` |
| `dailySupplyChargeType` `BAND` | excluded `banded_supply` |
| seasons not partitioning the year | excluded `seasons_not_partition` |
| TOU windows not partitioning a season's week | excluded `windows_not_partition` |
| day token `PUBLIC_HOLIDAYS` (or any non-weekday) | token dropped; approximation `public_holidays_ignored` |
| a block rate set (>1 rate) with period `P1D` or `P1M` | supported |
| a block rate set with period `P1Y` or absent | excluded `block_period_unclear` |
| a single rate with any period | supported (the period doesn't matter) |
| `measureUnit` other than `KWH` on usage | excluded `measure_unit:<unit>` |
| `timeZone` `AEST` or absent | clock `AEST`; `LOCAL` → clock `LOCAL` (a season's own `timeZone` wins) |
| demand charges whose `(days, window)` repeat within a season (season only in free text) | excluded `demand_season_unstated` |
| other demand charges with `measurementPeriod`/`chargePeriod` `DAY`/`MONTH` | supported; approximation `demand_day_count` (§7.5) |
| demand `TARIFF_PERIOD` | excluded `demand_period:TARIFF_PERIOD` |
| discount `percentOfBill`, `percentOfUse` | supported |
| discount `fixedAmount`, type `GUARANTEED` | supported as a one-off credit (§7.7) |
| discount conditional with category `OTHER` (any method) | not priced; note `conditional_discount_unpriced` |
| discount `percentOverThreshold` | not priced; approximation `discount_unpriced:percentOverThreshold` |
| fee with term `ANNUAL`, `MONTHLY`, `WEEKLY`, `DAILY`, `BIANNUAL` | supported (§7.6) |
| any other fee (event fees, `PERCENT_OF_BILL`, multi-year exit fees, `VARIABLE`, `PAPER_BILL`) | not priced; note `fee_not_periodic:<type>` |
| `incentives`, `greenPowerCharges`, `solarFeedInTariff`, `meteringCharges`, `controlledLoad` (when not excluded) | not priced; one note each |
| `effectiveTo` before `as_of`, or `effectiveFrom` after it | excluded `not_effective` |
| `fuelType` not `ELECTRICITY` | excluded `fuel:<type>` |
| malformed values (a non-decimal price, an unparseable time) | excluded `malformed:<field>` |
| every supply, usage and demand price zero (a companion export plan; added at acceptance, 2026-11-05) | excluded `zero_priced` |

Reasons are sorted and unique. A note never changes a price; an approximation is shown wherever the offer's cost is.

### 5.3 `offer_manual` (`core/models/offers.py`, `adapters/sources/offer_manual.py`)

A YAML document for a tariff not in CDR (a bill, a phone quote, a retention offer):
```yaml
kind: offer_manual
schema_version: 1
plan_id: MANUAL-<retailer>-<name>     # [A-Za-z0-9@._-]+, no "~"
brand: <brand>
display_name: <text>
published: "2025-07-01T00:00:00Z"     # quote, publish or bill date (quoted); the snapshot's lastUpdated
source: bill | quote | bpid | other
evidence: []                          # optional dataset IDs (bills, PDFs) it was transcribed from
prices_include_gst: true              # as written; the mapper divides by (1 + pricing.gst_rate) when true
effective_from: "2025-07-01"          # or null
effective_to: null
electricityContract: {...}            # the CDR electricityContract shape (camelCase), so one mapper serves both
```
- Parser `offer_manual` (version 1): detects YAML whose first lines hold `kind: offer_manual`; `inspect` loads it with
  `yaml.safe_load`, refuses unquoted dates (a YAML `date` value), validates the model and computes
  `offer_snapshot_id(plan_id, published, document)`; `source_ts` = `published`; `extra`: `plan_id`,
  `offer_snapshot_id`; `extension` `.yaml`. It is not a public source: a quote may be personal, so the premises check
  runs (it holds no identifier, so it warns `W_PREMISES_UNCHECKED` like other identifier-free files).
- Manual offers skip the `current` rule (§8 step 2) and are always normalised, with `source: manual`.
- The BPID or fact-sheet PDF the plan mentions as evidence waits for P6's decisions (§14).

---

## 6. Offer rules (`core/offer_rules.py`, `offer_rules/`, `config/offer_rules.local/`)

`offer_rule` v1, one YAML file per rule:
```yaml
kind: offer_rule
schema_version: 1
id: <kebab-case, unique across both directories>
description: <text>
match: {brand: <brand> | null, plan_id: <regex, fullmatch> | null, display_name: <regex, fullmatch> | null,
        from: "YYYY-MM-DD" | null, to: … | null}
effect: {exclude: <reason>} | {approximate: <reason>} | {note: <text>}
verified_on: "YYYY-MM-DD"
cites: [<offer snapshot IDs the rule was derived from>]
```
- `match` needs `brand`, `plan_id` or `display_name`; `from`/`to` compare with the snapshot's `lastUpdated` date.
  Brands are compared trimmed (`map_cdr` trims them: some retailers publish a trailing space).
- Rules apply after `map_cdr`, in rule-id order: `exclude` turns an offer excluded with reason `rule:<id>:<reason>`;
  `approximate` adds `rule:<id>:<reason>` to `approximations`; `note` adds a note. Applied rule IDs are recorded.
- Curated rules ship as package data `offer_rules/*.yaml` (the stage declares it as `package_data`, so a rule change
  reruns it); a local rule in the workspace's `config/offer_rules.local/*.yaml` with the same `id` replaces the curated
  one. Any invalid file fails the stage (`rules_valid`).
- P4 ships three curated rules, each `approximate` with reason `wholesale_estimate` for plans whose CDR rates are
  illustrative wholesale estimates (verified against their `variation` text and names), citing the snapshot IDs seen
  when they were written: `amber-wholesale` (brand `amber`), `flow-power-variable-estimate` and
  `globird-wholesave-estimate` (by display name). Acceptance (2026-11-05) added `io-energy-first-bill-discount`, which excludes two plans whose
  guaranteed percentage discount applies to the first bill only (free text CDR can't express).

---

## 7. The engine (`core/engine.py`)

Pure: `price(tariff, usage, periods, *, scenario, pricing) -> list[LineItem]`. `usage` is one consumption register's
rows (`utc_start`, `local_start`, `local_date`, `interval_minutes`, `kwh`); `periods` are `(first, last)` date pairs,
inclusive, each a billing period; `scenario` is `conditions_met` or `conditions_not_met`.

### 7.1 Clock, season and label
- The tariff clock is `local_start` for `LOCAL` and `utc_start + 10 h` for `AEST`. Weekday and minute-of-day come from
  that clock; an interval is labelled by its start minute.
- A row belongs to the period by `local_date` (cost_elec) or by `meter_date` (backtest, which passes periods in meter
  dates and selects rows itself); the season is chosen by the row's tariff-clock date `MM-DD`.

### 7.2 Energy
- Per period, season and charge, energy is taken in time order. Blocks fill per tariff-clock day (`P1D`) or per billing
  period (`P1M`); an interval that crosses a block threshold is split.
- Quantities are converted from float kWh to `Decimal` once per line, as `Decimal(f"{fsum:.6f}")`.
- Line: `usage` with `label`, `season`, `block` index, `quantity` kWh, `rate`, `amount` = round(quantity × rate).

### 7.3 Supply
- Line `supply` per season: `quantity` = the period's dates (local or meter dates, as the period is given) whose
  `MM-DD` falls in that season, `rate` = `daily_supply`.

### 7.4 Rounding
- Money is `Decimal`; each line amount is rounded to cents with `ROUND_HALF_UP`. Totals are sums of rounded lines.

### 7.5 Demand
- Demand per block of `pricing.demand_minutes` aligned to the clock: kW = block kWh × 60 / minutes, only for blocks
  wholly inside the window (`[start, end + 1 min)` on the listed days) and wholly present.
- `measurement: DAY` → each day's maximum; `MONTH` → the period's maximum. Chargeable kW = `max(0, min(kW, max_kw) −
  min_kw)`.
- `charge: DAY` → amount × chargeable kW × every day of the period (`DAY` measurement: summed per day); `charge:
  MONTH` → amount × chargeable kW once per period. Whether a per-day charge counts every day or only listed days is not
  stated by CDR; the engine counts every day and the tariff carries `demand_day_count`.

### 7.6 Fees
- Periodic fees are GST-inclusive amounts (§5.1), prorated per period: `ANNUAL` × days / 365, `BIANNUAL` × days /
  182.5, `MONTHLY` × days / (365 / 12), `WEEKLY` × days / 7, `DAILY` × days; rounded to cents; line `fee`. They are
  outside the GST base and the discount base.

### 7.7 Discounts and credits
- Base: `percent_of_bill` applies to usage + supply + demand; `percent_of_use` to usage only. Several percentages
  add on the same base (no compounding). Line `discount`, negative.
- Guaranteed discounts always apply; conditional ones with category in `pricing.assume_conditions_met` apply only in
  `conditions_met`; conditional `OTHER` never (a note, §5.2).
- `fixed` guaranteed amounts are one-off credits (GST-inclusive as published, like fees): not in any period's lines;
  `cost_elec` subtracts them from year 1 only.

### 7.8 GST
- Line `gst` = round((usage + supply + demand + discounts) × `gst_rate`). Period total = that sum + GST + fees.
- Verification at acceptance (§13 check 4): one landed CDR plan's published GST-inclusive prices (its BPID on Energy
  Made Easy, or the retailer's rate page) equal the CDR numbers × 1.1 to the published precision. If they don't, P4 stops
  and the owner decides; the constants change in one place.

### 7.9 Invariants (property-tested)
- Σ usage `quantity` per period = the period's metered kWh (to 1e-6 per line).
- Lines sum to the period total exactly; the engine never returns a float.
- Doubling every unit price doubles every usage line's amount before rounding; the order of rows doesn't change any line.
- A tariff with one charge and no blocks gives `usage` = round(Σ kWh × rate) per season.

---

## 8. `normalise_offers` (`services/stages/normalise_offers.py`)

- Inputs: `cdr` = `data/raw/offer_cdr`, `cdr_catalog` = its `_catalog.jsonl`, `index` = `data/raw/offer_cdr_index`,
  `index_catalog`, `manual` = `data/raw/offer_manual`, `manual_catalog`, `rules` = `config/offer_rules.local`. Params:
  `offers`, `pricing.gst_rate`. Package data: `offer_rules`. Output `offers` = `data/marts/offers`.
- Steps:
  1. Read the catalogs; check blobs against them (`blobs_match_catalog`) and detector versions (`detector_known`), as
     in P2.
  2. **Current CDR plans:** for each base, the plans `selected` in that base's latest fetch log (by `fetched_at`); for
     each, its newest landed snapshot (by `lastUpdated`, then `landed_at`). A selected plan with no landed snapshot is
     counted `missing_snapshot` and warned (`W_OFFERS_MISSING_SNAPSHOT`).
  3. `as_of` = the latest fetch log's `fetched_at` date (no fetch log → the newest manual `published`; nothing → no
     offers, an empty mart).
  4. Map each with `map_cdr` (manual offers: from their `electricityContract`, divided by `1 + gst_rate` when
     `prices_include_gst`), then apply rules.
- Outputs:
  - `tariffs.json`: `offer_tariffs` v1, `{as_of, tariffs: [Tariff…]}` sorted by snapshot ID; normalised and approximate
    only.
  - `offers.parquet`: `offer_status` v1, one row per considered offer: `offer_snapshot_id`, `plan_id`, `retailer`,
    `brand`, `display_name`, `source`, `pricing_model`, `status` (`normalised|approximate|excluded`), `reasons`,
    `approximations`, `notes`, `rules` (lists of strings); sorted by snapshot ID.
  - `summary.json`: `offers_summary` v1: `as_of`, counts by status, by reason and by retailer, `missing_snapshot`,
    the latest batch per base, checks.
- Checks (fail the stage): `blobs_match_catalog`, `detector_known`, `snapshot_ids_recompute`, `rules_valid`,
  `every_offer_accounted` (rows = current plans + manual offers, each with exactly one status, an excluded one with ≥ 1
  reason), `tariffs_partition` (every written tariff passes §5.1 again).

---

## 9. `cost_elec` (`services/stages/cost_elec.py`)

- Inputs: `offers` = `data/marts/offers`, `local` = `data/interim/elec_interval_local`, `profile` =
  `data/marts/usage_profile` (`summary.json` only). Params: `pricing`. Output `costs` = `data/marts/costs/elec`.
- The register: exactly one consumption register (`E*`) in the local table (`one_consumption_register`, fails
  otherwise: controlled-load pricing waits until a household needs it). Its reference window is the P3 summary's
  `window_start`–`window_end`; billing periods are its calendar months (local dates).
- For each tariff × scenario: `price(...)`; `annual_ongoing` = Σ period totals; `annual_year1` = ongoing − one-off
  credits.
- Outputs:
  - `line_items.parquet`: `elec_cost_line` v1: `offer_snapshot_id`, `scenario`, `period_start`, `period_end`, `line`,
    `label`, `season`, `block`, `quantity` (decimal128(18,6)), `unit`, `rate` (decimal128(18,6)), `amount`
    (decimal128(18,2)); sorted by those keys.
  - `totals.parquet`: `elec_cost_total` v1: `offer_snapshot_id`, `scenario`, `status`, `annual_ongoing`,
    `annual_year1`, `rank` (by `annual_ongoing` within the scenario, ties by snapshot ID), `approximations`.
  - `summary.json`: `elec_cost_summary` v1: the suffix, the window, scenarios, counts, the three cheapest snapshot IDs
    per scenario, checks.
- Checks: `one_consumption_register`, `window_present` (every window month has rows), `lines_sum` (lines = period
  totals, periods = annual), `energy_conserved` (Σ usage quantities = metered kWh per period, to 1e-4).

---

## 10. `backtest` (`services/stages/backtest.py`)

- Inputs: `offers`, `local`, `usage` = `data/raw/retailer_usage` (+ catalog), `txn` = `data/raw/retailer_txn` (+
  catalog). Params: `pricing`, `backtest`, `validation.backtest`. Output `backtest` = `data/reports/validation/backtest`.
- **Periods:** built from the current `retailer_usage` datasets exactly as P2 spec §9.3 steps 1–3 (newest dataset per
  read datetime, midnight reads, consecutive pairs give meter dates `r₀.date + 1 … r₁.date`). A period with any missing
  interval is `not_covered`.
- **Tariff in force:** the `backtest.tariffs` entry whose range contains the whole period; none → `no_tariff`; a
  period spanning two entries → `split_tariff` (not priced).
- **Invoices:** `core/retailer_txn.read_txn` returns rows `(kind: invoice|payment|other, at, amount)`; descriptions
  (which hold invoice numbers) never leave the reader. An invoice matches the period when its date lies in
  `[r₁.date, r₁.date + invoice_window_days]`; the earliest such invoice not already matched wins; none → `unmatched`.
- Price with the engine (scenario `conditions_met`), periods in meter dates.
- Outputs: `bills.parquet` (`backtest_bill` v1: `period_start`, `period_end`, `offer_snapshot_id`, `invoice_date`,
  `invoice_amount`, `engine_amount`, `diff`, `diff_pct`, `status` = `within|outside|unmatched|no_tariff|split_tariff|
  not_covered`), `line_items.parquet` (`elec_cost_line` v1, scenario `backtest`), `result.json` (`backtest_result` v1:
  counts by status, `max_abs_diff_pct`, the tolerance, checks).
- Checks (fail): `blobs_match_catalog`, `tariffs_resolve` (every configured snapshot ID is a normalised or approximate
  tariff in `tariffs.json`), `lines_sum`.
- Warnings (not failures, §16 Q5): `W_BACKTEST_OUTSIDE_TOLERANCE` (any `outside`, with counts),
  `W_BACKTEST_NO_TARIFF` (no tariff configured, or no period priced).

---

## 11. `energatron offers diff` (`services/offer_diff.py`)

Read-only, no lock. `offers diff [--plan <plan_id>] [--json]`: for each plan (or the one named) with at least two
landed `offer_cdr` snapshots, map the newest two with `map_cdr` (no rules, `as_of` = the newer `lastUpdated`) and list
field-level changes: `path` (e.g. `seasons[0].energy[PEAK].blocks[0].unit_price`), `old`, `new`; status changes and
reason changes too. Result `offer_diff_result` v1 sorted by plan ID then path. Text output, one line per change with the
signed difference for money (e.g. `daily_supply +0.0300`).

---

## 12. Kinds, codes and warnings added in P4

- Kinds: `params` v4, `region_profile` v3, `offer_fetch_log`, `fetch_offers_result`, `offer_manual`, `offer_rule`,
  `tariff`, `offer_tariffs`, `offer_status`, `offers_summary`, `elec_cost_line`, `elec_cost_total`,
  `elec_cost_summary`, `backtest_bill`, `backtest_result`, `offer_diff_result`, all v1 except the first two. Golden
  schemas for each.
- Codes: none new (`E_NETWORK` exists; offers that can't be normalised are data, not errors).
- Warnings: `W_FETCH_FAILED` (a base or detail failed), `W_OFFERS_MISSING_SNAPSHOT`, `W_BACKTEST_OUTSIDE_TOLERANCE`,
  `W_BACKTEST_NO_TARIFF`.

---

## 13. P4 acceptance checks

In the real workspace, with the tool committed. `TASKS.md` gets counts and pass/fail only; anything with a figure goes
in a workspace note.
1. `pipeline sync` (params v4, offer fields filled, `config/offer_rules.local/` created), `fingerprint`, commit.
2. `energatron fetch-offers --json`: every configured base listed; one `land` commit; no failed base (a failure is
   retried once by re-running; a base that keeps failing is recorded).
3. `dvc repro`: `normalise_offers` passes `every_offer_accounted`. **Plan §15 check:** every current SAPN residential
   electricity plan of the configured retailers is `normalised`, `approximate` or `excluded` with a reason; the counts
   per status and per reason go in `TASKS.md`.
4. **GST:** the agent opens one landed plan's BPID (Energy Made Easy) or the retailer's published rate page and records
   that its GST-inclusive supply and usage prices equal CDR × 1.1 at the published precision (pass/fail in `TASKS.md`,
   the plan ID only). If the agent can't render the page, the owner does it (P4.3.2).
5. `cost_elec` ran; the ranking and the cheapest offers go in the workspace note, never in the tool repo.
6. **Backtest:** if the owner has supplied the historical tariff (`offer_manual` + `backtest.tariffs`), every matched
   period is within ±1% or its deviation is explained in the workspace note; otherwise the stage warns
   `W_BACKTEST_NO_TARIFF`, and the check is completed in P4.3.2 by the owner's input.
7. `energatron offers diff` runs (after a second fetch on a later day it shows the changes; one fetch shows none).
8. `verify` passes; `dvc status` clean; privacy guard 0 violations; the pending backup is reported.

---

## 14. Tests

- **Fixtures:** `tests/fixtures/make_cdr.py` generates CDR list and detail documents from parameters (single rate,
  TOU with wrap, blocks, demand, discounts, fees, eligibility, CL, AEST), never from real files; `make_offer_manual`;
  a retailer transactions generator in `make_sources.py` aligned with the retailer usage generator.
- **Core:** `test_tariff` (partitions, windows inclusive→half-open, GST constants), `test_cdr_tariff` (every row of
  §5.2's table, one test each), `test_offer_rules`, `test_engine` against an independent oracle in
  `tests/fixtures/oracle.py` (minute-by-minute loops, no `energatron` import), `test_engine_properties` (§7.9),
  `test_retailer_txn`.
- **Adapters:** `test_cdr_provider` against a stdlib `http.server` on localhost (paging, headers allow-list, errors,
  too many pages); the two parsers' detect/inspect, including that `offer_cdr` never detects a fetch log and vice versa.
- **Services:** `test_fetch_offers` with a fake provider and a scratch workspace (filters and reasons, unchanged
  skip, `--all`, partial failure exit 8, one commit, staging removed, dry run writes nothing, no postcode in any output);
  `test_offer_diff`.
- **Stages:** one test module each, every check passing and failing; the golden `dvc.yaml` with eleven stages; the
  fixture workspace gains landed offers, a fetch log, a manual offer and a transactions export.
- **Integration:** `test_p4_pipeline` (scratch workspace: land, `fetch-offers` against the local HTTP server, `dvc
  repro`, `offers diff`, `verify`).

---

## 15. Plan and D1 changes

To be applied on `/done` of P4.1.1, as plan rev 12 and D1 rev 5.

**Plan**
- §8.2: `offer_cdr_index` is the fetch log (one per batch, list pages verbatim as base64); `offer_manual` is YAML in
  the CDR contract shape; its PDF evidence waits for P6.
- §9.1 DAG and §9.2 table: `normalise_offers` (deps raw offers, fetch logs, manual offers, local rules; params
  `offers`, `pricing.gst_rate`; outs `marts/offers/` with `tariffs.json`, `offers.parquet`, `summary.json`; no private
  config), `cost_elec` (deps offers, local intervals, `usage_profile`; params `pricing`), `backtest` (deps offers, local
  intervals, retailer usage and transactions, not `cost_elec`; params `pricing`, `backtest`, `validation.backtest`;
  tolerance breaches warn).
- §9.2 `params.yaml`: v4 with `offers`, `pricing`, `backtest`, `validation.backtest` as §4.1 (`offers.brands` becomes
  `offers.retailers`, CDR base URIs; distributors come from the region profile).
- §10.4: base URIs ≠ brands; local filter order and reasons; `unchanged` skip; the fetch log defines current plans.
- §10.5: the support table of §5.2; monthly calendar billing periods (CDR `billFrequency` is not simulated); the GST
  convention of §5.1; `Decimal` cents with `ROUND_HALF_UP`.
- §10.9: periods from retailer reads, invoices matched by date, the tariff history in `backtest.tariffs`.
- §14.1: the distributor and CDR retailers live in the region profile.
- §15 P4 acceptance: made concrete as §13.

**D1 (rev 5)**
- §2: core gains `tariff.py`, `cdr_tariff.py`, `offer_rules.py`, `engine.py`, `retailer_txn.py`, `models/costs.py`;
  services gain `fetch_offers.py`, `offer_diff.py` and the three stages; adapters gain `offers/cdr.py` and two parsers.
- §4.6: `Fetched` unchanged; the header allow-list and paging as §3.1.
- §7: the four P4 warnings.
- §12 (money): `Decimal`, cents, `ROUND_HALF_UP`, per line.

---

## 16. Open questions / decisions for the owner

Each is decided here so P4 can be built; the recommendation is what the spec says.
1. **Retailers are CDR base URIs from the region profile (§4).** Alternative: brand slugs, which fail for brands served
   from another base (RAA Energy).
2. **Controlled-load plan variants are excluded while `offers.controlled_load` is false (§5.2).** The household has no
   controlled-load register; pricing those variants would duplicate each plan with charges it can't incur.
3. **Annual-period block rates are excluded (`block_period_unclear`).** Their descriptions read as daily allowances
   while the field says yearly; pricing either way could be badly wrong. A curated rule can re-admit a plan once
   verified.
4. **Monthly calendar billing periods for `cost_elec` (§9).** Simulating each plan's `billFrequency` changes little
   for per-day blocks and adds periods that don't align with the reference window.
5. **Backtest breaches warn rather than fail (§10).** Real invoices carry adjustments, concessions and pro-rata changes
   the engine can't see; a failing stage would block `dvc repro` on data the owner can't fix. Plan §9.2 said "fail";
   this changes it.
6. **The historical tariff comes from the owner (P4.3.2).** The retailer of record has no current CDR plan, no bill PDF is
   landed and the legacy notebook's rates are another retailer's and undated. The agent doesn't infer rates from
   invoices (that would make the backtest circular).
7. **Network tariff (plan §16 Q4) is not used to filter in P4.** CDR carries it only in display names; plans for other
   network tariffs are priced too, and P6's recommendation handles eligibility. The private `network_tariff` stays
   optional.
8. **No new dependency:** `urllib` for HTTP, `decimal` for money.
