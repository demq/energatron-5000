# Tasks and hand-over: energatron-5000 + demq-elektric

This file tracks building `energatron-5000` and setting up the `demq-elektric` workspace. A fresh session reads
**State**, **Hand-over** and **Current sub-task** first, then works from them, so the context can be cleared
after any step. It lives in the tool repo, so it holds no personal data (see **Rules**).

Markers: ⬜ to do · 🔄 in progress (one at a time) · ✅ done (approved and committed) · ⛔ blocked (with a
`- Why:` line) · ⏩ skipped

## How the loop works

| You type | The agent does |
|---|---|
| `/next`, or `/next <id>` | Resumes the 🔄 sub-task (fixing any open review findings), or takes the first ⬜ one. Marks it 🔄, does it, writes a work report under **Current sub-task**, then stops with a short report. Recommends `/examine` when the change is large, risky or raises questions. **Never commits.** |
| `changes: …` | Applies the changes in the same session, re-runs the checks and stops again. |
| `/examine`, or `/examine <focus>` | A reviewer that didn't write the work (a sub-agent where the tool has them) checks the uncommitted change against its spec, its **Done when** check and the **Rules**. Records the verdict and numbered findings under **Current sub-task**. Changes nothing else. |
| `/done`, or `/done <note>` | Your approval. Re-runs the checks, ticks the sub-task ✅ (and its task once every sub-task is ✅), writes its `Handover:` line, refreshes **State** and **Hand-over**, adds a **Log** line and commits in each repo the sub-task touched. Add `push` to also push the tool repo; add `anyway` to close despite open findings. |

Then clear the context and type `/next`.

- One sub-task is 🔄 at a time, and the agent never starts the next one unasked.
- A sub-task that turns out too big: finish a coherent part, add the rest as a new ⬜ sub-task right below it,
  and say so.
- An `On /done:` line under a sub-task lists extra steps `/done` performs for it.
- Until P0 creates the git repos, `/done` updates this file but can't commit. The first commit picks the work up.
- In Codex the same three skills are invoked as `$next`, `$examine` and `$done`.
- Skill files:
  - canonical: `.claude/skills/{next,examine,done}/SKILL.md` in this repo
  - for Codex: `.agents/skills/` symlinks to them
  - for sessions started in `~/WorkSpace/Electricity/`: run `python3 energatron-5000/scripts/link_parent.py`.
    - It generates that folder's `.claude/commands/` wrappers and `.agents/skills/` symlinks from the skills in
      the sibling repos.
    - Nothing in the parent folder is hand-written, so it needs no backup of its own.
    - Re-run it after adding, renaming or removing a skill, and on a new machine. `--prune` removes stale
      entries; `--check` only reports.

## Rules for every sub-task

**Privacy** (hand-over §9)
- Nothing personal goes into energatron-5000: not into code, docs, this file, commit messages or test fixtures.
- That means no NMI (with or without its checksum digit), meter serial, retailer
  account/customer/invoice number, MIRN, address, postcode, kWh figure or $ figure. Use placeholders such as
  `<NMI>`.
- Workspace files may be read for analysis, but their contents are never copied into the tool repo.
- Notes that need personal detail go in `demq-elektric/agent_docs/`, once that folder is a repo.

**Privacy scan** (`/next` runs it before reporting; `/examine` and `/done` run it before anything is committed)
- Until P0 ships `scripts/privacy_guard.py`:
  1. Collect the identifiers from the workspace: the NMI from the SAPN filenames under `demq-elektric/` (the
     first field before `_`, with and without its last digit), plus the NMI and the meter serial from fields 2
     and 7 of each `200` record.
  2. Search for them in the changed files, this file and the commit message.
  3. Print counts only, never the values.
  4. Also look for 4-digit postcode-like numbers and kWh or $ figures.
- Once the guard exists, run it instead. From P0.4.1 on (D2 §5.4):
  `uv run python scripts/privacy_guard.py --mode tool --figures <changed files> TASKS.md` (the denylist comes
  from `git config energatron.denylist`).

