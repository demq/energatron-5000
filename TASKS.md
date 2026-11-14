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
| `/phase`, or `/phase <id>` | Your standing approval for a whole phase. Drafts and self-approves its spec, adds its sub-tasks, then does, self-reviews and commits each one in turn (the `/next`, `/examine` and `/done` steps), and stops when only your steps are left, with step-by-step instructions for them. Stops early for a decision that is yours or a command a permission prompt holds. |

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
- Last done: P9.2.3
- Last commit: energatron-5000 `P9.2.3: add energatron mcp, a stdio MCP server whose tools run the CLI`
- Checks: pytest -q 3850 passed; ruff, pyright, lint-imports (6 contracts) clean; privacy scan clean; pre-commit passed
- Review: none
- Next: P9.2.4 (workspace skills, .mcp.json, template v2)

## Hand-over (rewritten by every `/done`)

**Where we are**
- P0-P8 are done and archived; the tool repo is public. P9 (agent interface) runs under the owner's `/phase 9`
  (2026-11-14): spec approved, P9.2.1-P9.2.3 (read commands, gas bill tools, MCP server) done; P9.2.4 builds, P9.3.1 is the agent's acceptance,
  P9.3.2 the owner's.

**Read first** (for the next session)
- The P9 spec (`agent_docs/2026-11/2026-11-14_p9-agent-interface-spec.md`), the section the sub-task names; D1 rev 10
  §3 (the `mcp-via-cli` contract) and §8.

**Facts**
- Read commands (`datasets`, `usage`, `compare`, `explain`) are lock-free and return no identifier, file name, note or
  raw row; `status.py`'s `read_manifests`/`read_recommendation` read the run manifests and the recommendation.
- MCP tools run `[sys.executable, "-m", "energatron", …, "--json", "--workspace", root]` as children; the server holds no
  state and imports no services (contract `mcp-via-cli`).
- `gas_bill_record` v2 changes `core/models/gas.py`: the gas stages rerun once. No new codes or warnings in P9.
- Renaming a command, option or JSON field needs a deprecation (D1 §8.1); a template change bumps `TEMPLATE_VERSION`.

**Open questions for the owner**
- Optional: cut `v0.1.0`; decide whether to switch (`record-decision`); set Keep Downloaded on the iCloud Backups folder.

**Watch out**
- Never put a number or date read from a real bill into a test, docstring or doc, even as an example.
- pytest takes ~3 minutes in parallel (`test_demo` ~33 s); don't edit files while it runs.

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

### P4: Offers + electricity engine ✅

Archived verbatim in `agent_docs/2026-11/2026-11-05_tasks-archive-p4.md`: P4.1.1, P4.2.1–P4.2.9, P4.3.1–P4.3.2, all ✅.

### P5: Gas ✅

Archived verbatim in `agent_docs/2026-11/2026-11-05_tasks-archive-p5.md`: P5.1.1, P5.2.1–P5.2.6, P5.3.1, all ✅.

### P6: Bundles, recommendation, decisions ✅

Archived verbatim in `agent_docs/2026-11/2026-11-10_tasks-archive-p6.md`: P6.1.1, P6.2.1–P6.2.6, P6.3.1, all ✅.

### P7: Operations ✅

Archived verbatim in `agent_docs/2026-11/2026-11-14_tasks-archive-p7.md`: P7.1.1, P7.2.1–P7.2.5, P7.3.1–P7.3.2,
P7.4.1–P7.4.2, all ✅.

### P8: Open-source readiness ✅

