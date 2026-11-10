# P6 bundles, recommendation and decisions spec

- **Created:** 2026-11-10
- **Status:** Approved 2026-11-10 (by the agent, on the owner's instruction of 2026-11-10 to add and build P6 without
  stopping for review or `/done`), after a self-review; Q1-Q8 as written. Its §13 edits are in the plan as rev 14 and in
  D1 as rev 7
- **Deliverable:** P6.1.1 in `TASKS.md`
- **Builds on:** [`2026-10-23_data-pipeline-plan.md`](../2026-10/2026-10-23_data-pipeline-plan.md) rev 13 ("the plan"),
  [`2026-10-23_core-architecture-spec.md`](../2026-10/2026-10-23_core-architecture-spec.md) rev 6 ("D1", binding for all
  code), the P4 spec ([`2026-11-04_p4-offers-electricity-engine-spec.md`](2026-11-04_p4-offers-electricity-engine-spec.md))
  and the P5 spec ([`2026-11-05_p5-gas-spec.md`](2026-11-05_p5-gas-spec.md))
- **For:** the agent implementing P6; `/done` of P6.1.1 rewrites P6's sub-tasks from §2
- **Privacy note:** contains no identifiers or usage figures, and nothing added to it may. Facts about the workspace
  (§1) are structure only. Offer details are public (owner decision, 2026-11-03). Examples use placeholders.

---

## 1. What this spec fixes

P6 turns the priced offers into a recommendation the owner can act on and trace: electricity and gas costs are combined
into bundles, the ranking is stress-tested, a recommendation (`REC-…`) is written as JSON and HTML, `energatron run`
runs the pipeline and records each run, `energatron decide` records a switch (`DEC-…`), and an outcomes report compares
bills after a switch with what was projected. "Must" means required. D1 and the earlier specs stay binding; §13 lists
every change this spec makes to them. D1 §14 deferred three things to this spec: the recommendation payload (§7.3), the
run manifest (§8.2) and the decision schema (§9.2).

**What P6 builds**
- core: `core/ranking.py` (options, bundle rules, sensitivity verdicts; pure), `core/bundle_rules.py` (the
  `bundle_rule` model and loading), `core/models/recommend.py` (the P6 kinds), `core/recommendation_report.py` (the
  HTML page, pure)
- services: stages `sensitivity`, `bundles`, `recommend`, `outcomes`; `services/run.py`, `services/decide.py`
- adapters: CLI commands `run` and `decide`
- `Params` v6 (§3); `StageRun.stage_sha256` (§3.3); package data `bundle_rules/`
- no new third-party dependency

**Facts about the workspace** (structure only)
- Both fuels are priced: `cost_elec` (current CDR plans plus manual offers, two scenarios) and `cost_gas` (a partial
  gas year, warned).
- `backtest.tariffs` and `backtest.gas_tariffs` each end in an open range (`to: null`): the plan in force today for that
  fuel. The earlier electricity range is closed: a historical manual tariff, which must not be offered as a candidate.
- `private/config.toml` may or may not hold `current_plans`; agents never read it (§6.3 handles both).
- The workspace has no `data/runs/` or `data/decisions/` yet; `pipeline sync` creates them (§3.4).
- Pricing every electricity tariff once over the reference year takes about two seconds, so sensitivity can reprice
  every tariff (§4).

---

## 2. Order of work

`/done` of P6.1.1 turns the rows into sub-tasks.

