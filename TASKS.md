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
- Last done: P1.3.1
- Last commit: energatron-5000 `P1.3.1: add stage runner, registry, catalog_index and dvc.yaml emitter`; demq-elektric no change
- Checks: `ruff check` passed; `ruff format --check` 120 files formatted; `pyright` 0 errors; `lint-imports` 5
  kept; `pytest -q` 1154 passed; `pre-commit run --files` passed; privacy scan 0 violations
- Review: none
- Next: P1.3.2

## Hand-over (rewritten by every `/done`)

**Where we are**
- Spec stage done (archived in `agent_docs/2026-10/`); D1–D3 approved (D1 rev 2), plan rev 9, D4 = the P0–P2
  list below.
- P0 agent work is all ✅; both repos on `main`, no remote, nothing pushed.
- P1.1, P1.2 ✅; P1.3.1 ✅ (2026-10-30): `services/stages/{runner,registry,catalog_index,dvc_yaml}.py`, golden
  `tests/fixtures/golden/dvc.yaml`, `services.files.write_parquet`/`parquet_rows`, core `catalog_lines`.
- Owner steps open: P0.3.2, P0.4.2 (GitHub repo + push), P0.4.3 (first workspace backup), P0.5.2, P0.5.3.

**Read first** (for P1.3.2)
- D1 = `agent_docs/2026-10/2026-10-23_core-architecture-spec.md` (rev 2): §8.8, §9.4, §11.1–11.3, §13
  (`test_closure`, `test_fingerprint`, `test_stage_closure`, `test_stage_purity`).
- `src/energatron/services/stages/{runner,registry,dvc_yaml}.py`, `src/energatron/core/models/provenance.py`
  (`StageCode`), `src/energatron/adapters/wiring.py`.

**Facts**
- `run_stage(spec, inputs, params, outputs, *, code: StageCode, recorded_code)` raises `E_FINGERPRINT_STALE`
  when `recorded_code != code.stage_sha256`; P1.3.2 supplies the live `StageCode` and the recorded entry.
- `StageSpec` has `name`, `inputs`, `params`, `outputs`, `impl`, `package_data`; `registry.STAGES` is in
  `dvc.yaml` order. `dvc_yaml.CODE_FINGERPRINT_PATH` = `.energatron/code_fingerprint.json`, each stage's params
  list ends with `stages.<name>.stage_sha256` from it.
- `StageResult` is still a plain model in `runner.py`; P1.3.3 makes it the `stage_result` kind (D1 §5).
- Missing `params.yaml` → `E_PIPELINE_NOT_SYNCED`; a relative path override resolves under `root` (the P1.3.3
  CLI should absolutise it).
- Privacy scan: `uv run python scripts/privacy_guard.py --mode tool --figures <changed files> TASKS.md`.
  Standard checks: D2 §5.4, as the Rules list them. Every model change bumps `schema_version` and adds a
  migration; golden schemas in `tests/fixtures/schemas/`; fixtures come from `tests/fixtures/synthetic.py`.
- Agents never touch iCloud or run `energatron backup` (only `backup --status`). No pushing, GitHub repo, git
  remote or global installs.

**Open questions for the owner**
- none

**Watch out**
- Tests first: write them, see them fail, implement, see them pass (P1.3.1's core test skipped the red run).
  Layers point down; core does no I/O; stage modules can't import the registry.
- `build_context()` keeps the `_NotYetPipelineRunner` placeholder until P1.3.3.
- Record any file edited outside the sub-task's Files list as a deviation.
- `pre-commit run --files` skips ruff/import hooks for untracked files; run ruff and lint-imports directly too.
- pytest takes ~2 minutes. A safety hook holds any command containing `rm`. Never print an identifier or a kWh
  or $ figure from the owner's files; scans print counts only. Agents never read `demq-elektric/private/`.

## Current sub-task (filled by `/next` and `/examine`, reset by `/done`)

none

## Tasks

### S: Spec stage (hand-over §8) ✅

Archived verbatim in `agent_docs/2026-10/2026-10-24_tasks-archive-spec-stage.md`: S.1–S.5 (D1–D4), all ✅.

### P0: Bootstrap & guardrails ⬜

Built from D2 (`…_p0-bootstrap-spec.md`) in its §2 order; D1 binds all code. D4 (S.5.1) changed three things,
approved by the owner on 2026-10-24: the tool repo gets `git init` only in P0.4.1, just before its identity,
hooks and first commit; the parent links are installed at the end of P0.2.7, so the deny rules cover
`demq-elektric/private/` before P0.3.1 creates it; and the workspace's set-up and first commit (D2 §2 steps 3
and 6) are one sub-task, P0.3.1.
- ⛔ sub-tasks are owner steps. The loop skips them and carries on with agent work; the owner takes one with
  `/next <id>` when available. An agent sub-task names an owner step in its Done when only if it can't be done
  without it.
