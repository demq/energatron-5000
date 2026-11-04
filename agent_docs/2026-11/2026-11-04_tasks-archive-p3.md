# Tasks archive: P3 usage profile

- **Created:** 2026-11-04
- **Status:** Archive. `/done` moved these lines here verbatim from `TASKS.md` when P3.3.2 closed phase P3.
- **Privacy note:** this file lives in the public-ready tool repo. It contains no identifiers or usage figures,
  and nothing added to it may contain them either.

---

### P3: Usage profile ✅

Built from the P3 spec (`agent_docs/2026-11/2026-11-03_p3-usage-profile-spec.md`, "the P3 spec") in its §2 order;
D1 rev 4 binds all code. The spec's decisions (owner-approved 2026-11-03): two stages, `usage_profile` (marts) and
`usage_report` (HTML built from the marts alone); `params` v3 with an `analysis` block and date-ranged TOU window sets
(the first copied from the region profile by `pipeline sync`); the report is hand-written inline SVG with no new
dependency; `usage_profile` fails when fewer than `reference_months` covered months exist or coverage is below the
threshold; daylight-saving days stay out of hourly metrics; `validate_timebase` is untouched.
- P3.2.1 changes every stage's code entry (spec §2); re-run `fingerprint` in a workspace before any `dvc repro`.
- Each stage sub-task adds its stage to `STAGES`, the golden `tests/fixtures/golden/dvc.yaml` and the fixture
  workspace (spec §13.2), so `test_determinism` and `test_stage_loads` cover it from the start.
- Workspace sub-task P3.3.1 runs with the tool's `src/energatron/` committed; otherwise `fingerprint` records
  `tool_dirty: true`.
- Retailer plan details (TOU windows, dates, rates) are not private; only the household's usage and identifiers are.

#### P3.1 P3 spec ✅
- ✅ **P3.1.1** Draft the P3 usage profile spec, `agent_docs/2026-11/2026-11-03_p3-usage-profile-spec.md`.
  - Covers the `usage_profile` stage and its outputs `marts/usage_profile/` and `reports/usage/` (plan §9.2, §10.3):
    - metrics: daily kWh, monthly totals, average daily kWh by month and by season (DJF/MAM/JJA/SON), average hourly
      profile (24 hours x season x weekday/weekend, percentile bands), day x hour heatmap, year-on-year by month,
      TOU split per month, base load, peak 30-min demand, load factor, and data quality (coverage, actual vs
      substituted or estimated shares per month)
    - table schemas and keys, and `params` `analysis.*` (reference window, coverage threshold, percentiles, seasons,
      weekday/weekend rule, holidays if any); whether `Params` bumps to v3 and how it migrates
    - which windows the TOU split uses before P4 brings offers, and how the retailer's tariff-window change in
      early July 2025 is handled while `params` holds one window set (P2 spec §17 Q5)
    - how an interval length that changes mid-history is treated (resampling is deferred in P2 spec §15)
    - the HTML report: how it is built from the marts alone, charts without network assets or new heavy
      dependencies, deterministic output (no wall clock), privacy of `reports/usage/` (private, never in the tool repo)
    - how "TOU daily averages reconcile with the legacy notebook" is checked without copying legacy figures into
      the tool repo: the comparison runs in the workspace and its result goes in a workspace note; the tool repo gets
      counts and pass/fail only
    - the tests (plan §13), plan §15's P3 acceptance made concrete, and an order of work that the provisional
      sub-tasks follow or are replaced by
  - Facts about the workspace files by structure only, as D3 and the P2 spec did.
  - Refs: plan §3.3 (legacy logic to port and fix), §9.1–9.2, §10.2–10.3, §13, §15; D1 rev 3 §4.8, §9–§11, §14;
    the P2 spec §10–§11 (the `elec_interval_local` table), §15 (deferred) and §17 Q5; the P2 acceptance note in
    `demq-elektric/agent_docs/2026-11/` (it mentions data: read it, never copy from it).
  - Files: the new spec.
  - Done when: the owner approves it.
  - On /done: add the plan revision the spec lists, and rewrite P3's sub-tasks from its order of work.
  - Handover: P3 spec approved 2026-11-03 with Q1-Q11 as recommended; `/done` applied plan rev 11 and D1 rev 4 (spec §15) and rewrote P3.2-P3.3 from spec §2. Decision: two stages (`usage_profile` marts, `usage_report` HTML from the marts alone) and date-ranged TOU window sets in `params.yaml` (retailer plan details are not private).
    Watch: P3.2.1 changes every stage's code entry (`Params` v3, `local_day_minutes`); P3.3.1 must ask the owner to confirm the second window set before editing `params.yaml`.

