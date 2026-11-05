# Tasks archive: P4 offers and the electricity engine

- **Created:** 2026-11-05
- **Status:** Archive. `/done` moved these lines here verbatim from `TASKS.md` when P4.3.2 closed phase P4.
- **Privacy note:** this file lives in the public-ready tool repo. It contains no identifiers or usage figures,
  and nothing added to it may contain them either.

---

### P4: Offers + electricity engine ✅

Built from the P4 spec (`agent_docs/2026-11/2026-11-04_p4-offers-electricity-engine-spec.md`, "the P4 spec") in its §2
order; D1 rev 5 binds all code. The spec's decisions (approved 2026-11-04 on the owner's standing instruction, Q1-Q8 as
recommended): retailers are CDR base URIs from the region profile; the latest fetch log per base defines current plans;
controlled-load variants and annual-period block rates are excluded; monthly calendar billing periods; backtest breaches
warn; the historical tariff comes from the owner; no new dependency.
- P4.2.1 changes every stage's code entry (`Params` v4); re-run `fingerprint` in a workspace before any `dvc repro`.
- Each stage sub-task adds its stage to `STAGES`, the golden `tests/fixtures/golden/dvc.yaml` and the fixture workspace.
- Retailer plan details (rates, windows, dates) are public; only the household's usage and identifiers are private.