- Until P0.4.1 the tool repo isn't a git repo, so `/done` records the approval and the first commit picks the
  work up. Checks until then: D2 §5.4 without `pre-commit`, ruff with `--no-cache`, and the Rules' privacy scan,
  which from P0.2.5 also runs `privacy_guard.py --mode tool --no-denylist --figures <files> TASKS.md` (from
  P0.3.1 with `--denylist-dir ../demq-elektric/private` instead of `--no-denylist`).
- Acting-owner facts (2026-10-23): Codex isn't installed on this machine, Claude Code is; about 90 GB of disk is
  free (the "97% full" in older docs no longer holds); the interim safety copy is described under P0.1.1.

#### P0.1 Safety copy ✅
- ✅ **P0.1.1** Owner: check iCloud access and make the Step 0 safety copy.
  - Why: owner step (iCloud, D2 §4). Until it's done, agent work that touches workspace data relies on the
    interim local copy `~/WorkSpace/Electricity-local-backup/demq-elektric-step0-2026-10-23/`: 16 files, made
    with `icloud_backup.py --backups-dir ~/WorkSpace/Electricity-local-backup step0`, its `SHA256SUMS` verified
    with `shasum -a 256 -c`; accepted by the acting owner on 2026-10-23.
  - Refs: D2 §3, §4; the iCloud backup runbook, "Once: give your terminal access" and "Once, now".
  - Files: none (the copy goes to `iCloud Drive/Backups/`).
  - Done when: `check` reports access; `step0` prints `copy verified`; "Keep Downloaded" is on for `Backups/`;
    `check` then reports the step 0 folder as all downloaded. The owner reports these lines; only they go here.
  - Handover: done by the owner on 2026-10-29: iCloud access and the Step 0 copy worked, its SHA256SUMS
    verified (owner's report). The iCloud Step 0 copy is now the safety copy; the interim local one is a spare.

#### P0.2 Tool repo code (not a git repo yet) ✅
- ✅ **P0.2.1** Project set-up and core foundations.
  - `.python-version`, `pyproject.toml` (D2 §5.2), `uv.lock`, `.importlinter` (D1 §3), `.gitignore` (D2 §5.3);
    every D1 §2 package with its `__init__.py`; `core/errors.py` (all of D1 §7, D2's codes and warnings, and the
    warning model, not named `Warning`), `core/persisted.py`, `core/canonical.py`, `core/ids.py`.
  - D1 §5's "begin with two fields" means "contain" (an open D1 nit that P1.1.1 fixes).
  - Refs: D2 §5.1–5.4, §5.9, §14; D1 §2, §3, §5–§7, §9.4, §12, §13.
  - Files: `.python-version`, `pyproject.toml`, `uv.lock`, `.importlinter`, `.gitignore`,
    `src/energatron/**/__init__.py`, `src/energatron/core/{errors,persisted,canonical,ids}.py`,
    `tests/architecture/test_{core_purity,persisted,errors,process_state}.py`,
    `tests/unit/core/test_{canonical,ids}.py`.
  - Done when: `uv sync --locked` works and the checks in the P0 notes pass.
  - Handover: uv project, D1 §2 packages, core `canonical`/`errors`/`persisted`/`ids`, 235 tests; owner accepted
    deviations 1–4 (ruff format excludes `*.md`; D1/D3 code blocks stay as approved). Watch: no `.replace(` in core.
- ✅ **P0.2.2** Core privacy, the P0 models, the ports and `ServiceContext`.
  - `core/privacy.py` (`nmi_checksum`, `DenyEntry`, `derive_denylist`, `redact`, `redact_json`);
    `core/models/{workspace,private,vcs}.py`; every D1 §4 port in full, plus `VersionControl.backup_status()`;
    `services/context.py`; golden schemas for the P0 config kinds.
  - The ports name models that D1 §2 tags P1 or P4. Create what their signatures need, with the shapes D1 §4
    and D3 §3.1 and §4 give, and list them in the work report for P1.1.1.
  - Refs: D2 §1 (change 12), §5.9, §6, §7.3, §14; D1 §4, §5, §8.7, §13.
  - Files: `src/energatron/core/{privacy.py,models/}`, `src/energatron/ports/`,
    `src/energatron/services/context.py`, `tests/unit/core/test_{privacy,private_config,derive_denylist}.py`,
    `tests/fixtures/schemas/`.
  - Done when: the checks in the P0 notes pass; no test output holds a denylist value.
  - Handover: privacy core, P0 models (+ early P1/P4 ones), all ports, `ServiceContext`, 5 golden schemas, 419
    tests; owner decided JSON numbers are redacted too (D1 §8.7, via P1.1.1). Watch: guard whitespace rule.
- ✅ **P0.2.3** The git and DVC adapter, and the backup services.
  - `adapters/vcs/git_dvc.py` with all six methods and `commit()`'s hook mapping; `services/files.py`;
    `services/backup.py` (`backup`, `backup_status` and their result kinds).
  - Tests use scratch repos with a scratch local DVC remote and bundle folder; nothing touches iCloud.
  - Refs: D2 §1 (changes 6, 11–13), §6.4, §6.5, §8, §14; D1 §4.4, §7, §12.
  - Files: `src/energatron/adapters/vcs/`, `src/energatron/services/{files,backup}.py`,
    `tests/integration/test_{git_dvc,backup_status}.py`, `tests/unit/services/test_backup.py`,
    `tests/fixtures/schemas/`.
  - Done when: the checks in the P0 notes pass.
  - Handover: git+DVC adapter, backup services, `services/files.py`, 2 result kinds, 490 tests; owner accepted
    warnings outside `data` (the CLI lifts them) and `.import_linter_cache/` ignored. Watch: literal pathspecs.
- ✅ **P0.2.4** Workspace adapter, wiring and the CLI: `version`, `denylist`, `backup`.
  - `adapters/workspace.py`; `services/denylist.py`; `adapters/wiring.py` (`build_context()`, `SystemClock`, the
    `_NotYet` placeholders); `adapters/cli/` (`app.py`, `output.py`, the three commands); `__main__.py`.
  - With `energatron backup`, the `/done` skill's demq-elektric line becomes D2 §15's (`backup --status --json`
    and the pending-backup line), so no `/done` runs a backup.
  - Refs: D2 §4, §5.9, §6, §7.2, §8.5, §15; D1 §4.8, §7, §8.
  - Files: `src/energatron/{__main__.py,services/denylist.py,adapters/workspace.py,adapters/wiring.py}`,
    `src/energatron/adapters/cli/`, `tests/unit/adapters/test_{workspace,cli_envelope,locking}.py`,
    `tests/unit/services/test_denylist.py`, `tests/fixtures/schemas/`, `.claude/skills/done/SKILL.md`.
  - Done when: the checks in the P0 notes pass; `uv run energatron version --json` prints one envelope outside
    a workspace.
  - Handover: workspace adapter, wiring, CLI (`version`, `denylist`, `backup`), 3 kinds, 627 tests; 4 review fixes
    (`bundle_dir` checked only in real `backup`; `E_USAGE` never echoes arguments). Watch: envelope `command` (open Q).
- ✅ **P0.2.5** Privacy guard, agent-link check, pre-commit config and CI.
  - `scripts/privacy_guard.py` (stdlib only, never imports `energatron`; its path scans must also work before
    the tool repo is a git repo, given `--denylist-dir` or `--no-denylist`); `scripts/check_agent_links.py`;
    `.pre-commit-config.yaml` (D2 §5.5; installed in P0.4.1); `.github/workflows/ci.yml` (D2 §5.6, actions
    pinned by commit SHA, looked up read-only with `gh api`).
  - Matching: normalise an entry's whitespace with `value.split()`, as `core.privacy` does (P0.2.2 review).
  - `denylist.txt` parses exactly as `services/denylist.py`'s `parse_manual_denylist` (P0.2.4 review; D2 §7.2
    is silent on these):
    - decode as strict UTF-8; split lines on `\n` only, dropping one trailing `\r`, never `str.splitlines()`
      (form feed, U+2028, U+0085 and a lone `\r` stay inside the line); line numbers count from 1
    - `str.strip()` each line; skip it when empty or when its first non-blank character is `#` (no inline
      comments: an entry may hold `#`)
    - a `word:` prefix makes a whole-word entry, the rest `str.strip()`ped again; otherwise a substring entry
    - label `manual:<line number>`; an empty entry, or a substring entry shorter than 5 characters after
      `" ".join(value.split())`, is a malformed line (exit 2) named by its line number, never its value
  - Refs: D2 §5.5, §5.6, §7, §9; plan §5.
  - Files: `scripts/{privacy_guard,check_agent_links}.py`, `.pre-commit-config.yaml`,
    `.github/workflows/ci.yml`, `tests/unit/scripts/test_{privacy_guard,check_agent_links}.py`.
  - Done when: the checks in the P0 notes pass, including D2 §7.6's 20 cases.
  - Handover: stdlib guard, link check, pre-commit config, SHA-pinned CI, 826 tests; 5 review fixes; owner added
    `.gz .bz2 .xz .zst .7z .tar .tgz .docx .xlsm .ods .odt` to D2 §7.4's data types. Watch: tagged blobs unread.
