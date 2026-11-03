# Tasks archive: P0 bootstrap & guardrails

- **Created:** 2026-11-03
- **Status:** Archive. `/done` moved these lines here verbatim from `TASKS.md` when P0.4.2 closed phase P0.
- **Privacy note:** this file lives in the public-ready tool repo. It contains no identifiers or usage figures,
  and nothing added to it may contain them either.

---

### P0: Bootstrap & guardrails ✅

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

#### P0.3 Workspace ✅
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
- ✅ **P0.3.2** Owner: add the optional private values.
  - Why: owner step (only the owner writes these values; agents never read the file, D2 §11.2).
  - In an editor, add `street`, `postcode` and `accounts` to `demq-elektric/private/config.toml`. Then a
    `/next P0.3.2` session runs `uv run energatron denylist` and `--check`.
  - Refs: D2 §6.3, §7.2, §7.3, §11.2.
  - Files (`demq-elektric/`): `private.dvc`.
  - Done when: `energatron denylist` gives no `W_PRIVATE_FIELDS_MISSING` other than `premises.street`, and `--check`
    passes. The owner chose (2026-11-03) to leave `street` out; that warning stays and is expected.
  - Handover: postcode and an electricity account added by the owner; denylist 7 entries, `private.dvc` re-tracked.
    Decision: `street` stays out (omission is the opt-out). Watch: `denylist` keeps warning `premises.street`.
  - No agent sub-task waits for it: without it the denylist lacks those values, and P1.5.2's usage exports warn
    `W_UNKNOWN_IDENTIFIER` (account).