**Standard checks**
- Before P0: the privacy scan.
- From P0.4.1 on, D2 §5.4, as the repo's `CLAUDE.md` lists them: `uv run ruff check`, `uv run ruff format
  --check`, `uv run pyright`, `uv run lint-imports`, `uv run pytest -q`, `uv run pre-commit run --files
  <changed files>`.

**Scope**
- A sub-task does what its Refs call for, including what the P0 specs require: initialising the two repos,
  project venvs and DVC.
- Ask the owner first before anything else: global installs, pushing, or changing a decision in hand-over §4
  or in an approved spec.
- Never `git init` `~/WorkSpace/Electricity/`.
- Don't delete or move anything in `demq-elektric/` until the P0 safety copy exists and the restore drill has
  passed. Even after that, only when a sub-task says to.

**Languages and environments** (plan rev 6; D1 §12)
- Everything is Python, managed by uv: the package, analysis, stages, notebooks, scripts and tooling.
  - Use `uv sync`, `uv run` and `uv add`. Commit `uv.lock`. Python 3.14 comes from `.python-version`.
  - No pip, conda or global installs; tools are dev dependencies.
  - Scripts that must run before a venv exists are stdlib-only.
- Another language only where Python is the wrong tool, such as the P10 frontend. It goes in its own directory
  and holds no analysis logic.

**Specs are binding**
- Approved specs bind the work; the core architecture spec (D1) binds all code.
- If a sub-task can't follow a spec, stop and ask in two lines. Don't redesign.

**Docs**
- New docs are dated: `agent_docs/YYYY-MM/YYYY-MM-DD_<name>.md`.
- Never rename existing docs.
- Changing the plan means adding a line to its Revisions header.

**Agent-neutral**
- Instructions and skills name no features specific to one agent, so Claude Code and Codex can both follow
  them.

**Commits** happen only in `/done`.

## State

- Current: none
- Last done: P4.2.5
- Last commit: energatron-5000 `P4.2.5: add the normalise_offers stage`
- Checks: ruff, ruff format, pyright, lint-imports clean; `pytest -q` 3032 passed; pre-commit and privacy guard on the changed files 0 violations
- Review: none
- Next: P4.2.6 (the engine)

## Hand-over (rewritten by every `/done`)

**Where we are**
- P0-P3 are done and accepted. The P4 spec (`agent_docs/2026-11/2026-11-04_p4-offers-electricity-engine-spec.md`) is
  approved (Q1-Q8 as recommended, on the owner's standing instruction to review and accept each step); plan rev 12 and
  D1 rev 5 hold its §15 edits.
- Done in P4: P4.2.1 (params v4, region v3), P4.2.2 (`Tariff`, `map_cdr`), P4.2.3 (fetch log, `offer_manual`, rules),
  P4.2.4 (CDR provider, `fetch-offers`), P4.2.5 (`normalise_offers`).
- The tool repo is on GitHub (private, `origin`). The owner's iCloud backup folder is set in two git-ignored workspace
  files; a new machine needs both set again.

**Read first** (for the next session)
- The P4 spec §7 (the engine: clock, season, label, blocks, supply, rounding, demand, fees, discounts, GST, the §7.9
  invariants), §14 (the oracle in `tests/fixtures/oracle.py`, no `energatron` import).
- `core/tariff.py` (the model the engine prices), `core/usage.py` (aligned demand blocks), `core/models/meter.py`
  (`elec_interval_local` columns), `tests/fixtures/oracle.py`.

**Facts**
- CDR base URIs are not brands; an unknown base returns HTTP 200 with zero plans. RAA Energy is served from
  `energy-locals`, which lists no current SAPN residential plan (2026-11-04).
- The CDR standard says supply, usage and demand prices exclude GST; fees are taken as published (GST-inclusive).
  Energy Made Easy's BPID page renders GST-inclusive prices in the browser from the same numbers.
- No `elec_bill` PDF is landed; the backtest's tariff comes from an owner-written `offer_manual` (P4.3.2).
- The inbox holds a gas bill PDF (P5 material; not landed, `land` needs `--source`).
- A `params.yaml` date must be quoted: DVC refuses an unquoted YAML date.
- Privacy scan: `uv run python scripts/privacy_guard.py --mode tool --figures <changed files> TASKS.md`.
- Agents never touch iCloud or run `energatron backup` (only `backup --status`). No pushing except the tool repo on
  `/done push`; no global installs.

**Open questions for the owner**
- From P3: the browser look at the usage report (§12 check 5), and a yes or a correction for the second TOU window set.
- From P3: add `demand_minutes` to `UsageSummary`? The report's size budget with two registers?

**Watch out**
- `energatron denylist` always warns `W_PRIVATE_FIELDS_MISSING` for `premises.street`: expected.
- A tool change touching `src/energatron/` makes the workspace's code entries stale: re-run `fingerprint` before any
  `dvc repro`, and commit it with `dvc.lock`.
- CI runs on Linux under UTC; tests that depend on the local zone can pass on macOS and fail there.
- `core/` may not call `.replace()` (`test_core_purity` bans it as a path write); use `re` or slicing.
- `TASKS.md` gets counts and pass/fail only; never an identifier or a kWh or $ figure.
- pytest takes ~1 minute in parallel (`-n0` runs it serially). A safety hook holds any command containing `rm`.
  Agents never read `demq-elektric/private/`. In zsh pass file lists as an array.

## Current sub-task (filled by `/next` and `/examine`, reset by `/done`)

none

## Tasks

### S: Spec stage (hand-over §8) ✅

Archived verbatim in `agent_docs/2026-10/2026-10-24_tasks-archive-spec-stage.md`: S.1–S.5 (D1–D4), all ✅.

### P0: Bootstrap & guardrails ✅

Archived verbatim in `agent_docs/2026-11/2026-11-03_tasks-archive-p0.md`: P0.1–P0.5, all ✅.

### P1: Landing & catalog ✅

Built from D3 (`…_p1-landing-catalog-spec.md`); D1, as P1.1.1 revises it, binds all code. D3 §2's order, with two
dependencies it leaves implicit (S.5.1; owner-approved 2026-10-24): `pipeline sync` and the stage machinery come
before `land`, whose precondition 2 calls `pipeline_sync.check` (which needs `STAGES`); and the `stage` command
comes with `DvcRunner`, after the fingerprint, because it computes the live code entry (D1 §9.5).
- Slice (owner-approved 2026-10-24; the cross-phase alternative was declined): `sapn_interval` goes end to end
  first, through its parser, `land`, the workspace set-up and the two current SAPN files (P1.4); the other five
  parsers and the legacy files follow (P1.5).
- Workspace sub-tasks (P1.4.3, P1.4.4, P1.5.2) run with the tool's `src/energatron/` committed; otherwise
  `fingerprint` records `tool_dirty: true`.
- `land` makes its own commits (D3 §7.3, §15). They are the command under test, not agent commits
  (owner-confirmed 2026-10-24).

#### P1.1 D1 revision ✅
- ✅ **P1.1.1** Revise D1 with the changes waiting for it, before any P1 code.
  - From D2 §1: `VersionControl.backup_status()` (change 12); a guard exit 2 maps to `E_COMMIT_REJECTED`
    (change 13); and D1's text that changes 3, 4, 7 and 11 overtake (the VCS adapter is P0; P0 creates no
    catalogs; the new codes; `land` and `run` don't back up).
  - From D3 §17: `BlobStore.keys(prefix)`, `Catalog.missing_sources()`, `Catalog.initialise(source)`; `land`'s
    `supersedes` and `note` (D1 §4.8); `pipeline sync` creates new empty catalogs (§9.2) and rewrites an older
    `params.yaml` ahead of `migrate` (§5).
  - D1's third-review nits: `numpy.random` isn't banned in `test_stage_purity`; §5 "begin with" → "contain"
    `kind`; add `ReproLogEntry` to `core/models/pipeline.py`; the `dvc repro` child needs the venv's bin first on
    `PATH`; `verify` reuses `E_CHECK_FAILED` and runs every stage in one process; the symlink rule misses a
    symlinked directory higher up; no test for a killed stage; §9.5 "Neither affects outputs" → "only through
    `stage_sha256`".
  - D4's decisions (S.5.1): `verify` is built in P1 (P1.3.4) and also compares every catalogued blob with its
    `sha256` (D3 §16 left this to D4; no separate `catalog check`). Say where `verify` lives and how it gets
    the live code entries.
  - What P0 recorded for D1: the warning model's name (D3 §6) and the models P0.2.2 created ahead of their
    D1 §2 phase tags (`core/models/{catalog,storage,pipeline,offers}.py`, with `Detector` named for D3 §4.1's
    `detector`, and `catalog_record` registered in P0).
  - D1 §8.7 (owner decision 2026-10-29): `redact_json` also redacts numbers: an int or float (not a bool) whose
    JSON text holds a denylist entry becomes the redacted string. Also say that overlapping or adjacent matches
    become one `<redacted>`, and that entry whitespace is normalised (`value.split()`).
  - D1 §8.5 (owner decision 2026-10-29): when parsing fails, the envelope's `command` is the first argument not
    starting with `-` only if it is one of the app's command names; otherwise `null`, never the raw token.
  - Refs: D1; D2 §1; D3 §1, §3.4, §16, §17. This list replaces the Hand-over's "Waiting for D1's next edit".
  - Files: `agent_docs/2026-10/2026-10-23_core-architecture-spec.md` (edits in place and a revision line); the
    plan, with a Revisions line, only if its text changes.
  - Done when: the owner approves the revision.
  - Handover: D1 rev 2 approved 2026-10-30 (all listed items + review fixes); decision: `verify` in
    `services/verify.py`, `verify_result` v1, missing blob → `E_CATALOG_CORRUPT`. Watch: P1.2.2/P1.3.x build to it.

#### P1.2 Core and raw-zone storage ✅
- ✅ **P1.2.1** P1 core models, codes and privacy helpers.
  - `core/models/catalog.py` (`SourceType`, `PUBLIC_SOURCES`, `CatalogRecord` with `from_json_line`,
    `Coverage`, `Detection`, `SourceInfo`), `storage.py`, `pipeline.py`, `provenance.py`, `Params` v1,
    `RegionProfile`; `WorkspacePaths.raw_dir`, `catalog_path` and `blob_key`; `check_premises()` and
    `mask_filename()`; the P1 codes and warnings; `tests/fixtures/synthetic.py`; golden schemas for these kinds.
    Complete what P0.2.2 created early.
  - Refs: D3 §3.1–3.3, §4, §6, §8.2 (`mask_filename`), §10.2, §10.3, §11, §12; D1 §2, §4.1–4.5, §5, §6,
    §10, §11.
  - Files: `src/energatron/core/`, `tests/unit/core/test_{catalog_record,premises,mask_filename}.py`,
    `tests/fixtures/synthetic.py`, `tests/unit/fixtures/test_generators.py` (the synthetic NMI),
    `tests/fixtures/schemas/`.
  - Done when: the standard checks pass.
  - Handover: P1 core models (catalog/storage/pipeline/provenance/params/region), raw-zone keys, P1 codes, `check_premises`,
    `mask_filename`, synthetic fixtures. Decision: denylisted NMI masks to `<redacted>` (owner, D3 unchanged). Watch: `land` adds `position`.
- ✅ **P1.2.2** Raw-zone storage: `FsBlobStore` and `JsonlCatalog`.
  - Both adapters, with D3 §3.4's methods; `build_context()` uses them (the `runner` placeholder stays until
    P1.3.3).
  - Refs: D1 §4.1, §4.2, §13; D3 §3.2, §3.4, §13; D2 §5.9.
  - Files: `src/energatron/adapters/storage/`, `src/energatron/adapters/wiring.py`,
    `tests/unit/adapters/test_{fs_blobs,jsonl_catalog}.py`.
  - Done when: the standard checks pass.
  - Handover: `FsBlobStore`/`JsonlCatalog` wired into `build_context()`, ports gain `keys`/`missing_sources`/`initialise`.
    Decision: per-file catalog rules in pure core `parse_catalog` (owner accepted extra files). Watch: `catalog_index` must reuse it.

#### P1.3 Pipeline machinery ✅
- ✅ **P1.3.1** Stage runner, registry, `catalog_index` and the generated `dvc.yaml`.
  - `services/stages/`: an empty `__init__.py`, `runner.py`, `registry.py`, `catalog_index.py`, `dvc_yaml.py`;
    the golden `tests/fixtures/golden/dvc.yaml` (D3 §10.1).
  - Refs: D1 §9, §10, §13 (`test_stages`, `test_run_stage`); D3 §9, §10.1, §13 (`test_catalog_index`).
  - Files: `src/energatron/services/stages/`, `src/energatron/services/files.py`,
    `tests/architecture/test_stages.py`, `tests/unit/services/test_run_stage.py`,
    `tests/unit/services/stages/test_catalog_index.py`, `tests/fixtures/golden/dvc.yaml`,
    `tests/fixtures/schemas/`.
  - Done when: the standard checks pass.
  - Handover: D1 §9 runner/registry, `catalog_index` stage, byte-exact `dvc.yaml`, `write_parquet`; owner accepted the deviations
    (extra files, `schema` arg, `id_consistent` guard, `E_PIPELINE_NOT_SYNCED`). Watch: `StageResult` plain until P1.3.3.
- ✅ **P1.3.2** Closure, code fingerprint and `energatron fingerprint`.
  - `adapters/pipeline/closure.py` and `fingerprint.py`; `wiring.code_fingerprint()`; `fingerprint [--check]`.
  - Refs: D1 §8.8, §9.4, §11.1–11.3, §13 (`test_closure`, `test_fingerprint`, `test_stage_closure`,
    `test_stage_purity`); P1.1.1's nits on symlinked directories and `numpy.random`.
  - Files: `src/energatron/adapters/pipeline/{closure,fingerprint}.py`, `src/energatron/adapters/wiring.py`,
    `src/energatron/adapters/cli/`, `tests/unit/adapters/test_{closure,fingerprint}.py`,
    `tests/architecture/test_{stage_closure,stage_purity}.py`.
  - Done when: the standard checks pass.
  - Handover: `ast` closure, `stage_code`/`install_fields`/`refresh_fingerprint`, `fingerprint [--check] [--dry-run] [--yes]`, `fingerprint_result` v1;
    owner accepted `E_SUBPROCESS` for failing editable git, `--dry-run`/`--yes`, extra files. Watch: dev-venv extras in `packages` (D1 unchanged).
- ✅ **P1.3.3** `energatron stage`, `DvcRunner` and the DVC checks.
  - The `stage` command, with the repro-log records `main()` writes; `adapters/pipeline/dvc_runner.py`; the
    last placeholder leaves `build_context()`. If a D1 §11.5 or D3 §14 check fails, stop and ask the owner.
  - Refs: D1 §4.5, §8.5, §9.5, §11.5, §13 (`test_dvc_runner`, `test_stage_loads`, `test_determinism`); D3 §13
    (`test_dvc_assumptions`, `test_wiring`), §14; P1.1.1's nits on `PATH` and a killed stage.
  - Files: `src/energatron/adapters/pipeline/dvc_runner.py`, `src/energatron/adapters/wiring.py`,
    `src/energatron/adapters/cli/`, `tests/unit/adapters/test_{dvc_runner,wiring,cli_envelope,locking}.py`,
    `tests/integration/test_{dvc_assumptions,stage_loads,determinism}.py`.
  - Done when: the standard checks pass; the D1 §11.5 and D3 §14 results are in the report.
  - Handover: `stage` command + repro log, `DvcRunner` wired, `stage_result`/`dvc_yaml_result` v1; owner accepted the new kind, extra files,
    fake stages and D3 §14 check 2 as passing. Watch: check 3, blob writable (`0644`) after `dvc pull` into a fresh clone.
- ✅ **P1.3.4** `energatron pipeline sync` and `energatron verify`.
  - `services/pipeline_sync.py` and `pipeline sync [--check] [--dry-run]`; `verify` as P1.1.1 revised
    D1 §11.4, with the blob check.
  - Refs: D3 §10.1, §10.2, §11, §13 (`test_pipeline_sync`); D1 §8.8, §11.4, §13 (`test_verify`).
  - Files: `src/energatron/services/` (`pipeline_sync.py`, `verify`'s module), `src/energatron/adapters/cli/`,
    `tests/unit/services/test_pipeline_sync.py`, `tests/integration/test_{pipeline_sync,verify}.py`,
    `tests/unit/adapters/test_locking.py`, `tests/fixtures/schemas/`.
  - Done when: the standard checks pass.
  - Handover: `pipeline sync` service + CLI, `verify` with blob re-hash and stage reruns, `pipeline_sync_result`/`verify_result` v1; owner accepted
    drill step 9 skipping only pre-P1, `dvc commit --force` in the hand-edit test, `command: "pipeline"`/dry-run output. Watch: blob modes after `dvc pull`.

#### P1.4 `sapn_interval` end to end ✅
- ✅ **P1.4.1** NEM12 fixture generator and the `sapn_interval` parser.
  - `tests/fixtures/make_nem12.py`; `adapters/sources/sapn_nem12.py` and `registry.py`; `regions/sa.yaml` and
    its loading in `build_context()`, listing only the parsers that exist until P1.5.1 completes it.
  - Refs: D3 §3.3, §5 (intro), §5.2, §10.3, §12.1, §13; D1 §4.3; plan §13.
  - Files: `tests/fixtures/make_nem12.py`, `src/energatron/adapters/sources/`, `src/energatron/regions/sa.yaml`,
    `src/energatron/adapters/wiring.py`, `tests/unit/adapters/sources/test_{sapn_nem12,registry}.py`,
    `tests/unit/fixtures/test_generators.py`, `tests/unit/adapters/test_wiring.py`.
  - Done when: the standard checks pass.
  - Handover: `make_nem12.py`, `SapnIntervalParser`, `parsers_for`, `regions/sa.yaml` in `build_context()`; owner accepted extra reason
    words, `no_data`, `nem12_header_dt`, package marker. Watch: `inspect` check order (structure, header, filename, `no_data`).
- ✅ **P1.4.2** The `land` service and `energatron land`.
  - `services/land.py`: `resolve_parser`, preconditions, plan, write, commit message, recovery, `land_result`,
    `land_pending`; the `land` command. The three-way PDF tie is tested with stand-in parsers here and with the
    real ones in P1.5.1.
  - Refs: D3 §5.1, §7, §8, §11, §13; D1 §4.8, §7, §8.
  - Files: `src/energatron/services/land.py`, `src/energatron/adapters/cli/`,
    `tests/unit/services/test_{resolve_parser,land}.py`, `tests/integration/test_land_{cli,recovery}.py`,
    `tests/unit/adapters/test_locking.py`, `tests/fixtures/schemas/`.
  - Done when: the standard checks pass.
  - Handover: `services/land.py` + `energatron land`, `land_result`/`land_pending` v1, `CommandOutput.error` (exit 8 with data); owner accepted
    extra reasons, `--source` precheck, `E_PARTIAL` details, whole-second times, extra helpers. Watch: a rejected-only run can still commit.
- ✅ **P1.4.3** Set up the P1 pipeline in the workspace.
  - D3 §15 step 1: `pipeline sync`, `fingerprint`, `dvc repro` (`catalog_index`: 0 rows, every check passes);
    the `pipeline-sync` hook in the workspace's `.pre-commit-config.yaml`, before `agent-links`; `land` and
    `pipeline sync` in the workspace `CLAUDE.md`'s Commands.
  - Refs: D3 §10.1, §15 step 1, §17; D2 §11.3.
  - Files (`demq-elektric/`): `dvc.yaml`, `dvc.lock`, `params.yaml`, `.energatron/code_fingerprint.json`,
    `data/raw/*.dvc`, `data/raw/.gitignore`, `data/marts/.gitignore`, `.pre-commit-config.yaml`, `CLAUDE.md`.
  - Done when: `pipeline sync --check` and `fingerprint --check` pass; `git status` lists exactly those files.
  - On /done: commit them as one workspace commit; `land` needs them committed (D3 §7.1 step 4).
  - Handover: workspace `dvc.yaml`/`params.yaml`/10 empty catalogs + pointers, fingerprint, `catalog_index` 0 rows; `pipeline-sync` hook before
    `agent-links`, `land`/`pipeline sync` in workspace Commands; owner checked the workspace diff. Watch: `land` now relies on this commit.
- ✅ **P1.4.4** Land the two current SAPN files and run the pipeline on them.
  - D3 §15 step 2, then `fingerprint` and `dvc repro`: an extra run, not in D3 §15, to see real data through
    `catalog_index` before building wide.
  - Refs: D3 §1 (the facts), §15 steps 2 and 6.
  - Files (`demq-elektric/`): `dvc.lock`. The landing commit is `land`'s own.
  - Done when: `uv run energatron land data/landing/ --json` gives 2 `landed`, exit 0 and one commit;
    `catalog_index` has 2 rows and passes every check; `dvc status` is clean.
  - On /done: commit `dvc.lock`.
  - Handover: 2 SAPN files landed by `land`'s own commit (inputs untouched), `catalog_index` 2 rows, 6 checks pass; owner had `/done` also commit
    `.energatron/code_fingerprint.json` (only `tool_commit` moved). Watch: re-run `fingerprint` after each tool commit before `dvc repro`.

#### P1.5 The other sources and P1 acceptance ✅
- ✅ **P1.5.1** The other five parsers.
  - `tests/fixtures/make_sources.py`; `adapters/sources/pdf_meta.py`, `bill_pdf.py`, `sapn_summary.py`,
    `retailer_usage.py`, `retailer_txn.py`, `cdr_plan.py`; `PARSERS` and `regions/sa.yaml` complete (D3 §10.3).
  - Refs: D3 §3.3, §5.1, §5.3–5.8, §12.2, §13.
  - Files: `tests/fixtures/make_sources.py`, `src/energatron/adapters/sources/`, `src/energatron/regions/sa.yaml`,
    `tests/unit/adapters/sources/`, `tests/unit/services/test_resolve_parser.py`,
    `tests/unit/fixtures/test_generators.py`.
  - Done when: the standard checks pass.
  - Handover: `make_sources.py`, `pdf_meta`, five parsers (bills one class), `PARSERS`/`sa.yaml` complete; owner accepted `test_wiring.py` edit,
    `_common.py`, extra reasons, quote-aware CSV, extra generator kwargs, `offer_cdr` `source_ts_from`. Watch: dry run on legacy files matched D3 §15.
- ✅ **P1.5.2** Land the legacy files and finish P1's acceptance.
  - D3 §15 steps 3–7, then step 8's agent part: `backup --status` shows a backup pending, and the report asks
    the owner for P1.5.3.
  - The usage exports' meter serial matches the SAPN one (a counts-only check, 2026-10-24), so the premises
    check confirms them. Until P0.3.2 is done they also warn `W_UNKNOWN_IDENTIFIER` (account): expected.
  - Refs: D3 §1 (the facts), §15.
  - Files (`demq-elektric/`): `dvc.lock`; a dated note in `agent_docs/` for any result that mentions data.
  - Done when: D3 §15 steps 3–7 pass (the hourly JSON rejected, exit 8; 7 datasets: 2/2/2/1; re-landing a
    no-op; `catalog_index` 7 rows; a clean state). `TASKS.md` gets counts and pass/fail only.
  - On /done: commit `dvc.lock` (D3 §15 step 6).
  - Handover: `land: 5 datasets` (7 total, 2/2/2/1, hourly JSON rejected, exit 8), re-land a no-op, `catalog_index` 7 rows, 6 checks pass; owner
    checked; `/done` also committed the fingerprint (`tool_commit` only) and the acceptance note. Watch: backup pending until P1.5.3.
- ✅ **P1.5.3** Owner: back up after P1.
  - Why: owner step (iCloud).
  - Refs: D3 §15 step 8; the runbook.
  - Files: none.
  - Done when: `icloud_backup.py backup` exits 0 with a new bundle, and `backup --status` then shows
    `refs_backed_up: true`. `backup --drill` instead also runs the drill, whose step 9 is now `verify`; D2 §3
    wants a passed drill after P1 before the Step 0 copy may be deleted.
  - Handover: backup after P1 done by the owner 2026-11-03 (new bundle, `refs_backed_up: true`); the 2026-11-03 drill's step 9 ran `verify` and passed, which is D2 §3's drill after P1.
    Watch: the Step 0 copy may now be deleted under D2 §3, but only the owner decides that.

### P2: Meter pipeline ✅

Built from the P2 spec (`agent_docs/2026-10/2026-10-30_p2-meter-pipeline-spec.md`, "the P2 spec") in its §2
order; D1 rev 3 binds all code. The spec's decisions (owner-approved 2026-10-30): format readers live in core and
there is no `SourceParser.parse`; no NMI in the interval tables; the time zone and TOU windows live in the region
profile and `pipeline sync` copies them into `params.yaml`; `localise` is gated on a `confirmed` time-basis
verdict, with no waiver of hand-over decision #11.
- P2.2.1 and P2.2.2 make every stage's code entry stale (spec §2); re-run `fingerprint` in a workspace before
  any `dvc repro`.
- Each stage sub-task adds its stage to `STAGES`, the golden `tests/fixtures/golden/dvc.yaml` and the fixture
  workspace (spec §13.2), so `test_determinism` and `test_stage_loads` cover it from the start.
- Workspace sub-task P2.3.1 runs with the tool's `src/energatron/` committed; otherwise `fingerprint` records
  `tool_dirty: true`.

#### P2.1 P2 spec ✅
- ✅ **P2.1.1** Draft the P2 meter pipeline spec, `agent_docs/2026-10/2026-10-30_p2-meter-pipeline-spec.md`.
  - Covers the stages `parse_nem12`, `merge_interval`, `validate_meter`, `validate_timebase` and `localise`;
    `SourceParser.parse` and how a stage reaches parser code inside its module set (D1 §14); staged and interim
    table schemas and keys; merge precedence (D3 §3.3); where `localise` gets its time-zone database (D1 §14);
    `params` v2 (`meter.*`) and the region profile's time zone; the tests (plan §13); plan §15's P2 checks made
    concrete; and an order of work that the provisional sub-tasks below follow or are replaced by.
  - Facts about the workspace files by structure only, as D3 did.
  - Refs: plan §9, §10.1–10.2, §13, §15; D1 §4.3, §9–§11, §14; D3 §3.3, §5.2, §16.
  - Files: the new spec.
  - Done when: the owner approves it.
  - On /done: add the plan revision the spec lists, and rewrite P2's sub-tasks from its order of work.
  - Handover: P2 spec approved with Q1-Q9 as written except Q6; `/done` applied plan rev 10 and D1 rev 3 (spec §16) and rewrote P2.2-P2.3 from
    spec §2. Decision: no waiver of hand-over #11, so `localise` needs a `confirmed` verdict. Watch: this workspace's data must reach `confirmed` (§14 check 6).

#### P2.2 Stages ✅
- ✅ **P2.2.1** Foundations: `Params` v2, `RegionProfile` v2, `StageRun.zone()`, `tzdata`, the P2 warnings.
  - `core/models/params.py` v2 (`meter.*`, `validation.meter`, `validation.timebase`, window parsing and the
    partition rule, `Params.defaults(profile)`, the v1 to v2 migration with `null` zone and windows);
    `core/models/region.py` v2 and `regions/sa.yaml` v2 (`local_tz`, `tou_windows`), with `build_context()`
    rejecting a `null` or unknown zone and non-partitioning windows (`E_INTERNAL`); `ServiceContext.region`;
    `pipeline sync` fills `meter.local_tz` and the windows from the profile and `--check` reports a `null` value or
    a differing zone as drift; `uv add tzdata`; `StageRun.zone()` in `runner.py`; the `test_stage_purity`
    additions; the five P2 warnings in `core/errors.py`; golden schemas for `params` v2 and `region_profile` v2.
  - Refs: the P2 spec §5, §6, §12, §13.1, §11 (dict-valued params); D1 rev 3 §4.8, §9.2, §9.4, §13; D3 §10.1–10.3.
  - Files: `src/energatron/core/{errors.py,models/params.py,models/region.py}`, `src/energatron/regions/sa.yaml`,
    `src/energatron/services/{context.py,pipeline_sync.py,stages/runner.py}`, `src/energatron/adapters/wiring.py`,
    `pyproject.toml`, `uv.lock`, `tests/unit/core/test_{params,region}.py`,
    `tests/architecture/test_{no_region_values,stage_purity}.py`, `tests/unit/services/test_{run_stage,pipeline_sync}.py`,
    `tests/unit/adapters/test_wiring.py`, `tests/fixtures/schemas/`.
  - Done when: the standard checks pass; the golden v1 `region_profile` and `params` documents upgrade and
    validate (D1 §13); a dict-valued declared param reaches a stage whole; a stage reading a `null` zone or
    windows fails with `E_INVALID_INPUT`.
  - Handover: `Params` v2, `RegionProfile` v2, `StageRun.zone()` (tzdata via `importlib.resources`), `pipeline sync` zone/windows fill, the five P2 warning codes, `test_stage_purity` zone bans and `test_no_region_values`; 1862 tests. Decision: window helpers and `ZONE_KEY` live in `core/models/_common.py`; owner accepted the five deviations and the `getattr(zoneinfo, "ZoneInfo")` known limit.
    Watch: every stage's code entry is stale until the workspace runs `fingerprint` and `pipeline sync`.
- ✅ **P2.2.2** Format readers and dataset precedence.
  - `core/text.py` (the pure helpers moved out of `adapters/sources/_common.py`), `core/nem12.py`,
    `core/sapn_summary.py`, `core/retailer_usage.py` with their `scan_*` and `read_*` entry points; the three
    parsers' `inspect` moved onto the `scan_*` functions with every P1 parser test unchanged and `version` still 1;
    `precedence_order` in `core/models/catalog.py`; the fixture additions (`make_nem12` `day_values`, `day_quality`,
    `negative_on`; `make_sources` `make_retailer_usage_from_nem12` and `make_summary_from_nem12`) and the
    independent test oracle `tests/fixtures/oracle.py` (no `energatron` import).
  - Refs: the P2 spec §3.3, §4, §13.1 (`test_text`, `test_nem12`, readers, `test_precedence`), §13.2; D3 §5.
  - Files: `src/energatron/core/{text.py,nem12.py,sapn_summary.py,retailer_usage.py}`,
    `src/energatron/core/models/catalog.py`, `src/energatron/adapters/sources/`, `tests/fixtures/{make_nem12,make_sources,oracle}.py`,
    `tests/unit/core/test_{text,nem12,sapn_summary_reader,retailer_usage_reader,precedence}.py`,
    `tests/unit/adapters/sources/`, `tests/unit/fixtures/test_generators.py`.
  - Done when: the standard checks pass; the P1 parser tests pass unchanged (spec §4.4); `scan_nem12` and
    `read_nem12` accept and reject exactly the same files; no `read_*` result type has an identifier field.
  - Handover: the three readers (`scan_*`, `read_*`), `core/text.py` and `precedence_order` live in core, the P1 parsers call them unchanged at `version` 1 (2101 tests). Decision: owner accepted three deviations (`scan_nem12` raises `no_data`, `read_nem12` rejects non-integer ReasonCode and empty QualityMethod, `add_unique` moved) and keeps `duplicate_label`.
    Watch: the workspace is out of sync until P2.3.1 (`pipeline sync`; `land --dry-run` gives `E_PIPELINE_NOT_SYNCED`), and `uv run` there rewrites `uv.lock`.
- ✅ **P2.2.3** `parse_nem12`.
  - The stage and its `index.parquet`, the staged table, the kinds in `core/models/meter.py`, the registry entry,
    the golden `dvc.yaml`, and `tests/fixtures/pipeline_workspace.py` (two overlapping SAPN datasets with one
    conflict day, a SUMMARY CSV, two usage exports, spanning both DST changes of a year; it grows with each stage).
    The integration test confirms DVC accepts a whole raw directory as a dep and marks the stage stale when a
    dataset lands; if it doesn't, stop and ask (spec §11).
  - Refs: the P2 spec §3.1, §7.1, §11, §12, §13.3 (`test_parse_nem12`); D1 §9, §13.
  - Files: `src/energatron/services/stages/{parse_nem12,registry}.py`, `src/energatron/core/models/meter.py`,
    `tests/unit/services/stages/test_parse_nem12.py`, `tests/fixtures/{pipeline_workspace.py,golden/dvc.yaml}`,
    `tests/integration/` (the raw-directory dep test), `tests/fixtures/schemas/`.
  - Done when: the standard checks pass; every `parse_nem12` check passes and fails in its test; no NMI or serial
    in any output.
  - Handover: `parse_nem12` (eight checks, `elec_interval_staged` and `elec_interval_index` v1), `meter.py` kinds, the pipeline fixture workspace, golden `dvc.yaml` (2161 tests). Decision: owner accepted `meter.py` without pyarrow, `E_INVALID_INPUT` for a missing catalog and the edited verify, runner and assumption tests.
    Watch: the pyarrow schemas live in `parse_nem12.py`; `merge_interval` imports or moves them.
- ✅ **P2.2.4** `merge_interval` and its conflicts report.
  - The stage, `intervals.parquet`, `conflicts.parquet`, `summary.json`, `W_MERGE_SAME_UPDATE`, the golden
    `dvc.yaml` and the fixture workspace.
  - Refs: the P2 spec §7.2, §12, §13.3 (`test_merge_interval`).
  - Files: `src/energatron/services/stages/{merge_interval,registry}.py`, `src/energatron/core/models/meter.py`,
    `tests/unit/services/stages/test_merge_interval.py`, `tests/fixtures/{pipeline_workspace.py,golden/dvc.yaml}`,
    `tests/fixtures/schemas/`.
  - Done when: the standard checks pass; merged rows = distinct days x `1440 / L` per suffix in the test.
  - Handover: `merge_interval` (newest `update_dt` wins per `(suffix, meter_date)`, `intervals`, `conflicts` and `summary`, `W_MERGE_SAME_UPDATE`), shared `services/stages/tables.py`, golden `dvc.yaml` and `merge_summary` v1 (2202 tests). Decision: owner accepted the open summary shapes, `tables.py` and the `E_INVALID_INPUT` input guard.
    Watch: `validate_meter` reads `intervals.parquet` and `summary.json`; import schemas from `tables.py`, never from another stage.
- ✅ **P2.2.5** `validate_meter`: monthly totals against SAPN SUMMARY, the gap list, quality shares.
  - The stage and its four reports, `W_METER_GAPS` and `W_METER_UNVALIDATED`, the golden `dvc.yaml` and the
    fixture workspace.
  - Refs: the P2 spec §8, §12, §13.3 (`test_validate_meter`).
  - Files: `src/energatron/services/stages/{validate_meter,registry}.py`, `src/energatron/core/models/meter.py`,
    `tests/unit/services/stages/test_validate_meter.py`, `tests/fixtures/{pipeline_workspace.py,golden/dvc.yaml}`,
    `tests/fixtures/schemas/`.
  - Done when: the standard checks pass; `match` within the tolerance and `mismatch` just outside it in the test.
  - Handover: `validate_meter` (SUMMARY CSV monthly totals `match`/`mismatch`/`not_covered`, gaps, quality shares, `meter_report` v1, `W_METER_GAPS`/`W_METER_UNVALIDATED`), golden `dvc.yaml`, shared `params_v2_text` (2254 tests). Decision: owner accepted the two extra checks, the `MeterReport` shape, `load_table` and two edited integration tests; a `monthly_totals_match` failure halts `dvc repro` by design.
    Watch: `diff_kwh` is rounded to 6 decimals before the tolerance test; `merge_interval` keeps its own private copy of the table check.
- ✅ **P2.2.6** `validate_timebase`: hypotheses H1-H3 against the retailer's TOU export.
  - `core/timebase.py` (`Timebase`, `parse_windows`, `to_local`, `bucket`), the stage with periods, fit and the
    `confirmed`, `unconfirmed` and `contradicted` verdict, `W_TIMEBASE_UNCONFIRMED` and
    `W_TIMEBASE_UNEXPLAINED`, the golden `dvc.yaml` and the fixture workspace.
  - Refs: the P2 spec §9, §12, §13.1 (`test_timebase`), §13.3 (`test_validate_timebase`); plan §10.2.
  - Files: `src/energatron/core/timebase.py`, `src/energatron/services/stages/{validate_timebase,registry}.py`,
    `src/energatron/core/models/meter.py`, `tests/unit/core/test_timebase.py`,
    `tests/unit/services/stages/test_validate_timebase.py`, `tests/fixtures/{pipeline_workspace.py,golden/dvc.yaml}`,
    `tests/fixtures/schemas/`.
  - Done when: the standard checks pass; exports generated under each basis confirm that basis and contradict the
    others; the winter-only tie gives `unconfirmed` and passes.
  - Handover: `core/timebase.py` and `validate_timebase` (periods, fit under H1-H3, `confirmed`/`unconfirmed`/`contradicted`, `timebase_periods` and `timebase_result` v1, two warnings), golden `dvc.yaml` (2328 tests). Decision: `local_clock` gives the wall time for `local_wall`, and the stage adds `blobs_match_catalog` and `detector_known`; only `contradicted` fails.
    Watch: `localise` must read `result.json` (`configured`, `verdict`) from the report; `validate_timebase` fails on a `null` zone or windows, so tests need `params_v2_text(region=True)`.
- ✅ **P2.2.7** `localise`: meter time to local time, with the DST days, and the end-to-end tests.
  - The stage with its `timebase_gate` (a `confirmed` verdict naming the configured basis, else no table and
    `E_CHECK_FAILED`), `energy_conserved`, `key_unique`, `local_placeable` and `local_day_lengths`; the golden
    `dvc.yaml` with all five stages; `test_localise_tzdata` (a wrong system zone file changes nothing);
    `test_p2_pipeline` in a scratch workspace, ending with `verify`.
  - Refs: the P2 spec §6, §10, §11, §13.3; plan §10.2, §13; D1 §11.4.
  - Files: `src/energatron/services/stages/{localise,registry}.py`, `src/energatron/core/models/meter.py`,
    `tests/unit/services/stages/test_localise.py`, `tests/integration/test_{localise_tzdata,p2_pipeline}.py`,
    `tests/fixtures/{pipeline_workspace.py,golden/dvc.yaml}`, `tests/fixtures/schemas/`.
  - Done when: the standard checks pass; 23-hour and 25-hour local days appear in the test; on `unconfirmed`,
    `contradicted` or a mismatched basis the gate leaves no table.
  - Handover: `localise` (gate on a `confirmed` verdict naming the configured basis, else no table; `elec_interval_local` v1 with `utc_start`, `local_start`, `local_date`, `utc_offset_minutes`; four checks), all six stages in the golden `dvc.yaml`, `test_localise_tzdata` and `test_p2_pipeline` (2357 tests). Decision: an unplaceable `local_wall` row stays in the table with null times and fails `local_placeable`; pipeline tests that run every stage now land the fixture raw zone.
    Watch: the real workspace's gate needs a `confirmed` verdict on its own data; P2.3.1 stops and asks the owner on `contradicted` or a failed gate.

#### P2.3 Acceptance ✅
- ✅ **P2.3.1** P2 acceptance in the workspace.
  - The spec's §14 checks 1-9, in order: `pipeline sync` (params v2, zone and windows filled), `fingerprint`,
    `dvc repro`; interval counts, monthly totals against SAPN SUMMARY, merge conflicts reported, the time basis
    `confirmed` with evidence, the DST days; then `verify`, a clean state and the pending backup. If
    `validate_timebase` is `contradicted` or `localise` fails its gate, stop and ask the owner before changing
    `meter.timebase` (decision #11 allows nothing weaker).
  - Refs: the P2 spec §14; plan §15 (P2); D1 §11.4.
  - Files (`demq-elektric/`): `dvc.yaml`, `dvc.lock`, `params.yaml`, `uv.lock` (the tool gained `tzdata`), `.energatron/code_fingerprint.json`,
    `data/staged/.gitignore`, `data/interim/.gitignore`, `data/reports/.gitignore`; a dated note in
    `agent_docs/` for results that mention data.
  - Done when: every §14 check passes; `TASKS.md` gets counts and pass/fail only.
  - On /done: commit those files as one workspace commit.
  - Handover: workspace synced to params v2 and all six stages run; time basis `confirmed` (`nem_aest`), 22 SUMMARY rows match, 0 merge conflicts, 3 short and 3 long DST days; one workspace commit. Decision: the retailer's July-September reads fit no hypothesis because its tariff windows changed (found by probe, in the workspace note), so the clock basis stands.
    Watch: `params` holds one window set, so tariff-time costing after the change needs window history (spec §17 Q5).
- ✅ **P2.3.2** Owner: back up after P2.
  - Why: owner step (iCloud).
  - Refs: the runbook. Files: none.
  - Done when: `icloud_backup.py backup` exits 0, and `backup --status` then shows `refs_backed_up: true`.
  - Handover: the 2026-11-03 backup holds the P2 workspace commit (`refs_backed_up: true`, 0 commits pending); recorded in one batch with P0.4.3, P0.5.3 and P1.5.3.
    Watch: back up again after any new workspace commit (`/done` reports it pending).

### M: Maintenance (owner requests outside the phases) ✅

- ✅ **M.1** Speed up the test suite.
  - Run the tests in parallel and cut the per-test git and DVC set-up of the scratch repos.
  - Refs: owner request (2026-11-03); D1 (git and DVC stay subprocesses).
  - Files: `pyproject.toml`, `uv.lock`, `tests/integration/_scratch.py`, `TASKS.md`.
  - Done when: the standard checks pass and `uv run pytest -q` finishes in about a minute.
  - Handover: `pytest -q` runs in parallel via pytest-xdist (`-n auto --dist worksteal`), ~5 min → ~55 s; `make_scratch` copies a once-per-process template repo and writes `.dvc/config.local` itself. Decision: worksteal over the default `load` (60 s against 118 s).
    Watch: `-n0` runs serially (`-p no:xdist` fails while `-n` is in `addopts`); the run is now bound by its slowest tests (~13 s each).

### P3: Usage profile ✅

Archived verbatim in `agent_docs/2026-11/2026-11-04_tasks-archive-p3.md`: P3.1.1, P3.2.1–P3.2.5, P3.3.1–P3.3.2, all ✅.

### P4: Offers + electricity engine 🔄

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

#### P4.2 Build 🔄
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
- ⬜ **P4.2.6** The engine.
  - `core/engine.py` (spec §7), the oracle additions in `tests/fixtures/oracle.py`, property tests.
  - Refs: the P4 spec §7, §14.
  - Files: `src/energatron/core/engine.py`, `tests/fixtures/oracle.py`, `tests/unit/core/test_engine{,_properties}.py`.
  - Done when: the standard checks pass; the engine equals the oracle on the fixtures; spec §7.9 holds.
- ⬜ **P4.2.7** `cost_elec`.
  - The stage, its three outputs and four checks, the cost table kinds, the golden `dvc.yaml`, the fixture workspace.
  - Refs: the P4 spec §9; the P3 spec §8 (`usage_summary`).
  - Files: `src/energatron/services/stages/{cost_elec,registry}.py`, `src/energatron/core/models/costs.py`,
    `tests/unit/services/stages/test_cost_elec.py`, `tests/fixtures/{pipeline_workspace.py,golden/dvc.yaml}`,
    `tests/fixtures/schemas/`.
  - Done when: the standard checks pass; every check passes and fails in its test.
- ⬜ **P4.2.8** `backtest`.
  - `core/retailer_txn.py`, the stage, its outputs, checks and two warnings, a transactions generator, the golden
    `dvc.yaml`, the fixture workspace.
  - Refs: the P4 spec §10; the P2 spec §9.3.
  - Files: `src/energatron/core/retailer_txn.py`, `src/energatron/services/stages/{backtest,registry}.py`,
    `tests/unit/core/test_retailer_txn.py`, `tests/unit/services/stages/test_backtest.py`,
    `tests/fixtures/{make_sources.py,pipeline_workspace.py,golden/dvc.yaml}`, `tests/fixtures/schemas/`.
  - Done when: the standard checks pass; a generated invoice equal to the engine's bill is `within`, one 2% off is
    `outside` and warns.
- ⬜ **P4.2.9** `energatron offers diff` and the end-to-end P4 test.
  - `services/offer_diff.py`, the command, `offer_diff_result` v1; `tests/integration/test_p4_pipeline.py` (land,
    `fetch-offers` against a local HTTP server, `dvc repro`, `offers diff`, `verify`).
  - Refs: the P4 spec §11, §14; D1 rev 5 §11.4.
  - Files: `src/energatron/services/offer_diff.py`, `src/energatron/adapters/cli/`,
    `tests/unit/services/test_offer_diff.py`, `tests/integration/test_p4_pipeline.py`, `tests/fixtures/schemas/`.
  - Done when: the standard checks pass; the end-to-end test passes.

#### P4.3 Acceptance ⬜
- ⬜ **P4.3.1** P4 acceptance in the workspace.
  - The spec's §13 checks 1-8, in order. If the GST check fails, stop and ask the owner.
  - Refs: the P4 spec §13; plan §15 (P4).
  - Files (`demq-elektric/`): `dvc.yaml`, `dvc.lock`, `params.yaml`, `.energatron/code_fingerprint.json`,
    `config/offer_rules.local/README.md`, new `data/*/.gitignore` files; the `land` commit is `fetch-offers`' own; a
    dated note in `agent_docs/` for results that mention data.
  - Done when: checks 1-5, 7 and 8 pass; check 6 passes or waits on P4.3.2; `TASKS.md` gets counts and pass/fail only.
  - On /done: commit those files as one workspace commit.
- ⬜ **P4.3.2** Owner: the historical tariff, then back up after P4.
  - Why: owner step (the tariff in force comes from the owner's bills; iCloud).
  - The owner writes an `offer_manual` YAML for the plan in force, lands it, sets `backtest.tariffs`, runs `dvc repro`
    (spec §13 check 6, and check 4 if the agent couldn't render the BPID); then `icloud_backup.py backup`.
  - Refs: the P4 spec §5.3, §10, §13; the runbook.
  - Done when: the backtest is within ±1% or its deviations are explained in the workspace note, and `backup --status`
    shows `refs_backed_up: true`.

### Later (plan §15)
P5 gas · P6 bundles, recommendation, decisions · P7 operations. Each starts with its own spec sub-task.

## Log (newest first)

- 2026-11-04 · P4.2.5 · `normalise_offers` stage (current plans, newest snapshots, rules, three outputs, six checks), offer mart kinds, fixture offer zone, golden `dvc.yaml` with nine stages, pipeline tests moved to params v4 (3032 tests); self-review pass · energatron-5000 `P4.2.5: add the normalise_offers stage`
- 2026-11-04 · P4.2.4 · CDR provider over stdlib `urllib` (tested against a local HTTP server), `fetch-offers` service and command with the local filter, unchanged skip, partial failures and one `land` commit, lock coverage, `fetch_offers_result` v1 (3003 tests); self-review pass · energatron-5000 `P4.2.4: add the CDR provider and energatron fetch-offers`
- 2026-11-04 · P4.2.3 · fetch log and `offer_manual` models and parsers, offer rules (model, two directories, matching, effects) and three curated wholesale-estimate rules, fixture builders (2960 tests); self-review pass; spec §3.3 and §6 brought in line · energatron-5000 `P4.2.3: add the fetch log, offer_manual and offer rules`
- 2026-11-04 · P4.2.2 · `Tariff` model with partition rules and the GST constants, `map_cdr` covering every row of the spec's §5.2 table, `make_cdr` fixture generator (2907 tests); self-review pass; on the real SAPN market every plan normalises, is approximate or is excluded with a reason · energatron-5000 `P4.2.2: add the Tariff model and the CDR mapping`
- 2026-11-04 · P4.2.1 · `Params` v4 and `RegionProfile` v3 (distributor, CDR retailers), `pipeline sync` fills them and creates `config/offer_rules.local/`, four P4 warnings, golden `params/v4` and `region_profile/v3` (2815 tests); self-review pass, no findings · energatron-5000 `P4.2.1: add params v4, region profile v3 and the P4 warnings`
- 2026-11-04 · P4.1.1 · P4 offers and electricity engine spec approved (Q1-Q8 as recommended, on the owner's standing instruction); plan rev 12 and D1 rev 5 applied from its §15; P4.2-P4.3 rewritten from its order of work (P4.2.1-P4.2.9, P4.3.1, P4.3.2) · energatron-5000 `P4.1.1: approve the P4 offers and electricity engine spec and apply plan rev 12 and D1 rev 5`
- 2026-11-04 · P3.3.2 · owner backup done (`backup --status`: `refs_backed_up` true, 0 commits behind); P3.3 and P3 ticked, P3 archived · energatron-5000 `P3.3.2: close P3 after the owner's backup`
- 2026-11-04 · P3.3.1 · P3 acceptance in the workspace: params v3 with a second TOU window set (applied on the owner's behalf; to confirm), `fingerprint`, `dvc repro` (`usage_profile` 7 checks, `usage_report`), reference window full, report 1 file within budget with no external reference, legacy reconciliation R1-R4 pass, `verify` ok, privacy guard 0 violations; browser look and backup pending · demq-elektric `P3.3.1: record the P3 usage profile and report run in the workspace`; energatron-5000 `P3.3.1: record P3 acceptance in the workspace`
- 2026-11-04 · P3.2.5 · `usage_report` stage (marts in, `index.html` out, empty `params`), golden `dvc.yaml` with eight stages, `test_p3_pipeline` ending with `verify` (2703 tests); fresh-reviewer nits fixed · energatron-5000 `P3.2.5: add the usage_report stage and the end-to-end P3 test`
- 2026-11-04 · P3.2.4 · SVG chart primitives (`core/charts.py`) and the usage report page built from the marts alone (`core/usage_report.py`, golden `usage_report.html`) (2652 tests) · energatron-5000 `P3.2.4: add the SVG chart primitives and the usage report page`
- 2026-11-04 · P3.2.3 · `usage_profile` stage (nine tables and `summary.json`, seven checks, two warnings), golden `dvc.yaml` with seven stages, whole-pipeline tests moved to params v3 (2562 tests) · energatron-5000 `P3.2.3: add the usage_profile stage, its nine tables and summary`
- 2026-11-04 · P3.2.2 · Usage metric functions (`core/usage.py`), nine table kinds and `UsageSummary` v1 (`core/models/usage.py`), the oracle's local-table half and `build_marts` (2522 tests) · energatron-5000 `P3.2.2: add the usage metric functions, the usage table kinds and their test oracle`
- 2026-11-03 · P3.2.1 · `Params` v3 (`analysis` block, date-ranged TOU window sets, v2 to v3 migration), `pipeline sync` fills the first window set, `local_day_minutes` moved to `core/timebase.py`, two P3 warning codes (2463 tests) · energatron-5000 `P3.2.1: add Params v3, the first TOU window set in pipeline sync and local_day_minutes`
- 2026-11-03 · P3.1.1 · P3 usage profile spec approved (owner: Q1-Q11 as recommended; retailer plan details are not private); plan rev 11 and D1 rev 4 applied from its §15; P3.2-P3.3 rewritten from its order of work (P3.2.1-P3.2.5, P3.3.1, P3.3.2) · energatron-5000 `P3.1.1: approve the P3 usage profile spec and apply plan rev 11 and D1 rev 4`
- 2026-11-03 · M.1 · Test suite in parallel (pytest-xdist, worksteal) and a template scratch repo: `pytest -q` ~5 min → ~55 s, 2372 passed · energatron-5000 `M.1: run the tests in parallel and copy a template scratch repo`
- 2026-11-03 · P0.4.2 · CI fixes for 2 Linux/UTC-only test failures (`cdr_plan.py` maps an uncaught `RecursionError` to `bad_json`; tag test pinned off `+0000`), pushed to `origin`; P0.4 and P0 ticked, P0 archived · energatron-5000 `P0.4.2: fix the two Linux-only CI failures`
- 2026-11-03 · P0.5.2 · Fresh-session checks: Claude Code named `new-doc`, quoted the loaded `CLAUDE.md` and was denied `private/` reads in all three folders; Codex half waived (not installed); P0.5 ticked · energatron-5000 `P0.5.2: record the fresh-session checks`
- 2026-11-03 · P0.3.2 · Owner added the postcode and an electricity account to the private config and left the street out (Done when relaxed to allow it); `denylist` re-generated (7 entries) and `private/` re-tracked; `--check` ok · demq-elektric `P0.3.2: re-track private/ after adding the postcode and an account`; energatron-5000 `P0.3.2: record the private values step`
- 2026-11-03 · P0.4.3, P0.5.3, P1.5.3, P2.3.2 · Owner-run backup and drill steps recorded in one batch: first backup pushed 57 DVC files and wrote 1 bundle, repeats were no-ops, the agent-hosting app gets `E_STORAGE`; restore drill 12 of 12 steps, exit 0 (step 9 `verify` passed); `refs_backed_up: true`; P1 and P2 phases ticked · energatron-5000 `P0.4.3: record the first backup, the restore drill and the P1 and P2 backups`
- 2026-11-03 · P0.4.4 · `icloud_backup.py` defaults its backups folder to the parent of `backup.bundle_dir` in the workspace's `.energatron/local.toml` (else iCloud `Backups`; `--backups-dir` still wins); 12 new tests (2369 tests), D2 §4 and the runbook updated; owner moved the backup destinations through the git-ignored local config · energatron-5000 `P0.4.4: default the backups folder from the workspace's local.toml`
- 2026-11-02 · P2.3.1 · P2 acceptance in the workspace: `pipeline sync` (params v2), `fingerprint`, `dvc repro` (5 stages, 24 checks), time basis `confirmed`, 22 of 22 SUMMARY rows match, 0 merge conflicts, 3 short and 3 long DST days, `verify` ok; backup pending · demq-elektric `P2.3.1: record the P2 pipeline run and acceptance in the workspace`; energatron-5000 `P2.3.1: record P2 acceptance in the workspace`
- 2026-11-02 · P2.2.7 · `localise` stage (gated on a `confirmed` verdict, `elec_interval_local` v1, four checks), golden `dvc.yaml` with six stages, `test_localise_tzdata` and `test_p2_pipeline` (2357 tests); examined by the same agent, no findings; four existing pipeline tests now land the fixture raw zone · energatron-5000 `P2.2.7: add the localise stage and the end-to-end P2 tests`
- 2026-10-30 · P2.2.6 · `core/timebase.py` and the `validate_timebase` stage (hypotheses H1-H3 against the retailer export, verdict, `timebase_periods`/`timebase_result` v1, `W_TIMEBASE_UNCONFIRMED`/`UNEXPLAINED`), golden `dvc.yaml`, `params_v2_text(region=True)` (2328 tests) · energatron-5000 `P2.2.6: add the validate_timebase stage and core/timebase`
- 2026-10-30 · P2.2.5 · `validate_meter` stage (monthly totals against SAPN SUMMARY, gaps, quality shares, `meter_report` v1, `W_METER_GAPS`, `W_METER_UNVALIDATED`), `load_table`, shared `params_v2_text`, golden `dvc.yaml` (2254 tests); review nits 1, 2, 4 fixed, nit 3 answered; owner accepted the four recorded deviations · energatron-5000 `P2.2.5: add the validate_meter stage and its reports`
- 2026-10-30 · P2.2.4 · `merge_interval` stage (newest `update_dt` per day, `intervals`, `conflicts`, `summary`, `W_MERGE_SAME_UPDATE`), shared `services/stages/tables.py`, golden `dvc.yaml` and `merge_summary` v1 (2202 tests); owner accepted the three recorded deviations · energatron-5000 `P2.2.4: add the merge_interval stage and its conflicts report`
- 2026-10-30 · P2.2.3 · `parse_nem12` stage (whole raw dir, per-dataset tables and `index.parquet`, eight checks), `core/models/meter.py` kinds, fixture workspace `pipeline_workspace.py`, golden `dvc.yaml`, raw-directory DVC dep confirmed (2161 tests); review nits 1-4 fixed; owner accepted the recorded deviations · energatron-5000 `P2.2.3: add the parse_nem12 stage and the pipeline fixture workspace`
- 2026-10-30 · P2.2.2 · format readers (`core/nem12.py`, `sapn_summary.py`, `retailer_usage.py`, `text.py`) with `scan_*`/`read_*`, P1 parsers on `scan_*` unchanged at v1, `precedence_order`, fixture additions and the independent oracle (2101 tests); review nits 1–3 fixed; owner accepted three deviations and keeps `duplicate_label` · energatron-5000 `P2.2.2: move the format readers into core and add dataset precedence`
- 2026-10-30 · P2.2.1 · `Params` v2 and `RegionProfile` v2 (null zone and windows after migration), `regions/sa.yaml` v2, `ServiceContext.region`, `pipeline sync` fills zone and windows, `StageRun.zone()` on `tzdata`, five P2 warning codes, `test_stage_purity` zone bans, `test_no_region_values` (1877 tests); review nits 1–2 fixed; owner accepted the five deviations and the `getattr(zoneinfo, "ZoneInfo")` known limit · energatron-5000 `P2.2.1: add Params v2, RegionProfile v2, StageRun.zone() and the P2 warnings`
- 2026-10-30 · P2.1.1 · P2 meter pipeline spec approved (owner: Q1–Q9 as written, except Q6: no waiver of decision #11); plan rev 10 and D1 rev 3 applied from its §16; P2.2–P2.3 rewritten from its order of work (P2.2.1–P2.2.7, P2.3.1) · energatron-5000 `P2.1.1: approve the P2 meter pipeline spec and apply plan rev 10 and D1 rev 3`
- 2026-10-30 · P1.5.2 · `land` on the legacy folders (its own commit `land: 5 datasets`; 7 datasets 2/2/2/1, hourly JSON rejected, exit 8; owner checked, inputs intact); re-land a no-op; `fingerprint` (`tool_commit` only) + `dvc repro` → `catalog_index` 7 rows, 6 checks pass; `dvc status` clean; acceptance note in the workspace · demq-elektric `P1.5.2: record the pipeline run on the landed legacy files`; energatron-5000 `P1.5.2: record landing the legacy files and P1's acceptance`
- 2026-10-30 · P1.5.1 · `make_sources.py` generators, `pdf_meta.creation_date`, parsers `bill_pdf` (elec + gas), `sapn_summary` (CSV + PDF), `retailer_usage`, `retailer_txn`, `cdr_plan`; `PARSERS` and `regions/sa.yaml` complete, `resolve_parser` on the real registry (1690 tests); review nits 1–3 fixed; owner accepted deviations (`test_wiring.py` edit, `_common.py`, extra reasons, quote-aware CSV, extra generator kwargs, `offer_cdr` `extra.source_ts_from`); reviewer's dry run on the legacy files matched D3 §15 · energatron-5000 `P1.5.1: add the other five source parsers and their fixture generators`
- 2026-10-30 · P1.4.4 · `land` landed the two current SAPN files (its own commit, 2 datasets, inputs untouched; owner checked); `fingerprint` + `dvc repro` → `catalog_index` 2 rows, 6 checks pass; `dvc status` clean; fingerprint file committed too (`tool_commit` only) · demq-elektric `P1.4.4: record the pipeline run on the landed SAPN files`; energatron-5000 `P1.4.4: record landing the SAPN files and the pipeline run`
- 2026-10-30 · P1.4.3 · Workspace P1 set-up (D3 §15 step 1): `pipeline sync` (`dvc.yaml`, `params.yaml`, 10 empty catalogs + pointers), `fingerprint` (`tool_dirty: false`), `dvc repro` (`catalog_index` 0 rows, all checks pass), `pipeline-sync` hook before `agent-links`, `land`/`pipeline sync` in workspace Commands; owner checked the workspace diff · demq-elektric `P1.4.3: set up the P1 pipeline in the workspace`; energatron-5000 `P1.4.3: record the workspace P1 pipeline set-up`
- 2026-10-30 · P1.4.2 · `services/land.py` (`resolve_parser`, §7.1 preconditions, plan, write, recovery via `land_pending`), `energatron land`, `land_result`/`land_pending` v1, `CommandOutput.error` (1540 tests); review nits 1–2 fixed; owner accepted deviations (extra rejection reasons, `--source` precheck → `E_USAGE`, `E_PARTIAL` details, whole-second timestamps, extra helpers, `CommandOutput.error`) · energatron-5000 `P1.4.2: add the land service and energatron land`
- 2026-10-30 · P1.4.1 · `make_nem12.py` NEM12 generator (+ `nem12_header_dt`), `SapnIntervalParser` (`detect`, `inspect` in §5.2's order, `SourceInfo`), `PARSERS`/`parsers_for`, `regions/sa.yaml` loaded in `build_context()` (1460 tests); review finding 1 and nits 2–4 fixed; owner accepted deviations (extra reason words, `no_data`, `nem12_header_dt`, package marker) · energatron-5000 `P1.4.1: add the NEM12 fixture generator and the sapn_interval parser`
- 2026-10-30 · P1.3.4 · `pipeline sync [--check] [--dry-run]` (`services/pipeline_sync.py`), `verify` (fingerprint, DVC status, blob re-hash, stage reruns), `pipeline_sync_result`/`verify_result` v1, restore drill step 9 runs `verify` on P1 clones (1364 tests); review nits 1–3 fixed; owner accepted deviations 1–3 · energatron-5000 `P1.3.4: add energatron pipeline sync and energatron verify`
- 2026-10-30 · P1.3.3 · `energatron stage` (repro-log records in `main()`, `--emit-dvc-yaml`), `DvcRunner` wired into `build_context()`, `stage_result`/`dvc_yaml_result` v1, D1 §11.5 checks pass, D3 §14 checks 1, 2, 4 pass and 3 recorded (1325 tests); review finding 1 and nits 2–3 fixed; owner accepted the new kind, extra files and fake stages · energatron-5000 `P1.3.3: add energatron stage, DvcRunner and the DVC checks`
- 2026-10-30 · P1.3.2 · `ast` module closure, code fingerprint (`stage_code`, `install_fields`, `refresh_fingerprint`), `fingerprint [--check] [--dry-run] [--yes]`, `fingerprint_result` v1, stage closure/purity architecture tests (1270 tests); review finding 1 fixed; owner accepted the recorded decisions, D1 unchanged on dev-venv extras · energatron-5000 `P1.3.2: add stage closure, code fingerprint and energatron fingerprint`
- 2026-10-30 · P1.3.1 · Stage runner (`run_stage`/`prepare_stage`, provenance, atomic promote), registry, `catalog_index` stage (`datasets.parquet`, six checks), `dvc.yaml` emitter + golden, `write_parquet`/`parquet_rows`, lazy core `catalog_lines` (1154 tests); review finding 1 and nits 2–3 fixed; owner accepted the recorded deviations · energatron-5000 `P1.3.1: add stage runner, registry, catalog_index and dvc.yaml emitter`
- 2026-10-30 · P1.2.2 · `FsBlobStore` and `JsonlCatalog` raw-zone adapters wired into `build_context()`, port methods `keys`/`missing_sources`/`initialise`, core `parse_catalog`, `make_dirs` (1088 tests); review findings 1-5 and the nit fixed; owner accepted the deviations incl. the extra core/services files · energatron-5000 `P1.2.2: add FsBlobStore and JsonlCatalog raw-zone adapters`
- 2026-10-30 · P1.2.1 · P1 core models (catalog record, raw-zone keys, provenance, params, region, repro log), P1 codes, `check_premises`/`mask_filename`, synthetic fixtures, 5 golden schemas (1006 tests); owner accepted deviations 1–3 and `<redacted>` (D3 unchanged); nits 2–3 fixed by the owner · energatron-5000 `P1.2.1: add P1 core models, codes and privacy helpers`
- 2026-10-30 · P0.5.1 · P0 acceptance: D2 §13 check 2 passed in both repos (4/4 test commits rejected by the expected hook, labels only), `--history` 0 violations, link checks exit 0; owner verified one commit per repo and no scratch file · energatron-5000 `P0.5.1: record P0 acceptance of guard, links and history`
- 2026-10-30 · P0.4.1 · Tool repo prepared per D2 §5.8 (local identity, `energatron.denylist`, `pre-commit`/`commit-msg` hooks, `link_parent.py --check`), audit of 138 paths 0 violations (owner read the figure counts); Rules now name D2 §5.4's checks · energatron-5000 `P0: bootstrap energatron-5000`
- 2026-10-30 · P1.1.1 · D1 rev 2: D2 §1 and D3 §17 changes, third-review nits, D4's `verify` design (`services/verify.py`, `verify_result` v1), P0's record, §8.5/§8.7 owner decisions; 4 review fixes (missing blob → `E_CATALOG_CORRUPT` `missing_blob`, kinds `stage_result`/`fingerprint_result`) · not committed: tool repo's first commit is P0.4.1
- 2026-10-30 · P0.3.1 · Workspace set up per D2 §11.4 (git, uv, DVC with a `--local` iCloud remote, private config via the owner-run §11.2 snippet, denylist, `dvc add legacy`, hooks); audit 0 violations · demq-elektric `P0: bootstrap demq-elektric workspace`
- 2026-10-29 · P0.2.7 · `CLAUDE.md` + `AGENTS.md`, `parent/CLAUDE.md`, deny-only `.claude/settings.json`, `new-doc` skill, `link_parent.py` parent links and `--parent-dir` (877 tests), README rewritten, CHANGELOG new, parent links installed; 2 review fixes (no dangling `AGENTS.md`, foreign symlinks reported) · not committed: no repo yet
- 2026-10-29 · P0.2.6 · Stdlib `restore_drill.py` (D2 §12's 12 steps, text/`--json`, exit 0/1/2, `verify true` in the clone only) and tests for `icloud_backup.py` and the drill (858 tests); 3 review fixes (snapshot test: live workspace, bundle folder and remote unchanged; `uv run --frozen --no-sync`; one line per step after an unexpected exception) · not committed: no repo yet
- 2026-10-29 · P0.2.5 · Stdlib privacy guard (D2 §7: all sources, rules, redacted output, `--figures`), `check_agent_links.py`, pre-commit config, SHA-pinned CI; envelope `command` only for a known command (D1 §8.5, via P1.1.1) (826 tests); 5 review fixes (history blob scan, `--ignore-submodules=none`, UTF-16 via NUL removal, ref names and annotated tags); owner decision: D2 §7.4 data types extended with `.gz .bz2 .xz .zst .7z .tar .tgz .docx .xlsm .ods .odt` · not committed: no repo yet
- 2026-10-29 · P0.2.4 · Workspace adapter, wiring and the CLI (`version`, `denylist`, `backup`), 3 kinds incl. the `cli_result` envelope, `/done` skill uses `backup --status --json` (627 tests); 4 review fixes (`bundle_dir` checked only in real `backup`, generic `E_USAGE`, `denylist.txt` split on `\n` with rules written into P0.2.5, warnings in the leak test) · not committed: no repo yet
- 2026-10-29 · P0.2.3 · git+DVC adapter (six `VersionControl` methods, D2 §8.6 hook mapping, §8.1 backup with bundles, retention, dataless count and marker), `services/{files,backup}.py`, 2 result kinds (490 tests); 4 review findings fixed; owner accepted warnings outside `data` (lifted into the CLI envelope) and `.import_linter_cache/` in `.gitignore` (a D2 §5.3 deviation) · not committed: no repo yet
- 2026-10-29 · P0.2.2 · Core privacy (`nmi_checksum`, denylist, `redact`/`redact_json`), P0 models, all D1 §4 ports, `ServiceContext`, 5 golden schemas (419 tests); 5 review findings fixed; owner decision: JSON numbers are redacted too (D1 §8.7 change for P1.1.1) · not committed: no repo yet
- 2026-10-29 · P0.1.1 · Owner step done: iCloud access checked, Step 0 copy made and its SHA256SUMS verified (owner's report) · not committed: no repo yet
- 2026-10-29 · P0.2.1 · uv project, D1 §2 packages and core canonical/errors/persisted/ids (235 tests); review nit fixed (`allow_inf_nan=False`); owner accepted deviations 1–4 (ruff format excludes `*.md`, D1/D3 code blocks stay as approved) · not committed: no repo yet
- 2026-10-24 · S.5.1 · D4, the P0–P2 breakdown (38 sub-tasks), approved with its four D2/D3 deviations and slices inside each phase; spec stage ✅ and archived · not committed: no repo yet
- 2026-10-23 · S.4.1 · D3 P1 landing & catalog spec approved after 19 review fixes; plan rev 9 applies its §17 edits · not committed: no repo yet
- 2026-10-23 · S.3.1 · D2 P0 bootstrap spec approved, with `scripts/icloud_backup.py` and the owner runbook; plan rev 8 applies its §15 edits · not committed: no repo yet
- 2026-10-23 · S.2.1 · D1 core architecture spec approved; plan rev 7 applies its §15 edits · not committed: no repo yet
- 2026-10-23 · — · Owner's language rule (Python + uv everywhere; another language only for a frontend) recorded in plan rev 6, hand-over §4 #15, D1 §12 and the Rules here · not committed: no repo yet
- 2026-10-23 · — · `/review` renamed `/examine` (Claude Code and Codex both have a built-in `/review`); parent-folder wrappers and links now generated by `scripts/link_parent.py` · not committed: no repo yet
- 2026-10-23 · S.2.1 · D1 drafted; awaiting owner review · not committed: no repo yet
- 2026-10-23 · — · Tracker and `/next` `/review` `/done` skills set up · not committed: no repo yet
- 2026-10-23 · S.1.1 · Before-P0 answers and the Python check recorded in plan rev 5 · not committed: no repo yet
- 2026-10-23 · — · Architecture planning session: plan rev 1–4 and the hand-over written · not committed: no repo yet