- ✅ **P0.2.6** Restore-drill script and the backup-script tests.
  - `scripts/restore_drill.py` (stdlib only; step 9 SKIPs until `energatron verify` exists, P1.3.4); tests for
    the existing `scripts/icloud_backup.py`.
  - Refs: D2 §4, §12, §14 (`test_icloud_backup.py`).
  - Files: `scripts/restore_drill.py`, `tests/integration/test_restore_drill.py`,
    `tests/unit/scripts/test_icloud_backup.py`.
  - Done when: the checks in the P0 notes pass; the tests use scratch folders only, never iCloud or the real
    workspace.
  - Handover: stdlib restore drill (12 steps, 0/1/2) and 32 backup/drill tests (858); `verify true` only in the
    clone; 3 review fixes (live-state snapshot test, `--no-sync`, a line per step). Watch: step 9 SKIPs until P1.3.4.
- ✅ **P0.2.7** Agent files, parent links and the repo's docs.
  - `CLAUDE.md` and its `AGENTS.md` link, `parent/CLAUDE.md`, `.claude/settings.json`, the `new-doc` skill and
    its link (D2 §10.1, §10.3, §10.4); `scripts/link_parent.py` gains the parent links and `--parent-dir`
    (§10.2); `LICENSE`, `NOTICE`, `README.md`, `CHANGELOG.md` (§5.7).
  - Last, from the parent folder: `python3 energatron-5000/scripts/link_parent.py`.
  - Refs: D2 §5.7, §9, §10; plan §14.5.
  - Files: `CLAUDE.md`, `AGENTS.md`, `parent/CLAUDE.md`, `.claude/settings.json`, `.claude/skills/new-doc/`,
    `.agents/skills/new-doc`, `scripts/link_parent.py`, `tests/unit/scripts/test_link_parent.py`, `LICENSE`,
    `NOTICE`, `README.md`, `CHANGELOG.md`; in the parent folder, the generated links.
  - Done when: the checks in the P0 notes pass; `link_parent.py --check` and
    `check_agent_links.py --repo energatron-5000 --parent` exit 0.
  - Handover: `CLAUDE.md`/`AGENTS.md`, `parent/CLAUDE.md`, deny-only settings, `new-doc` skill, parent links via
    `link_parent.py --parent-dir`, README/CHANGELOG (877 tests); 2 review fixes. Watch: foreign symlinks are reported.

