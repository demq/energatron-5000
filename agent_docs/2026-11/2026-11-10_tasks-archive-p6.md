# Tasks archive: P6 bundles, recommendation and decisions

- **Created:** 2026-11-10
- **Status:** Archive. These lines were moved here verbatim from `TASKS.md` when P6.3.1 closed phase P6.
- **Privacy note:** this file lives in the public-ready tool repo. It contains no identifiers or usage figures,
  and nothing added to it may contain them either.

---

### P6: Bundles, recommendation, decisions ✅

Built from the P6 spec (`agent_docs/2026-11/2026-11-10_p6-recommendation-spec.md`, "the P6 spec") in its §2 order; D1
rev 7 binds all code. The owner asked (2026-11-10) for P6's tasks to be added and built as far as possible without
stopping for review or `/done`: the agent plays the owner's part (approvals, reviews, commits), as for P4 and P5.
Pushing, global installs, iCloud, `private/` and real plan decisions stay the owner's. The spec's decisions (Q1-Q8):
sensitivity reprices every tariff in its own stage; history = manual offers named only by closed backtest ranges; the
current plan falls back to the open backtest range; bundle rules are curated YAML shipped empty; one
`recommendation.json`; no fact-sheet source type; draft runs commit; no `rec/` tags.
- P6.2.1 changes every stage's code entry (`Params` v6, `runner.py`); re-run `fingerprint` in a workspace before any
  `dvc repro` (or use `energatron run` once it exists).
- Each stage sub-task adds its stage to `STAGES`, the golden `tests/fixtures/golden/dvc.yaml` and the fixture workspace.