Archived verbatim in `agent_docs/2026-11/2026-11-14_tasks-archive-p8.md`: P8.1.1, P8.2.1–P8.2.5, P8.3.1–P8.3.2, all ✅
(P8.3.2's clean-Mac check waived by the owner).

### P9: Agent interface 🔄

From plan §15 (P9) and §14.5: a local MCP server over the tool's commands, workspace skills, and agent-assisted gas bill
transcription that deterministic checks accept or reject, with `extracted_by` recorded. Started by the owner's `/phase
9` of 2026-11-14. D1 binds all code. The owner keeps a real agent session's check-in, pushing and backups.

#### P9.1 P9 spec ✅
- ✅ **P9.1.1** Draft the P9 spec.
  - Refs: plan §14.5, §14.7, §15 (P9); D1 §2, §3, §5, §8; the P5 spec §3 (gas bill records), the P6 spec §9, the P7
    spec §4, the P8 spec §3, §6 (template, `upgrade`); D2 §7.4.
  - Files: `agent_docs/2026-11/2026-11-14_p9-agent-interface-spec.md`, the plan and D1 (revision lines and edits),
    D2 (§7.4 and a revision line), `TASKS.md`; also the `/phase` skill the owner added
    (`.claude/skills/phase/SKILL.md`, `.agents/skills/phase`, `CLAUDE.md`, `parent/CLAUDE.md`).
  - Done when: the spec is approved and the build and acceptance sub-tasks are added from it.
  - Handover: P9 spec approved on the owner's `/phase 9` (self-review: demo `fetch_offers` is a dry run, `compare`/`explain`
    nulls, transcription tools in the check-in test); plan rev 18, D1 rev 10, D2 rev 3. Watch: tools run the CLI as children.

#### P9.2 Build 🔄
- ✅ **P9.2.1** Read commands: `datasets`, `usage`, `compare`, `explain`.
  - `services/{datasets,usage_summary,compare,explain}.py` with `datasets_result`, `usage_result`, `compare_result`,
    `explain_result` v1 (golden schemas); the four commands (`explain` gets the stale stages from the CLI as `status`
    does); no identifier, file name, note or raw row in any result.
  - Refs: the P9 spec §3, §8 (`test_datasets`, `test_usage_summary`, `test_compare`, `test_explain`), §9; D1 §8, §11.4;
    the P6 spec §6, §7; the P7 spec §4.
  - Files: `src/energatron/services/{datasets,usage_summary,compare,explain}.py`,
    `src/energatron/adapters/cli/{app.py,commands/}`, `tests/unit/services/test_{datasets,usage_summary,compare,explain}.py`,
    `tests/unit/adapters/` (CLI checks), `tests/fixtures/schemas/`, `CHANGELOG.md`.
  - Done when: the standard checks pass; each command runs in a scratch demo workspace and prints one envelope.
  - Handover: `datasets`, `usage`, `compare`, `explain` (services + commands, four result kinds; 3732 tests). Decision: `land`'s
    `source_type` is now public for reuse. Watch: `explain` needs the CLI's `code_state`; MCP tools reuse these commands as is.
- ✅ **P9.2.2** Gas bill transcription with validators and `extracted_by`.
  - `gas_bill_record` v2 (`extracted_by`) and its v1 → v2 migration; the skeleton at v2; the parser's
    `extra.extracted_by` (version +1); `gas-bill new --extracted-by`, `gas-bill check`, `gas-bill write`, `gas-bill
    text`; `gas_bill_check_result` and `gas_bill_text_result` v1; the demo's records at v2.
  - Refs: the P9 spec §4, §8, §9; the P5 spec §3, §6; D1 §5, §8.8.
  - Files: `src/energatron/core/models/gas.py`, `src/energatron/services/{gas_bill,demo}.py`,
    `src/energatron/adapters/sources/gas_bill_record.py`, `src/energatron/adapters/cli/commands/gas_bill.py`,
    `tests/unit/` (gas bill record, check, text, parser), `tests/fixtures/` (generators, schemas), `CHANGELOG.md`.
  - Done when: the standard checks pass; a record failing a check is refused with exit 6 and nothing written; a written
    record lands; landed v1 records still read.
  - Handover: `gas_bill_record` v2 (`extracted_by`, v1 migration), parser v2 (`extra.extracted_by`), `gas-bill check|write|text`,
    `new --extracted-by` (3774 tests). Decision: a failing check is `E_CHECK_FAILED` exit 7 with `details.failed`, `data` null (spec fixed). Watch: gas stages rerun once.
- ✅ **P9.2.3** The MCP server: `energatron mcp`.
  - `adapters/mcp/{protocol,tools,runner,server}.py`, the `mcp` command with `--allow-documents`, the `mcp-via-cli`
    import contract; tools per §6 running the CLI as child processes; fd 1 moved to stderr while serving.
  - Refs: the P9 spec §5, §6, §8 (`test_mcp_protocol`, `test_mcp_tools`, `test_mcp_server`), §10 (D1 §3, §8.4);
    D1 §7, §8.
  - Files: `src/energatron/adapters/mcp/`, `src/energatron/adapters/cli/{app.py,commands/mcp.py}`, `.importlinter`,
    `tests/unit/adapters/mcp/`, `tests/integration/test_mcp_server.py`, `CHANGELOG.md`.
  - Done when: the standard checks pass; a real `energatron mcp` child answers `initialize`, `tools/list` and a tool
    call over pipes.
  - Handover: `adapters/mcp/` (protocol, 15 tools, runner, server with fd 1 moved to stderr), `energatron mcp [--allow-documents]`,
    `mcp-via-cli` contract (3850 tests). Decision: `record_decision` dry-runs by default; outside a workspace exit 2. Watch: tools need the CLI's options as they are.
- ⬜ **P9.2.4** Workspace skills, `.mcp.json` and template v2.
  - The four skills (§7.1), their `.agents/skills` links and `.mcp.json` in the template (`TEMPLATE_VERSION` 2);
    `upgrade` adds missing links and seed files; the guard (and its template copy) allows `.mcp.json`; the template
    `CLAUDE.md` "Agents" paragraph; README "Agents" (checked by `test_readme`); `test_mcp_check_in`.
  - Refs: the P9 spec §7, §8 (`test_mcp_check_in`, template additions), §10 (D2 §7.4); the P8 spec §3, §6.
  - Files: `src/energatron/templates/workspace/`, `src/energatron/core/workspace_template.py`,
    `src/energatron/services/upgrade.py`, `scripts/privacy_guard.py`, `README.md`, `CHANGELOG.md`, `tests/` (template,
    upgrade, guard, readme, `tests/integration/test_mcp_check_in.py`).
  - Done when: the standard checks pass; `init --demo` writes the skills, links and `.mcp.json`; the scripted check-in
    passes its privacy checks.

#### P9.3 Acceptance ⬜
- ⬜ **P9.3.1** P9 acceptance (agent part).
  - The P9 spec §11 checks 1-4: the scripted check-in in a fresh demo workspace (`fetch_offers` as a dry run); the
    read-only tools in the owner's workspace through the server, denylist-checked; the owner's workspace gains
    `.mcp.json` and the four tool-aware skills (replacing its own), committed as one workspace commit; `upgrade
    --dry-run` from a v1 demo workspace lists the new files as `added`.
  - Refs: the P9 spec §11; plan §15 (P9).
  - Files: `TASKS.md`; (`demq-elektric/`) `.mcp.json`, `.claude/skills/*/SKILL.md`, `.agents/skills/*`.
  - Done when: checks 1-4 pass; `TASKS.md` gets counts and pass/fail only.
  - On /done: commit the workspace files as one workspace commit; report the pending backup.
- ⬜ **P9.3.2** Owner: a real agent check-in, push, backup.
  - Why: a real agent session, approving the MCP server, pushing and backups are the owner's.
  - Refs: the P9 spec §11 checks 5-7.
  - Files: none.
  - Done when: the owner reports a monthly check-in done through the `energatron` tools in Claude Code, the push with
    CI green, and the backup.

## Log (newest first)

- 2026-11-14 · P9.2.3 · `energatron mcp`: stdio MCP server, 15 tools each running the CLI command as a child, `--allow-documents`, `mcp-via-cli` import contract (3850 tests); self-review · energatron-5000 `P9.2.3: add energatron mcp, a stdio MCP server whose tools run the CLI`
- 2026-11-14 · P9.2.2 · `gas_bill_record` v2 with `extracted_by` (v1 upgraded on read, catalog `extra.extracted_by`), `gas-bill check`, `write`, `text`, `new --extracted-by` (3774 tests); self-review · energatron-5000 `P9.2.2: add gas bill records v2 with extracted_by and gas-bill check, write and text`
- 2026-11-14 · P9.2.1 · `energatron datasets`, `usage`, `compare`, `explain` (read-only; four result kinds; no file names, notes or raw rows) (3732 tests); self-review · energatron-5000 `P9.2.1: add energatron datasets, usage, compare and explain`
- 2026-11-14 · P9.1.1 · P9 agent interface spec drafted and approved on the owner's `/phase 9` (self-review); plan rev 18, D1 rev 10, D2 rev 3; P9.2-P9.3 added from its §2; the owner's `/phase` skill committed with it · energatron-5000 `P9.1.1: approve the P9 agent interface spec and apply plan rev 18, D1 rev 10 and D2 rev 3`
- 2026-11-14 · P8.3.2 · owner acceptance: workspace config-rewrite dry run clean, history audit clean, pushed, CI `check` and `demo` green, repo public, private vulnerability reporting on; clean-Mac check waived, no release yet; P8 ticked and archived · energatron-5000 `P8.3.2: record the owner's P8 acceptance and close P8`
- 2026-11-14 · P8.3.1 · P8 acceptance (agent part): quickstart through `uvx --from git+file://` to a recommendation in about a minute, demo checks clean, one tool fix (`/private` in the template's ignore), real workspace `diagnose` clean and untouched; its config-rewrite dry run left to the owner · energatron-5000 `P8.3.1: keep /private in the template's .gitignore`, `P8.3.1: record P8 acceptance (agent part)`
- 2026-11-14 · P8.2.5 · README quickstart and P8 sections (checked by `test_readme`, incl. `uvx` lines), CONTRIBUTING, SECURITY, bug-report template, CI `demo` job on macOS, `release.yml`, CHANGELOG (3689 tests); self-review; P8.2 ticked · energatron-5000 `P8.2.5: add the README quickstart, CONTRIBUTING, SECURITY, the CI demo job and the release workflow`
- 2026-11-14 · P8.2.4 · `energatron diagnose` (structure only: versions, config kinds, counts, stage names, codes; failed checks listed), `diagnose_result` v1, `test_region_profiles` (3688 tests); self-review · energatron-5000 `P8.2.4: add energatron diagnose and the region-profile tests`
- 2026-11-14 · P8.2.3 · `energatron upgrade` (managed files by recorded hash, `--force`), `energatron migrate` (TOML/YAML config through the migration chain, private config then denylist), `core/toml_text.py`, `W_TEMPLATE_MODIFIED`, two result kinds (3681 tests); self-review · energatron-5000 `P8.2.3: add energatron upgrade and energatron migrate`
- 2026-11-14 · P8.2.2 · demo generators and build, `FrozenProvider`, `demo_result` v1, `energatron demo` and `init --demo`, `test_demo` end to end with `verify` (3661 tests); self-review (only the demo's own files removed, commit count) · energatron-5000 `P8.2.2: add energatron demo and init --demo`
- 2026-11-14 · P8.2.1 · workspace template, `template_state`/`init_result` v1, `WorkspaceSetup` port and adapter, `energatron init` (real init in 21 s), guard allows `.energatron/template.json` (3655 tests); self-review · energatron-5000 `P8.2.1: add the workspace template and energatron init`
- 2026-11-14 · P8.1.1 · P8 open-source readiness spec drafted and approved on the owner's instruction (self-review); plan rev 17, D1 rev 9; P8.2-P8.3 added from its §2 · energatron-5000 `P8.1.1: approve the P8 open-source readiness spec and apply plan rev 17 and D1 rev 9`
- 2026-11-14 · P7.3.2 · owner acceptance: new SAPN download and invoices landed, tool pushed, full `run`, drill passed and recorded; no weekly schedule (agent removed); P7 ticked and archived · energatron-5000 `P7.3.2: record the owner's P7 acceptance and close P7`
- 2026-11-12 · P7.4.2 · the drill and `status` tell the owner to push the tool repo first (3616 tests); history rewritten on the owner's instruction to drop three gas-bill figures · energatron-5000 `P7.4.2: tell the owner to push the tool repo before a restore drill`
- 2026-11-11 · P7.4.1 · bill PDFs dated by issue date and recognised from their text (pypdf), one `land` for a folder of bills (3606 tests) · energatron-5000 `P7.4.1: date and recognise bill PDFs from their text`; demq-elektric `P7.4.1: lock pypdf for the bill PDF text`
- 2026-11-10 · P7.3.1 · P7 acceptance agent part: README cycle, live `watch`, schedule dry run, workspace instructions; two small tool fixes · energatron-5000 `P7.3.1: record P7 acceptance in the workspace (agent part)`; demq-elektric `P7.3.1: add the P7 commands to the workspace instructions and the check-in skill`, `land: 22 datasets`, two `run:` commits, `P7.3.1: record the P7 acceptance (agent part)`
- 2026-11-10 · P7.2.5 · README runbooks with `test_readme`, CHANGELOG (3565 tests); self-review; P7.2 complete · energatron-5000 `P7.2.5: rewrite the README as the runbook and check its commands`
- 2026-11-10 · P7.2.4 · `energatron schedule install|remove|status` over launchd, `schedule_result` v1 (3563 tests); self-review · energatron-5000 `P7.2.4: add energatron schedule for the weekly watch`
- 2026-11-10 · P7.2.3 · `energatron watch` and notifications, `test_p7_watch` (3555 tests); self-review · energatron-5000 `P7.2.3: add energatron watch and macOS notifications`
- 2026-11-10 · P7.2.2 · `energatron status` with six reminder warnings, `drill_state` record from `icloud_backup.py`, `core/operations.py` (3540 tests); self-review · energatron-5000 `P7.2.2: add energatron status, its reminders and the drill record`
- 2026-11-10 · P7.2.1 · `landing list|clear`, `landing_result` v1 (3503 tests); self-review · energatron-5000 `P7.2.1: add energatron landing list and clear`
- 2026-11-10 · P7.1.1 · P7 operations spec drafted and approved on the owner's instruction (self-review); plan rev 15, D1 rev 8, D2 rev 2; P7.2-P7.3 added from its §2 · energatron-5000 `P7.1.1: approve the P7 operations spec and apply plan rev 15, D1 rev 8 and D2 rev 2`
- 2026-11-10 · P6.3.1 · P6 acceptance in the workspace: params v6 synced, clean `run` (20 stages, REC cited by manifest and commit), rerun all cached with the same ID, `verify` ok, `decide --dry-run` ok, three workspace skills; P6 ticked and archived · demq-elektric `P6.3.1: sync the pipeline to params v6 and the P6 stages`, two `run:` commits, `P6.3.1: add the P6 workspace skills and record the P6 acceptance`; energatron-5000 `P6.3.1: record P6 acceptance in the workspace and close P6`
- 2026-11-10 · P6.2.5, P6.2.6 · `energatron run` (manifest, commit), `energatron decide`, the `outcomes` stage, `test_p6_pipeline` (3490 tests); self-review · energatron-5000 `P6.2.5-P6.2.6: add energatron run, energatron decide and the outcomes stage`
- 2026-11-10 · P6.2.4 · `recommend` stage, `recommendation` v1 with its content ID, the HTML page (3453 tests); self-review · energatron-5000 `P6.2.4: add the recommend stage, the recommendation document and its page`
- 2026-11-10 · P6.2.3 · `core/ranking.py` and the `bundles` stage (options per variation, bundle rules, history excluded) (3429 tests); self-review · energatron-5000 `P6.2.3: add core/ranking and the bundles stage`
- 2026-11-10 · P6.2.1, P6.2.2 · params v6, `StageRun.stage_sha256`, bundle rules, P6 folders in `pipeline sync`, three warnings, shared costing helpers; `sensitivity` stage (3395 tests); self-review · energatron-5000 `P6.2.1-P6.2.2: add params v6, bundle rules, the P6 folders and the sensitivity stage`
- 2026-11-10 · P6.1.1 · P6 bundles, recommendation and decisions spec drafted and approved on the owner's instruction (self-review); plan rev 14, D1 rev 7; P6.2-P6.3 added from its §2 · energatron-5000 `P6.1.1: approve the P6 recommendation spec and apply plan rev 14 and D1 rev 7`
- 2026-11-05 · P5.3.1 · P5 acceptance in the workspace: gas bill landed and transcribed, 73 gas plans, 47/25/2 normalised/approximate/excluded, backtest within, verify ok; P5 ticked and archived · energatron-5000 `P5.3.1: record P5 acceptance in the workspace and close P5`
- 2026-11-05 · P5.2.6 · fresh-review findings of P5.2.4-P5.2.6 fixed (2 should-fix, 2 of 4 nits) · energatron-5000 `P5.2.6: fix the review findings in the gas offers and costs`
- 2026-11-05 · P5.2.6 · backtest_gas, test_p5_pipeline, sixteen stages (3339 tests); fresh review of P5.2.4-P5.2.6 on the commits · energatron-5000 `P5.2.6: add backtest_gas and the end-to-end P5 test`
- 2026-11-05 · P5.2.5 · gas engine with an oracle and properties, cost_gas, engine settle step shared; bill-derived numbers in two earlier tests and a docstring replaced (3326 tests); self-review · energatron-5000 `P5.2.5: add the gas engine and the cost_gas stage`
- 2026-11-05 · P5.2.4 · gas tariff model, CDR gas mapping, offer_manual v2, normalise_gas_offers with a shared normaliser (3300 tests); self-review pass · energatron-5000 `P5.2.4: add gas tariffs, offer_manual v2 and normalise_gas_offers`
- 2026-11-05 · P5.2.3 · fresh-review findings fixed (4 should-fix, 3 nits) · energatron-5000 `P5.2.3: fix the review findings in the gas bill stages`
- 2026-11-05 · P5.2.3 · gas_bills and gas_profile stages, core/gas_bill.py, the fixture gas zone (3240 tests); fresh review on the commit · energatron-5000 `P5.2.3: add the gas_bills and gas_profile stages`
- 2026-11-05 · P5.2.2 · gas_bill_record model and parser, gas-bill new, gas_bill_scaffold_result, make_gas fixtures (3182 tests); self-review pass · energatron-5000 `P5.2.2: add gas bill records and energatron gas-bill new`
- 2026-11-05 · P5.2.1 · params v5, region v4, offer_fetch_log v2, per-fuel distributor filter, W_GAS_GAP and W_GAS_PARTIAL_YEAR (3143 tests); self-review pass · energatron-5000 `P5.2.1: add params v5, region profile v4 and the per-fuel fetch filter`
- 2026-11-05 · P5.1.1 · P5 gas spec drafted, fresh review (changes requested, 11 findings applied), approved on the owner's instruction; plan rev 13, D1 rev 6; P5.2-P5.3 written from its §2 · energatron-5000 `P5.1.1: approve the P5 gas spec and apply plan rev 13 and D1 rev 6`
- 2026-11-05 · P4.3.2 · the owner's tariffs transcribed from invoices into two `offer_manual` documents and landed, `backtest.tariffs` set, backtest 7 within / 1 outside (retailer double-billing, explained) / 1 split / 1 unmatched, backup done; P4 ticked and archived · demq-elektric `land: 2 datasets`, `P4.3.2: configure the backtest tariffs and record the backtest`; energatron-5000 `P4.3.2: record the P4 backtest and close P4`
- 2026-11-05 · P4.3.1 · P4 acceptance in the workspace: sync to params v4, `fetch-offers` (636 plans, 0 failures, one `land` commit), `dvc repro` of eleven stages, every plan accounted for (191/90/355), GST verified against a published GST-inclusive rate sheet, costs ranked, `offers diff` and `verify` ok; two tool fixes from real data (`zero_priced`, first-bill-discount rule); backtest and backup wait on P4.3.2 · demq-elektric `P4.3.1: sync the pipeline to params v4 and the P4 stages`, `land: 637 datasets`, `P4.3.1: record the P4 offers and costs run and acceptance`; energatron-5000 `P4.3.1: exclude zero-priced companion plans`, `P4.3.1: exclude the two first-bill-discount plans by a curated rule`, `P4.3.1: record P4 acceptance in the workspace`
- 2026-11-05 · P4.2.9 · `offers diff` service and command, `offer_diff_result` v1, end-to-end `test_p4_pipeline` (fetch against a local server, repro of eleven stages, diff, verify) (3113 tests); self-review pass; P4.2 ticked · energatron-5000 `P4.2.9: add energatron offers diff and the end-to-end P4 test`
- 2026-11-05 · P4.2.8 · transactions reader and `backtest` stage (periods from reads, tariff in force, invoice matching, statuses, three checks, two warnings), golden `dvc.yaml` with eleven stages (3106 tests); self-review pass · energatron-5000 `P4.2.8: add the backtest stage`
- 2026-11-05 · P4.2.7 · `cost_elec` stage (reference-window months, both scenarios, line items, ranked totals, summary, four checks), cost kinds, shared costing helpers, golden `dvc.yaml` with ten stages (3082 tests); self-review pass · energatron-5000 `P4.2.7: add the cost_elec stage`
- 2026-11-05 · P4.2.6 · cost engine (`core/engine.py`: clocks, seasons, TOU labels, daily and monthly blocks, demand, discounts, GST, prorated fees, `Decimal` cents) checked against an independent interval-by-interval oracle and five properties (3062 tests); self-review pass · energatron-5000 `P4.2.6: add the cost engine`
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