#### P0.3 Workspace ⬜
- ✅ **P0.3.1** Set up the workspace and prepare its first commit.
  - D2 §11.4 in order, up to the commit: `git init -b main`; the §11.1 files; `uv sync`; `dvc init`; the
    `--local` iCloud remote and `.energatron/local.toml` (configuration only: nothing here reads or writes
    iCloud); `private/config.toml`, written by a stdlib snippet that prints no value, never read back (§11.2);
    `energatron denylist` (expect `W_PRIVATE_FIELDS_MISSING`) and `--check`; `dvc add legacy` (the files stay in
    place); `pre-commit install`; the `--figures` audit.
  - Needs a safety copy: P0.1.1's, or until then the interim local copy.
  - Refs: D2 §4, §6.1–6.3, §10.1 (workspace `CLAUDE.md`), §10.3, §11.
  - Files (`demq-elektric/`): `CLAUDE.md`, `AGENTS.md`, `README.md`, `.claude/settings.json`, `pyproject.toml`,
    `uv.lock`, `.python-version`, `.gitignore`, `.dvcignore`, `.pre-commit-config.yaml`, `.dvc/config`,
    `.dvc/.gitignore`, `.energatron/workspace.toml`, `config/private.example.toml`, `private.dvc`, `legacy.dvc`.
  - Done when: `energatron denylist --check` passes; the audit finds no violations (figure counts in the
    report); `uv run energatron backup --status --json` gives `refs_backed_up: false`.
  - On /done: commit exactly those files with the subject `P0: bootstrap demq-elektric workspace` (D2 §11.4).
  - Handover: done 2026-10-30 by the acting owner's session: workspace committed (16 files, hooks passed); the
    owner ran the §11.2 snippet (counts 2/1/1) after the classifier blocked the agent. Watch: backup pending (P0.4.3).