#### P6.1 P6 spec ✅
- ✅ **P6.1.1** Draft the P6 bundles, recommendation and decisions spec.
  - Refs: plan §9.4, §10.7, §10.8, §11, §14.5, §15; D1 rev 6 §6.3, §9.4, §14; the P4 and P5 specs.
  - Files: the new spec; plan rev 14; D1 rev 7.
  - Done when: the owner approves it (here: the agent, on the owner's instruction of 2026-11-10, after a self-review).
  - Handover: spec approved 2026-11-10; plan rev 14, D1 rev 7. Decision: sensitivity as its own stage repricing every
    tariff. Watch: `recommend` reads `private/config.toml` as a stage input (only public snapshot IDs leave it).

#### P6.2 Build ✅
- ✅ **P6.2.1** Foundations: `Params` v6, `StageRun.stage_sha256`, `bundle_rule`, the new workspace directories.
  - `core/models/params.py` v6 (`recommend`, migration v5 to v6); `StageRun.stage_sha256`; `core/bundle_rules.py` and
    package data `bundle_rules/` (readme only); `pipeline sync` creates `config/bundle_rules.local/`, `data/runs/` and
    `data/decisions/` (tracked); the three P6 warnings; the billing-period helpers moved into `costing.py`; golden
    schemas; `params_v6_text`.
  - Refs: the P6 spec §3, §5.1, §11; D1 rev 7 §5, §9.2.
  - Files: `src/energatron/core/{models/params.py,bundle_rules.py,errors.py}`, `src/energatron/bundle_rules/`,
    `src/energatron/services/{pipeline_sync.py,stages/runner.py,stages/costing.py,stages/cost_elec.py}`, tests and
    fixtures.
  - Done when: the standard checks pass; a v5 `params.yaml` syncs to v6.
  - Handover: params v6 (`recommend`), `StageRun.stage_sha256`, `bundle_rule` v1 with loader and an empty curated
    `bundle_rules/`, `pipeline sync` creates and tracks `data/runs` and `data/decisions` (readmes) and
    `config/bundle_rules.local`, three warnings, the period and energy helpers moved into `costing.py`; self-review.
    Watch: DVC-driven tests need `params_v6_text` (DVC reads `recommend` from the file).
- ✅ **P6.2.2** `sensitivity`.
  - The stage and its kinds (`sensitivity_total`, `sensitivity_summary`); golden `dvc.yaml`; fixture workspace.
  - Refs: the P6 spec §4.
  - Files: `src/energatron/services/stages/{sensitivity,registry,tables}.py`, `src/energatron/core/models/recommend.py`,
    tests.
  - Done when: the standard checks pass; every check passes and fails in a test.
  - Handover: `sensitivity` (usage ±, the earlier window per fuel, ranks per fuel and variation, `energy_scaled`,
    `W_SENSITIVITY_UNAVAILABLE`), `sensitivity_total`/`sensitivity_summary`, seventeen stages; committed with P6.2.1.
    Watch: stage-list tests now use `list(STAGES)`; the fixture zone has too little history for `alt_window`.
- ✅ **P6.2.3** `bundles` and `core/ranking.py`.
  - Candidates and history, options, rule effects; the stage and its kinds (`bundle_option`, `bundles_summary`).
  - Refs: the P6 spec §3.2, §5.
  - Files: `src/energatron/core/ranking.py`, the stage, tests.
  - Done when: the standard checks pass.
  - Handover: `core/ranking.py` (history, candidates, pairs with rules, options, action, recheck date, verdicts) and
    `bundles` (five variations, unavailable ones fall back to base per fuel, three checks), `bundle_option` and
    `bundles_summary`; eighteen stages; self-review. Watch: a same-brand independent pair keeps the same-brand form.
- ✅ **P6.2.4** `recommend`: the recommendation document, its ID and page.
  - `core/models/recommend.py` (`recommendation`), the current plan, action and sensitivity verdicts in
    `core/ranking.py`, `core/recommendation_report.py`, the stage.
  - Refs: the P6 spec §6, §7; D1 §6.3.
  - Files: the modules above, tests, golden HTML.
  - Done when: the standard checks pass; the ID is stable across runs and changes with a cited input.
  - Handover: `recommendation` v1, the ID over the document without ID and checks, `core/recommendation_report.py`
    (golden HTML), `recommend` (current plan from private config else the open backtest range, savings, action,
    verdicts, four checks); nineteen stages; fixture workspaces get a synthetic private config. Different from the spec:
    the usage window comes from the cost-mart summaries and bundle rules are an input (spec §6.1 edited); with no
    candidates (no offers yet) the document has no ID and passes. Watch: `verify`'s hand-edit test now edits a leaf.
- ✅ **P6.2.5** `energatron run`.
  - `services/run.py`, `run_manifest`, `run_result`, the command (lock, `--draft`, `--json`).
  - Refs: the P6 spec §8; plan §9.4; D1 §4.4, §4.5, §8.8.
  - Files: `src/energatron/services/run.py`, `src/energatron/adapters/cli/commands/run.py`, `app.py`, tests.
  - Done when: the standard checks pass; `run --json` prints one document.
  - Handover: `services/run.py` and `energatron run [--draft]` (sync check, dirty config and dirty tool refused
    unless draft, repro, `run_manifest` v1 in `data/runs/`, commit of `dvc.lock`, fingerprint, `data/runs.dvc` and
    dirty `.gitignore`s, `W_BACKUP_PENDING`), `run_result` v1; committed with P6.2.6. Watch: tests call the service
    with a clean `ToolState` because the development checkout is dirty while building.
- ✅ **P6.2.6** `energatron decide`, `outcomes` and the end-to-end test.
  - `services/decide.py`, `decision`, `decide_result`, the command; the `outcomes` stage (`outcome_bill`,
    `outcomes_summary`); `test_p6_pipeline` (run, decide, run, verify); the determinism test's REC check.
  - Refs: the P6 spec §9, §10, §12.
  - Files: the modules above, tests.
  - Done when: the standard checks pass.
  - Handover: `services/decide.py` and `energatron decide` (current REC only, a clean run must name it, choices from
    `priced`, evidence must be landed, `DEC-<local date>-nn`, `--dry-run`), `decision`/`decide_result` v1; the
    `outcomes` stage (twenty stages); `test_p6_pipeline` (fetch, run, decide, run again with the same REC and only
    `outcomes` rerun, verify, `run --json` one document, draft-only refusal) (3490 tests); self-review. Decision: the
    reminder to set `current_plans` is in the human output, not the result. P6.2 complete.

#### P6.3 Acceptance ✅
- ✅ **P6.3.1** P6 acceptance in the workspace, and the three workspace skills.
  - Refs: the P6 spec §14; plan §14.5, §15.
  - Files: `demq-elektric` (`params.yaml`, `dvc.yaml`, `dvc.lock`, pointers, readmes, `.claude/skills/`, a workspace
    note); `TASKS.md`.
  - Done when: §14's checks pass or are explained in the workspace note; the pending backup is reported.
  - Handover: P6 acceptance: params v6 synced; a clean `energatron run` (20 stages, a `REC-…`, manifest and commit
    naming both IDs); a second run all cached with the same ID; `verify` 20 stages reproduce; `decide --dry-run` on the
    top option valid and linked to the clean run (no real decision); three workspace skills linked. Watch: the gas
    reference is one bill (partial year), and the top electricity plan is wholesale-linked (approximate); backup pending.