#### P0.4 First tool commit, GitHub and the first backup ✅
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
- ✅ **P0.4.2** Owner: create the private GitHub repo and push; CI goes green.
  - Why: owner step (creating the repo and pushing wait for the owner's go-ahead; agents never push).
  - D2 §5.8's GitHub commands, `privacy_guard.py --mode tool --history` first. The owner runs them, or gives
    the go-ahead in a `/next P0.4.2` session.
  - Refs: D2 §5.6, §5.8, §13 check 1.
  - Files: none.
  - Done when: CI is green on GitHub, including `privacy_guard.py --tracked --no-denylist` (D2 §13 check 1,
    its CI half).
  - Partial (2026-11-03, parked for P0.5.2; code changes uncommitted in the tree):
    - **Summary:** The owner had already created the private GitHub repo (`origin`, private, `main` tracking
      `origin/main` at the P0.3.2 commit) and pushed. The first CI run failed at `pytest -q`: 2 failed, 2367 passed; the
      later steps (`privacy_guard --tracked --no-denylist`, `check_agent_links.py`) didn't run. Both failures were
      Linux/UTC-only:
      1. `test_history_with_clean_refs_and_tags_passes`: the guard searches the whole annotated tag object, tagger line
         included. Under UTC that line ends in `+0000`, which is the synthetic postcode as a word. The guard is right;
         the test now pins `GIT_COMMITTER_DATE` with a `+1030` offset (D2 §5.6: tests that commit set `GIT_COMMITTER_*`).
      2. `test_deeply_nested_json_is_bad_json`: Linux's stack parsed 100,000 levels, so a bad `lastUpdated` failed first.
         Probing showed a real bug: Python parses deeper than it encodes, so a valid plan nested ~50,000 deep parsed and
         then crashed `inspect` with an uncaught `RecursionError` in `offer_snapshot_id`. `cdr_plan.py` now maps that to
         `bad_json` too. Tests: a valid plan nested 1,000,000 deep is `bad_json` on any stack; two deterministic tests
         force `RecursionError` in the parse and in the hash; nesting 100 deep is accepted.
    - **Files:** energatron-5000: `src/energatron/adapters/sources/cdr_plan.py`, `tests/unit/adapters/sources/test_cdr_plan.py`,
      `tests/unit/scripts/test_privacy_guard.py`, `TASKS.md` (P0.4.2 claimed; P0.5.2 parked ⛔ with its partial record moved
      into its entry). demq-elektric: none.
    - **Checks:** `ruff check`, `ruff format --check` clean; `pyright` 0 errors; `lint-imports` 5 kept; `pytest -q` 2372 passed,
      and 2372 passed again under `TZ=UTC` (the CI condition; both failures reproduced red under UTC first); `pre-commit run
      --files` on the 4 changed files passed; CI's other steps run locally: `privacy_guard --tracked --no-denylist` 0
      violations in 260 paths, `check_agent_links.py` exit 0; D2 §5.8's `privacy_guard --mode tool --history` 0 violations
      in 26 commits and 2 refs; privacy guard `--figures` on the changed files and `TASKS.md` 0 violations (energy and
      money counts only in the synthetic guard tests). CI on GitHub is not green yet: it needs the push.
      Watch: this changes `src/energatron/`, so the workspace's `fingerprint --check` goes stale until re-run.
    - **Different from the spec:** nothing. `CdrPlanParser.version` stays 1: only inputs that used to crash change, and no
      landed result does.
    - **Open questions:** (1) Go-ahead to push the fix after `/done` (`/done push`), so CI re-runs? (2) After the push, P0.5.3's
      drill can run without `--tool-source local` (owner step).
    - **Review:** recommended: it touches a source parser (persisted landing results) and the privacy-guard tests.
    - **Review result:** not run.
  - Handover: CI fixes for 2 Linux/UTC-only failures (a real uncaught `RecursionError` in `cdr_plan.py`, now `bad_json`;
    a tag test pinned off `+0000`); pushed on owner go-ahead. Watch: the workspace `fingerprint` is stale until re-run.
- ✅ **P0.4.3** Owner: the first workspace backup.
  - Why: owner step (iCloud, D2 §4). Needs P0.1.1's terminal access.
  - Refs: D2 §8, §13 check 5; the runbook, "After every workspace commit".
  - Files: none.
  - Done when (D2 §13 check 5): `icloud_backup.py backup` exits 0 with a new bundle; run again, it exits 0 with
    "bundle unchanged" and `DVC files pushed: 0`; in the terminal app that hosts the agents, the owner runs
    `uv run energatron backup --json` and gets `E_STORAGE` with nothing pushed (agents never run it, D2 §4);
    an agent's `uv run energatron backup --status --json` then shows `refs_backed_up: true`. The agent records
    counts only.
  - Handover: done by the owner 2026-11-03: first backup pushed 57 DVC files and wrote 1 bundle, repeats pushed 0; the agent-hosting app gets `E_STORAGE`, nothing pushed; `refs_backed_up: true`. Decision: the backup folder follows `local.toml` (P0.4.4).
    Watch: a backup run in the wrong terminal is a harmless no-op, but the E_STORAGE check must come from the agent-hosting app.
- ✅ **P0.4.4** `icloud_backup.py` takes its default backups folder from the workspace's `.energatron/local.toml`.
  - Why: added 2026-11-03 at the owner's request. The owner's iCloud folder is not `iCloud Drive/Backups`, and
    the script's hard-coded default made `check` and `step0` look in the wrong place. The owner moved the
    destinations by editing `backup.bundle_dir` in `.energatron/local.toml` and the DVC remote URL (both local,
    git-ignored); the script now follows them, so nothing owner-specific is written into this repo.
  - Default order for the Backups folder: `--backups-dir` if given; else the parent of `backup.bundle_dir` in
    `<workspace>/.energatron/local.toml` (`~` expanded; must be absolute); else `iCloud Drive/Backups` (the old
    default, for `step0` before a workspace is set up). A `local.toml` that isn't valid TOML, or a `bundle_dir`
    that isn't an absolute path string, is exit 2 and the message never echoes a value.
  - Refs: D2 §4 (the script's options), §6.2 (`local.toml`), §8; the iCloud runbook.
  - Files: `scripts/icloud_backup.py`, `tests/unit/scripts/test_icloud_backup.py`, D2 §4 (one bullet, edited in
    place), the runbook (default folder).
  - Done when: the standard checks pass; the new tests cover each branch of the default order.
  - Handover: `default_backups_dir`/`configured_backups_dir` in `icloud_backup.py` (option > `bundle_dir`'s parent > iCloud `Backups`), 12 new tests, D2 §4 and the runbook updated (2369 tests). Decision: no owner path in this repo; the owner's destination lives only in the git-ignored `local.toml` and `.dvc/config.local`.
    Watch: the owner's Step 0 copy is not inside the configured folder, so `check` won't list it there until the owner moves it.

#### P0.5 Acceptance ✅
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
- ✅ **P0.5.2** Owner: fresh-session checks of instructions, skills and deny rules.
  - Why: owner step (fresh Claude Code and Codex sessions). Codex isn't installed, and there's no global install
    for now (owner, 2026-10-24): the owner installs it or waives its half when doing this step.
  - Refs: D2 §10.3, §13 checks 3 and 4.
  - Files: none, unless a deny pattern needs its syntax fixed (D2 §10.3: keep the intent, record it).
  - Done when: in fresh sessions started in the parent folder and in `energatron-5000/`, Claude Code and Codex
    each name the `new-doc` skill and quote the first rule of the loaded `CLAUDE.md`/`AGENTS.md`, and Codex
    lists `new-doc` (check 3); in Claude Code sessions started in each of the three folders, reading
    `demq-elektric/private/config.toml` with the file-read tool is denied (check 4). The agent records results.
  - Handover: Claude Code checks 3 and 4 passed in the parent folder, `energatron-5000/` and `demq-elektric/` (owner, 2026-11-03).
    Decision: Codex half waived by the owner (not installed). Watch: re-run check 3 in Codex if it is installed later.
- ✅ **P0.5.3** Owner: the restore drill.
  - Why: owner step (reads iCloud, D2 §12). Needs P0.4.3.
  - Refs: D2 §12, §13 check 6; the runbook, "At P0 acceptance".
  - Files: none.
  - Done when: `icloud_backup.py drill` exits 0, with step 9 SKIP until `verify` exists (P1.3.4); add
    `-- --tool-source local` while P0.4.2 is open. The agent records the summary's counts.
  - Handover: restore drill by the owner 2026-11-03: 12 steps, 0 failed, 0 skipped, exit 0 (`--tool-source local`; step 9 `verify` passed, privacy guard clean over 37 tracked files).
    Watch: once P0.4.2 gives the tool repo an `origin`, run the drill without `--tool-source local`.