- ⛔ **P0.3.2** Owner: add the optional private values.
  - Why: owner step (only the owner writes these values; agents never read the file, D2 §11.2).
  - In an editor, add `street`, `postcode` and `accounts` to `demq-elektric/private/config.toml`. Then a
    `/next P0.3.2` session runs `uv run energatron denylist` and `--check`.
  - Refs: D2 §6.3, §7.2, §7.3, §11.2.
  - Files (`demq-elektric/`): `private.dvc`.
  - Done when: `energatron denylist` gives no `W_PRIVATE_FIELDS_MISSING`, and `--check` passes.
  - No agent sub-task waits for it: without it the denylist lacks those values, and P1.5.2's usage exports warn
    `W_UNKNOWN_IDENTIFIER` (account).

#### P0.4 First tool commit, GitHub and the first backup ⬜
- ✅ **P0.4.1** Prepare the tool repo's first commit.
  - `git init -b main`; the repo's own identity from `gh api user` (the noreply address; `gh` is logged in, and
    reading isn't pushing; never written into a file); `git config energatron.denylist ../demq-elektric/private`;
    `uv run pre-commit install`; `link_parent.py --check` (re-run `link_parent.py` if needed); the audit
    `privacy_guard.py --mode tool --figures $(git ls-files -co --exclude-standard)`, its figure counts in the
    report for the owner. The **Rules**' privacy scan and standard checks become D2 §5.4's.
  - Refs: D2 §2 step 4, §5.4, §5.8 ("Before the first commit"), §10.2.
  - Files: everything in D2 §5.1, plus `TASKS.md`, `agent_docs/`, `.claude/skills/`, `.agents/skills/` and
    `scripts/`.
  - Done when: the audit has no hits; `check_agent_links.py --parent` exits 0; the D2 §5.4 checks pass, with
    `pre-commit run --files` over the audited list.
  - On /done: stage exactly that list (never `-A`) and commit with the subject `P0: bootstrap energatron-5000`
    (D2 §5.8), letting the hooks run.
  - Handover: identity, `energatron.denylist` and hooks set locally; first commit of the 138 audited paths, hooks
    passed. Owner read the figure counts (plan-doc tolerances, synthetic guard tests). Watch: no remote yet (P0.4.2).