| # | Sub-task | Builds | Sections |
|---|---|---|---|
| 1 | P6.2.1 Foundations | `Params` v6 and its migration; `StageRun.stage_sha256`; `bundle_rule` model, loader and the package directory; `pipeline sync` creates `config/bundle_rules.local/`, `data/runs/`, `data/decisions/`; the P6 warnings; costing helpers shared by `cost_elec` and `sensitivity`; golden schemas | §3, §5.1, §11 |
| 2 | P6.2.2 `sensitivity` | the stage, its kinds, golden `dvc.yaml`, fixture workspace | §4 |
| 3 | P6.2.3 `bundles` | `core/ranking.py` (options), the stage, its kinds | §5 |
| 4 | P6.2.4 `recommend` | `core/models/recommend.py`, the payload and ID, `core/recommendation_report.py`, the stage | §6, §7 |
| 5 | P6.2.5 `energatron run` | `services/run.py`, `run_manifest`, `run_result`, the command | §8 |
| 6 | P6.2.6 `decide`, `outcomes` and the end-to-end test | `services/decide.py`, `decision`, `decide_result`, the command; the `outcomes` stage; `test_p6_pipeline` | §9, §10, §12 |
| 7 | P6.3.1 Acceptance | in the real workspace; the workspace skills `explain-recommendation`, `monthly-check-in`, `record-decision` (plan §14.5) | §14 |

`Params` v6 and `StageRun.stage_sha256` change every stage's code entry; `fingerprint` (or `energatron run`) refreshes
it in the workspace. Each stage sub-task updates the golden `dvc.yaml` and the fixture workspace for its stage.

---

## 3. Foundations

### 3.1 `params` v6 (`core/models/params.py`)

```yaml
recommend:
  top: 5                    # options shown in the recommendation (≥ 1)
  usage_scale_pct: "10"     # sensitivity: usage scaled up and down by this percentage (> 0, < 100)
  min_saving: "50"          # dollars a year, GST-inclusive: a smaller saving than this means "stay"
  recheck_days: 90          # recheck_by is at most this many days after as_of …
  price_reset: "07-01"      # … and never later than the next MM-DD on which network tariffs reset
```
- Decimal strings as elsewhere (`gst_rate`); `price_reset` a valid `MM-DD`.
- Migration v5 → v6 inserts the block with these defaults. No region field: the 1 July reset is national (NEM).

### 3.2 Candidates and history

A **candidate** is a tariff the household could switch to or stay on. Every tariff in a cost stage's `totals.parquet`
is a candidate except a manual offer named only by **closed** ranges of `backtest.tariffs` or `backtest.gas_tariffs`
(`to` not null): that tariff is history, kept for the backtest. A manual offer in an open range is the plan in force; a
manual offer in no range (a quote) is an ordinary candidate. `bundles` and `recommend` apply this rule
(`core/ranking.py: candidates(totals, backtest)`); the cost stages keep pricing everything.

### 3.3 `StageRun.stage_sha256`

`StageRun` gains a read-only `stage_sha256: str`, the live code entry `run_stage` was given. Only `recommend` reads it
(the payload, §7.3); it is already in every output's provenance, so no determinism rule changes.

### 3.4 `pipeline sync`

Creates, when missing, and lists among the files to commit:
- `config/bundle_rules.local/README.md` (git-tracked config, like `config/offer_rules.local/`)
- `data/runs/README.md` and `data/decisions/README.md`, each directory then tracked with `dvc add` (pointers
  `data/runs.dvc`, `data/decisions.dvc`). The readme keeps the directory non-empty and says what it holds.

`--check` reports any of them missing as drift. `pipeline_sync_result` keeps its fields (the readmes and pointers go in
`pointer_files`).

### 3.5 Sharing the costing helpers

The billing-period helpers `cost_elec` keeps privately (the window from the usage summary, calendar months, rows in a
period) move to `services/stages/costing.py`, which every cost stage already imports, so `sensitivity` uses the same
code. `cost_elec`'s outputs don't change.

---

## 4. `sensitivity` (`services/stages/sensitivity.py`)

Plan §10.8: the top choice is "robust" only if it holds when the usage window, the usage level and the conditional
discounts change. The discount scenario is already priced by the cost stages; this stage prices the rest.

- Inputs: `offers` = `data/marts/offers`, `gas_offers` = `data/marts/offers_gas`, `local` =
  `data/interim/elec_interval_local`, `profile` = `data/marts/usage_profile`, `gas_profile` = `data/marts/gas_profile`.
  Params: `pricing`, `analysis.reference_months`, `gas`, `recommend`. Output `sensitivity` =
  `data/marts/costs/sensitivity`.