#### P4.1 P4 spec ✅
- ✅ **P4.1.1** Draft the P4 offers and electricity engine spec, `agent_docs/2026-11/2026-11-04_p4-offers-electricity-engine-spec.md`.
  - Covers the CDR client and `fetch-offers`, `offer_manual`, the `Tariff` model and CDR mapping, offer rules,
    `normalise_offers`, the engine, `cost_elec`, `backtest`, the offer diff, `params` v4 and the region profile, the
    tests, plan §15's P4 acceptance made concrete, an order of work.
  - Refs: plan §8.2, §8.4, §9.1-9.2, §10.4-10.5, §10.8-10.9, §13, §15, §16 Q4-Q7; D1 rev 4 §4.6, §4.8, §5, §6, §9-§11,
    §12; D3 §5.7; the P2 spec §9.3; the P3 spec §8, §14.
  - Files: the new spec.
  - Done when: the owner approves it.
  - Handover: P4 spec approved 2026-11-04 (Q1-Q8 as recommended, on the owner's standing instruction); `/done` applied plan rev 12 and D1 rev 5 (spec §15) and rewrote P4.2-P4.3 from spec §2. Decision: CDR base URIs, not brand slugs (RAA Energy is served from `energy-locals`, with no current SAPN plan).
    Watch: no historical tariff exists for the backtest until the owner writes an `offer_manual` (P4.3.2).

#### P4.2 Build ✅
- ✅ **P4.2.1** Foundations: `Params` v4, `RegionProfile` v3, `pipeline sync` for the offer fields, the P4 warnings.
  - `core/models/params.py` v4 (`offers`, `pricing`, `backtest`, `validation.backtest`, the v3 to v4 migration with
    `null` region fields, `Params.defaults(profile)`); `core/models/region.py` v3 and `regions/sa.yaml` v3
    (`distributor`, `cdr_retailers`), `build_context()` refusing unset values; `pipeline sync` fills `offers.retailers`
    and `offers.distributor`, reports `null` as drift, creates `config/offer_rules.local/README.md`; the four P4
    warnings; golden schemas; `params_v4_text` in `tests/fixtures/params_text.py`.
  - Refs: the P4 spec §4, §12; D1 rev 5 §5, §7; the P3 spec §4.3 (the same fill pattern).
  - Files: `src/energatron/core/{errors.py,models/params.py,models/region.py}`, `src/energatron/regions/sa.yaml`,
    `src/energatron/services/pipeline_sync.py`, `src/energatron/adapters/wiring.py`, `tests/unit/core/test_{params,region}.py`,
    `tests/unit/services/test_pipeline_sync.py`, `tests/architecture/test_no_region_values.py`,
    `tests/fixtures/{params_text.py,schemas/}`.
  - Done when: the standard checks pass; the golden v3 `params` and v2 `region_profile` documents upgrade and validate;
    a stage reading a `null` `offers.retailers` fails with `E_INVALID_INPUT`.
  - Handover: `Params` v4 (`offers`, `pricing`, `backtest`, `validation.backtest`; v3 to v4 migration with `null` retailers and distributor), `RegionProfile` v3 and `sa.yaml` v3 (SA Power Networks, 28 CDR base URIs), `pipeline sync` fills both and creates `config/offer_rules.local/README.md`, four P4 warnings, `params_v4_text` (2815 tests). Decision: `pointer_files` also lists the rules readme (no version bump); spec §1/§4.2 corrected to 27 bases (all 93 AER bases probed).
    Watch: every stage's code entry is stale until a workspace runs `fingerprint`; P4 stage tests need `params_v4_text` (DVC reads keys from the file).
- ✅ **P4.2.2** Tariff model and CDR mapping.
  - `core/tariff.py` (`Tariff` and parts, partition checks, inclusive-to-half-open windows, the two GST constants),
    `core/cdr_tariff.py` (`map_cdr` → `Tariff` or `Excluded`, every row of spec §5.2), `tests/fixtures/make_cdr.py`.
  - Refs: the P4 spec §1 (CDR facts), §5.1, §5.2, §14.
  - Files: `src/energatron/core/{tariff.py,cdr_tariff.py}`, `tests/fixtures/make_cdr.py`,
    `tests/unit/core/test_{tariff,cdr_tariff}.py`, `tests/unit/fixtures/test_generators.py`.
  - Done when: the standard checks pass; one test per row of spec §5.2's table.
  - Handover: `core/tariff.py` (`Tariff`, `Season`, `EnergyCharge`, `Window`, `Block`, `DemandCharge`, `Discount`, `Fee`; partition checks; the two GST constants) and `core/cdr_tariff.py` (`map_cdr(data, Plan, Household, as_of=)` → `Tariff` or `Excluded`, every §5.2 row tested), `tests/fixtures/make_cdr.py` (2907 tests). Decision: the clock lives on each `Season` (a CDR tariff period may override `timeZone`); unpriced discounts are notes, so `Discount.kind` is guaranteed or conditional only.
    Watch: run on the 609 real SAPN plans in the agent's scratch folder the mapper gave 189 normalised, 81 approximate, 339 excluded, 0 malformed (counts of public offers).
- ✅ **P4.2.3** Offer sources and rules: the fetch log, `offer_manual`, `offer_rule`.
  - `core/models/offers.py` (`OfferFetchLog`, `OfferManual`); parsers `adapters/sources/{cdr_index,offer_manual}.py`
    and `regions/sa.yaml` listing them; `core/offer_rules.py` (model, loading from two directories, matching, effects);
    curated `src/energatron/offer_rules/wholesale-pass-through.yaml`; `make_offer_manual` and a fetch-log generator.
  - Refs: the P4 spec §3.3, §5.3, §6; D3 §5.7, §6.
  - Files: `src/energatron/core/{models/offers.py,offer_rules.py}`, `src/energatron/adapters/sources/`,
    `src/energatron/regions/sa.yaml`, `src/energatron/offer_rules/`, `tests/fixtures/make_cdr.py`,
    `tests/unit/core/test_offer_rules.py`, `tests/unit/adapters/sources/`, `tests/fixtures/schemas/`.
  - Done when: the standard checks pass; `offer_cdr` never detects a fetch log and `cdr_index` never detects a plan.
  - Handover: `OfferFetchLog` and `OfferManual` (`core/models/offers.py`), parsers `cdr_index` and `offer_manual` registered and in `sa.yaml`, `core/offer_rules.py` (`load_rules`, `apply_rules`), three curated `offer_rules/*.yaml`, fixture builders (2960 tests). Decision: rules may also match `display_name` and three curated wholesale-estimate rules ship (spec §6 updated); `map_cdr` trims brands (a real brand has a trailing space).
    Watch: the fetch log must be written with sorted keys (`write_json`) so `kind` precedes the page bodies in the 64 KiB head.
- ✅ **P4.2.4** CDR client and `energatron fetch-offers`.
  - `adapters/offers/cdr.py` (`CdrProvider`), wired into `ServiceContext.offers`; `services/fetch_offers.py` and the
    command; `fetch_offers_result` v1.
  - Refs: the P4 spec §3.1, §3.2; D1 rev 5 §4.6, §8.6; D3 §7.
  - Files: `src/energatron/adapters/{offers/cdr.py,wiring.py,cli/}`, `src/energatron/services/fetch_offers.py`,
    `tests/unit/adapters/test_cdr_provider.py`, `tests/unit/services/test_fetch_offers.py`,
    `tests/unit/adapters/test_locking.py`, `tests/fixtures/schemas/`.
  - Done when: the standard checks pass; the tests of spec §14 (adapters, services) pass against a local HTTP server.
  - Handover: `CdrProvider` (`adapters/offers/cdr.py`: paging on the same host, at most 50 pages, header allow-list, `E_NETWORK` reasons) wired as `ctx.offers["cdr"]` (`ENERGATRON_CDR_ROOT` overrides the root), `services/fetch_offers.py` and `energatron fetch-offers` (filter, unchanged skip, `--all`, staging under `data/landing/.fetch-<batch>/`, one `land` commit), `fetch_offers_result` v1 (3003 tests). Decision: the result also counts `land`'s rejected files, and the log keeps per-base `listed` and `left_out` counts.
    Watch: the log's list pages hold public postcode lists verbatim (base64); only its own fields must never name the household's postcode.
- ✅ **P4.2.5** `normalise_offers`.
  - The stage, its three outputs and six checks, `W_OFFERS_MISSING_SNAPSHOT`, the golden `dvc.yaml`, the fixture workspace.
  - Refs: the P4 spec §6, §8, §14.
  - Files: `src/energatron/services/stages/{normalise_offers,registry}.py`, `src/energatron/core/models/costs.py`,
    `tests/unit/services/stages/test_normalise_offers.py`, `tests/fixtures/{pipeline_workspace.py,golden/dvc.yaml}`,
    `tests/fixtures/schemas/`.
  - Done when: the standard checks pass; every check passes and fails in its test.
  - Handover: `normalise_offers` (current plans from each base's latest error-free fetch log, newest snapshot per plan, `map_cdr` then rules; `offers.parquet`, `tariffs.json`, `summary.json`; six checks; `W_OFFERS_MISSING_SNAPSHOT`), the `offer_status`/`offer_tariffs`/`offers_summary` kinds (`core/models/costs.py`), `tests/fixtures/offer_workspace.py`, golden `dvc.yaml` with nine stages (3032 tests). Decision: a manual offer is judged effective on its own dates, so historical tariffs stay priceable for the backtest.
    Watch: fixture raw zones now create `config/offer_rules.local/` (a DVC dependency), and tests that run `dvc repro` need `params_v4_text`.
- ✅ **P4.2.6** The engine.
  - `core/engine.py` (spec §7), the oracle additions in `tests/fixtures/oracle.py`, property tests.
  - Refs: the P4 spec §7, §14.
  - Files: `src/energatron/core/engine.py`, `tests/fixtures/oracle.py`, `tests/unit/core/test_engine{,_properties}.py`.
  - Done when: the standard checks pass; the engine equals the oracle on the fixtures; spec §7.9 holds.
  - Handover: `core/engine.py`: `prepare_usage(frame)` (arrays for both clocks), `price(tariff, usage, periods, scenario=, pricing=, by=)` → `Bill` of `LineItem`s (supply, usage by block, demand, discounts, GST, fees) and a one-off credit; equals the independent `oracle_bill` on DST-spanning half-hourly and 5-minute usage; the §7.9 properties hold (3062 tests). Decision: monthly blocks restart at a season boundary inside a period, and the season is chosen on the first season's clock.
    Watch: about 4 ms per tariff-year of 5-minute data; the scenario `backtest` prices conditional discounts like `conditions_met`.
- ✅ **P4.2.7** `cost_elec`.
  - The stage, its three outputs and four checks, the cost table kinds, the golden `dvc.yaml`, the fixture workspace.
  - Refs: the P4 spec §9; the P3 spec §8 (`usage_summary`).
  - Files: `src/energatron/services/stages/{cost_elec,registry}.py`, `src/energatron/core/models/costs.py`,
    `tests/unit/services/stages/test_cost_elec.py`, `tests/fixtures/{pipeline_workspace.py,golden/dvc.yaml}`,
    `tests/fixtures/schemas/`.
  - Done when: the standard checks pass; every check passes and fails in its test.
  - Handover: `cost_elec` (the one consumption register over the P3 reference window's calendar months, every tariff in both scenarios; `line_items.parquet`, `totals.parquet` with ranks, `summary.json`; four checks), the cost kinds in `core/models/costs.py`, shared `services/stages/costing.py`, golden `dvc.yaml` with ten stages (3082 tests). Decision: rates are stored as `decimal128(24, 10)` (manual offers keep ten places; D1 §12 updated), and `lines_sum` cross-checks the two tables.
    Watch: `cost_elec` reruns whenever the usage profile changes (it reads its summary); `run_stage_named` in `tests/integration/_stage_inputs.py` runs one stage for stage tests.
- ✅ **P4.2.8** `backtest`.
  - `core/retailer_txn.py`, the stage, its outputs, checks and two warnings, a transactions generator, the golden
    `dvc.yaml`, the fixture workspace.
  - Refs: the P4 spec §10; the P2 spec §9.3.
  - Files: `src/energatron/core/retailer_txn.py`, `src/energatron/services/stages/{backtest,registry}.py`,
    `tests/unit/core/test_retailer_txn.py`, `tests/unit/services/stages/test_backtest.py`,
    `tests/fixtures/{make_sources.py,pipeline_workspace.py,golden/dvc.yaml}`, `tests/fixtures/schemas/`.
  - Done when: the standard checks pass; a generated invoice equal to the engine's bill is `within`, one 2% off is
    `outside` and warns.
  - Handover: `core/retailer_txn.py` (`read_txn`: kind, time, amount; descriptions never leave it) and `backtest` (periods from the retailer reads, the tariff in force from `backtest.tariffs`, invoices matched within `invoice_window_days`, statuses within/outside/unmatched/no_tariff/split_tariff/not_covered; `bills.parquet`, `line_items.parquet`, `result.json`; three checks; two warnings), golden `dvc.yaml` with eleven stages (3106 tests). Decision: an invoice dated on or after a period's last date matches it, earliest first, amounts rounded to cents.
    Watch: with no `backtest.tariffs` the stage warns `W_BACKTEST_NO_TARIFF` and passes; the owner supplies the tariff in P4.3.2.
- ✅ **P4.2.9** `energatron offers diff` and the end-to-end P4 test.
  - `services/offer_diff.py`, the command, `offer_diff_result` v1; `tests/integration/test_p4_pipeline.py` (land,
    `fetch-offers` against a local HTTP server, `dvc repro`, `offers diff`, `verify`).
  - Refs: the P4 spec §11, §14; D1 rev 5 §11.4.
  - Files: `src/energatron/services/offer_diff.py`, `src/energatron/adapters/cli/`,
    `tests/unit/services/test_offer_diff.py`, `tests/integration/test_p4_pipeline.py`, `tests/fixtures/schemas/`.
  - Done when: the standard checks pass; the end-to-end test passes.
  - Handover: `services/offer_diff.py` and `energatron offers diff [--plan]` (the two newest snapshots of each plan mapped with a neutral household and compared field by field, signed deltas for numbers), `offer_diff_result` v1, `tests/integration/test_p4_pipeline.py` (sync, fixture raw zone, `fetch-offers` against a localhost CDR stand-in, `dvc repro` of all eleven stages, a second fetch, `offers diff`, `verify`) (3113 tests). Decision: the diff maps with no rules and a household that meets every eligibility rule, so excluded variants still diff.
    Watch: P4 build is complete; the workspace still runs params v3 and needs `pipeline sync` first (P4.3.1).

#### P4.3 Acceptance ✅
- ✅ **P4.3.1** P4 acceptance in the workspace.
  - The spec's §13 checks 1-8, in order. If the GST check fails, stop and ask the owner.
  - Refs: the P4 spec §13; plan §15 (P4).
  - Files (`demq-elektric/`): `dvc.yaml`, `dvc.lock`, `params.yaml`, `.energatron/code_fingerprint.json`,
    `config/offer_rules.local/README.md`, new `data/*/.gitignore` files; the `land` commit is `fetch-offers`' own; a
    dated note in `agent_docs/` for results that mention data.
  - Done when: checks 1-5, 7 and 8 pass; check 6 passes or waits on P4.3.2; `TASKS.md` gets counts and pass/fail only.
  - On /done: commit those files as one workspace commit.
  - Handover: Workspace synced to params v4 (eleven stages), `fetch-offers` landed 636 plans and a fetch log in one commit (28 bases, 0 failures), `dvc repro` and `verify` pass; 636 current plans: 191 normalised, 90 approximate, 355 excluded with reasons; GST convention verified exactly against EnergyAustralia's published SA standing-offer rates; backtest waits on P4.3.2. Decision: two acceptance fixes in the tool, `zero_priced` (companion export plans) and a curated rule excluding two first-bill-discount plans.
    Watch: a one-off percentage discount can't be priced yet (follow-up below); fees are taken as GST-inclusive without a checked source.
- ✅ **P4.3.2** Owner: the historical tariff, then back up after P4.
  - Why: owner step (the tariff in force comes from the owner's bills; iCloud).
  - The owner writes an `offer_manual` YAML for the plan in force, lands it, sets `backtest.tariffs`, runs `dvc repro`
    (spec §13 check 6, and check 4 if the agent couldn't render the BPID); then `icloud_backup.py backup`.
  - Refs: the P4 spec §5.3, §10, §13; the runbook.
  - Done when: the backtest is within ±1% or its deviations are explained in the workspace note, and `backup --status`
    shows `refs_backed_up: true`.
  - Handover: Two `offer_manual` tariffs transcribed by the agent from the owner's invoices (rates to 2025-07-03 and from 2025-07-04), landed, set in `backtest.tariffs`; backtest 7 within, 1 outside (a retailer double-billing, explained in the workspace note), 1 split, 1 unmatched; backup done. Decision: the agent wrote the tariffs at the owner's request.
    Watch: the `elec_bill` parser rejects these retailer PDFs (`no_creation_date`), so they are not landed.