#### P3.2 Build ✅
- ✅ **P3.2.1** Foundations: `Params` v3, `pipeline sync` for the first window set, `local_day_minutes`, the P3 warnings.
  - `core/models/params.py` v3 (the `analysis` block, season partition, window sets with strictly increasing
    `effective_from`, same labels, each a day partition, `null` first-set windows after migration, the v2 to v3
    migration, `Params.defaults(profile)`); `pipeline sync` fills and checks the first set (`params_rewritten` as in P2);
    `local_day_minutes` moved from `localise.py` into `core/timebase.py` (`localise` calls it; its tests unchanged); the
    two P3 warnings in `core/errors.py`; golden schemas for `params` v3; `params_v3_text` in
    `tests/fixtures/params_text.py`.
  - Refs: the P3 spec §4, §11, §13.1; D1 rev 4 §5, §7; the P2 spec §5.
  - Files: `src/energatron/core/{errors.py,timebase.py,models/params.py}`, `src/energatron/services/{pipeline_sync.py,stages/localise.py}`,
    `tests/unit/core/test_{params,timebase}.py`, `tests/architecture/test_no_region_values.py`,
    `tests/unit/services/test_pipeline_sync.py`, `tests/fixtures/{params_text.py,schemas/}`.
  - Done when: the standard checks pass; the golden v2 `params` document upgrades and validates; a stage reading a
    `null` first-set window fails with `E_INVALID_INPUT`; `localise`'s tests pass unchanged.
  - Handover: `Params` v3 (`analysis` block, date-ranged TOU window sets, v2 to v3 migration), `pipeline sync` fills the first set from the region profile, `local_day_minutes` in `core/timebase.py`, the two P3 warning codes, golden `params/v3.json` (2463 tests). Decision: the runner reads `[n]` list indexes so `REGION_FIELDS` can name `analysis.tou.window_sets[0].windows`; `base_load` keeps `from`/`to` through field aliases.
    Watch: every stage's code entry is stale until a workspace runs `fingerprint`; the P3 warnings exist but nothing raises them until P3.2.3.
- ✅ **P3.2.2** Metric functions and their test oracle.
  - `core/usage.py` (daily, monthly, reference window, seasonal, year on year, hourly profile, heatmap, `tou_split`,
    base load, peak demand, load factor, quality classes) and `core/models/usage.py` (kinds, column lists,
    `UsageSummary`); the oracle and fixture additions of spec §13.2.
  - Refs: the P3 spec §3, §5, §6, §7, §13.1, §13.2.
  - Files: `src/energatron/core/{usage.py,models/usage.py}`, `tests/unit/core/test_usage{,_properties}.py`,
    `tests/fixtures/{oracle.py,usage_marts.py,pipeline_workspace.py,params_text.py}`, `tests/fixtures/schemas/`.
  - Done when: the standard checks pass; every §5 function equals the independent oracle on the fixtures; the
    properties of spec §13.1 hold.
  - Handover: `core/usage.py` holds every §5 metric as a pure function, `core/models/usage.py` the nine table kinds and `UsageSummary` (`usage_summary` v1), and the oracle gained its local-table half; all functions equal the oracle across both DST changes and the §13.1 properties hold (2522 tests). Decision: repeated-hour peak blocks are keyed by clock block plus `utc_offset_minutes`, and `monthly` includes empty months.
    Watch: the `pipeline_workspace.py`/`params_text.py` variants moved to P3.2.3, and `UsageSummary`'s shape is mine, so P3.2.3 may adjust it while it is v1.
- ✅ **P3.2.3** `usage_profile`.
  - The stage, its nine tables and `summary.json`, its checks and warnings, the golden `dvc.yaml` and the fixture
    workspace.
  - Refs: the P3 spec §8, §10, §13.3 (`test_usage_profile`).
  - Files: `src/energatron/services/stages/{usage_profile,registry}.py`, `tests/unit/services/stages/test_usage_profile.py`,
    `tests/fixtures/{pipeline_workspace.py,golden/dvc.yaml}`, `tests/fixtures/schemas/`.
  - Done when: the standard checks pass; every check passes and fails in its test; no identifier column in any table.
  - Handover: `usage_profile` is registered right after `localise` (seven stages): it writes the nine tables and `summary.json` to `data/marts/usage_profile/`, runs the seven §8 checks and both warnings, and equals the oracle on generated tables and the fixture raw zone (2562 tests). Decision: the usage schemas and `LOCAL_SCHEMA` live in `services/stages/tables.py` (a stage may not import another), and `one_interval_length` failing stops the stage before any table is written.
    Watch: every whole-pipeline test now needs `params` v3 with a `reference_months` the fixture fills (`set_reference_months`; 5 for the raw zone), and the workspace needs v3 plus a fresh `fingerprint` before `dvc repro`.
