# P0 bootstrap spec (D2)

- **Created:** 2026-10-23
- **Status:** Approved 2026-10-23 (owner, `/done`); its §15 edits are in the plan as rev 8
- **Deliverable:** D2 of [`2026-10-23_handover-next-planning-stage.md`](2026-10-23_handover-next-planning-stage.md) §8
- **Builds on:** [`2026-10-23_data-pipeline-plan.md`](2026-10-23_data-pipeline-plan.md) rev 7 ("the plan") and
  [`2026-10-23_core-architecture-spec.md`](2026-10-23_core-architecture-spec.md) ("D1", binding for all code)
- **For:** the agent or person implementing P0, and D4, which turns §2 into sub-tasks
- **Owner runbook:** [`2026-10-23_icloud-backup-runbook.md`](2026-10-23_icloud-backup-runbook.md), for every
  step that touches iCloud
- **Privacy note:** this file lives in the public-ready tool repo. It contains no NMI, meter serial, account
  numbers, MIRN, address, postcode or usage figures, and nothing added to it may contain them either.

---

## 1. What this spec fixes

P0 turns two plain folders into two git repos with guardrails, a backup that is proven by a restore, and agent
instructions that Claude Code and Codex both load. This spec gives every file, command and rule P0 needs, so
P0 can be built without design decisions.

"Must" means required. "Owner" marks a step only the owner can do; everything else is agent work.

**Verified on 2026-10-23** (scratch projects; nothing installed globally):
- uv 0.12.19 with `python-preference = "only-managed"` downloads and uses a uv-managed CPython 3.14.7, even
  though Homebrew also has a 3.14.
- `pyright[nodejs]` 1.1.414 runs with no Node on `PATH`. The extra installs `nodejs-wheel-binaries` (Node 24)
  into the venv, so **pyright needs no system Node**. (Homebrew Node exists on this machine; nothing depends on
  it.)
- The `uv_build` backend with `module-name = "energatron"` builds an `energatron-5000` wheel that includes YAML
  package data under `src/energatron/`.
- `dvc install --use-pre-commit-tool` writes a *remote* hook repo (`github.com/treeverse/dvc`, with
  `additional_dependencies: [".[all]"]`). pre-commit would then pip-install a second DVC into its own
  environment, which breaks the uv-only rule. `dvc git-hook {pre-commit,post-checkout,pre-push}` exists and can
  be called from a local hook instead (§5.5).
- `dvc status --cloud --json` lists one entry per object that is missing on the remote, the `.dir` object
  included. Its count matched `dvc push`'s "N files pushed", and it prints `{}` once the remote is complete.
- When a hook fails, pre-commit prints `- hook id: <id>` and `- exit code: <n>` under it. A `commit-msg` hook
  receives `.git/COMMIT_EDITMSG` as its argument.
- `git bundle create <f> --all`: `git bundle verify` passes, `git bundle list-heads` lists every branch, every tag
  and `HEAD`, and `git clone <f>` restores the history, with `origin` pointing at the bundle file.
- iCloud Drive returns "Operation not permitted" to agent sessions **even outside the agent's sandbox**. The
  cause is macOS privacy control (TCC) on the app that hosts the agent, not the agent (§4).
- The AEMO NMI checksum algorithm in §7.3 gives the right checksum digit for the real NMI in the workspace
  files. Only the result was printed.
- `stat.SF_DATALESS` (`0x40000000`) exists in Python 3.14, so dataless iCloud placeholders can be detected
  without shelling out.

**Changes from the plan and from D1** (also listed in §15):
1. No `dvc install --use-pre-commit-tool`. DVC's hooks are local pre-commit hooks run through `uv run`, and only
   `post-checkout` is installed (§5.5, §11.3).
2. The denylist is two files: `private/denylist.generated.txt`, written by `energatron denylist`, and an
   optional hand-written `private/denylist.txt` (§7.2). The plan had one auto-derived file.
3. All of `adapters/vcs/git_dvc.py` moves from P1 to P0, because P0 ships `energatron backup` (D1 §2 tags it P1).
4. P0 creates no `_catalog.jsonl` files. `SourceType` arrives in P1, and the P1 command that regenerates
   `dvc.yaml` creates them anyway (D1 §9.2 says workspace setup does it in P0).
5. The `params` kind is defined by the P1 spec (D3). Nothing in P0 reads `params.yaml`.
6. `BackupReport` gains `bundle_created: bool` and `dataless_files: int`, which D1 §4.4 allows.
7. New codes: `E_BACKUP_NOT_CONFIGURED`, `E_DENYLIST_STALE` (both `state`, exit 4); warnings
   `W_PRIVATE_FIELDS_MISSING`, `W_BACKUP_DATALESS`.