- Every tariff of each offers mart is priced in scenario `conditions_met` under three variations:
  - `usage_up` / `usage_down`: the reference usage scaled by `1 ± usage_scale_pct / 100`. Electricity: every interval's
    kWh of the reference register is multiplied before `prepare_usage` (the TOU shape is kept). Gas: each reference
    period's MJ multiplied and quantised to 6 places.
  - `alt_window`: the window before the reference. Electricity: the `reference_months` covered months (the usage
    profile's `monthly.parquet`, `covered`, the register `cost_elec` priced) immediately before the reference window's
    first month; fewer than `reference_months` → unavailable (`too_little_history`). Gas: the `gas_profile` periods
    before the reference, newest first, until they hold at least `gas.reference_bills` bills; not reached →
    unavailable.
- Annual figures as the cost stages compute them: electricity `annual_ongoing` = the 12 months' total; gas
  `annual_ongoing` = round(cost × 365 / days of the periods priced); `annual_year1` = `annual_ongoing` − one-off credits.
  Ranks per fuel and variation as `costing.rank`.
- A fuel with nothing priced in its cost stage (no register or window, no gas bills) is `unavailable` (`no_usage`) in
  every variation.
- Outputs:
  - `totals.parquet` (`sensitivity_total` v1): `fuel` (`electricity`|`gas`), `variation`, `offer_snapshot_id`,
    `annual_ongoing`, `annual_year1` (decimal128(18,2)), `rank`; sorted by fuel, variation, rank.
  - `summary.json` (`sensitivity_summary` v1): per fuel and variation `{available, reason, window_start, window_end,
    factor}`; `tariffs` per fuel; `usage_scale_pct`; checks.
- Checks (fail): `energy_scaled` — per fuel and available variation, the usage lines' quantities add up to the scaled
  (or alternative-window) usage: electricity within 1e-4 kWh per period, gas exactly.
- Warning `W_SENSITIVITY_UNAVAILABLE` for each fuel with usage whose `alt_window` is unavailable.

---

## 5. Bundles

### 5.1 `bundle_rule` v1 (`core/bundle_rules.py`)

Dual-fuel deals aren't expressed in CDR data in a form the engine can use, so they are curated rules, as offer rules
are (P4 spec §6): package data `bundle_rules/` (curated, shipped empty with a readme in P6) and the workspace's
`config/bundle_rules.local/` (a local rule with a curated rule's `id` replaces it).

```yaml
kind: bundle_rule
schema_version: 1
id: <kebab-case>
description: <why>
brand: <brand>                    # matched case-insensitively against both tariffs' `brand`
electricity_plan: <regex> | null  # full match on the electricity tariff's plan_id; null = any plan of the brand
gas_plan: <regex> | null
effect: {annual_credit: "<$>"}    # or {percent_off: "<pct>"}: exactly one
verified_on: "YYYY-MM-DD"
cites: []                         # offer snapshot IDs the rule was read from
```
- Amounts GST-inclusive, as annual totals are. `extra="forbid"`; quoted dates and decimals; `annual_credit` > 0,
  `0 < percent_off < 100`. Invalid file → `E_INVALID_INPUT` naming the file and field. Duplicate `id` within one
  directory → `E_INVALID_INPUT`.

### 5.2 Options (`core/ranking.py`)

An **option** is a pair (electricity tariff or none, gas tariff or none) with a combined annual cost. For one variation:
- **independent:** the rank-1 candidate of each fuel that has candidates.
- **same brand:** for every brand with candidates in both fuels, every pair of that brand's candidates, with the
  matching rules applied; the cheapest pair per brand (ties: smaller electricity, then gas snapshot ID) is the brand's
  option.
- Applying rules: the pair's base is `elec.annual_ongoing + gas.annual_ongoing` (and the same for `annual_year1`).
  Each matching rule, in `id` order, adds an adjustment: `−annual_credit`, or `−round(base × percent_off / 100)` taken
  from the pre-adjustment base. A rule matches when the brands match each other and the rule, and both plan patterns
  match (null matches anything).
- The options are de-duplicated by pair (a pair found both ways keeps the same-brand form, whose cost is never higher)
  and ranked by combined `annual_ongoing` (ties: `option_key`). `option_key` = `<elec snapshot or ->|<gas snapshot or
  ->`.
- With one fuel only, every option is that fuel's rank-1 tariff; same-brand options need both fuels.

### 5.3 `bundles` (`services/stages/bundles.py`)

- Inputs: `elec` = `data/marts/costs/elec`, `gas` = `data/marts/costs/gas`, `sensitivity` =
  `data/marts/costs/sensitivity`, `offers` = `data/marts/offers`, `gas_offers` = `data/marts/offers_gas` (brands and
  plan IDs), `rules` = `config/bundle_rules.local`; package data `bundle_rules`. Params: `backtest`. Output `bundles` =
  `data/marts/costs/bundles`.
- Variations: `base` (the cost stages' `conditions_met`), `conditions_not_met` (theirs), and `usage_up`, `usage_down`,
  `alt_window` (sensitivity). A variation unavailable for one fuel uses that fuel's `base` figures; one unavailable for
  every fuel with candidates is skipped and listed.
- Outputs:
  - `options.parquet` (`bundle_option` v1): `variation`, `rank`, `option_key`, `kind` (`independent`|`same_brand`),
    `brand` (null for independent), `elec_snapshot_id`, `gas_snapshot_id`, `elec_annual_ongoing`,
    `gas_annual_ongoing`, `adjustment`, `annual_ongoing`, `annual_year1`, `rules` (ids); sorted by variation, rank.
  - `summary.json` (`bundles_summary` v1): variations used and skipped, candidates per fuel, history excluded per fuel,
    rules loaded and applied, option counts, checks.
- Checks (fail): `rules_valid` (every rule file loads); `eligibility_applied` (every same-brand option's tariffs share
  a brand and every option's tariffs are candidates, §3.2); `totals_add_up` (each option's `annual_ongoing` =
  elec + gas + adjustment, exactly).

---

## 6. `recommend` (`services/stages/recommend.py`)

### 6.1 Inputs and params

`bundles` = `data/marts/costs/bundles`, `elec` = `data/marts/costs/elec`, `gas` = `data/marts/costs/gas`, `offers` =
`data/marts/offers`, `gas_offers` = `data/marts/offers_gas`, `profile` = `data/marts/usage_profile`, `gas_profile` =
`data/marts/gas_profile`, `catalog` = `data/marts/catalog`, `private_config` = `private/config.toml`. Params:
`recommend`, `backtest`. Output `recommendation` = `data/reports/recommendations` (`recommendation.json`, `index.html`).

### 6.2 What it decides

- **as_of** = the later of the two offers marts' `as_of`; neither → the check `as_of_known` fails.
- **recheck_by** = the earlier of `as_of + recheck_days` and the first `price_reset` date after `as_of`.
- **Priced lists:** per fuel, every candidate (§3.2) from the cost stage's `conditions_met` totals: snapshot, plan ID,
  brand, display name, source, status, approximations, notes (eligibility and conditions as the normaliser recorded
  them), `annual_ongoing`, `annual_year1`, rank among candidates.
- **Options:** the `base` options of `bundles`, top `recommend.top`, each with its two tariffs' details.
- **Current plan** per fuel (§6.3) and the current pair's combined cost (bundle rules applied as in §5.2).
- **Savings** per option = current − option, ongoing and year 1, when there is a current plan for every fuel the
  option prices; otherwise null.
- **Action:** `no_current_plan` when a fuel with candidates has no current plan; else `stay` when the top option is the
  current pair or its ongoing saving is below `min_saving`; else `switch`.
- **Sensitivity:** for each variation of `bundles` other than `base`: the rank-1 option there, the base top option's
  rank there, `holds` (the base top option is rank 1). `robust` = it holds in every variation used.

### 6.3 The current plan

Per fuel: `current_plans.<fuel>` from the private config when set; else the snapshot of the open range of
`backtest.tariffs` (electricity) or `backtest.gas_tariffs` (gas); else none. `source` records which
(`private_config`|`backtest`). A named snapshot that isn't priced → none, with `W_REC_CURRENT_NOT_PRICED`; none for a
fuel with candidates → `W_REC_NO_CURRENT_PLAN`. Only the public snapshot IDs leave the private config; its hash is in
the provenance as for any input.

### 6.4 Checks (fail)

- `as_of_known`; `options_present` (at least one option when any fuel has candidates).
- `ids_resolve`: every cited snapshot ID is in its offers mart's `tariffs.json`, and every cited dataset ID is in
  `marts/catalog`'s datasets.
- `id_recomputes`: the written document's ID equals `rec_id(as_of, payload)` recomputed from the written payload.

---

## 7. The recommendation document

### 7.1 `recommendation` v1 (`core/models/recommend.py`)

```
recommendation_id, as_of, recheck_by, action: switch|stay|no_current_plan, robust: bool,
usage: {electricity: {window_start, window_end} | null,
        gas: {reference_start, reference_end, reference_days, partial_year} | null},
current: {electricity: CurrentPlan | null, gas: CurrentPlan | null, annual_ongoing | null, annual_year1 | null},
options: [Option{rank, option_key, kind, brand, electricity: Priced | null, gas: Priced | null, adjustment, rules,
                 annual_ongoing, annual_year1, saving_ongoing | null, saving_year1 | null}],
sensitivity: [Variation{variation, top_option_key, base_top_rank | null, holds}],
priced: {electricity: [Priced…], gas: [Priced…]},
inputs: {dataset_ids: [...], offer_snapshot_ids: [...], params_sha256, stage_sha256},
checks: {name: bool}
```
`CurrentPlan` = `{offer_snapshot_id, source, annual_ongoing, annual_year1}`. Money is a decimal string with 2 places.
Lists are sorted (options and priced by rank, IDs lexically).

### 7.2 Citations

`inputs.dataset_ids` = the union of the `dataset_ids` of every stage input's `_provenance.json` (D1 §10), sorted;
`inputs.offer_snapshot_ids` = every snapshot in `priced` and `current`, sorted; `params_sha256` =
`content_hash` of the declared params values; `stage_sha256` = `StageRun.stage_sha256`. The run ID and commit aren't in
the document (D1 §9.4): the run manifest (§8.2) and the run commit's message name the recommendation, which is how a
recommendation is linked to its run and commit (plan §11).

### 7.3 The payload and ID (D1 §6.3)

`payload` = the document's JSON (`model_dump(mode="json")`) without `recommendation_id`. `recommendation_id =
rec_id(as_of, payload)`. Everything in it is content: no clock, run ID or commit, so re-running the same inputs, params
and code reproduces the same ID (plan §13 determinism test).

### 7.4 The page (`core/recommendation_report.py`)

`index.html`: one deterministic, self-contained page (inline CSS, no script, no external asset), like the usage
report. Sections: the action and saving in one line; as of and recheck by; the current plan; the top options table;
the sensitivity table with the robust verdict; the best electricity and gas plans (top `recommend.top` of `priced`),
with their notes; the partial gas year warning when set; the cited IDs inside `<details>`. Every text is HTML-escaped.

---

## 8. `energatron run` (`services/run.py`)

### 8.1 Steps

`run(ctx, *, tool: ToolState, draft: bool) -> RunResult`; the command takes the lock (D1 §8.8). `ToolState` (from the
live code fingerprint the CLI computes: `energatron_version`, `install`, `tool_commit`, `tool_dirty`) is passed in, as
`verify` gets its fingerprints.
1. `pipeline_sync.check` must pass (`E_PIPELINE_NOT_SYNCED`).
2. Unless `draft`: no tracked config may be dirty — `params.yaml`, `dvc.yaml`, `config/`, `private.dvc`, and every
   `*.dvc` under `data/` except `data/runs.dvc` — else `E_DIRTY` with `details.paths`; and the tool must be clean
   (`tool_dirty` false), else `E_DIRTY` with `details.reason: "tool_dirty"`.
3. `head = vcs.head()` (none → `E_DIRTY`, `reason: "no_commit"`); `run_id = run_id(clock.now(), head)`.
4. `runner.repro()` (it refreshes the fingerprint). A failure propagates unchanged; nothing is written or committed.
5. Writes `data/runs/<run_id>.json` (§8.2), then `vcs.track(["data/runs"])`.
6. Commits exactly: `dvc.lock`, `.energatron/code_fingerprint.json`, `data/runs.dvc`, and any dirty `.gitignore` under
   `data/` (DVC adds lines for new outputs). Message `run: <run_id> <recommendation_id>`, or `run: <run_id>` with no
   recommendation. Ends with `W_BACKUP_PENDING` as `land` does (D1 §4.4).
- A draft run does the same; only the manifest's `draft: true` marks it, and a decision can't cite it (§9.1).

### 8.2 `run_manifest` v1

```
run_id, draft, started_at, finished_at (UTC), workspace_head (full SHA at the start),
tool: {energatron_version, install, tool_commit | null, tool_dirty},
stages: {ran: [...], restored: [...], cached: [...]},   # dvc.yaml order, from ReproReport
datasets: [...],                                         # every catalogued dataset ID, sorted
fetch_batches: [...],                                    # the batches the two offers marts' summaries name, sorted
recommendation_id | null, action | null, robust | null
```
Immutable (D1 §5); never rewritten.

### 8.3 `run_result` v1 and the command

`energatron run [--draft] [--json]`: `run_id`, `draft`, `recommendation_id`, `action`, `robust`, `ran`, `restored`,
`cached` (counts), `commit` (SHA). Human output: one line per field.

---

## 9. `energatron decide` (`services/decide.py`)

### 9.1 Rules

`energatron decide <REC-id> --switch-date YYYY-MM-DD [--elec <snapshot>] [--gas <snapshot>] [--evidence <dataset_id>]…
[--note <text>] [--dry-run] [--json]`; takes the lock.
- The ID must parse (`E_USAGE`) and be the `recommendation_id` of the current
  `data/reports/recommendations/recommendation.json` (`E_INVALID_INPUT`, `reason: not_current`).
- A non-draft manifest in `data/runs/` must name it (`E_INVALID_INPUT`, `reason: not_in_clean_run`); the newest such
  manifest is the decision's `run_id`.
- At least one of `--elec`/`--gas` (`E_USAGE`); each must be in the recommendation's `priced` list for its fuel
  (`E_INVALID_INPUT`, `field`), which carries the projection copied into the decision.
- Each `--evidence` ID must be in the catalog (e.g. a landed plan fact sheet or a welcome letter); unknown →
  `E_INVALID_INPUT`. P6 adds no source type for fact sheets: a PDF lands under an existing type or waits (§15 Q6).
- `decision_id(local date of clock.now() in the region's zone, existing IDs in data/decisions/)`.
- Writes `data/decisions/<DEC-id>.json`, `vcs.track(["data/decisions"])`, commits `data/decisions.dvc` with
  `decide: <DEC-id> <REC-id>`; `W_BACKUP_PENDING`. `--dry-run` validates and writes nothing.
- It never edits `private/config.toml`; the result reminds the owner to set `current_plans` (and to add the new plan
  to `backtest.tariffs` / `gas_tariffs` from the switch date) once the switch happens.

### 9.2 `decision` v1 and `decide_result` v1

```
decision_id, decided_at (UTC), recommendation_id, run_id, switch_date,
choices: {electricity: Choice | null, gas: Choice | null},   # Choice{offer_snapshot_id, annual_ongoing, annual_year1}
evidence: [...], note: str | null
```
Immutable. `decide_result`: `decision_id`, `recommendation_id`, `run_id`, `path`, `commit` (null on a dry run),
`dry_run`.

---

## 10. `outcomes` (`services/stages/outcomes.py`)

- Inputs: `decisions` = `data/decisions`, `backtest` = `data/reports/validation/backtest`, `gas_bills` =
  `data/interim/gas_bills`. No params. Output `outcomes` = `data/reports/outcomes`.
- Each fuel's bills after a switch are attributed to the latest decision for that fuel whose `switch_date` is on or
  before the bill's `period_start`. Electricity bills are `backtest`'s periods with a matched invoice
  (`invoice_amount`); gas bills are `gas_bills`' current bills (`total`).
- Per bill: `actual`; `projected` = round(choice `annual_ongoing` × days / 365); `diff`, `diff_pct`.
- Outputs: `outcomes.parquet` (`outcome_bill` v1: `decision_id`, `fuel`, `period_start`, `period_end`, `days`,
  `actual`, `projected`, `diff`, `diff_pct`); `summary.json` (`outcomes_summary` v1: per decision and fuel the bills,
  totals and `diff_pct`; decisions read; checks).
- Checks (fail): `decisions_valid` (every `DEC-*.json` validates and its file name is its ID). Files other than
  `DEC-*.json` (the readme) are ignored. With no decision it passes with empty outputs.

---

## 11. Kinds, codes and warnings added in P6

- Kinds: `params` v6, and v1 of `bundle_rule`, `sensitivity_total`, `sensitivity_summary`, `bundle_option`,
  `bundles_summary`, `recommendation`, `run_manifest`, `run_result`, `decision`, `decide_result`, `outcome_bill`,
  `outcomes_summary`. Golden schemas for each.
- Codes: none new.
- Warnings: `W_SENSITIVITY_UNAVAILABLE`, `W_REC_NO_CURRENT_PLAN`, `W_REC_CURRENT_NOT_PRICED`.
- Golden `dvc.yaml`: twenty stages, the four new ones after `backtest_gas` in the order `sensitivity`, `bundles`,
  `recommend`, `outcomes`.

---

## 12. Tests

- **Core:** `test_bundle_rules` (model rules, loading, local replacing curated); `test_ranking` (candidates and
  history, independent and same-brand options, rule effects, de-duplication, ties, one fuel only, sensitivity
  verdicts, current plan and action rules); `test_recommendation_report` (golden HTML for a synthetic document, escaping,
  no script).
- **Stages:** one module each, every check passing and failing; `recommend`'s ID is stable across two runs and changes
  when a cited input changes.
- **Services:** `test_run` (dirty config, dirty tool, `--draft`, manifest fields, commit paths and message, failed
  repro writes nothing, `W_BACKUP_PENDING`); `test_decide` (each refusal, dry run, ID numbering, projections copied).
- **CLI:** `run --json` and `decide --json` print exactly one document (D1 §13).
- **Integration:** `test_p6_pipeline` in a scratch workspace: the P5 fixture zone, `energatron run` (clean, via the
  service with a clean `ToolState`), a `REC-…` cited by the manifest and the commit message; `decide`; a second run
  (outcomes rerun, same `REC-…`); `verify`. `test_determinism` gains the whole-pipeline REC check (D1 §13).

---

## 13. Plan and D1 changes

To be applied on `/done` of P6.1.1, as plan rev 14 and D1 rev 7.

**Plan**
- §9.1 DAG and §9.2 table: `sensitivity` (offers marts, local intervals, usage and gas profiles), `bundles` (cost marts,
  sensitivity, offers marts, `config/bundle_rules.local`, params `backtest`), `recommend` (§6.1 inputs; outputs
  `recommendation.json` and `index.html`, not `REC-*.json`), `outcomes` (decisions, backtest report, gas bills).
- §9.2 `params.yaml`: v6 adds `recommend` (§3.1).
- §9.4: the manifest's fields (§8.2); a draft run commits like a clean one and is marked only in its manifest; the
  run refuses dirty tracked config or a dirty tool without `--draft`; no `rec/` tag in P6 (the commit message names the
  recommendation; `git log --grep` finds it).
- §10.7: bundle rules are curated YAML (§5.1); options as §5.2; manual offers named only by closed backtest ranges
  are history, not candidates (§3.2).
- §10.8: the current plan comes from `current_plans`, else the open backtest range (§6.3); sensitivity as §4 and §6.2;
  `recheck_by` as §6.2.
- §11 step 4 and §12: `decide` as §9 (evidence is any landed dataset ID).
- §15 P6 acceptance made concrete as §14.

**D1 (rev 7)**
- §2: core gains `ranking.py`, `bundle_rules.py`, `recommendation_report.py`, `models/recommend.py`; services gain
  `run.py`, `decide.py` and the four stages (`compare.py`, `explain.py` stay later); package data `bundle_rules/`; CLI
  commands `run` and `decide`.
- §6.3: the recommendation payload is §7.3.
- §7: the three P6 warnings.
- §9.2: `StageRun.stage_sha256`.
- §14: the payload, manifest and decision schema are settled by this spec.

---

## 14. P6 acceptance checks

In the real workspace, with the tool committed. `TASKS.md` gets counts and pass/fail only; figures go in a workspace
note.
1. `pipeline sync` (params v6, the three new directories), commit what it lists.
2. `energatron run --json` (not draft): twenty stages pass; a `REC-…`; the manifest names it, with the run ID, the
   datasets and fetch batches; the run commit's message names both IDs. **Plan §15:** the recommendation cites offer
   snapshot IDs and dataset IDs, and the run ID and commit through its manifest and commit.
3. `verify` passes: every stage, `recommend` included, reruns byte-identically from the committed inputs, so the same
   `REC-…` comes back (**plan §15:** a re-run reproduces the recommendation ID). A second `energatron run` with nothing
   changed reports every stage cached and the same ID.
4. `decide <REC> --dry-run` with the top option validates (**plan §15:** decision entries link back: the dry run's
   result names the recommendation and the run). No real decision is recorded: switching plans is the owner's call.
5. The workspace skills `explain-recommendation`, `monthly-check-in` and `record-decision` exist, call only the CLI,
   and are linked (`link_parent.py`).
6. `dvc status` clean; privacy guard 0 violations; the pending backup is reported.

---

## 15. Open questions / decisions for the owner

Each is decided here so P6 can be built; the recommendation is what the spec says.
1. **Sensitivity reprices every tariff** in a separate stage, rather than only the top few: it is fast (§1) and a
   lower-ranked plan can overtake under more usage.
2. **History is excluded by the backtest ranges** (§3.2): no new field on `offer_manual`.
3. **The current plan falls back to the open backtest range** when the private config has none (§6.3), so a
   recommendation has savings without anyone editing `private/`.
4. **Bundle rules are curated YAML, shipped empty.** CDR data doesn't express dual-fuel discounts in a form the engine
   can price; a rule is added when one is found in a plan's terms.
5. **One recommendation file per output** (`recommendation.json`), not `REC-<id>.json`: the stage output is replaced
   on every run, the ID is inside, and the run manifests keep the history of IDs.
6. **No fact-sheet source type in P6.** `decide --evidence` takes any landed dataset ID; a `offer_pdf` source type
   (plan §8.2's "BPID or fact-sheet PDF evidence") can come with P7 or later.
7. **Draft runs commit too** (§8.1), marked only in the manifest, so the workspace never holds an uncommitted
   `dvc.lock` after a run.
8. **No `rec/` tags** in P6: the commit message is enough for `git log --grep`, and the port gains no method.