- ⛔ **P0.4.2** Owner: create the private GitHub repo and push; CI goes green.
  - Why: owner step (creating the repo and pushing wait for the owner's go-ahead; agents never push).
  - D2 §5.8's GitHub commands, `privacy_guard.py --mode tool --history` first. The owner runs them, or gives
    the go-ahead in a `/next P0.4.2` session.
  - Refs: D2 §5.6, §5.8, §13 check 1.
  - Files: none.
  - Done when: CI is green on GitHub, including `privacy_guard.py --tracked --no-denylist` (D2 §13 check 1,
    its CI half).
- ⛔ **P0.4.3** Owner: the first workspace backup.
  - Why: owner step (iCloud, D2 §4). Needs P0.1.1's terminal access.
  - Refs: D2 §8, §13 check 5; the runbook, "After every workspace commit".
  - Files: none.
  - Done when (D2 §13 check 5): `icloud_backup.py backup` exits 0 with a new bundle; run again, it exits 0 with
    "bundle unchanged" and `DVC files pushed: 0`; in the terminal app that hosts the agents, the owner runs
    `uv run energatron backup --json` and gets `E_STORAGE` with nothing pushed (agents never run it, D2 §4);
    an agent's `uv run energatron backup --status --json` then shows `refs_backed_up: true`. The agent records
    counts only.

#### P0.5 Acceptance ⬜
- ✅ **P0.5.1** P0 acceptance: guard test commits, links and history.
  - In each repo, stage a scratch file holding the NMI (copied by a snippet that never prints it) and try to
    commit: `privacy-guard` rejects it and prints the label, not the value. Again with the NMI only in the
    commit message: `privacy-guard-msg` rejects it. Then `git restore --staged` the file and delete it from
    Python. If a commit goes through, stop: don't push or rewrite history; ask the owner.
  - Refs: D2 §2 steps 7–8, §13 checks 1 (local half), 2 and 7.
  - Files: none (pass/fail and counts go in the report).
  - Done when: D2 §13 check 2 passes in both repos; `privacy_guard.py --mode tool --history` finds nothing;
    `link_parent.py --check` and `check_agent_links.py --parent` exit 0 in both repos.
  - Handover: 4/4 test commits rejected (NMI in file → `privacy-guard`, in message → `privacy-guard-msg`, labels
    only); history 0 violations; links exit 0. Owner verified one commit per repo, no scratch left. Watch: none.
- ⛔ **P0.5.2** Owner: fresh-session checks of instructions, skills and deny rules.
  - Why: owner step (fresh Claude Code and Codex sessions). Codex isn't installed, and there's no global install
    for now (owner, 2026-10-24): the owner installs it or waives its half when doing this step.
  - Refs: D2 §10.3, §13 checks 3 and 4.
  - Files: none, unless a deny pattern needs its syntax fixed (D2 §10.3: keep the intent, record it).
  - Done when: in fresh sessions started in the parent folder and in `energatron-5000/`, Claude Code and Codex
    each name the `new-doc` skill and quote the first rule of the loaded `CLAUDE.md`/`AGENTS.md`, and Codex
    lists `new-doc` (check 3); in Claude Code sessions started in each of the three folders, reading
    `demq-elektric/private/config.toml` with the file-read tool is denied (check 4). The agent records results.
- ⛔ **P0.5.3** Owner: the restore drill.
  - Why: owner step (reads iCloud, D2 §12). Needs P0.4.3.
  - Refs: D2 §12, §13 check 6; the runbook, "At P0 acceptance".
  - Files: none.
  - Done when: `icloud_backup.py drill` exits 0, with step 9 SKIP until `verify` exists (P1.3.4); add
    `-- --tool-source local` while P0.4.2 is open. The agent records the summary's counts.

### P1: Landing & catalog ⬜

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

#### P1.3 Pipeline machinery 🔄
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
- ⬜ **P1.3.2** Closure, code fingerprint and `energatron fingerprint`.
  - `adapters/pipeline/closure.py` and `fingerprint.py`; `wiring.code_fingerprint()`; `fingerprint [--check]`.
  - Refs: D1 §8.8, §9.4, §11.1–11.3, §13 (`test_closure`, `test_fingerprint`, `test_stage_closure`,
    `test_stage_purity`); P1.1.1's nits on symlinked directories and `numpy.random`.
  - Files: `src/energatron/adapters/pipeline/{closure,fingerprint}.py`, `src/energatron/adapters/wiring.py`,
    `src/energatron/adapters/cli/`, `tests/unit/adapters/test_{closure,fingerprint}.py`,
    `tests/architecture/test_{stage_closure,stage_purity}.py`.
  - Done when: the standard checks pass.
- ⬜ **P1.3.3** `energatron stage`, `DvcRunner` and the DVC checks.
  - The `stage` command, with the repro-log records `main()` writes; `adapters/pipeline/dvc_runner.py`; the
    last placeholder leaves `build_context()`. If a D1 §11.5 or D3 §14 check fails, stop and ask the owner.
  - Refs: D1 §4.5, §8.5, §9.5, §11.5, §13 (`test_dvc_runner`, `test_stage_loads`, `test_determinism`); D3 §13
    (`test_dvc_assumptions`, `test_wiring`), §14; P1.1.1's nits on `PATH` and a killed stage.
  - Files: `src/energatron/adapters/pipeline/dvc_runner.py`, `src/energatron/adapters/wiring.py`,
    `src/energatron/adapters/cli/`, `tests/unit/adapters/test_{dvc_runner,wiring,cli_envelope,locking}.py`,
    `tests/integration/test_{dvc_assumptions,stage_loads,determinism}.py`.
  - Done when: the standard checks pass; the D1 §11.5 and D3 §14 results are in the report.
- ⬜ **P1.3.4** `energatron pipeline sync` and `energatron verify`.
  - `services/pipeline_sync.py` and `pipeline sync [--check] [--dry-run]`; `verify` as P1.1.1 revised
    D1 §11.4, with the blob check.
  - Refs: D3 §10.1, §10.2, §11, §13 (`test_pipeline_sync`); D1 §8.8, §11.4, §13 (`test_verify`).
  - Files: `src/energatron/services/` (`pipeline_sync.py`, `verify`'s module), `src/energatron/adapters/cli/`,
    `tests/unit/services/test_pipeline_sync.py`, `tests/integration/test_{pipeline_sync,verify}.py`,
    `tests/unit/adapters/test_locking.py`, `tests/fixtures/schemas/`.
  - Done when: the standard checks pass.

#### P1.4 `sapn_interval` end to end ⬜
- ⬜ **P1.4.1** NEM12 fixture generator and the `sapn_interval` parser.
  - `tests/fixtures/make_nem12.py`; `adapters/sources/sapn_nem12.py` and `registry.py`; `regions/sa.yaml` and
    its loading in `build_context()`, listing only the parsers that exist until P1.5.1 completes it.
  - Refs: D3 §3.3, §5 (intro), §5.2, §10.3, §12.1, §13; D1 §4.3; plan §13.
  - Files: `tests/fixtures/make_nem12.py`, `src/energatron/adapters/sources/`, `src/energatron/regions/sa.yaml`,
    `src/energatron/adapters/wiring.py`, `tests/unit/adapters/sources/test_{sapn_nem12,registry}.py`,
    `tests/unit/fixtures/test_generators.py`, `tests/unit/adapters/test_wiring.py`.
  - Done when: the standard checks pass.
- ⬜ **P1.4.2** The `land` service and `energatron land`.
  - `services/land.py`: `resolve_parser`, preconditions, plan, write, commit message, recovery, `land_result`,
    `land_pending`; the `land` command. The three-way PDF tie is tested with stand-in parsers here and with the
    real ones in P1.5.1.
  - Refs: D3 §5.1, §7, §8, §11, §13; D1 §4.8, §7, §8.
  - Files: `src/energatron/services/land.py`, `src/energatron/adapters/cli/`,
    `tests/unit/services/test_{resolve_parser,land}.py`, `tests/integration/test_land_{cli,recovery}.py`,
    `tests/unit/adapters/test_locking.py`, `tests/fixtures/schemas/`.
  - Done when: the standard checks pass.
- ⬜ **P1.4.3** Set up the P1 pipeline in the workspace.
  - D3 §15 step 1: `pipeline sync`, `fingerprint`, `dvc repro` (`catalog_index`: 0 rows, every check passes);
    the `pipeline-sync` hook in the workspace's `.pre-commit-config.yaml`, before `agent-links`; `land` and
    `pipeline sync` in the workspace `CLAUDE.md`'s Commands.
  - Refs: D3 §10.1, §15 step 1, §17; D2 §11.3.
  - Files (`demq-elektric/`): `dvc.yaml`, `dvc.lock`, `params.yaml`, `.energatron/code_fingerprint.json`,
    `data/raw/*.dvc`, `data/raw/.gitignore`, `data/marts/.gitignore`, `.pre-commit-config.yaml`, `CLAUDE.md`.
  - Done when: `pipeline sync --check` and `fingerprint --check` pass; `git status` lists exactly those files.
  - On /done: commit them as one workspace commit; `land` needs them committed (D3 §7.1 step 4).
- ⬜ **P1.4.4** Land the two current SAPN files and run the pipeline on them.
  - D3 §15 step 2, then `fingerprint` and `dvc repro`: an extra run, not in D3 §15, to see real data through
    `catalog_index` before building wide.
  - Refs: D3 §1 (the facts), §15 steps 2 and 6.
  - Files (`demq-elektric/`): `dvc.lock`. The landing commit is `land`'s own.
  - Done when: `uv run energatron land data/landing/ --json` gives 2 `landed`, exit 0 and one commit;
    `catalog_index` has 2 rows and passes every check; `dvc status` is clean.
  - On /done: commit `dvc.lock`.

#### P1.5 The other sources and P1 acceptance ⬜
- ⬜ **P1.5.1** The other five parsers.
  - `tests/fixtures/make_sources.py`; `adapters/sources/pdf_meta.py`, `bill_pdf.py`, `sapn_summary.py`,
    `retailer_usage.py`, `retailer_txn.py`, `cdr_plan.py`; `PARSERS` and `regions/sa.yaml` complete (D3 §10.3).
  - Refs: D3 §3.3, §5.1, §5.3–5.8, §12.2, §13.
  - Files: `tests/fixtures/make_sources.py`, `src/energatron/adapters/sources/`, `src/energatron/regions/sa.yaml`,
    `tests/unit/adapters/sources/`, `tests/unit/services/test_resolve_parser.py`,
    `tests/unit/fixtures/test_generators.py`.
  - Done when: the standard checks pass.
- ⬜ **P1.5.2** Land the legacy files and finish P1's acceptance.
  - D3 §15 steps 3–7, then step 8's agent part: `backup --status` shows a backup pending, and the report asks
    the owner for P1.5.3.
  - The usage exports' meter serial matches the SAPN one (a counts-only check, 2026-10-24), so the premises
    check confirms them. Until P0.3.2 is done they also warn `W_UNKNOWN_IDENTIFIER` (account): expected.
  - Refs: D3 §1 (the facts), §15.
  - Files (`demq-elektric/`): `dvc.lock`; a dated note in `agent_docs/` for any result that mentions data.
  - Done when: D3 §15 steps 3–7 pass (the hourly JSON rejected, exit 8; 7 datasets: 2/2/2/1; re-landing a
    no-op; `catalog_index` 7 rows; a clean state). `TASKS.md` gets counts and pass/fail only.
  - On /done: commit `dvc.lock` (D3 §15 step 6).
- ⛔ **P1.5.3** Owner: back up after P1.
  - Why: owner step (iCloud).
  - Refs: D3 §15 step 8; the runbook.
  - Files: none.
  - Done when: `icloud_backup.py backup` exits 0 with a new bundle, and `backup --status` then shows
    `refs_backed_up: true`. `backup --drill` instead also runs the drill, whose step 9 is now `verify`; D2 §3
    wants a passed drill after P1 before the Step 0 copy may be deleted.

### P2: Meter pipeline ⬜

P2.1.1 writes the P2 spec. The sub-tasks after it are provisional: the spec's order of work may rewrite them.

#### P2.1 P2 spec ⬜
- ⬜ **P2.1.1** Draft the P2 meter pipeline spec, `agent_docs/<YYYY-MM>/<YYYY-MM-DD>_p2-meter-pipeline-spec.md`.
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

#### P2.2 Stages (provisional) ⬜
- ⬜ **P2.2.1** (provisional) NEM12 parsing and the `parse_nem12` stage.
  - Refs: the P2 spec; plan §9.2, §10.1, §13. Files: as the spec says. Done when: its tests and the standard
    checks pass.
- ⬜ **P2.2.2** (provisional) `merge_interval` and its conflicts report.
  - Refs: the P2 spec; plan §9.2, §10.1. Files: as the spec says. Done when: its tests and the standard checks
    pass.
- ⬜ **P2.2.3** (provisional) `validate_meter`: monthly totals against SAPN SUMMARY, the gap list, quality shares.
  - Refs: the P2 spec; plan §9.2, §10.1. Files: as the spec says. Done when: its tests and the standard checks
    pass.
- ⬜ **P2.2.4** (provisional) `validate_timebase`: hypotheses H1–H3 against the retailer's TOU export.
  - Refs: the P2 spec; plan §9.2, §10.2. Files: as the spec says. Done when: its tests and the standard checks
    pass.
- ⬜ **P2.2.5** (provisional) `localise`: meter time to local time, with the DST days.
  - Refs: the P2 spec; plan §9.2, §10.2, §13. Files: as the spec says. Done when: its tests and the standard
    checks pass.

#### P2.3 Acceptance (provisional) ⬜
- ⬜ **P2.3.1** (provisional) P2 acceptance in the workspace.
  - `pipeline sync`, `fingerprint`, `dvc repro`; plan §15's P2 checks as the spec makes them concrete (interval
    counts, monthly totals against SAPN SUMMARY, merge conflicts reported, the time basis chosen by evidence,
    DST tests); then `energatron verify` passes.
  - Refs: the P2 spec; plan §15 (P2); D1 §11.4.
  - Files (`demq-elektric/`): the pipeline files the spec lists; a dated note in `agent_docs/` for results that
    mention data.
  - Done when: every check passes; `TASKS.md` gets counts and pass/fail only.
- ⛔ **P2.3.2** Owner: back up after P2.
  - Why: owner step (iCloud).
  - Refs: the runbook. Files: none.
  - Done when: `icloud_backup.py backup` exits 0, and `backup --status` then shows `refs_backed_up: true`.

### Later (plan §15)
P3 usage profile · P4 offers + electricity engine · P5 gas · P6 bundles, recommendation, decisions · P7
operations. Each starts with its own spec sub-task.

## Log (newest first)

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
