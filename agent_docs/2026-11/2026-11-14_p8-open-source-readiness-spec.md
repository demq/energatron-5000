# P8 open-source readiness spec

- **Created:** 2026-11-14
- **Status:** Approved 2026-11-14 (by the agent, on the owner's instruction of 2026-11-14 to implement all of P8 without
  stopping for approval, self-review and run `/done` and `/next`), after a self-review; Q1-Q10 as written. Its §12
  edits are in the plan as rev 17 and in D1 as rev 9
- **Deliverable:** P8.1.1 in `TASKS.md`
- **Builds on:** [`2026-10-23_data-pipeline-plan.md`](../2026-10/2026-10-23_data-pipeline-plan.md) rev 16 ("the plan"),
  [`2026-10-23_core-architecture-spec.md`](../2026-10/2026-10-23_core-architecture-spec.md) rev 8 ("D1", binding for all
  code), the P0 spec ([`2026-10-23_p0-bootstrap-spec.md`](../2026-10/2026-10-23_p0-bootstrap-spec.md), "D2") §6, §10, §11
  (the hand-built workspace this spec turns into a template), the P1 spec ("D3") §16 and the P7 spec
- **For:** the agent implementing P8; `/done` of P8.1.1 writes P8's sub-tasks from §2
- **Privacy note:** contains no identifiers or usage figures, and nothing added to it may. Examples use placeholders.

---

## 1. What this spec fixes

P8 lets someone other than the owner use the tool: install it from GitHub, create a workspace with one command, try
it on synthetic data before giving it their own, keep the workspace current as the tool changes, and ask for help
without exposing data. Plan §15's P8 row: "Workspace template + `energatron init` / `--demo` / `upgrade` / `migrate`;
region profiles; `diagnose --redacted`; README quickstart, CONTRIBUTING, release workflow. Acceptance: a new user on a
clean Mac gets from README to a demo report in under 15 minutes; the demo end-to-end test runs in CI." "Must" means
required. D1 and the earlier specs stay binding; §12 lists every change this spec makes.

**What P8 builds**
- A **workspace template** in package data (`src/energatron/templates/workspace/`), rendered by pure core code (§3).
- `energatron init <dir>`: a new workspace from the template: git, uv, DVC, an optional local DVC remote, the private
  config, the denylist, the pipeline files, the hooks and a first commit (§4).
- `energatron demo` and `init --demo`: a workspace filled with synthetic meter data, a retailer usage export, gas bills
  and frozen offers, landed and run through the real commands to a recommendation (§5).
- `energatron upgrade`: refreshes the template-managed files of a workspace that `init` made (§6).
- `energatron migrate`: rewrites editable config files written at an older schema version (§7).
- `energatron diagnose`: a redacted support report: versions, workspace structure and state, no values (§8).
- Region profiles: the contribution path and the tests every profile must pass; SA stays the only profile (§9).
- README quickstart, `CONTRIBUTING.md`, `SECURITY.md`, a CI demo job and a release workflow (§10).