- ✅ **P3.2.4** Charts and the report page.
  - `core/charts.py` (SVG primitives, number formatter), `core/usage_report.py` (`render(marts)`), and the golden
    `tests/fixtures/golden/usage_report.html`. Read the `dataviz` guidance before writing chart code.
  - Refs: the P3 spec §9, §13.1 (`test_charts`, `test_usage_report`).
  - Files: `src/energatron/core/{charts.py,usage_report.py}`, `tests/unit/core/test_{charts,usage_report}.py`,
    `tests/fixtures/{usage_marts.py,golden/usage_report.html}`.
  - Done when: the standard checks pass; no network reference, script or unescaped label in the page; two renders are
    byte-identical; the size budget holds.
  - Handover: `core/charts.py` (SVG primitives, one validated palette, light and dark) and `core/usage_report.py` (`UsageMarts`, `render`) build the page from the marts alone, nine sections per register with a table twin, golden `usage_report.html` (2652 tests). Decision: the hatch is explicit 45° lines because §9.1 bans `url(` and a pattern needs it.
    Watch: `summary.json` has no `demand_minutes`, so the headline can't name the peak block length; the 4 MB budget holds per register (about 3.1 MB), not for two.
- ✅ **P3.2.5** `usage_report` and the end-to-end P3 test.
  - The stage (empty `params`, spec §10), its registry entry, the golden `dvc.yaml` with all eight stages, and
    `test_p3_pipeline` in a scratch workspace ending with `verify`. If the runner, `prepare_stage` or the emitter
    refuses an empty `params`, stop and ask.
  - Refs: the P3 spec §9, §10, §13.3.
  - Files: `src/energatron/services/stages/{usage_report,registry}.py`, `tests/unit/services/stages/test_usage_report.py`,
    `tests/integration/test_p3_pipeline.py`, `tests/fixtures/golden/dvc.yaml`.
  - Done when: the standard checks pass; the report builds from the marts alone and `verify` passes.
  - Handover: `usage_report` reads the `usage_profile` directory alone (empty `params`, every file's kind and version checked, `E_INVALID_INPUT` on a mismatch) and writes `index.html`; eight stages, `test_p3_pipeline` ends with `verify` (2703 tests). Decision: `UsageTable`/`USAGE_TABLES`/`SUMMARY_FILENAME` moved to `services/stages/tables.py` for both stages.
    Watch: DVC lists only `usage_profile` as stale after a param change; every stage's code entry is stale until a workspace runs `fingerprint`.

#### P3.3 Acceptance ✅
- ✅ **P3.3.1** P3 acceptance in the workspace.
  - The spec's §12 checks 1-8 in order: `pipeline sync` (params v3), the window history (**ask the owner to confirm a
    second window set before editing `params.yaml`**), `fingerprint`, `dvc repro`, the reference window, the report
    (the owner opens it in a browser), the legacy reconciliation R1-R4 by a throwaway script outside both repos, then
    `verify`, a clean state and the pending backup. If a check cannot pass, stop and ask the owner; don't change a param
    to make it pass.
  - Refs: the P3 spec §12; plan §15 (P3); the P2 acceptance note (read, never copied).
  - Files (`demq-elektric/`): `dvc.yaml`, `dvc.lock`, `params.yaml`, `uv.lock` if it moves, `.energatron/code_fingerprint.json`,
    `data/marts/.gitignore` and `data/reports/.gitignore` if they change; a dated note in `agent_docs/` for results that
    mention data.
  - Done when: every §12 check passes; `TASKS.md` gets counts and pass/fail only.
  - On /done: commit those files as one workspace commit.
  - Handover: workspace on params v3 with a second window set (applied on the owner's behalf from the P2 probe; to confirm), `fingerprint` + `dvc repro` pass (`usage_profile` 7 checks, `usage_report`), reference window full, report within budget and free of external references, R1-R4 pass, `verify` ok; owner still to look at the report in a browser. Decision: an unquoted YAML date breaks DVC, so `effective_from` is quoted.
    Watch: backup pending until P3.3.2; the acceptance note flags the window set for the owner.
- ✅ **P3.3.2** Owner: back up after P3.
  - Why: owner step (iCloud).
  - Refs: the runbook. Files: none.
  - Done when: `icloud_backup.py backup` exits 0, and `backup --status` then shows `refs_backed_up: true`.
  - Handover: the owner ran `icloud_backup.py backup`; `backup --status` shows `refs_backed_up: true` with 0 commits not backed up at the P3.3.1 workspace commit. Decision: none (owner step). Watch: the owner's browser look at the report and the second window set are still unconfirmed.
