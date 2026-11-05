# Tasks archive: P5 gas

- **Created:** 2026-11-05
- **Status:** Archive. `/done` moved these lines here verbatim from `TASKS.md` when P5.3.1 closed phase P5.
- **Privacy note:** this file lives in the public-ready tool repo. It contains no identifiers or usage figures,
  and nothing added to it may contain them either.

---

### P5: Gas ✅

Built from the P5 spec (`agent_docs/2026-11/2026-11-05_p5-gas-spec.md`, "the P5 spec") in its §2 order; D1 rev 6 binds
all code. The owner asked (2026-11-05) for P5 to run end to end without stopping for `/next` and `/done`: the agent plays
the owner's part (approvals, reviews, commits), as P4's standing instruction did. Pushing, global installs, iCloud and
`private/` stay the owner's. The spec's decisions (Q1-Q8 as recommended): separate gas stages; no PDF text extraction;
a short reference is annualised by days and warned; usage spread evenly over a bill's days; eligibility as P4; `P1M`
gas blocks prorated by days; the tariff in force transcribed from the bill; the gas account number is the owner's to add.
- P5.2.1 changes every stage's code entry (`Params` v5); re-run `fingerprint` in a workspace before any `dvc repro`.
- Each stage sub-task adds its stage to `STAGES`, the golden `tests/fixtures/golden/dvc.yaml` and the fixture workspace.