**Not in P8** (stay later or the owner's): making the GitHub repo public, pushing, tagging and publishing a release
(owner); PyPI publishing and a docs site (plan §14.7); other region profiles (by contribution, §9); workspace agent
skills (P9); a non-macOS notifier or scheduler.

**Facts it relies on** (checked 2026-11-14, structure only)
- The owner's workspace was set up by hand (D2 §11) and its hooks call `../energatron-5000/scripts/*.py`; a workspace
  elsewhere can't reach those scripts.
- `install_fields()` already maps a `vcs_info` install (`uv tool install git+…`, `uvx --from git+…`) to `install:
  "vcs"` with its commit, and an editable one to `install: "editable"` (D1 §11.2).
- `DvcRunner` runs `dvc` and `energatron stage` from the running interpreter's `bin` (D1 §4.5), so the pipeline must
  run inside the workspace's own venv, not inside an ephemeral `uvx` environment.
- The privacy guard rejects data-file extensions (`*.json`, `*.csv`, …) outside `tests/fixtures/` in the tool repo, so
  template files can't carry such extensions in package data.
- Every persisted editable config kind is still at version 1 except `params` (v6), which `pipeline sync` already
  rewrites (D1 §5).

---

## 2. Order of work

`/done` of P8.1.1 turns the rows into sub-tasks.

| # | Sub-task | Builds | Sections |
|---|---|---|---|
| 1 | P8.2.1 Template and `init` | `templates/workspace/`, `core/workspace_template.py`, `template_state` v1, `init_result` v1, `adapters/setup/`, `services/init.py`, the command | §3, §4 |
| 2 | P8.2.2 Demo | `services/demo.py` (generators and the build), `adapters/offers/frozen.py`, `demo_result` v1, `demo` and `init --demo`, `test_demo` | §5 |
| 3 | P8.2.3 `upgrade` and `migrate` | `services/upgrade.py`, `services/migrate.py`, `core/toml_text.py`, `upgrade_result` and `migrate_result` v1, the commands, `W_TEMPLATE_MODIFIED` | §6, §7 |
| 4 | P8.2.4 `diagnose` and region profiles | `services/diagnose.py`, `diagnose_result` v1, the command; `test_region_profiles` | §8, §9 |
| 5 | P8.2.5 Docs and release | README quickstart, `CONTRIBUTING.md`, `SECURITY.md`, CHANGELOG, CI demo job, `.github/workflows/release.yml`, issue template | §10 |
| 6 | P8.3.1 Acceptance (agent part) | the quickstart on this Mac in a fresh folder, timed; `diagnose`, `upgrade --dry-run`, `migrate --dry-run` on the real workspace | §13 |
| 7 | P8.3.2 Owner: publish | push, CI green, repo public, quickstart from GitHub on a clean Mac, tag the first release | §13 |

`W_TEMPLATE_MODIFIED` goes into `core/errors.py`, which every stage imports: P8.2.3 changes every stage's code entry
once, and the next `run` reruns all stages (same outputs).

---

## 3. The workspace template

### 3.1 Files

Package data under `src/energatron/templates/workspace/`. Every file ends in `.tmpl`, which is dropped when it is
written (so `settings.json.tmpl` → `.claude/settings.json`; the guard's data-file rule never sees a `.json` in the tool
repo). Dot-files are stored as `dot-<name>.tmpl` (`dot-gitignore.tmpl` → `.gitignore`).

| Written as | Class | Content |
|---|---|---|
| `pyproject.toml` | seed | D2 §11.1's, with `name` from the folder, `dependencies = ["energatron-5000"]`, the `[tool.uv.sources]` line from §4.2, `dev = ["dvc>=3.67.1", "pre-commit>=4.7", "nbstripout>=0.9.1"]` |
| `.python-version` | managed | `3.14` |
| `.gitignore` | managed | D2 §11.1's list plus P7's two entries; `dvc add` appends `/private` itself |
| `.dvcignore` | managed | `.DS_Store` |
| `.pre-commit-config.yaml` | managed | D2 §11.3's hooks and P1's `pipeline-sync` hook, calling `.energatron/scripts/` (§3.3) |
| `.energatron/scripts/privacy_guard.py`, `check_agent_links.py` | managed | byte copies of `scripts/` (§3.3) |
| `.energatron/workspace.toml` | seed | D2 §6.1 with the chosen region |
| `config/private.example.toml` | managed | D2 §6.3's template |
| `CLAUDE.md` (+ `AGENTS.md` → `CLAUDE.md`) | seed | generic workspace instructions: D2 §10.1's rules without the owner's names, and the P1-P7 commands |
| `.claude/settings.json` | seed | D2 §10.3's deny list for this workspace's own path (§3.2) |
| `README.md` | seed | what the folder is and the three commands to start: `status`, `land`, `run` |

- **Seed** files are written once by `init` and belong to the user afterwards. **Managed** files belong to the tool:
  `upgrade` rewrites them while the user hasn't edited them (§6).
- No `dvc.yaml` or `params.yaml` in the template: `init` runs `pipeline sync`, which writes them from `STAGES` and the
  region profile, so they can never drift from the golden file (replaces D1 §9.5's "templates/workspace/dvc.yaml must
  equal the golden file").

### 3.2 Rendering (`core/workspace_template.py`)

`render(values: TemplateValues, sources: Mapping[str, str]) -> list[TemplateFile]` is pure: `sources` maps each
template's name to its text (the adapter reads package data). `TemplateValues`: `name` (the folder name), `region`,
`tool_source` (the `[tool.uv.sources]` line, or None), `deny_root` (the workspace path for the deny rules: `~/…` when
under the home folder, else absolute; only in the user's own `.claude/settings.json`, never in the tool repo).
Placeholders are `{{name}}`, `{{region}}`, `{{tool_source}}`, `{{deny_root}}`; an unknown or unused placeholder is a
bug (`E_INTERNAL`). `TemplateFile`: `path`, `text`, `managed: bool`, `mode` (`0o644`, scripts `0o755`), and
`symlink_to` for `AGENTS.md`. `TEMPLATE_VERSION` (an int, starting at 1) is bumped with any template change.

### 3.3 Hooks in a templated workspace

The guard and the agent-link check stay stdlib-only and never import `energatron` (D2 §7, §9). The template carries
byte copies at `.energatron/scripts/`; a test (`test_template_scripts`) fails when they differ from `scripts/`, so the
copies are refreshed in the same change as the originals. The hooks call `uv run --frozen python
.energatron/scripts/privacy_guard.py --mode workspace …`. The owner's workspace keeps its `../energatron-5000/scripts/`
hooks (it wasn't made by `init`; §6).

### 3.4 `template_state` v1 (`.energatron/template.json`, git-tracked)

`{"kind": "template_state", "schema_version": 1, "template_version": 1, "files": {"<path>": "<sha256 of the bytes
written>"}}` for the managed files only. Written by `init` and `upgrade`; a workspace without it wasn't made by `init`. The privacy guard's
workspace mode allows this one JSON path, as it allows `.energatron/code_fingerprint.json` (built in P8.2.1).

---

## 4. `energatron init` (`services/init.py`, `adapters/setup/`)

### 4.1 Command

`energatron init [DIR] [--region sa] [--remote PATH] [--tool-source SOURCE] [--nmi …] [--meter-serial …]
[--postcode …] [--demo] [--no-sync] [--dry-run] [--json]`. `DIR` defaults to the current folder. Works outside a
workspace (D1 §8.3). It takes no lock (there is no workspace yet).

- On a terminal, a missing `--nmi` / `--meter-serial` is asked for (empty answer: skip); with `--json` or without a
  terminal it never prompts (D1 §8.6). Values given on the command line reach shell history: the README recommends the
  prompt. `--demo` uses synthetic values and refuses `--nmi`, `--meter-serial` and `--postcode` (`E_USAGE`).
- `DIR` must not exist, or be an empty folder; otherwise `E_USAGE` (`reason: "not_empty"`). Nothing is written then.
- `--region` must name a shipped profile (`E_INVALID_INPUT`, `field: "region"`).
- `--dry-run` reports the files it would write and the steps it would run, and writes nothing.

### 4.2 Tool source

The workspace's `[tool.uv.sources]` line comes from, in order: `--tool-source` (a folder → `{ path = "<abs>", editable
= true }`; a `git+https://…[@ref]` URL → `{ git = "…", rev = "<ref>" }`); else the running install: `vcs` → its URL
and commit; `editable` → its folder; else (an index install) no line, so uv resolves `energatron-5000` from the index.
`TemplateValues.tool_source` holds the rendered line.

### 4.3 Steps, in order

1. Check `DIR`, the region, the arguments; check that `git` and `uv` are on `PATH` (`E_SUBPROCESS`, `tool: …`).
2. Render the template and write it (seed and managed), create `data/landing/`, `.energatron/` and `private/`.
3. `git init -b main`. If git has no `user.email` / `user.name` for this repo, set repo-local ones (`energatron
   workspace` / `workspace@localhost`): the history never leaves the machine (D2 §11.4). Reported in the result.
4. `uv sync` in `DIR` (skipped with `--no-sync`, which the tests use: their venv already has the tool and DVC).
5. `dvc init -q` with the workspace venv's `dvc` (or, with `--no-sync`, the running interpreter's).
6. `--remote PATH`: `dvc remote add --local -d backup <PATH>` (a local folder, as D2 §8.1 needs; created if missing).
   `.energatron/local.toml` with `backup.bundle_dir` = `<PATH>/../<name>-git` is written too. Without it, `status`
   and `backup` say the backup isn't configured, as today.
7. Private config: with an NMI (prompted, given, or the demo's), write `private/config.toml` (a fresh salt from
   `secrets`, the values, nothing else) and run the `denylist` service (it `dvc add`s `private/`). Without one, the
   result says to copy `config/private.example.toml` and run `energatron denylist` (`next_steps`).
8. `pipeline sync` (the service): `dvc.yaml`, `params.yaml`, catalogs, folders.
9. `pre-commit install` (the venv's), unless `--no-sync`.
10. Write `.energatron/template.json` and commit every written path in one commit `init: energatron workspace`.
11. `--demo`: run `uv run --frozen energatron demo --json` in `DIR` (in-process with `--no-sync`) and include its
    result (§5).

A failure after step 2 leaves the folder for inspection and names the step (`E_SUBPROCESS` with `step`); `init` never
deletes anything.

### 4.4 `init_result` v1

`root_name` (the folder's name, never its path), `region`, `tool_source_kind` (`path`/`git`/`index`), `files`
(written paths), `steps` (`{name, status: "done"|"skipped"}`), `git_identity_set: bool`, `private_config: bool`,
`demo: demo_result | null`, `dry_run`, `next_steps: list[str]`.

---

## 5. The demo (`services/demo.py`, `adapters/offers/frozen.py`)

### 5.1 `energatron demo`

Runs inside a workspace whose private config holds the demo's synthetic NMI (`DEMO_NMI`, checksum-valid, made up),
else `E_USAGE` (`reason: "not_demo"`): it must never mix synthetic files into a real workspace. Takes the lock for
its own writes; `land` and `run` take it in turn. Steps:

1. Generate the files into `data/landing/demo/` (§5.2), deterministic from a fixed seed.
2. `land` the meter file, the usage export and the gas bill PDFs (`--source gas_bill`); then generate the gas bill
   records naming the landed PDF IDs and `land` them.
3. Fetch the frozen offers: `fetch_offers` with the frozen provider in place of `cdr` and `retailers = ["demo"]`.
4. `run` (not a draft), and return the run ID, the recommendation ID and the report paths. The meter data spans 14
   whole months, so the default twelve-month reference window fits and `params.yaml` stays as `pipeline sync` wrote
   it (built so in P8.2.2; no `demo: params` commit). The demo's own generated copies are removed from the inbox.

`demo_result` v1: `landed` (count), `offers` (count), `run_id`, `recommendation_id`, `reports` (relative paths of the
two HTML pages), `commits` (count).

### 5.2 What it generates (all made up, generated by code)

- One SAPN NEM12 file, 30-minute intervals, register `E1`, 14 local months ending before a fixed date, with a daily
  shape (morning and evening peaks), named as SAPN names its downloads with `DEMO_NMI`.
- A retailer usage export with monthly reads, computed from the meter file under `nem_aest` with the region's TOU
  windows, so `validate_timebase` confirms the configured basis and `localise` runs.
- Twelve monthly gas bill PDFs (one page each, dated by their creation date) and their `gas_bill_record` YAML,
  consistent so every `gas_bills` check passes.
- A frozen offer set: three electricity plans (single rate, time of use, one with a fee) and two gas plans, from two
  made-up brands, for the region's distributors, with fixed `lastUpdated` dates. Plan IDs end in `@DEMO`.

### 5.3 The frozen provider

`FrozenProvider(documents)` implements `OfferProvider` over in-memory list and detail bodies. Its `name` is `cdr`, so
the fetch log reads as a normal fetch; its pages carry a `self` link of `demo://plans`. It never touches the network.

### 5.4 `test_demo` (integration, in CI)

`init --demo --no-sync` in a scratch folder (the test venv runs DVC and the stages), then: the run's manifest names the
recommendation; both HTML pages exist; `status` shows no `W_RUN_BEHIND`; `verify` passes; the workspace is clean; the
privacy guard over the workspace reports 0 violations. A second test checks `demo` refuses a non-demo workspace.

---

## 6. `energatron upgrade` (`services/upgrade.py`)

`energatron upgrade [--force] [--dry-run] [--json]`, in a workspace, under the lock.

1. No `.energatron/template.json` → `E_USAGE` (`reason: "not_templated"`): a hand-built workspace is upgraded by hand.
2. Render the current template with the workspace's values (name, region from `workspace.toml`, `deny_root`; the tool
   source isn't re-rendered: `pyproject.toml` is a seed file).
3. For each managed file: `unchanged` (same bytes as the new render); `updated` (the file's hash equals the recorded
   one, so the user hasn't edited it: rewrite it); `modified` (edited by the user: left alone with
   `W_TEMPLATE_MODIFIED`, unless `--force`); `added` (new in the template); `removed` from the template: left in place
   and reported.
4. Write the new `template.json` and commit the written paths (`upgrade: template v<N>`). Nothing written → no commit.

`upgrade_result` v1: `from_version`, `to_version`, `files: [{path, status}]`, `commit`, `dry_run`. The README's "After a
tool update" runs `upgrade`, `migrate`, `pipeline sync`, then `run`.

---

## 7. `energatron migrate` (`services/migrate.py`)

`energatron migrate [--dry-run] [--json]`, in a workspace, under the lock. Rewrites the editable config files of D1
§5's table whose `schema_version` is older than the tool's: `.energatron/workspace.toml`, `private/config.toml`,
`config/offer_rules.local/*.yaml`, `config/bundle_rules.local/*.yaml`. `params.yaml` stays `pipeline sync`'s.

- Each file is read, upgraded through the migration chain (`upgrade()`), validated and written back: TOML through
  `core/toml_text.py` (a pure writer for the shapes these models use: strings, ints, bools, lists of strings, nested
  tables), YAML through `yaml.safe_dump` with `kind` and `schema_version` first. Comments are not kept; `--dry-run`
  lists the files first.
- A newer file → `E_SCHEMA_TOO_NEW`; an invalid file → `E_INVALID_INPUT` naming the path, never a value.
- A rewritten `private/config.toml` is never printed; the `denylist` service then refreshes the denylist and `dvc
  add`s `private/`.
- One commit `migrate: <n> files` with exactly the rewritten paths (and `private.dvc`). Nothing older → no commit.

`migrate_result` v1: `files: [{path, kind, from_version, to_version}]`, `commit`, `dry_run`.

---

## 8. `energatron diagnose` (`services/diagnose.py`)

Read-only, no lock; agents may run it. Plan §14.4's `diagnose --redacted` becomes the only mode: the report is built
from structure, never from values, and then passed through the denylist redaction like every envelope (D1 §8.7).
`--redacted` is accepted and does nothing more, so the plan's command line works.

`diagnose_result` v1:
- `tool`: version, install kind, whether the tool commit is dirty, Python, platform (`sys.platform`, machine), the
  versions of git, uv and DVC (`None` when missing).
- `workspace`: region, template version (or `None`), kind and schema version of each config file and whether it is
  current, `pipeline sync --check` drift (paths), stale stages (names), catalog record counts per source type, counts
  of runs and decisions, the newest run's ID and whether it was a draft, `private_config` present (never its fields).
- `status`: the reminder warning codes `status` would give.
- `problems`: one line per failed check above, with its error code.

Never included: file names under `data/`, catalog `original_filename`s or notes, dataset IDs, plan names, kWh or $
figures, paths outside the workspace. The human output says to paste the JSON into an issue and to read it first.

---

## 9. Region profiles

SA stays the only profile in P8 (plan §14.7). What P8 adds:
- `test_region_profiles`: every `regions/*.yaml` loads through `load_region_profile`, its windows partition the day,
  its zone is in `tzdata`, its parsers exist, and `init --region <name> --dry-run` renders with it.
- `CONTRIBUTING.md` §"Adding a region": the profile's fields, where the distributor names and CDR base URIs come from
  (the AER's list), the synthetic fixture it needs, and that a region PR holds no real data.
- `init --region` lists the shipped profiles in its help and error.

---

## 10. Docs and release

- **README:** a "Quickstart (demo)" first: install uv, `uvx --from git+https://github.com/<owner>/energatron-5000
  energatron init --demo ~/energatron-demo`, `cd`, `uv run energatron status`, open the two reports; then "Your own
  workspace" (`init` with `--remote`, the private config prompt, landing a first download) before the existing
  runbooks. The P0 "set up by hand" step goes. `test_readme` keeps checking every command line.
- **`CONTRIBUTING.md`:** never attach your own data (use `diagnose`), the checks, tests-first, the layer rules,
  synthetic fixtures only, adding a region, adding a parser, the CHANGELOG, commit messages without figures.
- **`SECURITY.md`:** report privately (GitHub security advisories); data never leaves the machine by design.
- **`.github/ISSUE_TEMPLATE/bug_report.md`:** asks for `energatron diagnose --json` output and forbids data files.
- **CI:** a second job, `demo`, on `macos-latest`: `uv run energatron init --demo "$RUNNER_TEMP/demo" --tool-source
  "$GITHUB_WORKSPACE"`, then `uv run energatron status --json` and the two report files exist. The `check` job's
  pytest already runs `test_demo`.
- **Release workflow** (`.github/workflows/release.yml`): on a `v*` tag: the checks, `uv build`, a check that the tag
  equals `project.version`, and a GitHub release with the wheel, the sdist and the tag's CHANGELOG section. No PyPI.
  SemVer; from P8 on, renaming a command, option or JSON field needs a deprecation release first (D1 §8.1).
- **Version:** stays `0.1.0.dev0` until the owner tags; the release checklist in `CONTRIBUTING.md` says how to cut one.

---

## 11. Kinds, codes and warnings added in P8

- Kinds (v1, golden schemas): `template_state`, `init_result`, `demo_result`, `upgrade_result`, `migrate_result`,
  `diagnose_result`.
- Codes: none new (`E_USAGE`, `E_SUBPROCESS`, `E_INVALID_INPUT`, `E_SCHEMA_TOO_NEW` cover P8).
- Warnings: `W_TEMPLATE_MODIFIED`.
- CLI: `init`, `demo`, `upgrade`, `migrate`, `diagnose`, each with `--json`. Locks: `demo`, `upgrade`, `migrate` take
  it; `init` (no workspace yet) and `diagnose` (read-only) don't.
- Workspace files: `.energatron/template.json` (tracked), `.energatron/scripts/` (tracked).

**Tests:** `test_workspace_template` (render: placeholders, classes, modes, the symlink, unknown placeholder);
`test_template_scripts` (byte copies); `test_init` (service with a fake setup adapter: steps in order, not-empty
refusal, dry run writes nothing, tool-source kinds, identity, no private config → next step; the CLI never prompts
with `--json`); `test_init_cli` (integration, `--no-sync`: a real git/DVC workspace that `status` accepts, guard
clean); `test_demo` (§5.4); `test_frozen_provider`; `test_upgrade` (each file status, `--force`, not templated, dry
run); `test_toml_text` (round trip through `tomllib` for every shape, escaping); `test_migrate` (a v1 document under a
test-registered v2 migration is rewritten and committed; a current workspace → no commit; too new; private config
values never in the result); `test_diagnose` (structure only: a fixture workspace's report holds none of the
synthetic identifiers, file names or figures); `test_region_profiles` (§9); CLI one-document checks (D1 §13).

---

## 12. Plan and D1 changes

To be applied on `/done` of P8.1.1, as plan rev 17 and D1 rev 9.

**Plan**
- §14.3: template updates come through a built-in `energatron upgrade` (managed files tracked by hash), not copier.
- §14.4: `uvx --from git+…` (or `uv tool install`) then `energatron init`; the workspace pins the same tool commit;
  `diagnose` is always redacted; the demo uses a frozen offer set; SA remains the only region in P8.
- §15 P8: made concrete as this spec's §13.

**D1 (rev 9)**
- §2: core gains `workspace_template.py`, `toml_text.py`; services gain `init.py`, `demo.py`, `upgrade.py`,
  `migrate.py`, `diagnose.py` (`compare.py`, `explain.py` move to P9+); ports gain `setup.py` (`WorkspaceSetup`, the
  set-up subprocesses as `init` needs them; added in P8.2.1); adapters gain `setup/` (git, uv, DVC and
  pre-commit set-up subprocesses) and `offers/frozen.py`; `templates/workspace/` holds `.tmpl` files.
- §8.3: `init` works outside a workspace. §8.8: `demo`, `upgrade` take the lock; `diagnose` doesn't.
- §9.5: the template has no `dvc.yaml`; `init` runs `pipeline sync` (replaces "templates/workspace/dvc.yaml must
  equal the golden file").
- §7: `W_TEMPLATE_MODIFIED`.

---

## 13. P8 acceptance checks

**Agent part (P8.3.1)**, on this Mac, with the tool committed:
1. The README quickstart in a fresh folder outside `~/WorkSpace/Electricity`, with `--tool-source` pointing at the
   local clone (the repo isn't public yet): `init --demo` ends with a recommendation and both reports; timed, under
   15 minutes from an empty uv cache is the owner's check (P8.3.2), here the wall time is recorded.
2. In the demo workspace: `status` (no `W_RUN_BEHIND`), `verify` ok, `diagnose --json` holds no synthetic identifier,
   `upgrade --dry-run` all `unchanged`, `migrate --dry-run` nothing to do; the guard scans 0 violations.
3. In the real workspace: `diagnose --json` (the guard finds no denylist entry in it), `migrate --dry-run` nothing to
   do, `upgrade --dry-run` refused as `not_templated`. Nothing is committed there.
4. The workflow files parse as YAML, and the demo job's commands are the README's.

**Owner part (P8.3.2)**
5. Push; CI's `check` and `demo` jobs pass.
6. Make the GitHub repo public (plan §16 Q10).
7. **Plan §15:** on a clean Mac (or a new macOS user), follow the README quickstart from GitHub to the demo report in
   under 15 minutes.
8. Optional: bump the version, tag `v0.1.0`, and see the release workflow publish the release.

---

## 14. Open questions / decisions for the owner

Each is decided here so P8 can be built; the recommendation is what the spec says.
1. **`upgrade` is built in**, not copier: no new dependency, and only files the user hasn't edited are rewritten.
2. **Hooks use copies of the scripts in the workspace** (`.energatron/scripts/`), kept equal by a test, so the guard
   still never imports the package; the owner's workspace keeps its sibling-repo hooks.
3. **The demo's offers are frozen and generated by code**, not fetched: CI must not depend on the AER, and the tool
   repo can't hold JSON data files.
4. **`diagnose` is always redacted**; `--redacted` is accepted for the plan's command line.
5. **No new region profiles in P8**; a contribution path and the profile tests instead.
6. **`init` sets a repo-local git identity only when none is configured**, so a clean Mac can commit.
7. **The workspace pins the tool `init` ran from** (the same git commit or folder), so the demo is reproducible.
8. **`demo` refuses a workspace whose NMI isn't the demo's**, so synthetic files never enter real data.
9. **`migrate` doesn't keep comments** in the files it rewrites; it lists them in a dry run first and commits them.
10. **No release is cut by an agent**: the version stays a dev version; tagging and publishing are the owner's.