8. In P0, `build_context()` fills the ports that arrive in P1 with placeholders that raise `E_INTERNAL` (§5.9).
9. The tool repo's dev group includes DVC, because the VCS adapter and restore-drill tests need a real DVC
   (D1 §12 says the package doesn't depend on DVC; that still holds).
10. The parent folder gains `CLAUDE.md`, `AGENTS.md` and `.claude/settings.json` as symlinks, created by
    `scripts/link_parent.py` (§10.2).
11. Agents never touch iCloud; the owner runs every iCloud step with `scripts/icloud_backup.py` (§4).
    - `land` (P1) and `run` (P6) don't call `backup()`. D1 (`land`) and the plan (`run`) had them back up at the
      end.
    - `/done` doesn't run the backup. It reports whether one is pending, from a local marker
      (`backup_state`, §6.5) that `energatron backup --status` reads.
12. `VersionControl` gains a sixth method, `backup_status() -> BackupStatus`, so the status check stays out of
    the services layer, which may not run git (D1 §3). D1 §4.4 lists five methods.
13. `commit()` maps a privacy-guard exit 2 (a setup error, such as a stale denylist) to `E_COMMIT_REJECTED`, not
    `E_DENYLIST_HIT`. D1 §4.4 maps every failed guard hook to `E_DENYLIST_HIT`, but a setup error isn't evidence
    of personal data (§8.6).
14. The restore drill runs at P0 acceptance, then quarterly, as plan §6 says, and is always owner-run (§4, §12).

---

## 2. Order of work

Each step needs the ones before it. D4 makes them sub-tasks.

| # | Step | Who | Section |
|---|---|---|---|
| 0 | Safety copy of `demq-elektric/data/landing/` and `legacy/` to iCloud, verified; "Keep Downloaded" on `Backups/` | Owner (`icloud_backup.py step0`) | §3 |
| 1 | Check that the owner's terminal can reach iCloud (`icloud_backup.py check`) | Owner | §4 |
| 2 | Tool repo: `git init`, skeleton, `pyproject.toml`, configs, the P0 package code, both guard scripts, tests. No commit yet | Agent | §5–§7, §9 |
| 3 | Workspace: `git init`, uv project, `.energatron/`, DVC, `private/config.toml`, denylist. No commit yet | Agent, then owner fills private values | §11 |
| 4 | Parent folder: `link_parent.py` installs `CLAUDE.md`, `AGENTS.md`, `.claude/settings.json` (the tool repo's `agent-links` hook checks them). Then the tool repo: point the guard at the workspace denylist, install hooks, audit, first commit | Agent | §10.2, §5.8 |
| 5 | GitHub: create the private repo, push | Agent, after the owner's go-ahead | §5.8 |
| 6 | Workspace: `dvc add legacy`, hooks, agent files, first commit; then the first backup | Agent; backup: owner (`icloud_backup.py backup`) | §8, §11 |
| 7 | Parent folder: `link_parent.py --check` passes now that both repos are committed; re-run `link_parent.py` if it doesn't | Agent | §10.2 |
| 8 | Acceptance: guard test commits, agent-loading check; restore drill | Agent and owner; drill: owner (`icloud_backup.py drill`) | §13 |

The tool repo's code exists before the workspace's denylist (step 2 before 3), but the tool repo's first commit
waits for that denylist (step 4), so the guard checks the very first commit.

---

## 3. Step 0: the safety copy

Nothing is backed up today, so this comes before every other P0 step. It's a one-off: a plain copy, not DVC.

- **Destination:** `iCloud Drive/Backups/demq-elektric-step0-<YYYY-MM-DD>/`, beside `demq-elektric-dvc/` and
  `demq-elektric-git/`, dated with the day it runs.
- **Content:** `demq-elektric/data/landing/` and `demq-elektric/legacy/`, with a `SHA256SUMS` manifest. The
  manifest holds original filenames, which start with the NMI. It lives only in iCloud, which is private, and is
  never copied into either repo.
- **Retention:** keep it. It's about 16 MB. It may be deleted only after P1 has landed every file and a restore
  drill has passed, and only on the owner's word.

**Owner, in a terminal with iCloud access** (§4). `scripts/icloud_backup.py` exists already, so Step 0 needs
nothing else from P0. The runbook has the full steps.

```bash
cd ~/WorkSpace/Electricity
uv run energatron-5000/scripts/icloud_backup.py check
uv run energatron-5000/scripts/icloud_backup.py step0
```

`step0`:
1. refuses if today's folder, or a `.partial` one, already exists; it never overwrites or deletes
2. hashes every file under `data/landing/` and `legacy/` (skipping `.DS_Store`), then copies both trees into
   `demq-elektric-step0-<date>.partial/`
3. re-hashes the copy. Any missing, changed or extra file → `FAIL`, and the `.partial` folder stays for the
   owner to delete in Finder
4. writes `SHA256SUMS` in `shasum -a 256 -c` format, then renames the folder into place
5. prints counts and the folder name only

Then:
1. In Finder, right-click `iCloud Drive/Backups` → **Keep Downloaded**. This covers all three sub-folders,
   including the two P0 creates later.
2. Wait until Finder shows the folder as uploaded (no cloud-with-arrow badge).
3. Run `check`. It counts dataless files (`st_flags & SF_DATALESS`) per `demq-elektric-*` folder and must report
   each as `all downloaded`.

**Done when:** `step0` printed `copy verified` and `check` reported the folder as all downloaded. The owner
reports both lines, and that's all that goes into `TASKS.md`.

---

## 4. iCloud steps are the owner's

**Cause:** macOS TCC blocks the app that hosts the agent (Terminal, iTerm, VS Code, the Claude or Codex desktop
app) from `~/Library/Mobile Documents`. Changing agent sandbox settings doesn't help.

**Decision (owner, 2026-10-23):** no app that hosts agents gets iCloud access. The owner runs every iCloud step
by hand, in a terminal app that has iCloud Drive access and doesn't host agents. The runbook says how.

**Steps that touch iCloud, all owner-run:**

| Step | Command (from the parent folder) | When |
|---|---|---|
| Access check | `uv run energatron-5000/scripts/icloud_backup.py check` | first, and when anything fails |
| Step 0 copy | `… icloud_backup.py step0` | once, before P0 starts (§3) |
| Backup | `… icloud_backup.py backup` (runs `uv run energatron backup --json` in the workspace) | after each workspace commit, when `/done` reports it pending |
| Restore drill | `… icloud_backup.py backup --drill`, or `drill` | at P0 acceptance, then quarterly (plan §6; §12) |

**`scripts/icloud_backup.py`** (written with this spec, stdlib only, PEP 723 header so `uv run` gives it a
managed Python 3.14):
- options `--backups-dir` and `--workspace` (default the sibling `demq-elektric/`), so tests can point it at
  scratch folders. Without `--backups-dir`, the Backups folder is the parent of `backup.bundle_dir` in the
  workspace's `.energatron/local.toml` (§6.2), else `iCloud Drive/Backups` (P0.4.4, 2026-11-03: the owner's
  iCloud folder isn't `Backups/`, and nothing owner-specific belongs in this repo)
- every command first checks that `Backups/` (or its parent) can be listed and written: a temp file is created
  and removed. `PermissionError` → exit 2 with the System Settings path to fix it
- `backup` and `drill` exit 2 with "not set up yet" until `.energatron/workspace.toml` exists, and `drill` also
  until `scripts/restore_drill.py` exists
- `backup` prints `dvc_files_pushed`, whether a bundle was written, kept/removed counts, the warning codes and,
  on failure, the error code and message. It never prints a file name or path
- `drill` passes anything after `--` on to `restore_drill.py`
- `step0` refuses a source tree that holds a symlink (exit 2, nothing written): it copies and verifies regular
  files only. An `OSError` during the copy → exit 1, with the `.partial` folder left
- `backup` exits 2 for `usage` and `state` errors (not configured, workspace locked) and 1 for the rest
- `.DS_Store` files are neither copied nor counted; dataless counting reads `st_flags` through `getattr`, so it
  also runs on Linux CI, where nothing is dataless
- exit codes: 0 done, 1 a check or the backup failed, 2 not set up, no access, or the workspace busy. It never
  deletes anything

**What agents do instead:**
- they never run `energatron backup` or the script. If one does by mistake, it fails fast with `E_STORAGE`
  (§8.1 step 1) and changes nothing
- `/done`, after a workspace commit, runs `uv run energatron backup --status --json` (§8.5). If
  `refs_backed_up` is false, its reply ends with one of these lines, then "Run `icloud_backup.py backup` in
  your terminal.":
  - no marker (`last_backup_head` is null): "Backup pending: nothing is backed up yet."
  - `commits_not_backed_up` above 0: "Backup pending: N commits not in iCloud."
  - otherwise (0, or null because the old head is gone): "Backup pending: refs changed since the last backup
    (for example a new tag)."
- the workspace `CLAUDE.md` says so (§10.1)

---

## 5. Tool repo (`energatron-5000/`)

### 5.1 Files P0 adds

Existing files (`TASKS.md`, `agent_docs/`, `.claude/skills/`, `.agents/skills/`, `scripts/link_parent.py`) stay
where they are.

```
energatron-5000/
├── README.md  LICENSE  NOTICE  CHANGELOG.md
├── CLAUDE.md                        §10.1 draft
├── AGENTS.md → CLAUDE.md
├── .claude/settings.json            §10.3
├── .claude/skills/new-doc/SKILL.md  §10.4
├── .agents/skills/new-doc → ../../.claude/skills/new-doc
├── parent/CLAUDE.md                 the parent folder's instructions (§10.1); link_parent.py links it
├── pyproject.toml  uv.lock  .python-version  .importlinter  .gitignore  .pre-commit-config.yaml
├── .github/workflows/ci.yml
├── scripts/privacy_guard.py  check_agent_links.py  restore_drill.py  link_parent.py (extended, §10.2)
│           icloud_backup.py (exists already, §4)
├── src/energatron/                  every package from D1 §2, with __init__.py; P0 code per §5.9
└── tests/
    ├── architecture/                the D1 §13 tests that apply in P0
    ├── unit/{core,adapters,scripts}/
    ├── integration/
    └── fixtures/schemas/<kind>/v1.json
```

`.python-version` holds `3.14`.

### 5.2 `pyproject.toml`

```toml
[project]
name = "energatron-5000"
version = "0.1.0.dev0"
description = "Household energy usage analysis and retail offer comparison, local-first."
readme = "README.md"
license = "Apache-2.0"
license-files = ["LICENSE", "NOTICE"]
requires-python = ">=3.14,<3.15"
dependencies = [
    "pydantic>=2.14",
    "pandas>=3.1",
    "numpy>=2.5",
    "pyarrow>=26",
    "pyyaml>=6.0",
    "typer>=0.27",
    "filelock>=4.2",
]

[project.scripts]
energatron = "energatron.adapters.cli.app:main"

[dependency-groups]
dev = [
    "pytest>=9.1",
    "hypothesis>=6.170",
    "ruff>=0.17",
    "pyright[nodejs]>=1.1.414",
    "import-linter>=2.15",
    "pre-commit>=4.7",
    "pandas-stubs",
    "dvc>=3.67.1",
]

[build-system]
requires = ["uv_build>=0.12,<0.13"]
build-backend = "uv_build"

[tool.uv]
required-version = ">=0.12.19"
python-preference = "only-managed"

[tool.uv.build-backend]
module-name = "energatron"

[tool.ruff]
line-length = 120
target-version = "py314"
src = ["src", "tests", "scripts"]

[tool.ruff.lint]
select = ["E", "F", "W", "I", "B", "UP", "SIM", "RUF"]

[tool.pyright]
include = ["src", "tests", "scripts"]
pythonVersion = "3.14"
typeCheckingMode = "standard"
venvPath = "."
venv = ".venv"

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = ["--strict-markers", "--strict-config"]
xfail_strict = true
markers = ["integration: runs git or DVC as subprocesses in scratch repos"]
```

- Lower bounds are the versions verified on 2026-10-23. `uv.lock` pins the exact versions.
- Dependencies arrive with the phase that first imports them: httpx in P4, pdfplumber in P5. duckdb becomes the
  optional extra `sql` when something first uses it.
- `.importlinter` is D1 §3, verbatim.

### 5.3 `.gitignore`

```
.venv/
__pycache__/
*.py[co]
.pytest_cache/
.ruff_cache/
.hypothesis/
dist/
build/
*.egg-info/
.DS_Store
```

### 5.4 Standard checks

Run from the tool repo root:

```bash
uv run ruff check
uv run ruff format --check
uv run pyright
uv run lint-imports
uv run pytest -q
uv run pre-commit run --files <changed files>
```

From P0 on, these replace the "before P0" scan in `TASKS.md` **Rules**. The privacy scan becomes the guard:

```bash
uv run python scripts/privacy_guard.py --mode tool --figures <changed files> TASKS.md
```

### 5.5 `.pre-commit-config.yaml` (tool repo)

Every hook is `repo: local` and `language: system`, and runs through `uv run --frozen`, so it uses the tools
pinned in `uv.lock` and pre-commit installs nothing of its own. `--frozen` syncs the venv to the lock without
re-resolving it.

```yaml
default_install_hook_types: [pre-commit, commit-msg]
repos:
  - repo: local
    hooks:
      - id: privacy-guard
        name: privacy guard
        entry: uv run --frozen python scripts/privacy_guard.py --mode tool --staged
        language: system
        pass_filenames: false
        always_run: true
        stages: [pre-commit]
      - id: privacy-guard-msg
        name: privacy guard (commit message)
        entry: uv run --frozen python scripts/privacy_guard.py --mode tool --commit-msg
        language: system
        stages: [commit-msg]
      - id: agent-links
        name: agent links
        entry: uv run --frozen python scripts/check_agent_links.py --parent
        language: system
        pass_filenames: false
        always_run: true
        stages: [pre-commit]
      - id: ruff
        name: ruff
        entry: uv run --frozen ruff check --force-exclude
        language: system
        types_or: [python, pyi]
        stages: [pre-commit]
      - id: ruff-format
        name: ruff format
        entry: uv run --frozen ruff format --check --force-exclude
        language: system
        types_or: [python, pyi]
        stages: [pre-commit]
      - id: lint-imports
        name: import contracts
        entry: uv run --frozen lint-imports
        language: system
        pass_filenames: false
        files: ^(src/|\.importlinter$)
        stages: [pre-commit]
```

- pyright and pytest stay out of the hook (they're slow); CI and the standard checks run them.
- `uv run pre-commit install` reads `default_install_hook_types` and installs both hook types.
- The hook IDs `privacy-guard` and `privacy-guard-msg` are an interface: `VersionControl.commit` maps them to
  `E_DENYLIST_HIT` (§8.6).

### 5.6 CI (`.github/workflows/ci.yml`)

```yaml
name: ci
on: [push, pull_request]
permissions:
  contents: read
jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@<full commit SHA>     # pin by SHA, looked up when P0 writes the file
      - uses: astral-sh/setup-uv@<full commit SHA>
        with:
          version: "0.12.19"
      - run: uv sync --locked
      - run: uv run ruff check
      - run: uv run ruff format --check
      - run: uv run pyright
      - run: uv run lint-imports
      - run: uv run pytest -q
      - run: uv run python scripts/privacy_guard.py --mode tool --tracked --no-denylist
      - run: uv run python scripts/check_agent_links.py
```

- Linux runners: in a private repo, macOS minutes cost ten times as much. So code must handle `st_flags` being
  absent (Linux); dataless checks then report 0.
- CI can't see the denylist. It runs the guard's file-type and path rules (§7.4), which is what the plan asks of
  CI.
- Tests that create commits set `GIT_AUTHOR_*`, `GIT_COMMITTER_*` and `GIT_CONFIG_GLOBAL=/dev/null` themselves,
  so they don't depend on the runner's git config.

### 5.7 `LICENSE`, `NOTICE`, `README.md`, `CHANGELOG.md`

- `LICENSE`: the unmodified Apache-2.0 text.
- `NOTICE`: `energatron-5000` / `Copyright 2026 The energatron-5000 authors`. No personal name (decided, open
  question 3).
- `README.md`: what the tool is, its status (pre-release; P0), the privacy model in five lines, and the dev setup
  (`uv sync`, §5.4). The runbooks come in P7.
- `CHANGELOG.md`: Keep a Changelog format, with an `Unreleased` section. D1 §8.1 needs it for CLI renames.

### 5.8 First commit and the GitHub repo

**Before the first commit** (step 4):
1. `git init -b main`. This happens in step 2; nothing is committed then.
   - From the parent folder, run `python3 energatron-5000/scripts/link_parent.py`. The `agent-links` hook runs
     `link_parent.py --check`, so the first commit is rejected until the parent links and the `new-doc`
     wrapper exist.
2. Commit identity: P8 makes the history public, so set the repo's own identity before anything is committed:
   ```bash
   login=$(gh api user --jq .login); id=$(gh api user --jq .id)
   git config user.name "$login"
   git config user.email "$id+$login@users.noreply.github.com"
   ```
   Both come from the GitHub account `gh` is logged in to (owner decision 2026-10-23, open question 3), so
   neither is written into any file.
3. `git config energatron.denylist ../demq-elektric/private`. This is local config, never committed (§7.2).
4. `uv run pre-commit install`
5. Audit everything that will be committed:
   `uv run python scripts/privacy_guard.py --mode tool --figures $(git ls-files -co --exclude-standard)`.
   It must report no hits, and the owner reads the figure counts.
6. `git add` the exact file list from §5.1 plus the existing files, then commit with
   `P0: bootstrap energatron-5000`. The hooks run on everything staged.

**GitHub** (step 5; pushing needs the owner's go-ahead, per **Rules**):

```bash
gh auth status
uv run python scripts/privacy_guard.py --mode tool --history     # every commit's patch and message
gh repo create energatron-5000 --private --source . --remote origin \
  --description "Household energy usage analysis and retail offer comparison, local-first"
git push -u origin main
gh repo edit --enable-wiki=false --enable-projects=false --delete-branch-on-merge
```

- After the push, CI must pass on GitHub.
- The workspace never gets a GitHub remote (plan rev 5).

### 5.9 P0 package code

D1 binds all of this. P0 implements only what P0's three commands need, plus the D1 §13 tests that apply in P0.

| Module | P0 content |
|---|---|
| `core/errors.py` | the full D1 §7 hierarchy, plus `E_BACKUP_NOT_CONFIGURED` and `E_DENYLIST_STALE` (state) |
| `core/persisted.py` | `Persisted`, the kind registry, `@migration`, `upgrade()` |
| `core/canonical.py`, `core/ids.py` | all of D1 §6 |
| `core/privacy.py` | `nmi_checksum()`, `DenyEntry`, `derive_denylist()` (§7.3), `redact()`, `redact_json()` |
| `core/models/workspace.py` | `WorkspacePaths`, `WorkspaceConfig`, `LocalConfig` (§6) |
| `core/models/private.py` | `PrivateConfig` (§6.3) |
| `core/models/vcs.py` | `BackupReport` with `bundle_created` and `dataless_files` (§8.1); `BackupState` (§6.5); `BackupStatus` (§8.5) |
| `ports/*.py` | every protocol from D1 §4, in full, plus `VersionControl.backup_status()` (§1, change 12) |
| `services/context.py`, `services/files.py` | D1 §4.8 and §12 |
| `services/denylist.py` | `refresh_denylist(ctx, *, check=False, dry_run=False) -> DenylistResult` (§7.2) |
| `services/backup.py` | `backup(ctx, *, dry_run=False) -> BackupResult`; `backup_status(ctx) -> BackupStatusResult`, which wraps `ctx.vcs.backup_status()` (§8) |
| `adapters/workspace.py` | find the root (D1 §8.3); read `workspace.toml`, `local.toml`, `private/config.toml` |
| `adapters/vcs/git_dvc.py` | `GitDvcVersionControl`, all six methods (§8) |
| `adapters/wiring.py` | `build_context()`, plus a `SystemClock` |
| `adapters/cli/` | `app.py`, `output.py` (envelope, redaction, D1 §8), and the commands `version`, `denylist`, `backup` |

**Placeholders.** `ServiceContext` needs `catalog`, `blobs` and `runner`, which arrive in P1, and `parsers` is
`()`.
- `build_context()` passes an instance of `wiring._NotYet<Port>` for each missing port. Every method raises
  `E_INTERNAL("<Port> arrives in P1")`.
- P1 removes them, and a P1 test asserts that none are left.
- The placeholders change no D1 interface. P0's one D1 interface change is `backup_status()` (§1, change 12).

**Commands:**
- `energatron version`: works outside a workspace. Result kind `version_result`: `energatron_version`,
  `python`.
- `energatron denylist [--check]`: takes the lock. See §7.2.
- `energatron backup`: takes the lock. Owner-run (§4). See §8.
- `energatron backup --status`: no lock, no iCloud. See §8.5.

---

## 6. Persisted kinds defined here

Each is `Persisted` (D1 §5), version 1, and has a golden `tests/fixtures/schemas/<kind>/v1.json`.

### 6.1 `workspace_config`: `.energatron/workspace.toml` (git-tracked, non-personal)

```toml
kind = "workspace_config"
schema_version = 1
region = "sa"                 # a region profile name; P1 checks that the profile exists

[backup]
keep_latest = 10              # bundles kept regardless of age; at least 3
keep_monthly = true           # also keep the newest bundle of every calendar month (UTC)
```

### 6.2 `local_config`: `.energatron/local.toml` (git-ignored, machine-specific)

```toml
kind = "local_config"
schema_version = 1

[backup]
bundle_dir = "~/Library/Mobile Documents/com~apple~CloudDocs/Backups/demq-elektric-git"
```

- `~` is expanded on read, and the result must be absolute and outside the workspace. Otherwise →
  `E_BACKUP_NOT_CONFIGURED`.
- The DVC remote isn't repeated here. It lives in `.dvc/config.local`, as DVC expects.
- A missing `local.toml` matters only to `backup`, which then raises `E_BACKUP_NOT_CONFIGURED`.

### 6.3 `private_config`: `private/config.toml` (DVC-tracked)

Its template, `config/private.example.toml`, is committed in the workspace:

```toml
kind = "private_config"
schema_version = 1
salt = "<32 lowercase hex characters, generated once>"

[premises]
nmi = "<NMI>"                          # 10 characters, or 11 including the checksum digit
meter_serials = ["<meter serial>"]
street = "<street number and name>"    # optional, but the denylist is weaker without it
postcode = "<postcode>"                # optional, as above
network_tariff = "<network tariff code>"   # optional until P4
gas_mirn = "<MIRN>"                    # optional until P5

[accounts]
electricity = ["<retailer account or customer number>"]   # optional; any number of entries
gas = []

[current_plans]
electricity = "<offer snapshot id>"    # optional until P4
gas = "<offer snapshot id>"            # optional until P5
```

Validation (`core/models/private.py`):
- `nmi`: upper-cased, `^[A-Z0-9]{10}[0-9]?$`. With 11 characters, the last one must equal `nmi_checksum()` of
  the first 10, or → `E_INVALID_INPUT`.
- `salt`: `^[0-9a-f]{32}$`.
- `postcode`: `^\d{4}$`.
- `gas_mirn`: `^\d{10,11}$`.
- `meter_serials` and the `accounts` entries: non-empty strings with no surrounding whitespace.
- `current_plans` values: must parse with `parse_offer_snapshot_id`.
- **Privacy:** every field is declared with `repr=False`, so `repr()`, tracebacks and pydantic validation errors
  never show values. A validation failure is re-raised as `E_INVALID_INPUT`, naming the field but not its value.

### 6.4 Result kinds

| Kind | Fields |
|---|---|
| `version_result` | `energatron_version`, `python` |
| `denylist_result` | `written: bool`, `entries: int` (generated), `manual_entries: int`, `pointer_files: list[str]`, `dry_run: bool` |
| `backup_result` | `report: BackupReport \| null`, `dry_run: bool` |
| `backup_status_result` | `head: str \| null`, `last_backup_head: str \| null`, `last_backup_at: str \| null` (UTC ISO), `refs_backed_up: bool`, `commits_not_backed_up: int \| null` |

`cli_result` is D1 §8.5. `params` is left to D3 (§1, change 5).

### 6.5 `backup_state`: `.energatron/backup_state.json` (git-ignored, machine-specific)

What the last successful `energatron backup` stored, so agents can tell whether a backup is pending without
reaching iCloud.

```json
{"kind": "backup_state", "schema_version": 1, "head": "<sha>", "refs_sha256": "<hex>",
 "bundle_name": "20261023T093000Z-1a2b3c4.bundle", "at": "2026-10-23T09:30:00Z"}
```

- `refs_sha256` is the sha256 of the sorted `<objectname> <refname>` lines from §8.1 step 3, `HEAD` included.
- Written atomically (temp file, `os.replace`) as the last step of a backup that succeeded, whether or not a new
  bundle was written.
- A missing file means "never backed up". A file that fails validation → `E_INVALID_INPUT`.
- It's a hint only. Nothing restores from it, and the restore drill checks the bundle itself (§12 step 8).

---

## 7. Privacy guard (`scripts/privacy_guard.py`)

Stdlib only, so it also runs with a bare `python3` before a venv exists. It never imports `energatron`; a broken
package must not disable the guard.

### 7.1 Command line

```
privacy_guard.py --mode {tool,workspace} SOURCE [--denylist-dir DIR | --no-denylist] [--figures]
SOURCE is exactly one of:
  --staged            every added, copied, modified, renamed or type-changed path in the index; the hook mode
  --commit-msg FILE   the commit message; the commit-msg hook mode
  --tracked           every path in the index; CI and audits
  --history           every commit reachable from any ref: its patch and its message
  PATH...             these working-tree files (scans of uncommitted work)
```

- Content comes from the **index** for `--staged` and `--tracked` (`git ls-files --stage -z`, then
  `git cat-file --batch`). A partially staged file is therefore checked as it will be committed.
- Symlinks are checked by their target text. Gitlinks (submodules) are a violation: neither repo has any.
- `--figures`: after the normal checks, prints per-file counts of three patterns for a human to read:
  - energy figures: `\d[\d,]*(\.\d+)?\s*(kWh|MWh|MJ|m3|m³)`
  - money: `\$\s*\d`
  - 4-digit numbers outside 1900–2099 that aren't part of a longer digit run
  Counts only, never the matches. It doesn't change the exit code.

### 7.2 Where the denylist comes from

The **denylist directory** holds `config.toml`, `denylist.generated.txt` and optionally `denylist.txt`:
- workspace mode: `private/` by default
- tool mode: the value of `git config --get energatron.denylist`, relative to the repo root
  - unset → exit 2, printing how to set it, or how to opt out with the value `none` (other contributors before
    P8)
  - the owner's value is `../demq-elektric/private` (§5.8)
- `--denylist-dir DIR` overrides both; `--no-denylist` skips the denylist rules, and CI uses it

**`denylist.generated.txt`**, written only by `energatron denylist`:

```
# energatron denylist v1. Generated by `energatron denylist`; do not edit.
# source-sha256: <sha256 of config.toml's bytes>
<label>\t<substring|word>\t<value>
```

- Lines are sorted, one per entry. Labels come from §7.3.
- The service writes it atomically, then calls `ctx.vcs.track(["private"])`, so `private.dvc` follows. It never
  commits; the caller commits `private.dvc`.
- `--check` writes nothing. If the file is missing or its `source-sha256` differs →
  `E_DENYLIST_STALE`.
- A missing optional field (`street`, `postcode`, `meter_serials`, `accounts`) → `W_PRIVATE_FIELDS_MISSING`,
  naming the fields but not values.
- `ctx.private` is `None` → `E_PRIVATE_CONFIG_MISSING`.

**`denylist.txt`** is optional and hand-written:
- one entry per line; `#` starts a comment, and blank lines are ignored
- a `word:` prefix makes the entry a whole-word match
- labels are `manual:<line number>`
- a substring entry shorter than 5 characters is a setup error (exit 2): it would match almost everything. Use
  `word:` for those.

**Guard setup checks** (each one is exit 2):
- `denylist.generated.txt` missing
- the directory contains `config.toml` whose sha256 differs from `source-sha256` ("denylist is stale; run
  `uv run energatron denylist`")
- a malformed line

If `config.toml` is missing but the generated file exists, the generated file is used as it is.

D1 §8.7 redaction in the CLI derives its entries in memory from the private config, plus `denylist.txt`, so it
never depends on the generated file being current.

### 7.3 Derivation (`core.privacy.derive_denylist(private) -> tuple[DenyEntry, ...]`)

`DenyEntry(label, value, match)` with `match` either `substring` or `word`. The output is sorted by
`(label, value)`, with no duplicates.

| Source | Entries |
|---|---|
| `premises.nmi` | `nmi`: the 10-character NMI; `nmi_checksum`: the same plus its checksum digit. Both `substring` |
| `premises.meter_serials[]` | `meter_serial`: `substring`, or `word` if shorter than 5 characters |
| `premises.street` | `street`: `substring`, with whitespace runs normalised to one space |
| `premises.postcode` | `postcode`: `word` |
| `premises.gas_mirn` | `mirn`: as given; plus `mirn_base`, the first 10 digits, if 11 were given. Both `substring` |
| `accounts.*[]` | `account`: `substring`, or `word` if shorter than 5 characters |

Never included: `salt` (random), `network_tariff` and `current_plans` (public codes and IDs).

**NMI checksum** (AEMO's published algorithm), for the first 10 characters, upper-cased:
1. Walk the characters from right to left, starting with the rightmost.
2. Take each character's ASCII code, and double it for the rightmost character and every second one after it.
3. Add up the decimal digits of every resulting number.
4. The checksum is `(10 − total mod 10) mod 10`.

Tests use synthetic NMIs with worked examples in the test file.

### 7.4 Rules

| Rule | Mode | Violation when |
|---|---|---|
| Denylist, content | both | an entry matches the content (case-insensitive) |
| Denylist, paths | both | an entry matches a path or file name |
| File size | both | a file over 5 MiB |
| Data file types | both | `.csv .tsv .parquet .pdf .json .jsonl .xlsx .xls .zip .png .jpg .jpeg .sqlite .db .duckdb .pkl .pickle .feather .arrow` outside the allowed places (below) |
| DVC in the tool repo | tool | any `*.dvc`, `dvc.lock` or `.dvc/` path, or `dvc.yaml` outside `tests/fixtures/` |
| Private folders | tool | a top-level `data/`, `private/` or `legacy/`, or any `*.ipynb` |
| Workspace data | workspace | under `data/`, anything but `*.dvc` and `.gitignore`; anything under `private/`, `legacy/`, `.venv/`; `.energatron/local.toml`, `.energatron/lock`, `.energatron/tmp/**` |
| Notebook outputs | workspace | an `*.ipynb` with a non-empty `outputs` or a non-null `execution_count` in any cell |

- **Allowed places for data types:** `tests/fixtures/**` in both modes, `.claude/settings.json` in both,
  `config/**` (workspace), `.energatron/code_fingerprint.json` (workspace).
- **Matching:** every entry becomes a regex. The value is escaped, each space matches `\s+`, and matching is
  case-insensitive.
  - `word` entries are wrapped in `(?<![0-9A-Za-z_-])…(?![0-9A-Za-z_-])`. So a postcode equal to the number in
    the repo name matches where it stands alone, but not inside `energatron-…`. A test pins exactly that case.
- **Decoding:** content is decoded as UTF-8 with `errors="replace"`. ASCII identifiers survive any binary file.
- `--commit-msg` applies only the denylist rule. `--history` applies the denylist rule to each patch and
  message, and the path rules to each changed path.

### 7.5 Output and exit codes

- One line per violation on stderr, for example:
  - `privacy_guard: src/x.py:12: denylist hit (nmi_checksum)`
  - `privacy_guard: notes/<redacted>.md: denylist hit in path (nmi)`
  - `privacy_guard: commit 1a2b3c4: denylist hit (street)`
- **Never** an entry's value or the matching text. Printed paths and commit subjects are redacted by the same
  matcher first.
- The last line is a summary of counts.
- Exit codes: 0 clean, 1 violations, 2 setup error (denylist missing or stale, bad arguments, git failure).

### 7.6 Tests (`tests/unit/scripts/test_privacy_guard.py`; synthetic values only)

Each test builds a scratch git repo and a scratch denylist directory, generated by `derive_denylist` from a
synthetic `PrivateConfig`.

1. Each derived label blocks a staged file containing it. The NMI is blocked with and without its checksum
   digit, and in upper and lower case.
2. A hit only in a path or file name is blocked, and the printed path is redacted.
3. `--commit-msg` blocks a message containing the meter serial.
4. A partially staged file: a hit only in the unstaged part passes, and a hit only in the staged part fails.
5. The postcode `word` entry, with a synthetic postcode equal to the repo name's number: the number standing
   alone after "SA " fails; the repo name and a longer digit run containing the number pass.
6. A street with different spacing and case is blocked.
7. A stale `source-sha256` → exit 2. A missing generated file → exit 2. Tool mode with `energatron.denylist`
   unset → exit 2, and with `none` → runs without a denylist.
8. A 4-character substring entry in `denylist.txt` → exit 2. The same entry with `word:` is accepted.
9. File types: a `.csv` at the root fails; one under `tests/fixtures/` passes; `.claude/settings.json` passes.
10. Tool mode: `x.dvc`, `dvc.lock` and `.dvc/config` fail; `tests/fixtures/golden/dvc.yaml` passes; a notebook
    fails.
11. Workspace mode: `data/raw/x.csv` fails, `data/raw/sapn_interval.dvc` passes, and `private/config.toml`,
    `.energatron/local.toml` and `legacy/x` fail.
12. A notebook with outputs fails; the same notebook stripped passes.
13. A 6 MiB file fails.
14. `--history` finds a hit in an old commit that a later commit removed.
15. No test's captured stdout or stderr contains any denylist value.
16. `--figures` prints counts and exits 0.
17. A staged symlink whose target text holds a denylist value fails; the file it points to isn't read.
18. A staged gitlink (submodule entry) fails in both modes.
19. `--tracked` checks every path in the index: a hit in an unchanged, already-committed file fails.
20. A malformed line in `denylist.generated.txt` → exit 2, and the message doesn't echo the line.

---

## 8. Backups: `VersionControl.backup()`

### 8.1 What it does (`GitDvcVersionControl.backup`)

The workspace lock is held by the caller, `energatron backup`, which only the owner runs (§4).

1. **Configuration and access:**
   - read `local_config`'s `backup.bundle_dir`
   - check that `dvc remote default` names a remote
   - either missing → `E_BACKUP_NOT_CONFIGURED`
   - `head()` is `None` → `E_USAGE` ("nothing to back up yet")
   - list both the bundle directory and the remote directory. One that doesn't exist yet, but whose parent
     (`Backups/`) can be listed, is created with `mkdir` (not `parents=True`), so the first backup sets both up
   - any `OSError` here → `E_STORAGE`, with `details.hint` "run `icloud_backup.py backup` in a terminal with
     iCloud access". This runs before `dvc push`, so an agent session fails here and changes nothing
2. **DVC:**
   1. count the objects listed by `dvc status --cloud --json`
   2. run `dvc push`
   3. run `dvc status --cloud --json` again; anything other than `{}` → `E_STORAGE`
   - `dvc_files_pushed` is the count from step 1
3. **Has anything changed?**
   - list the refs: `git for-each-ref --format='%(objectname) %(refname)'`, plus `HEAD`
   - list the newest bundle's heads: `git bundle list-heads`
   - if they're equal, no new bundle is written: `bundle_created = false`, and `bundle_name` is that bundle
   - no bundle in `bundle_dir` yet (the first backup) → step 4
4. **New bundle:**
   1. `git bundle create .energatron/tmp/<name> --all`
   2. `git bundle verify` it, and check that its heads equal the refs from step 3
   3. copy it to `<bundle_dir>/.<name>.partial`, `fsync`, and compare sha256 with the local file
   4. `os.replace` it to `<bundle_dir>/<name>`, then `fsync` the directory
   5. delete the local temp file in a `finally`
   - any failure → `E_STORAGE`, and nothing is renamed into place
5. **Retention** (§8.3) runs only after step 4 succeeded, or step 3 found an existing bundle.
6. **Dataless check:** count dataless files (`st_flags & SF_DATALESS`) under `bundle_dir` and the DVC remote
   directory, into `dataless_files`.
   - When it's above 0, the service adds `W_BACKUP_DATALESS` with the count to its result.
   - It's a warning, not an error, because the data is in iCloud. But "Keep Downloaded" has failed, and the
     restore drill will fail too.
7. **Marker:** write `.energatron/backup_state.json` (§6.5) with the refs from step 3.

**Errors:**
- `OSError` on either backup location (TCC's `EPERM` included) → `E_STORAGE`. `details.hint` says which access
  is missing. No path is shown.
- `BackupReport` gains `bundle_created: bool` and `dataless_files: int`, and still holds counts and file names
  only (D1 §4.4).

### 8.2 Naming and locations

| What | Where | Name |
|---|---|---|
| DVC remote | `iCloud Drive/Backups/demq-elektric-dvc/` | DVC's content-addressed layout |
| Git bundles | `iCloud Drive/Backups/demq-elektric-git/` | `<compact_utc(now)>-<head[:7]>.bundle`, e.g. `20261023T093000Z-1a2b3c4.bundle` |
| Step 0 copy | `iCloud Drive/Backups/demq-elektric-step0-<YYYY-MM-DD>/` | §3 |

- `now` comes from `ctx.clock`, which the service passes in when it builds the adapter.
- Names sort chronologically, and the newest bundle is the last name in sort order.
- Every bundle holds the full history (`--all`), so any one bundle restores everything up to its time.

### 8.3 Retention

- Keep the newest `keep_latest` bundles (`workspace.toml`, default 10).
- With `keep_monthly`, also keep the newest bundle of every calendar month (UTC), with no age limit. Bundles are
  small: the workspace history holds pointers and config only.
- Delete everything else that matches `^\d{8}T\d{6}Z-[0-9a-f]{7}\.bundle$`. Also delete a leftover
  `.<name>.partial` older than a day. Nothing else in the folder is ever touched.
- The DVC remote is never pruned: no `dvc gc --cloud`, ever (plan §6).

### 8.4 Trigger points

The workspace has no git remote, so nothing fires on push, and agents can't reach iCloud (§4). So the only
trigger is the owner running `icloud_backup.py backup`. To make that hard to forget:
1. `/done` reports a pending backup after every workspace commit (§4).
2. From P1, every command that commits (`land`, and `run` from P6) ends with the warning `W_BACKUP_PENDING`
   instead of calling `backup()`. D3 adds the code. The plan had them back up inside the lock.
3. `energatron backup --status` answers at any time.

There's deliberately no `post-commit` hook. It would run inside agent sessions, where the backup can't work.

### 8.5 `energatron backup`

- Takes the lock, then calls `services.backup.backup(ctx)`, which calls `ctx.vcs.backup()`.
- `--dry-run` checks that `head()` exists and returns `{"report": null, "dry_run": true}`. It doesn't touch
  iCloud.
- `--status` takes no lock and doesn't touch iCloud. The service calls `ctx.vcs.backup_status()`. The adapter
  reads `backup_state` (§6.5), compares `refs_sha256` with the current refs, and counts
  `git rev-list --count <last_backup_head>..HEAD` (`null` when there's no marker, or the old head no longer
  exists). It returns a `BackupStatus` (`core/models/vcs.py`) with the `backup_status_result` fields (§6.4).
  Agents may run it.
- Exit codes follow D1 §7.

### 8.6 `commit()`'s hook mapping (D1 §4.4, made concrete)

`git commit` runs with its output captured. On a non-zero exit, the adapter parses the `- hook id: <id>` lines
and the `- exit code: <n>` line under each one.

| What failed | Error |
|---|---|
| `privacy-guard` or `privacy-guard-msg`, with exit code 1 | `E_DENYLIST_HIT`, `details.hooks` |
| any other hook, or the guard with exit code 2 (setup) | `E_COMMIT_REJECTED`, `details.hooks` |
| no hook lines at all | `E_SUBPROCESS`, with git's exit code |

Hook output is never logged or returned, because it may describe the content it rejected.

---

## 9. `scripts/check_agent_links.py`

Stdlib only.

```
check_agent_links.py [--repo DIR] [--parent]
```

`--repo` defaults to the git root of the current directory. It checks:

1. `CLAUDE.md` is a regular UTF-8 file of at most 16 KiB, and has no line starting with `@`, so nothing essential
   sits behind Claude-only imports.
2. `AGENTS.md` is a symlink whose target is exactly `CLAUDE.md`.
3. Every `.claude/skills/<name>/SKILL.md` has frontmatter whose `name` equals `<name>` and whose `description` is
   not empty.
   - Its `.agents/skills/<name>` is a symlink to exactly `../../.claude/skills/<name>`.
4. Every entry in `.agents/skills/` is such a symlink, to a skill that exists. There are no strays and no broken
   links.
5. `.claude/settings.json`, if present, parses as JSON.
6. With `--parent`: `python3 <tool repo>/scripts/link_parent.py --check` exits 0. Local hooks pass `--parent`;
   CI doesn't, because a CI checkout has no parent folder.

- Output is one line per problem, then exit 1. Clean is exit 0.
- Tests (`tests/unit/scripts/test_check_agent_links.py`): each rule has a failing scratch tree and a passing
  one, plus a stray link and a broken link.

---

## 10. Agent files

### 10.1 `CLAUDE.md` drafts

Each is plain Markdown under the §9 size limit, and `AGENTS.md` links to it. P0 writes them as below; editing
them later is normal tool-repo or workspace work.

**Parent folder.** Source: `energatron-5000/parent/CLAUDE.md`, linked from `~/WorkSpace/Electricity/CLAUDE.md`.

````markdown
# ~/WorkSpace/Electricity

This folder holds two independent git repos. It is not a repo: never run `git init` here. Nothing in it is
written by hand; `python3 energatron-5000/scripts/link_parent.py` generates it from the repos.

- `energatron-5000/`: the tool. Public-ready code and design docs; a private GitHub repo until P8.
- `demq-elektric/`: the owner's private workspace. Data is DVC-managed and backed up to iCloud.

Before working in either repo, read its `CLAUDE.md` (the same file as its `AGENTS.md`).

## How work happens

Progress, rules and hand-over notes live in `energatron-5000/TASKS.md`. Start every session by reading its
State, Hand-over and Current sub-task sections.

| The owner types | The agent |
|---|---|
| `/next` (Codex: `$next`) | resumes the in-progress sub-task or takes the next one, does it, reports, never commits |
| `changes: …` | applies the changes and re-runs the checks |
| `/examine` | has a fresh reviewer check the uncommitted work and records findings |
| `/done` | the owner's approval: checks, ticks, hand-over, commits, then says whether a workspace backup is pending (the owner runs backups) |

The context is cleared between sub-tasks, so everything the next session needs goes into `TASKS.md`.

## Rules that apply everywhere

- Nothing personal goes into `energatron-5000`: no NMI, meter serial, account or invoice number, MIRN,
  address, postcode, kWh or $ figure, not even in commit messages. Use placeholders such as `<NMI>`.
- Workspace files may be read for analysis; their contents are never copied into the tool repo.
- Never read `demq-elektric/private/`.
- Don't delete or move anything in `demq-elektric/` unless the current sub-task says to.
- Commits happen only in `/done`. Never `--no-verify`, amend, rebase or force-push.
- Ask the owner before pushing, before any global install, and before changing a decision recorded in
  the plan or an approved spec.
````

**Tool repo.** `energatron-5000/CLAUDE.md`:

````markdown
# energatron-5000

A local-first tool that analyses household electricity and gas usage, prices retail offers against it and
recommends the best value, with every result traceable to its data, offers and code. This repo holds code and
design docs only; real data lives in a workspace repo (the owner's is `../demq-elektric`).

## Read first
- `TASKS.md`: state, rules, hand-over and the current sub-task. Work happens through `/next`, `/examine` and
  `/done` (Codex: `$next`, `$examine`, `$done`); see the table at the top of `TASKS.md`.
- `agent_docs/2026-10/2026-10-23_core-architecture-spec.md` (D1) before changing any code. It is binding.
- The phase spec for the code you are touching, listed in the sub-task's Refs.

## Privacy (hard rules)
- Never write an NMI, meter serial, account/customer/invoice number, MIRN, address, postcode, kWh figure or
  $ figure into this repo: not in code, tests, docs, `TASKS.md` or commit messages. Use `<NMI>`-style
  placeholders. Test fixtures are generated by code, never derived from real files.
- No data files (`*.csv *.parquet *.pdf *.json *.xlsx` …) outside `tests/fixtures/`; no `*.dvc` or `dvc.lock`.
- The pre-commit privacy guard enforces this. If it rejects a commit, fix the content; never bypass it.
- Privacy scan of uncommitted work:
  `uv run python scripts/privacy_guard.py --mode tool --figures <files> TASKS.md`

## Code
- Python 3.14 via uv only: `uv sync`, `uv run`, `uv add`. No pip, conda or global installs.
- Layers point down: `adapters → services → ports → core`. Core does no I/O; services touch only files at
  paths passed in; git and DVC are subprocesses in `adapters/vcs` and `adapters/pipeline`.
- Every persisted document has `kind` and `schema_version`; any change to a model bumps its version and adds
  a migration.
- Every CLI command supports `--json`; errors use the codes in `core/errors.py`.
- Tests first: write them, see them fail, implement, see them pass.

## Checks (all must pass before `/done`)
uv run ruff check · uv run ruff format --check · uv run pyright · uv run lint-imports · uv run pytest -q ·
uv run pre-commit run --files <changed files>

## Docs
- New docs: `agent_docs/YYYY-MM/YYYY-MM-DD_<name>.md`, dated by creation; the `new-doc` skill creates them.
- Never rename existing docs. Changing the plan means adding a line to its Revisions header.
- Docs that mention personal data belong in the workspace's `agent_docs/`, not here.

## Agents
- This file is canonical; `AGENTS.md` is a symlink to it. Skills live in `.claude/skills/<name>/SKILL.md`,
  with `.agents/skills/<name>` symlinks. After adding or renaming a skill, run
  `python3 energatron-5000/scripts/link_parent.py` from the parent folder.
- Instructions and skills name no agent-specific tools, so Claude Code and Codex can both follow them.
````

**Workspace.** `demq-elektric/CLAUDE.md`:

````markdown
# demq-elektric (private workspace)

The owner's energy workspace for the `energatron` tool (`../energatron-5000`, installed editable). Git holds
config and DVC pointers only; every data byte is DVC-managed and backed up to iCloud Drive. This repo has no
git remote and must never get one on a public host.

Development work is tracked in `../energatron-5000/TASKS.md` and runs through `/next`, `/examine` and `/done`.
Start those sessions in the parent folder (`~/WorkSpace/Electricity`), where the commands are installed.

## Never
- Read anything under `private/` (identifiers). Change private values only by asking the owner.
- Edit `data/raw/**` (immutable) or `legacy/**` (archive).
- Commit data: only `*.dvc`, `dvc.lock` and config reach git. Stage exact paths; never `git add -A` or `.`.
- Copy workspace contents into `../energatron-5000`, or put identifiers or figures in commit messages.
- Run `dvc gc`, or delete anything, unless the current sub-task says so.

## Commands (run here, through uv)
- `uv run energatron backup --status`: is the last commit backed up? The backup itself is owner-only (Backups).
- `uv run energatron denylist`: after the owner edits `private/config.toml`; then commit `private.dvc`.
- `uv run dvc status`: what changed in DVC-tracked data.
- Later phases add `energatron land`, `run`, `fetch-offers` …; each command supports `--json`.

## Layout
- `data/landing/`: inbox, git-ignored and untracked. `data/raw/`: landed datasets (P1 on).
- `private/`: DVC-tracked private config and denylist. `legacy/`: DVC-tracked archive.
- `.energatron/workspace.toml`: workspace settings (tracked). `.energatron/local.toml`: this machine's backup
  path (git-ignored).
- `agent_docs/`: notes that may mention personal data, named `YYYY-MM/YYYY-MM-DD_<name>.md`.

## Backups
- Agents can't reach iCloud. The owner runs every backup and restore drill by hand with
  `../energatron-5000/scripts/icloud_backup.py`, following
  `../energatron-5000/agent_docs/2026-10/2026-10-23_icloud-backup-runbook.md`.
- Never run `energatron backup` or that script. After a commit, run `uv run energatron backup --status --json`
  and, if `refs_backed_up` is false, tell the owner the backup is pending.
````

### 10.2 `scripts/link_parent.py` extension

Besides the command wrappers and skill links, it maintains three symlinks in the parent folder, from the one
repo that has a `parent/` directory (more than one such repo → exit with an error):

| Parent path | Symlink target |
|---|---|
| `CLAUDE.md` | `energatron-5000/parent/CLAUDE.md` |
| `AGENTS.md` | `CLAUDE.md` |
| `.claude/settings.json` | `../energatron-5000/.claude/settings.json` |

- Symlinks, unlike the command wrappers: these are fixed file names, so no directory discovery is involved. If an
  agent turns out not to follow one, fall back to a generated copy carrying `MARKER` and record it.
- Handling is the same as for skill links: a path that exists and isn't the expected symlink is a problem, not
  overwritten; `--check` reports; `--prune` removes these links when `parent/` is gone.
- Tests (`tests/unit/scripts/test_link_parent.py`) run the script on a scratch parent folder by making `PARENT`
  overridable with `--parent-dir`, a new option used only by tests. They cover create, up to date, relink, a
  foreign file left alone, `--check` exit codes and `--prune`.

### 10.3 `.claude/settings.json` (Claude-only, extra protection)

The same deny list is used in three places:
- `energatron-5000/.claude/settings.json`
- the parent folder, through the symlink above
- `demq-elektric/.claude/settings.json`, a committed copy

```json
{
  "permissions": {
    "deny": [
      "Read(~/WorkSpace/Electricity/demq-elektric/private/**)",
      "Edit(~/WorkSpace/Electricity/demq-elektric/private/**)",
      "Edit(~/WorkSpace/Electricity/demq-elektric/data/raw/**)",
      "Edit(~/WorkSpace/Electricity/demq-elektric/legacy/**)",
      "Bash(git push --force:*)",
      "Bash(git commit --no-verify:*)",
      "Bash(dvc gc:*)",
      "Bash(uv run dvc gc:*)"
    ]
  }
}
```

- The `~/` form makes one rule work whichever folder a session starts in.
- The guard and the hooks are the hard guarantees. These rules only stop the agent's own file tools; a shell
  command can still read `private/`.
- Acceptance (§13) proves the read rule in each of the three start folders. If the rule syntax doesn't match,
  fix the patterns and record it, without changing their intent.
- Codex has no equivalent file. The `CLAUDE.md` rules apply to it.
- From P8 the workspace template generates these paths.

### 10.4 `new-doc` skill (`.claude/skills/new-doc/SKILL.md`)

````markdown
---
name: new-doc
description: Create a new dated document in agent_docs/YYYY-MM/YYYY-MM-DD_<name>.md in the right repo. Use when asked to write a new spec, design doc, note or report.
---

# new-doc

1. Pick the repo:
   - design docs, specs and anything free of personal data → `energatron-5000/agent_docs/`
   - anything that mentions the owner's data, identifiers or figures → `demq-elektric/agent_docs/`
2. Name it `agent_docs/<YYYY-MM>/<YYYY-MM-DD>_<name>.md`, with today's date and a short kebab-case `<name>`.
   Create the month folder if needed. If the name is taken, choose a more specific one; never overwrite or
   rename an existing doc.
3. Start the file with:

   ```markdown
   # <Title>

   - **Created:** <YYYY-MM-DD>
   - **Status:** Draft
   - **Privacy note:** <for the tool repo: "contains no identifiers or usage figures, and nothing added to it may">
   ```
4. In the tool repo, run the privacy scan from `CLAUDE.md` on the new file before reporting.
5. Report the path.
````

---

## 11. Workspace bootstrap (`demq-elektric/`)

### 11.1 Files

```
demq-elektric/
├── CLAUDE.md  AGENTS.md → CLAUDE.md  README.md
├── .claude/settings.json            §10.3
├── pyproject.toml  uv.lock  .python-version (3.14)
├── .gitignore  .dvcignore  .pre-commit-config.yaml
├── .dvc/config                      written by dvc init; no remote in it
├── .energatron/workspace.toml       §6.1
├── .energatron/local.toml           §6.2, git-ignored
├── config/private.example.toml      §6.3
├── private/config.toml  private/denylist.generated.txt     DVC-tracked (private.dvc)
├── legacy/                          DVC-tracked (legacy.dvc)
└── data/landing/                    unchanged, git-ignored
```

**`pyproject.toml`:**

```toml
[project]
name = "demq-elektric"
version = "0"
requires-python = ">=3.14,<3.15"
dependencies = ["energatron-5000"]

[tool.uv]
package = false
required-version = ">=0.12.19"
python-preference = "only-managed"

[tool.uv.sources]
energatron-5000 = { path = "../energatron-5000", editable = true }

[dependency-groups]
dev = ["dvc>=3.67.1", "pre-commit>=4.7", "nbstripout>=0.9.1", "jupyterlab"]
```

- `uv sync` installs `dev` by default, so `uv run jupyter lab` works, as D1 §12 expects.

**`.gitignore`:**

```
.venv/
__pycache__/
.ipynb_checkpoints/
.DS_Store
data/landing/
.energatron/local.toml
.energatron/backup_state.json
.energatron/lock
.energatron/tmp/
```

- `dvc add` appends `/legacy` and `/private` itself.

**`.dvcignore`:** `.DS_Store`

### 11.2 `private/config.toml`

1. The agent writes the file from the template with a stdlib snippet:
   - `salt = secrets.token_hex(16)`
   - the NMI from the SAPN filenames under `data/landing/` (the first field, 11 characters)
   - the meter serials from field 7 of each `200` record
   - the values go from the source files into the new file without being printed or logged
   - the optional fields are left out
2. The owner adds `street`, `postcode` and `accounts` in an editor. The agent never reads the file back; the
   deny rules (§10.3) stop its file tools.
3. `uv run energatron denylist` writes the generated denylist and runs `dvc add private`. It warns
   `W_PRIVATE_FIELDS_MISSING` until the owner has added the optional fields.

### 11.3 `.pre-commit-config.yaml` (workspace)

The guard and link-check scripts come from the sibling tool repo. P8 decides how a template workspace reaches
them.

```yaml
default_install_hook_types: [pre-commit, commit-msg, post-checkout]
repos:
  - repo: local
    hooks:
      - id: privacy-guard
        name: privacy guard
        entry: uv run --frozen python ../energatron-5000/scripts/privacy_guard.py --mode workspace --staged
        language: system
        pass_filenames: false
        always_run: true
        stages: [pre-commit]
      - id: privacy-guard-msg
        name: privacy guard (commit message)
        entry: uv run --frozen python ../energatron-5000/scripts/privacy_guard.py --mode workspace --commit-msg
        language: system
        stages: [commit-msg]
      - id: nbstripout
        name: nbstripout
        entry: uv run --frozen nbstripout
        language: system
        files: \.ipynb$
        stages: [pre-commit]
      - id: agent-links
        name: agent links
        entry: uv run --frozen python ../energatron-5000/scripts/check_agent_links.py
        language: system
        pass_filenames: false
        always_run: true
        stages: [pre-commit]
      - id: dvc-post-checkout
        name: dvc checkout
        entry: uv run --frozen dvc git-hook post-checkout
        language: system
        pass_filenames: false
        always_run: true
        stages: [post-checkout]
```

- **Left out on purpose:**
  - DVC's `pre-push` hook: there is no remote, so it would never fire
  - DVC's `pre-commit` hook: it only prints `dvc status`
- `post-checkout` keeps the data in step with a `git checkout`, which is what the plan §9.4 lookup path needs.

### 11.4 Commands, in order

```bash
cd ~/WorkSpace/Electricity/demq-elektric
git init -b main
# the agent writes the §11.1 files (not private/), then:
uv sync
uv run dvc init
uv run dvc remote add -d --local icloud "$HOME/Library/Mobile Documents/com~apple~CloudDocs/Backups/demq-elektric-dvc"
# §11.2: the agent writes private/config.toml; the owner adds the optional values
uv run energatron denylist          # writes private/denylist.generated.txt, then dvc add private
uv run energatron denylist --check
uv run dvc add legacy
uv run pre-commit install
uv run python ../energatron-5000/scripts/privacy_guard.py --mode workspace --figures \
  $(git ls-files -co --exclude-standard)
git add CLAUDE.md AGENTS.md README.md .claude/settings.json pyproject.toml uv.lock .python-version \
  .gitignore .dvcignore .pre-commit-config.yaml .dvc/config .dvc/.gitignore .energatron/workspace.toml \
  config/private.example.toml private.dvc legacy.dvc
git commit -m "P0: bootstrap demq-elektric workspace"
uv run energatron backup --status   # refs_backed_up: false; the agent asks the owner for the first backup
```

The owner then runs `icloud_backup.py backup` (the first `dvc push` and the first bundle), and `check`.

- Git's identity in the workspace may stay the owner's global one. The repo never leaves the machine and iCloud.
- The DVC cache uses APFS reflinks by default, so tracking `legacy/` doesn't double its disk use.

---

## 12. Restore drill (`scripts/restore_drill.py`)

It's stdlib only and runs from the parent folder. It needs iCloud read access, so only the owner runs it,
through `icloud_backup.py drill` (§4): at P0 acceptance, then quarterly (plan §6).

```
python3 energatron-5000/scripts/restore_drill.py [--workspace demq-elektric] [--tool-source origin|local]
                                                  [--keep] [--json]
```

1. **Inputs:**
   - the bundle directory, from the live workspace's `.energatron/local.toml`
   - the DVC remote URL, from `uv run dvc remote list --local` in the live workspace
   - the tool source:
     - `origin` (the default): `git -C energatron-5000 remote get-url origin`, which tests the real disaster
       case
     - `local`: the local repo path, for offline runs
2. **Scratch:** `$TMPDIR/energatron-restore-<compact_utc>/`.
3. **Clone:**
   - the tool, into `energatron-5000/`
   - the newest bundle (`git bundle verify`, then `git clone`), into `demq-elektric/`, then
     `git remote remove origin` there
4. **Tool version:** if `.energatron/code_fingerprint.json` exists (P1 on) and names a `tool_commit`, check out
   that commit in the tool clone.
5. **Install:** `uv sync --locked` in the workspace clone.
6. **Data:**
   1. `uv run dvc remote add -d --local icloud <url>`
   2. `uv run dvc pull`
   3. `uv run dvc status --json` and `uv run dvc status --cloud --json` must both print `{}`
7. **Pipeline:** only if a `dvc.yaml` exists (P1 on). After `uv run dvc repro`, `git status --porcelain` must be
   empty.
8. **Bundle is current:** the clone's `HEAD` must equal the live workspace's `HEAD`. A difference means the last
   commit wasn't backed up: FAIL.
9. **Verify:** `uv run energatron verify`, once it exists (no later than P2). Before that: SKIP.
10. **Privacy:** `privacy_guard.py --mode workspace --tracked` on the clone, with the clone's `private/` as the
    denylist directory.
11. **Dataless:** no dataless file under the bundle directory or the remote.
12. **Clean-up:** delete the scratch folder with `shutil.rmtree`, unless `--keep` was given.

**Output:**
- one `PASS` / `FAIL` / `SKIP` line per step; with `--json`, a single summary document with counts and step
  results, no paths
- exit codes: 0 when every step passed or was skipped, 1 on any FAIL, 2 on a setup error

**Tests:** `tests/integration/test_restore_drill.py` builds a scratch tool repo, a scratch workspace, a local
"remote" directory and a bundle directory, then runs the drill with `--tool-source local`.
- It passes on a fresh backup.
- It fails at step 6 after one remote object is altered.
- It fails at step 8 after a commit that wasn't backed up.

---

## 13. P0 acceptance checks

From plan §15, made concrete. D4 attaches them to sub-tasks.

1. **Tool repo has no data:**
   - CI is green on GitHub, including `privacy_guard.py --tracked --no-denylist`
   - locally, `privacy_guard.py --mode tool --history` reports no violations
2. **The guard blocks the NMI:** in each repo, stage a scratch file holding the NMI and try to commit it.
   - The commit is rejected by `privacy-guard`, and the printed lines show the label and not the value.
   - Then unstage the scratch file and delete it. The check created it, so this is not a workspace delete.
   - The same with the NMI only in the commit message: rejected by `privacy-guard-msg`.
   - Run by the agent, with values copied from file to file by a snippet that never prints them.
3. **Both agents load the same instructions and skills.** In fresh sessions started in the parent folder and in
   `energatron-5000/`:
   - Claude Code and Codex each name the `new-doc` skill and quote the first rule of the loaded `CLAUDE.md` /
     `AGENTS.md`
   - Codex lists `new-doc` among its skills
   - Done by the owner; the agent records the results
4. **Deny rules:** in Claude Code sessions started in each of the three folders, reading
   `demq-elektric/private/config.toml` with the file-read tool is denied.
5. **Backup** (owner runs, pastes the output; the agent records it):
   - `icloud_backup.py backup` exits 0 with a new bundle
   - run again, it exits 0 with "bundle unchanged" and `DVC files pushed: 0`
   - in an agent session, `uv run energatron backup --status --json` then shows `refs_backed_up: true`, and
     `uv run energatron backup` fails with `E_STORAGE` before pushing anything
6. **Restore drill:** `icloud_backup.py drill` exits 0 (step 9 SKIP). Owner runs it.
7. **Links:** `link_parent.py --check` and `check_agent_links.py --parent` exit 0 in both repos.

---

## 14. Tests that P0 adds

Besides the script tests in §7.6, §9, §10.2 and §12, P0 adds these. They're written first and run red, per the
**Rules**.

| Test | Covers |
|---|---|
| D1 §13 tests that apply in P0 | `lint-imports`, `test_core_purity`, `test_persisted` (every §6 kind), `test_errors` (the new codes included), `test_process_state`, `test_privacy`, `test_cli_envelope` (`version`, `denylist`, `backup`), `test_locking` (`denylist`, `backup`; `backup --status` succeeds while another process holds the lock) |
| `tests/unit/core/test_private_config.py` | Each validation rule in §6.3. An 11-character NMI with a wrong checksum digit is rejected. A validation error and `repr()` never contain a value. |
| `tests/unit/core/test_derive_denylist.py` | Each row of §7.3; `word` vs `substring` by length; sorted and deduplicated; `salt` and `current_plans` excluded; worked checksum examples |
| `tests/unit/services/test_denylist.py` | The written file's header and lines; `source-sha256` matches; `--check` → `E_DENYLIST_STALE` after a config edit; `track(["private"])` is called; `W_PRIVATE_FIELDS_MISSING`; `E_PRIVATE_CONFIG_MISSING`; `--dry-run` writes nothing |
| `tests/unit/scripts/test_icloud_backup.py` | Against scratch `--backups-dir` and `--workspace` folders: `check` with no `Backups/`, with a folder, and with an unreadable parent (exit 2); `step0` copies, verifies and renames, the `SHA256SUMS` passes `shasum -a 256 -c`, `.DS_Store` is skipped, a second run, a leftover `.partial` and a symlink in a source tree each exit 2 and write nothing, an injected copy fault → exit 1 with the `.partial` left; `check` with an injected dataless file → exit 1, and on Linux (no `st_flags`) → exit 0; `backup` against a fake `uv` printing canned envelopes (success, a `state` error → 2, an `external` error → 1, no JSON, JSON that isn't an object); `drill` passes its arguments on; no output line contains a source file name |
| `tests/integration/test_backup_status.py` | `GitDvcVersionControl.backup_status()` against scratch repos: no marker → `refs_backed_up: false`, count `null`; after a backup → `true`, count 0; a new commit → `false`, count 1; a new tag alone → `false`, count 0; a corrupt marker → `E_INVALID_INPUT`; the backup directories are never touched (they're unreadable in the test). The service test uses a fake `VersionControl` |
| `tests/unit/adapters/test_workspace.py` | Root discovery order (D1 §8.3); `E_NOT_A_WORKSPACE`; `~` expansion; a relative or in-workspace `bundle_dir` → `E_BACKUP_NOT_CONFIGURED` |
| `tests/integration/test_git_dvc.py` | Against scratch repos with a local DVC remote:<br>• `head()` is `None` before the first commit<br>• `commit` stages exactly the given paths<br>• a failing ordinary hook → `E_COMMIT_REJECTED`; a failing `privacy-guard` → `E_DENYLIST_HIT`; guard exit 2 → `E_COMMIT_REJECTED`; no hook output in the error<br>• `track` returns the pointer files<br>• `backup`: pushed count; bundle verified; no-op when refs are unchanged; retention keeps `keep_latest` plus monthly and never touches foreign files; a corrupted bundle (an injected copy fault) → `E_STORAGE`, with nothing renamed into place; missing config → `E_BACKUP_NOT_CONFIGURED`; an unwritable bundle directory → `E_STORAGE`; dataless files (an injected `stat`) → `W_BACKUP_DATALESS`; an unreadable remote directory → `E_STORAGE` before `dvc push` runs; a first backup into a `Backups/` holding neither folder creates both, writes a bundle and pushes; a missing `Backups/` → `E_STORAGE`; the marker is written only after success |

---

## 15. Plan changes (for the next plan revision, rev 8)

To be added to the plan's Revisions header on `/done`, with these edits in place:
- **§4 stack:** `pyright[nodejs]` (Node comes as a wheel; no system Node); `pandas-stubs`; DVC is also a dev
  dependency of the tool repo, for tests. `python-preference = "only-managed"` in both repos.
- **§5 (privacy table and item 7):**
  - the denylist is `private/denylist.generated.txt` (written by `energatron denylist`) plus an optional
    hand-written `private/denylist.txt`
  - the guard's modes and rules are as in the P0 spec
  - the tool repo finds the denylist through local `git config energatron.denylist`
  - figure detection is a report, not a blocker
- **§6:**
  - DVC's hooks are local pre-commit hooks through `uv run`; only `post-checkout` is installed, and there is no
    `dvc install --use-pre-commit-tool`
  - bundle naming `<utc>-<sha7>.bundle`, with retention `keep_latest` plus monthly
  - backups are owner-run: `scripts/icloud_backup.py` (`check`, `step0`, `backup`, `drill`), following the
    iCloud backup runbook; agents only read `energatron backup --status`
  - the Step 0 folder `Backups/demq-elektric-step0-<date>/`, written by `icloud_backup.py step0`
  - agents never get iCloud access (macOS TCC on their host app stays off; owner decision 2026-10-23)
  - the restore drill is owner-run through `icloud_backup.py drill`, at P0 acceptance and then quarterly
- **§9, `run` step 5, and wherever `land` backs up:** no backup at the end. The command ends with
  `W_BACKUP_PENDING`, and the owner backs up.
- **§4:** jupyterlab is in the workspace's `dev` group.
- **§7 layout:**
  - tool repo adds `parent/CLAUDE.md`, `scripts/restore_drill.py` and `scripts/icloud_backup.py`
  - workspace adds `.energatron/backup_state.json` (git-ignored)
  - workspace adds `.dvcignore`, `config/private.example.toml` and `private/denylist.generated.txt`
  - the parent folder's `CLAUDE.md`, `AGENTS.md` and `.claude/settings.json` are symlinks made by
    `link_parent.py`
- **§12:** the restore drill is `scripts/restore_drill.py`.
- **§15, P0 row:**
  - replace `dvc install --use-pre-commit-tool` with the local `post-checkout` hook
  - replace "optional private GitHub repo or `git bundle` backup" with "`energatron backup` (dvc push and a
    verified bundle)"
  - add `energatron denylist` and the parent `.claude/settings.json`

Skill change, made in P0 together with `energatron backup`: the `/done` skill's line "**demq-elektric:** once its
backup command exists (from P0), run it after the commit" becomes "run `uv run energatron backup --status
--json` after the commit; if `refs_backed_up` is false, end the reply with the pending-backup line (P0 spec §4)".

---

## 16. Open questions for the owner

1. ~~**iCloud access**~~ Decided 2026-10-23: no access for agents; the owner runs the iCloud steps (§4).
2. ~~**Disk space**~~ Done 2026-10-23: the owner freed space (65 GB free).
3. ~~**Public identity**~~ Decided 2026-10-23: the login and noreply email of the GitHub account `gh` is logged
   in to (§5.8), and `NOTICE` names "The energatron-5000 authors".