#### P5.1 P5 spec ✅
- ✅ **P5.1.1** Draft the P5 gas spec, `agent_docs/2026-11/2026-11-05_p5-gas-spec.md`.
  - Refs: plan §8.2, §9.1-9.2, §10.6, §10.8-10.9, §15, §16 Q3; D1 rev 5; D3 §5.6; the P4 spec.
  - Files: the new spec; plan rev 13; D1 rev 6.
  - Done when: the owner approves it (here: the agent, on the owner's instruction of 2026-11-05, after a fresh review).
  - Handover: spec approved 2026-11-05 after a fresh review (11 findings applied: existing error codes only, `fuel_type` in the fetch log's selected plans, MJ checked to the printed precision, `gst_free` lines, reference and `P1M` rules); plan rev 13, D1 rev 6. Decision: separate gas stages.
    Watch: the CDR SA gas market is 73 single-rate plans from 9 bases (2026-11-05); the bill's plan isn't among them.

#### P5.2 Build ✅
- ✅ **P5.2.1** Foundations: `Params` v5, `RegionProfile` v4, `offer_fetch_log` v2, the per-fuel distributor filter, the P5 warnings.
  - `core/models/params.py` v5 (`offers.gas_distributor`, `GAS` fuel, `gas`, `backtest.gas_tariffs`,
    `validation.gas_bills`, migration v4 to v5); `core/models/region.py` v4 and `regions/sa.yaml` v4; `build_context()`
    refusing an unset gas distributor; `pipeline sync` fills it; `offer_fetch_log` v2 (`filter.gas_distributor`,
    `selected[].fuel_type`); `fetch-offers` per-fuel distributor; `W_GAS_GAP`, `W_GAS_PARTIAL_YEAR`; golden schemas;
    `params_v5_text`.
  - Refs: the P5 spec §4, §11; D1 rev 6 §5, §7; the P4 spec §3.2, §4.
  - Files: `src/energatron/core/models/{params,region,offers}.py`, `regions/sa.yaml`, `adapters/wiring.py`,
    `services/{pipeline_sync,fetch_offers}.py`, `core/errors.py`, tests and fixtures.
  - Done when: the standard checks pass; a v4 `params.yaml` syncs to v5 with the gas distributor filled.
  - Handover: params v5 (gas distributor, `GAS` fuel, `gas`, `backtest.gas_tariffs`, `validation.gas_bills`), region v4, fetch log v2 (`selected[].fuel_type`), fetch filter per fuel, two warnings, `params_v5_text`; self-review. Decision: the v4 to v5 migration leaves an empty fuel list invalid. Watch: stage tests that declare `offers` need v5 params text (a migrated gas distributor is `null`).
- ✅ **P5.2.2** Gas bill records: the `gas_bill_record` model and parser, `energatron gas-bill new`.
  - `core/models/gas.py` (record, §3.2 rules and conventions), `adapters/sources/gas_bill_record.py` (listed in
    `regions/sa.yaml`), `services/gas_bill.py`, `adapters/cli/commands/gas_bill.py`, `gas_bill_scaffold_result` v1;
    `tests/fixtures/make_gas.py`.
  - Refs: the P5 spec §3, §13; D3 §5, §6; the P4 spec §5.3 (the YAML parser pattern).
  - Files: the modules above, tests, golden schemas.
  - Done when: the standard checks pass; a scaffold can't land unfilled, a filled one lands.
  - Handover: `gas_bill_record` v1 (no identifier fields, quoted decimals keep their places, `gst_free` lines), its parser (listed in `sa.yaml`; every source type now has one), `gas-bill new` writing the skeleton to `data/landing/`, `make_gas.py`; self-review. Decision: an existing skeleton → `E_CONFIRMATION_REQUIRED` unless `--force`. Watch: `make_gas.record_text` quotes numbers and dates, which `land` requires.
- ✅ **P5.2.3** `gas_bills` and `gas_profile`.
  - `core/gas_bill.py` (validation, current records, combining estimates, gaps, reference), the two stages, their
    kinds, golden `dvc.yaml`, fixture workspace.
  - Refs: the P5 spec §6, §7, §11, §13; the P2 spec (stage checks pattern).
  - Files: the modules above, tests.
  - Done when: the standard checks pass; every check has a passing and a failing test.
  - Handover: `core/gas_bill.py` (checks at the printed MJ precision, combining, gaps, reference, monthly), stages `gas_bills` (ten checks, newest record per PDF) and `gas_profile` (three checks, `W_GAS_GAP`), `raw_zone.py` shared reader, gas fixture zone; golden `dvc.yaml` with 13 stages. Different from the spec: `lines.parquet` has `gst_free`, the profile summary `reference_bills`, the `pdfs` input is the gas_bill directory (catalog only read). Fresh review (2026-11-05): approve with changes, 4 should-fix and 3 nits, all fixed in a follow-up commit (bounded decimal places, reads oldest first, a PDF whose newest record is unusable has none, `read_types_flagged` recomputed, failing tests for every check).
- ✅ **P5.2.4** Gas offers: `GasTariff`, `map_cdr_gas`, `offer_manual` v2, `normalise_gas_offers`.
  - `core/gas_tariff.py`, `core/cdr_gas_tariff.py` (every row of §5.2), `offer_manual` v2 (`gasContract`), the shared
    normalise implementation with the fuel filter in `normalise_offers`; `make_cdr` gas contracts.
  - Refs: the P5 spec §5, §8, §13; the P4 spec §5, §6, §8.
  - Files: the modules above, tests, golden schemas and `dvc.yaml`.
  - Done when: the standard checks pass; `normalise_offers` ignores gas plans.
  - Handover: `GasTariff` (seasons with MJ block ladders), `map_cdr_gas` (every row of spec §5.2, sharing `cdr_tariff` helpers through public aliases), `offer_manual` v2 (`gasContract`), `services/stages/normalising.py` shared by `normalise_offers` and `normalise_gas_offers` (`marts/offers_gas`, `gas_offer_tariffs`), offer rules for both tariff types; the fixture offer zone gains two gas plans and a gas manual offer. Decision: the fuel comes from `selected[].fuel_type`. Watch: `normalise_offers` code entry changed (fingerprint in the workspace).
- ✅ **P5.2.5** Gas engine and `cost_gas`.
  - `core/gas_engine.py` (seasons, blocks, the shared settle step of `core/engine.py`), an oracle in
    `tests/fixtures/oracle.py`, property tests; the `cost_gas` stage and kinds.
  - Refs: the P5 spec §9, §13; the P4 spec §7, §9.
  - Files: the modules above, tests.
  - Done when: the standard checks pass; electricity engine tests unchanged and green.
  - Handover: `core/gas_engine.py` (seasons by days with the last taking the remainder, MJ block allowances, `P1M` prorated), `core.engine.settle` shared for discounts, GST and fees (electricity results unchanged), a day-by-day fraction oracle and property tests; `cost_gas` (three checks, annualised by days, `W_GAS_PARTIAL_YEAR`). Different from the spec: `cost_gas` declares only `pricing` (the partial-year flag comes from the profile) and passes with empty outputs when there are no gas bills. Watch: bill-derived numbers found in two earlier local commits (open question).
- ✅ **P5.2.6** `backtest_gas` and the end-to-end test.
  - The stage and kinds; `test_p5_pipeline` (land a PDF and its record, fetch gas plans from the local server, a gas
    `offer_manual`, `dvc repro` of sixteen stages, `verify`).
  - Refs: the P5 spec §10, §13; the P4 spec §10, §14.
  - Files: the stage, tests.
  - Done when: the standard checks pass.
  - Handover: `backtest_gas` (each bill as billed against `backtest.gas_tariffs`, statuses within/outside/no_tariff/split_tariff, two checks, P4 warnings), `gas_backtest_bill`, `gas_backtest_result`; golden `dvc.yaml` with sixteen stages; `test_p5_pipeline` (land PDF, `gas-bill new`, land record, fetch gas plans, manual tariff, repro, verify). Fresh review of P5.2.4-P5.2.6 (2026-11-05): approve with changes, 2 should-fix and 4 nits; fixed in a follow-up commit (one ranking helper, fuel-split tests, the closing season takes the remainder, `as_of` from this fuel's manual offers); left: the `cdr_tariff` helper aliases (a later rename), `GasTariff` tests live in `test_cdr_gas_tariff.py`.

#### P5.3 Acceptance ✅
- ✅ **P5.3.1** P5 acceptance in the workspace, and the `transcribe-gas-bill` workspace skill.
  - Refs: the P5 spec §12; plan §14.5.
  - Files: `demq-elektric` (`params.yaml`, `dvc.yaml`, `dvc.lock`, pointers, `.claude/skills/transcribe-gas-bill/`,
    a workspace note); `TASKS.md`.
  - Done when: §12's checks pass or are explained in the workspace note; the pending backup is reported.
  - Handover: P5 acceptance: params v5 synced, the gas bill PDF and its agent transcription landed (`transcribe-gas-bill` skill added), 73 gas plans fetched, the tariff in force as an `offer_manual` v2, sixteen stages pass, `verify` ok; plan §15 checks 1 and 2 pass (the second partly circular). Watch: one autumn bill, so the gas reference is a partial year; backup pending.
