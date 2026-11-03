# Energy Usage & Offer Comparison — Data Pipeline Plan

- **Created:** 2026-10-23
- **Status:** Draft for review
- **Revisions:** 2026-10-23 rev 2 — designed for later expansion to other households, AI agents and a web app
  (tool repo / workspace split, layered core; see §14).
  2026-10-23 rev 3 — repos named `energatron-5000` (tool) and `demq-elektric` (workspace), both sub-folders of
  `~/WorkSpace/Electricity/`; Claude-centric agent setup that Codex also reads (§14.5).
  2026-10-23 rev 4 — license decided: Apache-2.0.
  2026-10-23 rev 5 — before-P0 answers: the workspace git history stays local (no GitHub) and is backed up as
  timestamped `git bundle` files in `iCloud Drive/Backups/demq-elektric-git/` (never an rsync of `.git`); the
  tool repo is a private GitHub repo until P8; iCloud Advanced Data Protection is on; Python is pinned to 3.14
  (3.15 fails because DVC's `pygit2` dependency has no 3.15 wheel). With no git remote, DVC's pre-push hook never
  fires, so backups need an explicit trigger. See the P0 bootstrap spec.
  2026-10-23 rev 6 — language rule (owner): everything is Python, with environments and tools managed by uv;
  another language (e.g. TypeScript on Node) only where Python is the wrong tool, such as the P10 frontend, kept
  in its own directory and holding no analysis logic (§4, §14.6).
  2026-10-23 rev 7 — core architecture spec (D1, `2026-10-23_core-architecture-spec.md`) approved, and its §15
  edits applied: `VersionControl` and `Clock` ports; stage outputs are directories with `_provenance.json`;
  stage bodies live in `services/stages/`; per-stage code fingerprints replace the whole-package fingerprint and
  the `uv.lock` dependency; provenance holds no timestamps, run IDs or tool commit; the run ID and `--draft` live
  only in the run manifest; dataset IDs use the full source type; `landed_at` is UTC; `kind` beside
  `schema_version`; `.energatron/` workspace files; git and DVC as subprocesses; no pre-push `dvc push`;
  `energatron verify` in the restore drill (§4, §6, §7, §8.3, §8.4, §9.2–§9.4, §12, §14.1, §14.2, §15, §17).
  2026-10-23 rev 8 — P0 bootstrap spec (D2, `2026-10-23_p0-bootstrap-spec.md`) approved, and its §15 edits
  applied: `pyright[nodejs]` (no system Node), `pandas-stubs`, DVC also a tool-repo dev dependency,
  `python-preference = "only-managed"`, jupyterlab in the workspace `dev` group; a generated plus a manual
  denylist file, found by the tool repo through `git config energatron.denylist`; DVC hooks as local
  pre-commit hooks (`post-checkout` only); bundle naming and retention; backups and restore drills are
  owner-run through `scripts/icloud_backup.py`, agents never get iCloud access and only read
  `energatron backup --status`, and `run`/`land` end with `W_BACKUP_PENDING` instead of backing up; the parent
  folder's agent files are symlinks made by `link_parent.py` (§4, §5, §6, §7, §9.4, §12, §15).
  2026-10-23 rev 9 — P1 landing & catalog spec (D3, `2026-10-23_p1-landing-catalog-spec.md`) approved, and its
  §17 edits applied: `source_ts` per source type (retailer exports: latest datetime in the content; bills: PDF
  creation date); the catalog record drops `landed_by_commit` (the `land: <ids>` commit message is the link),
  adds `file_mtime` and `detector.evidence`; the NMI check becomes a premises check (NMI, meter serials,
  accounts); a multi-file `land` lands what it can (exit 8) and a foreign file aborts it; `offer_cdr` re-fetches
  with the same snapshot ID are no-ops; `marts/catalog/` leaves out file names and notes; `energatron pipeline
  sync` writes `dvc.yaml`, `params.yaml` and empty catalogs, and its `--check` is a workspace pre-commit hook;
  `params.yaml` carries `kind`/`schema_version` and gains keys phase by phase (§7, §8.2, §8.3, §9.2, §12, §15).
  2026-10-30 rev 10 — P2 meter pipeline spec (`2026-10-30_p2-meter-pipeline-spec.md`) approved, and its §16
  edits applied: `localise` also depends on `validate_timebase`'s report and builds local-time data only from a
  `confirmed` verdict (hand-over decision #11 unchanged); `validate_meter` warns rather than fails without
  SUMMARY or on gaps; `validate_timebase` fails only when another basis beats the configured one and warns when
  the evidence is short or tied; interval tables have no NMI column and are keyed by `(suffix, meter_start)`;
  merge per day; the retailer read-date alignment; `params` v2 (`meter.*`, `validation.meter`,
  `validation.timebase`); the time zone and TOU windows live in the region profile and `pipeline sync` copies
  them into `params.yaml`; the meter-file formats P2 reads are core modules, not registry plugins, until a
  second format arrives; `tzdata` as a runtime dependency (§4, §9.1, §9.2, §10.1, §10.2, §14.1).
  2026-11-03 rev 11 — P3 usage profile spec (`2026-11-03_p3-usage-profile-spec.md`) approved, and its §15
  edits applied: two stages, `usage_profile` (marts) and `usage_report` (a self-contained HTML built from the
  marts alone); `params` v3 with the `analysis` block (reference window, coverage threshold, seasons, weekend,
  band percentiles, base-load window, demand block, date-ranged TOU window sets, the first copied from the
  region profile by `pipeline sync`); `sensitivity` moves to P6; the metric definitions made concrete
  (§9.1, §9.2, §10.3, §14.1, §15).
- **Scope:** Electricity usage analysis from SA Power Networks (SAPN) interval data, gas usage from monthly bills,
  and comparison of retail offers (electricity, gas, and electricity + gas combos), with end-to-end traceability
  from every recommendation back to the exact data, offers and code that produced it.
- **Privacy note:** This document is meant to live in the public tool repo (§7). It deliberately contains no NMI,
  meter serial, account numbers, address, postcode or usage figures. Keep it (and every other doc) that way.

---

## 1. Proposal in one page

1. **Two repos, Git + DVC, no server.** Both live as sub-folders of `~/WorkSpace/Electricity/`. The
   public-ready **tool repo**, `energatron-5000`, holds the code: Python package, CLI, workspace template,
   docs. Your **workspace**, `demq-elektric`, holds the pipeline config and *hash pointers* (`*.dvc`,
   `dvc.lock`); its git history can go to a private GitHub repo. DVC manages every byte of data: a
   local cache plus a DVC "local remote" folder in iCloud Drive as the backup.
2. **Everything is a landed dataset.** Meter downloads, bills, bill transcriptions and offer snapshots enter
   through one CLI command. It hashes the file, assigns a dataset ID, appends a catalog record and stores the
   file immutably under a neutral name (no NMI in filenames).
3. **Reproducible pipeline.** `dvc.yaml` turns raw → staged → interim → marts → reports. `dvc repro` reruns only
   stale stages; the git commit that contains `dvc.lock` links that code version to the data versions it used.
4. **Offers are versioned snapshots.** Offers come from the AER's public Consumer Data Right (CDR) Energy Product
   Reference Data API (verified working on 2026-10-23, no auth) and are stored verbatim. Manual offers (quotes,
   promos, PDFs) go through the same path. Each offer gets a **snapshot ID** = `planId` + `lastUpdated` +
   content hash.
5. **Recommendations and decisions have IDs.** A recommendation ID is derived from its content and cites the
   offer snapshot IDs, the usage dataset IDs, the run ID and the commit. A decision ("switched to X") is logged
   against a recommendation ID. Bills that arrive after the switch then show actual vs projected savings.
6. **Built to expand, not built out.** A pure core with thin adapters (CLI now; agent tools and a web API
   later), storage behind small interfaces, region specifics in config, and versioned schemas. Other
   households, AI agents and a web app can then be added later without rework (§14).

---

## 2. Goals and non-goals

**Goals**
1. Understand usage: average hourly profile, daily and monthly totals, seasonal patterns, time-of-use (TOU)
   split, base load, peak demand, data quality.
2. Price any electricity, gas or combined offer against *actual* 5-minute usage and actual gas bills. Rank the
   offers and project annual cost and savings against the current plan.
3. Treat every input (meter download, bill, offer) as a dated, catalogued, immutable dataset.
4. Full lineage: decision → recommendation → exact offer snapshots + usage datasets → code commit + params.
5. Code is public-safe on GitHub. All personal data stays local, backed up to iCloud Drive only.
6. Keep the design open to other households (install from GitHub, then `energatron init`), to AI agents operating
   it, and to a later web app, without building any of that now.

**Non-goals (for now)**
- Automated meter-data retrieval. CDR consumer-data APIs need accreditation, so SAPN downloads stay manual.
- Solar, battery or EV payback modelling. The legacy viewer notebook has a solar model. The engine leaves room
  for feed-in tariffs (FiT) and export, but the meter has no export register today.
- Building the multi-user, agent (MCP) or web features now. Only the seams for them are built (§14).

---

## 3. What exists today

### 3.1 Inventory (paths as found on 2026-10-23, before the reorganisation in §7)

| Path | Content | Notes |
|---|---|---|
| `data/raw/` | Two SAPN `*_SAPN_DETAILED.csv` interval downloads (2-year windows, generated 2025-10 and 2026-10) | Filenames start with the NMI |
| `legacy/notebooks/Meter_NEM12_to_JSON.ipynb` | NEM12 → hourly JSON, TOU split, hand-entered retailer rates, monthly cost comparisons | Saved outputs contain usage data |
| `legacy/notebooks/Meter_NEM12_Analyser.ipynb` | Refactor of the above, with plots and bill-period cost checks | Same |
| `legacy/notebooks/Electricity-Viewer.ipynb` | Older bill-level analysis and a solar payback model | References older (2022–2025) SAPN downloads that are not in this folder |
| `legacy/Electricity-Analysis/data/` | One SAPN DETAILED file (likely a byte-duplicate of one in `data/raw/`), SAPN SUMMARY CSV/PDF (monthly totals), derived hourly JSON | SUMMARY is a validation source |
| `legacy/Electricity-Analysis/raa/` | Retailer usage export (monthly Peak / Off-peak / Shoulder kWh) and transactions (invoices, payments) | Key for backtesting and for checking the meter time basis |
| `legacy/Electricity-Analysis/plots/` | PNG charts | Regenerated by the new pipeline |

### 3.2 Observations about the raw meter files (NEM12 format)

- No `100` header record: files start at `200` and end with `900`.
- `200` record fields: NMI, NMI configuration, register `E1`, suffix `E1`, meter serial, unit `KWH`, interval
  length `05` (minutes). There is a consumption register only, with no export (`B1`), so there is no solar
  today.
- `300` record fields: date, 288 interval values, QualityMethod (`A` actual, or `V` variable, meaning
  per-interval quality follows in `400` records), ReasonCode, ReasonDescription, **UpdateDateTime** and
  MSATSLoadDateTime. UpdateDateTime is set per day, which gives a clean rule for picking between overlapping
  versions of the same day.
- `400` records carry substitution and estimate flags (e.g. `F17`, `F19` = final substituted, with a reason
  code).
- The newer download repeats the `200` header 8 times and contains blank lines. The parser must tolerate both.
- The two downloads overlap by 347 days. SAPN only serves the last 2 years per request, so history only grows if
  every download is kept and merged.

### 3.3 Legacy logic to port, and issues to fix

**Port:** 5-min → hourly roll-up, TOU bucketing (peak 06–10 and 15–01, off-peak 01–06, shoulder 10–15, which
matches the current GloBird SAPN windows), period cost calculations, monthly and rolling comparisons, plots.

**Fix:**
- No time-zone handling: the meter clock and the tariff clock differ (see §10.2).
- Quality flags are ignored, and the malformed-row threshold is hard-coded (`< 103` fields).
- Only the latest file is used; there is no merge across downloads.
- Retailer rates are typed in by hand, with mixed units (cents vs dollars), inconsistent GST treatment, and no
  record of where or when the rates came from. This is the traceability gap this plan closes.
- Bugs: `print_usage()` reads the global `en` instead of its parameter, `calc_prices` reads a global
  `dict_input`, and a July 2024 imputation is hard-coded.
- Notebook outputs contain usage numbers and paths that include the NMI, so these notebooks must never be
  committed as they are.

---

## 4. Tooling decision

**Recommendation: Git + DVC, with the DVC remote in iCloud Drive.**

| Option | Verdict | Reasoning |
|---|---|---|
| **DVC** | ✅ Chosen | Git-native: `.dvc` and `dvc.lock` hold only hashes, sizes and paths. Built-in pipelines (`dvc.yaml`, `dvc repro`, params, metrics) detect stale stages and give code↔data lineage through git history. A "local remote" is just a folder, so iCloud works without a server. Data stays plain files addressed by md5, so moving off DVC later is easy. Now stewarded by lakeFS (acquired Nov 2025) and positioned for individuals and small datasets, which is exactly this case. |
| lakeFS | ❌ | Needs a running server, an object store and a metadata DB. Built for data lakes, doesn't version code, and is awkward to back up to a consumer sync folder. |
| git-annex / DataLad | ⚠️ Viable | Strong provenance (`datalad run`), and a directory special remote works with iCloud. But it uses symlinked worktrees, has a steeper learning curve, and its pipeline/params/metrics story is weaker. |
| Git LFS | ❌ | Needs an LFS server, and GitHub LFS would put personal data on GitHub. |
| Plain git for data (private repo in iCloud) | ❌ | iCloud sync corrupts git internals (conflicted `index 2` files etc.), and there are no pipeline semantics. |
| Dolt, DuckLake, etc. | ❌ | Table-versioning databases. Raw PDFs, CSVs and JSON don't fit, and it's overkill. |

**Stack (all pinned in `uv.lock`):**
- uv-managed project pinned to Python 3.14, using a uv-managed interpreter rather than Homebrew's. Verified
  2026-10-23: DVC 3.67, pandas 3.1, pyarrow 26 and duckdb 2.0 work on 3.13 and 3.14; 3.15 fails (no `pygit2`
  wheel).
- **Language rule (owner, 2026-10-23):** everything is Python, managed by uv. That covers the package, pipeline
  stages, analysis, notebooks, scripts and dev tooling.
  - Environments are created with `uv sync`, commands run with `uv run`, and dependencies change only through
    `uv add`.
  - `uv.lock` is committed. No pip, conda or global installs.
  - Another language only where Python is the wrong tool, such as a browser frontend (P10). It lives in its own
    directory and talks to the Python services through the HTTP API. All analysis, pricing and ID logic stays in
    Python.
- DVC as a *dev dependency* of the workspace, so the DVC version is pinned too. The `energatron` package calls
  git and DVC as subprocesses and doesn't depend on DVC.
- pandas + pyarrow (Parquet), pydantic v2 (schemas), typer (CLI), httpx (API client), matplotlib/plotly
  (reports), duckdb (optional ad-hoc SQL over Parquet), pdfplumber (later, for bill PDFs).
- `tzdata` (from P2) as an explicit runtime dependency, so stages read time-zone rules from a pinned
  distribution rather than the system database (rev 10).
- pytest + hypothesis, ruff, pyright, pre-commit, nbstripout, all dev dependencies run through `uv run`.
  - pyright is installed as `pyright[nodejs]`: Node comes as a wheel inside the venv, so no system Node is
    needed. `pandas-stubs` gives it pandas types.
  - The tool repo also has DVC as a dev dependency, for the VCS adapter and restore-drill tests.
  - jupyterlab is in the workspace's `dev` group.
  - Both repos set `python-preference = "only-managed"`.
- Added only when their phase starts: copier (workspace template updates, P8), the MCP Python SDK (agent
  tools, P9), FastAPI (local web UI, P10).

---

## 5. Privacy boundary

**Rule: the tool repo holds code only. The workspace repo holds config without personal values, and hashes.
Everything else is managed by DVC.**

| Item | Lives in | On GitHub? |
|---|---|---|
| Package code, tests (synthetic fixtures only), workspace template, region profiles, curated offer rules, `agent_docs/` | Tool repo | Yes (public-ready) |
| `dvc.yaml`, `params.yaml`, `*.dvc`, `dvc.lock`, `.dvc/config` (no remote URL), `.energatron/code_fingerprint.json` | Workspace repo | Optional, **private** repo only |
| `.dvc/config.local` (backup remote path) | Workspace, local only (DVC git-ignores it by default) | No |
| `data/raw/**`, `data/staged…reports/**`, `data/runs/`, `data/decisions/` | DVC → iCloud remote | No |
| `private/config.toml` (NMI, postcode, network tariff, gas MIRN, current plans), `private/denylist.generated.txt`, `private/denylist.txt` | DVC → iCloud remote | No |
| `legacy/` | DVC (archived as one directory) | No |
| `data/landing/` (inbox) | Workspace, local only, git-ignored | No |

**Ways data could leak, and the mitigation for each:**
1. **Filenames in pointer files.** SAPN filenames start with the NMI. Track *directories* (one `.dvc` per
   source directory) so only a `.dir` hash reaches git, **and** rename files on landing to neutral dataset IDs.
2. **Params in `dvc.lock`.** DVC writes declared `params` *values* into `dvc.lock`, so personal values never go
   in `params.yaml`. Private config is declared as a `deps` entry, which records only its hash. Add a random
   `salt` line to `private/config.toml` so its md5 can't be brute-forced from a small value space (e.g. a
   4-digit postcode).
3. **Metrics and plots.** DVC keeps metric files in git when `cache: false` is set. Never set `cache: false` on
   any output.
4. **Notebooks.** An `nbstripout` pre-commit hook strips outputs. Notebooks only read marts, and reports are DVC
   outputs, not notebook outputs.
5. **Test fixtures.** Synthetic NEM12, bill and offer fixtures are generated by code. Nothing is derived from
   real files.
6. **Free text.** Commit messages, docs, issues and tags never contain amounts, NMI or addresses. Dataset, run
   and recommendation IDs are opaque by design, so they are safe to use in commit messages and tags.
7. **Guard rails.**
   - A pre-commit hook, `scripts/privacy_guard.py`, rejects staged changes that contain any denylist entry
     (NMI with and without its checksum digit, meter serial, account and customer numbers, MIRN, street,
     postcode). The denylist is two files: `private/denylist.generated.txt`, written by `energatron denylist`
     from `private/config.toml`, and an optional hand-written `private/denylist.txt`. So other users get the
     same protection without having to know about it.
   - The tool repo finds the workspace's denylist through local `git config energatron.denylist`. The guard's
     modes and rules are those in the P0 spec §7. Figure detection (kWh, $, postcode-like numbers) is a
     report for a human, not a blocker.
   - It also rejects data-type files (`*.csv *.parquet *.pdf *.json *.xlsx`) outside `tests/fixtures/` and
     `config/`.
   - Tool-repo CI repeats the file-type check and also rejects any `*.dvc` or `dvc.lock`. It cannot see the
     denylist. `gitleaks` is optional.
8. **GitHub visibility.** The tool repo never contains workspace files, so it can be public from its first
   commit (audit `git log -p` before making it public anyway). If you push the workspace repo, keep it
   **private**: even hash-only history shows when you landed data and compared offers.

---

## 6. Storage and backup topology

```
~/WorkSpace/Electricity/                  (plain folder; NOT inside iCloud Drive)
 ├─ energatron-5000/   tool repo ──────── git push ──►  GitHub: energatron-5000 (public-ready, code only)
 │        ▲ editable install
 └─ demq-elektric/     workspace repo ─── git push ──►  GitHub: demq-elektric (private; hashes + config; optional)
     ├─ .dvc/cache   (APFS reflinks → no duplicate disk use)
     └─ data/  private/  legacy/
              │  dvc push / dvc pull
              ▼
~/Library/Mobile Documents/com~apple~CloudDocs/Backups/demq-elektric-dvc/
              (content-addressed files/md5/xx/…; write-once)
              │  iCloud sync
              ▼
           iCloud
```

- Keep both repos and the DVC cache **outside** iCloud Drive. Only the DVC remote lives there; its files are
  write-once and content-addressed, so they never conflict during sync.
- The workspace repo isn't pushed anywhere (rev 5), so its git history is backed up as verified `git bundle`
  files in `Backups/demq-elektric-git/`, named `<utc>-<sha7>.bundle`. Retention keeps the newest `keep_latest`
  bundles plus the newest one of every month. The backup itself is `VersionControl.backup()`: `dvc push`,
  then a verified bundle.
- **Backups are owner-run** (owner decision 2026-10-23). Agents never get iCloud access: macOS privacy control
  (TCC) on their host app stays off. The owner runs `scripts/icloud_backup.py` (`check`, `step0`, `backup`,
  `drill`) in their own terminal, following the iCloud backup runbook. Agents only read
  `energatron backup --status`, and `/done` reports a pending backup.
- The Step 0 safety copy is `Backups/demq-elektric-step0-<date>/`, written by `icloud_backup.py step0`.
- Configure the remote locally only: `dvc remote add -d --local icloud "<path above>"`.
- In Finder, mark the remote folder **Keep Downloaded**. The disk is ~97% full, and with "Optimise Mac Storage"
  on, macOS evicts iCloud files to dataless placeholders.
- DVC's hooks are local pre-commit hooks run through `uv run`, and only `post-checkout` is installed. There's
  no `dvc install --use-pre-commit-tool`: it would pip-install a second DVC. With no git remote, a pre-push
  hook would never fire anyway; backups run only as described above.
- Consider turning on iCloud **Advanced Data Protection** (end-to-end encryption), since this is personal data.
- Never run `dvc gc --cloud` against the remote; it can delete history. Before pruning anything, run a restore
  drill and use `--all-commits --all-tags`.
- **Restore drill** (Phase 0 acceptance criterion, then quarterly; owner-run through `icloud_backup.py drill`,
  which runs `scripts/restore_drill.py`): fresh clones of both repos side by side in a
  temp dir (or the workspace from its bundle) → `uv sync` (installs the tool) → recreate
  `.dvc/config.local` (one command, documented in the README) → `dvc pull` → `dvc status` is clean →
  `dvc repro` reports nothing to do → `energatron verify` passes, once it exists (no later than P2).

---

## 7. Repository layout

Everything lives under `~/WorkSpace/Electricity/`, which stays a plain folder (never `git init` it). It holds two
independent git repos side by side; §14.1 explains why the split can't wait.

```
~/WorkSpace/Electricity/          plain folder, not a repo
├── CLAUDE.md → energatron-5000/parent/CLAUDE.md   local orientation; symlinks made by link_parent.py
├── AGENTS.md → CLAUDE.md         same file for Codex
├── .claude/settings.json → ../energatron-5000/.claude/settings.json
├── energatron-5000/              tool repo → GitHub (public-ready)
└── demq-elektric/                workspace repo → private GitHub (optional)
```

The tool's distribution name is `energatron-5000`. Its Python import package and CLI are `energatron`, because
Python package names can't contain hyphens.

**Tool repo: `energatron-5000/`** (GitHub, public-ready, never contains data)

```
energatron-5000/
├── README.md  LICENSE  CONTRIBUTING.md  CHANGELOG.md
├── CLAUDE.md                     canonical agent instructions (development rules), plain Markdown
├── AGENTS.md → CLAUDE.md         symlink, so Codex reads the same file
├── .claude/
│   ├── skills/<name>/SKILL.md    canonical skills (Agent Skills format)
│   ├── agents/                   Claude-only subagents (e.g. a privacy reviewer)
│   └── settings.json             Claude-only permissions and hooks
├── .agents/skills/<name>         symlinks to ../../.claude/skills/<name>, so Codex finds the same skills
├── pyproject.toml  uv.lock  .python-version
├── .pre-commit-config.yaml  .github/workflows/ci.yml
├── src/energatron/
│   ├── core/                     pure domain, no I/O: models, nem12, timebase, usage, tariff, engine, ranking, ids
│   ├── services/                 use cases: land, fetch_offers, run, compare, explain, decide, migrate;
│   │                             stages/ holds the stage bodies and the stage registry
│   ├── ports/                    interfaces: Catalog, BlobStore, PipelineRunner, OfferProvider, SourceParser,
│   │                             VersionControl, Clock
│   ├── adapters/
│   │   ├── storage/              JSONL catalog, filesystem blob store (DVC-tracked)
│   │   ├── pipeline/             DVC runner, code fingerprint
│   │   ├── offers/               CDR client, manual offers
│   │   ├── sources/              SAPN NEM12, SAPN summary, retailer exports, bills
│   │   └── cli/                  typer app: `energatron …`, including `energatron stage <name>`; every
│   │                             command supports --json
│   ├── regions/                  region profiles (sa.yaml first)
│   ├── offer_rules/              curated public facts about offers (bundle eligibility, …)
│   └── templates/workspace/      files `energatron init` writes (dvc.yaml, params.yaml, CLAUDE.md, skills, hooks, …)
├── parent/CLAUDE.md               the parent folder's instructions, linked by link_parent.py
├── scripts/privacy_guard.py  check_agent_links.py  link_parent.py  restore_drill.py  icloud_backup.py
├── tests/                        unit, property, golden, demo end-to-end; fixtures are synthetic only
└── agent_docs/YYYY-MM/YYYY-MM-DD_<name>.md
```

**Workspace: `demq-elektric/`** (private; one per household)

```
demq-elektric/
├── CLAUDE.md                     operating rules for agents (from the template)
├── AGENTS.md → CLAUDE.md
├── .claude/skills/  .agents/skills/   workspace skills (land, monthly check-in, …), same symlink pattern
├── .claude/settings.json         Claude-only: e.g. deny reading private/**
├── pyproject.toml  uv.lock       depends on energatron-5000: editable ../energatron-5000 for you, a git tag for others
├── .pre-commit-config.yaml       privacy guard, nbstripout, pipeline-sync check, DVC hooks (from the template)
├── .gitignore  .dvcignore        data/landing/, .venv, notebook checkpoints, …
├── .dvc/config                   committed; config.local / cache / tmp are git-ignored by DVC
├── dvc.yaml  dvc.lock  params.yaml   dvc.yaml and params.yaml are written by `energatron pipeline sync`
├── .energatron/code_fingerprint.json per-stage code entries (each stage tracks only its own) + tool commit
├── .energatron/workspace.toml    marks the workspace; non-personal settings
├── .energatron/local.toml  lock  tmp/  backup_state.json   git-ignored: machine-specific settings, workspace
│                                 lock, scratch (incl. land_pending.json while a landing is unfinished),
│                                 the last backup's marker
├── config/private.example.toml   template for private/config.toml, no values
├── config/offer_rules.local/     your offer rules that aren't upstreamed yet
├── notebooks/                    exploration on marts only; outputs stripped
├── data/                         DVC-managed (only *.dvc pointer files are in git)
│   ├── landing/                  inbox (git-ignored, not tracked)
│   ├── raw/<source>/             immutable; one .dvc per source directory; files at <YYYY>/<dataset_id><ext>
│   ├── staged/  interim/  marts/  reports/       dvc.yaml outputs
│   ├── runs/                     run manifests (dvc add)
│   └── decisions/                decision log (dvc add)
├── private/                      DVC-tracked: config.toml, denylist.generated.txt, optional denylist.txt
└── legacy/                       DVC-tracked archive of old notebooks and data
```

The workspace's git history is your traceability record. Either push it to a **private** GitHub repo (it only
contains hashes and non-personal config) or keep it local and back it up with `git bundle` (§6).

**Current state (2026-10-23):** the existing folders have been moved into this layout:
- `agent_docs/` → `energatron-5000/agent_docs/`
- `legacy/` → `demq-elektric/legacy/`
- `data/raw/*.csv` → `demq-elektric/data/landing/` (landed properly in P1)

Neither sub-folder is a git repo yet; that happens in P0.

Documentation convention: every doc goes in `agent_docs/YYYY-MM/`, named `YYYY-MM-DD_<name>.md` with the
creation date. Record this in the tool repo's `AGENTS.md` so agents follow it. Design docs (like this one) live
in the tool repo; anything that mentions your own data stays in the workspace.

---

## 8. Data model

### 8.1 Lifecycle zones

| Zone | Mutability | Produced by | Tracked as |
|---|---|---|---|
| `data/landing/` | Scratch inbox | You (downloads, scans) | Not tracked |
| `data/raw/<source>/` | Immutable, append-only | `energatron land`, `energatron fetch-offers` | `dvc add` per source dir |
| `data/staged/` | Rebuildable | Parse stages: one schema-validated Parquet file per raw dataset | `dvc.yaml` outs |
| `data/interim/` | Rebuildable | Merge/dedupe, time-basis conversion, bill tables | `dvc.yaml` outs |
| `data/marts/` | Rebuildable | Usage profile, normalised offers, cost matrices | `dvc.yaml` outs |
| `data/reports/` | Rebuildable | Validation reports, recommendations (JSON + HTML) | `dvc.yaml` outs |
| `data/runs/`, `data/decisions/` | Append-only | `energatron run`, `energatron decide` | `dvc add` |

### 8.2 Source types

| `source` | Example input | How it lands | `source_ts` (dataset time) |
|---|---|---|---|
| `sapn_interval` | SAPN `*_SAPN_DETAILED.csv` | Manual download → landing | Generation timestamp from the SAPN filename; else the `100` record's DateTime, else the latest `UpdateDateTime` |
| `sapn_summary` | SAPN `*_SAPN_SUMMARY.csv` / `.pdf` | Manual | Generation timestamp from the filename; else content (CSV) or PDF creation date |
| `retailer_usage` | Retailer monthly TOU usage export | Manual | Latest `Read Datetime` in the content (the export carries no export date) |
| `retailer_txn` | Retailer transactions export (invoices, payments) | Manual | Latest `Date` in the content |
| `elec_bill`, `gas_bill` | Bill PDF | Manual | PDF creation date (P1); the issue date once P5 adds text extraction |
| `gas_bill_record` | YAML transcription of one gas bill (references the PDF's dataset ID) | Manual, scaffolded by CLI | Issue date |
| `offer_cdr` | Plan-detail JSON from the AER CDR API | `energatron fetch-offers` | Plan `lastUpdated` |
| `offer_cdr_index` | Plan-list response per brand, plus the fetch log | `energatron fetch-offers` | `fetched_at` |
| `offer_manual` | YAML for offers not in CDR (phone or retention quote, promo), plus the Basic Plan Information Document (BPID) or fact-sheet PDF | Manual | Quote or publish date |

### 8.3 Catalog

Each source has an append-only catalog at `data/raw/<source>/_catalog.jsonl`. A cheap `catalog_index` stage
unions them into `data/marts/catalog/` for querying (DuckDB/pandas).

```json
{
  "kind": "catalog_record",
  "schema_version": 1,
  "dataset_id": "sapn_interval-20261023T100345-3fa9c2e1",
  "source": "sapn_interval",
  "sha256": "…",
  "bytes": 1234567,
  "stored_path": "data/raw/sapn_interval/2026/sapn_interval-20261023T100345-3fa9c2e1.csv",
  "original_filename": "<kept here only — this file is private>",
  "landed_at": "2026-10-23T00:32:10Z",
  "file_mtime": "2026-10-23T00:05:41Z",
  "source_ts": "2026-10-23T10:03:45",
  "coverage": {"start": "2024-10-23", "end": "2026-10-22"},
  "detector": {"name": "sapn_interval", "version": 1, "evidence": ["filename", "content"]},
  "extra": {"interval_minutes": 5, "nmi_suffixes": ["E1"], "days": 731, "...": "..."},
  "supersedes": null,
  "notes": ""
}
```

Rules:
- Landing is idempotent on `sha256`: re-landing the same bytes does nothing. An `offer_cdr` re-fetch with the
  same offer snapshot ID is a no-op too.
- Raw files are made read-only.
- Corrections are new datasets that name what they `supersede`; nothing is edited in place.
- Landing runs a premises check against `private/config.toml` (NMI, meter serials, accounts), so another
  premises' data can't be landed by accident. A foreign file aborts the whole `land`; other rejected files
  don't stop the rest (exit 8).
- The record holds no landing commit (that commit contains the catalog's hash). The commit message,
  `land: <ids>`, is the link: `git log --grep <dataset_id>` finds it. A pending marker carries the IDs across an
  interrupted landing.
- `marts/catalog/` leaves out `original_filename` and `notes`.
- The full rules are in the P1 spec (`2026-10-23_p1-landing-catalog-spec.md`).

### 8.4 Identifier scheme

| ID | Format | Derived from | Example |
|---|---|---|---|
| Dataset | `{source}-{source_ts:%Y%m%dT%H%M%S}-{sha256[:8]}`, with the full source type | File bytes | `sapn_interval-20261023T100345-3fa9c2e1` |
| Offer snapshot | `{planId}~{lastUpdated:%Y%m%dT%H%M%SZ}~{sha256(canonical JSON)[:8]}` | API `data` object, keys sorted, no whitespace | `GLO1202240MRE1@EME~20261004T063928Z~9c1d0b7a` |
| Fetch batch | `fetch-{utc:%Y%m%dT%H%M%SZ}` | Wall clock | `fetch-20261023T013000Z` |
| Run | `run-{utc:%Y%m%dT%H%M%SZ}-{git sha7}` | Wall clock + HEAD | `run-20261023T014500Z-1a2b3c4` |
| Recommendation | `REC-{as_of:%Y%m%d}-{sha256(inputs + ranking)[:8]}` | Content (deterministic) | `REC-20261023-5e6f7a8b` |
| Decision | `DEC-{date:%Y%m%d}-{nn}` | Human action | `DEC-20261025-01` |

- **Why content-derived IDs:** the same code on the same inputs gives the same recommendation ID and
  byte-identical outputs. That makes "re-run from a checkout" the reproducibility test.
- `as_of` = max(latest offer `fetched_at`, end of the usage window), never the wall clock, so outputs stay
  deterministic.
- An offer re-fetch that differs only in raw bytes (whitespace, `meta`, key order) but not in canonical content
  is **not** a new snapshot. It is logged in the fetch log only.
- No ID contains personal data, so a hosted multi-user version only needs to add a tenant prefix
  (`<tenant>/<id>`).

---

## 9. Pipeline

### 9.1 DAG

```
raw/sapn_interval ─► parse_nem12 ─► merge_interval ─┬─► validate_timebase ◄── raw/retailer_usage
                                                     ├─► validate_meter    ◄── raw/sapn_summary
                                                     └─► localise ◄── validate_timebase's report (gate)
                                                              └─► usage_profile ───────────────┐
                                                              │                                  │
raw/offer_cdr + raw/offer_manual + offer_rules*       ─► normalise_offers ─┬─► cost_elec ◄───────┤
                                                              │            └─► cost_gas ◄──┐     │
raw/gas_bill + raw/gas_bill_record ─► gas_bills ─► gas_profile ────────────────────────────┘     │
                                                                                                 │
raw/retailer_txn + raw/elec_bill + gas_bills + costs ─► backtest                                 │
cost_elec + cost_gas + offer_rules*       + private/config ─► bundles ─► recommend ◄─────────────┘
                                                                             └─► reports/recommendations/
```

`usage_profile` is followed by `usage_report`, which reads only `marts/usage_profile` and writes `reports/usage/`
(P3 spec §9, §10).

`offer_rules*` = the curated rules shipped in the package, plus the workspace's `config/offer_rules.local/`.

### 9.2 Stages (`dvc.yaml`)

`dvc.yaml` lives in the workspace and is generated from the template. Each stage command is
`energatron stage <name>`, so the logic stays in the package and the file stays thin.

Every stage also tracks its own code entry, `stages.<name>.stage_sha256` in `.energatron/code_fingerprint.json`,
as a DVC param. That entry covers the stage's modules, its declared package data, the third-party packages it
imports and the Python version, so a stage reruns only when code it uses changes; no stage depends on `uv.lock`.
The file also records the tool commit, for the §9.4 lookup path. `energatron run` refreshes it before
`dvc repro`. DVC's run cache stays on, so run manifests list the stages that ran, were restored from the cache,
or were up to date. (D1 §11.)

Every output is a directory holding its files plus `_provenance.json`, which is why the outputs below are
directories.

| Stage | Main deps | Outs | Key checks (fail the stage) |
|---|---|---|---|
| `catalog_index` | one `raw/<source>/_catalog.jsonl` per source type (an empty file, made by `pipeline sync`, for a source with nothing landed) | `marts/catalog/` (no file names or notes) | Schema; source matches file; unique sha256 and dataset_id; IDs and paths recompute; supersedes valid |
| `parse_nem12` | `raw/sapn_interval` | `staged/elec_interval/` (one `<dataset_id>.parquet` each, plus `index.parquet`) | Blobs match the catalog; detector versions known; values per day = 1440/interval; units; 400-record coverage of `V` days; non-negative |
| `merge_interval` | `staged/elec_interval` | `interim/elec_interval_nem/`, `reports/validation/merge/` | Key uniqueness; conflicts logged |
| `validate_timebase` | `interim/elec_interval_nem/`, `raw/retailer_usage`, params `meter.local_tz`, `meter.timebase`, `validation.timebase` | `reports/validation/timebase/` | No other hypothesis fits better than the configured one (fails only then); too little evidence, or a tie, is a warning (`unconfirmed`) |
| `validate_meter` | `interim/…`, `raw/sapn_summary`, param `validation.meter` | `reports/validation/meter/` | Monthly totals within ±0.01 kWh of SAPN SUMMARY (fails on a mismatch); gap list and a missing SUMMARY are warnings; % of intervals with quality `A` |
| `localise` | `interim/elec_interval_nem/`, `reports/validation/timebase/`, params `meter.local_tz`, `meter.timebase` | `interim/elec_interval_local/` | Gate: the timebase verdict is `confirmed` for the configured basis; energy conserved; DST days have 23/25 local hours |
| `usage_profile` | `interim/elec_interval_local/`, params `meter.local_tz`, `analysis` | `marts/usage_profile/` | One interval length per register; daily and TOU totals conserve energy and every interval gets one TOU label; `reference_window`: the last `reference_months` covered months at the coverage threshold (P3 spec §8) |
| `usage_report` | `marts/usage_profile/` (no params) | `reports/usage/` (`index.html`) | A deterministic, self-contained HTML (inline SVG, no script, no network asset) built from the marts alone (P3 spec §9) |
| `gas_bills` | `raw/gas_bill`, `raw/gas_bill_record` | `interim/gas_bills/` | Each bill's total recomputes within ±$0.05 |
| `gas_profile` | `interim/gas_bills/` | `marts/gas_profile/` | Periods contiguous; read types flagged |
| `normalise_offers` | `raw/offer_cdr`, `raw/offer_manual`, `config/offer_rules.local`, `private/config.toml`, params `offers.*` | `marts/offers/` (`tariffs.parquet`, `exclusions.json`) | TOU windows partition the day; unsupported features listed |
| `cost_elec` | `marts/offers`, `interim/elec_interval_local/`, params `pricing.*` | `marts/costs/elec/` | Line items sum to totals |
| `cost_gas` | `marts/offers`, `interim/gas_bills/` | `marts/costs/gas/` | Same |
| `backtest` | costs, `raw/retailer_txn`, `raw/elec_bill`, `interim/gas_bills/` | `reports/validation/backtest/` | Within ±`validation.backtest_tolerance_pct` |
| `bundles` | `marts/costs/*`, `config/offer_rules.local` | `marts/costs/bundles/` | Eligibility rules applied |
| `recommend` | `marts/costs/*`, `marts/usage_profile`, `private/config.toml`, params | `reports/recommendations/` (`REC-*.json`, `.html`) | Deterministic ID; every cited ID resolves |

`params.yaml` holds non-personal settings only. It begins with `kind: params` and `schema_version`. P1's
version 1 holds no keys; each block below arrives with its phase's spec as a new version, and
`energatron pipeline sync` rewrites an older file. Target shape:

```yaml
kind: params
schema_version: 3
meter:
  timebase: nem_aest            # confirmed by validate_timebase (see §10.2)
  local_tz: Australia/Adelaide  # copied from the region profile by pipeline sync
analysis:                       # P3 spec §4.1; the plan's `sensitivity` returns with P6
  reference_months: 12
  coverage_threshold: 0.95
  seasons: {DJF: [12, 1, 2], MAM: [3, 4, 5], JJA: [6, 7, 8], SON: [9, 10, 11]}
  weekend: [saturday, sunday]
  band_percentiles: [10, 50, 90]
  base_load: {from: "01:00", to: "05:00", percentile: 10}
  demand_minutes: 30
  tou:
    window_sets:                # ordered by effective_from; the first (null) is copied from the region profile
      - effective_from: null
        windows: {Peak: [...], Off Peak: [...], Shoulder: [...]}
offers:
  brands: [globird, agl, origin, raa, energyaustralia, …]   # AER CDR brand slugs
  distributors: {electricity: "SA Power Networks", gas: "AGN Metro/Barossa/Peterborough"}
  customer_type: RESIDENTIAL
pricing:
  assume_conditions_met: [PAY_ON_TIME, DIRECT_DEBIT]
  gst_rate: 0.10
validation:
  meter: {total_tolerance_kwh: 0.01}
  timebase:
    windows: {Peak: [...], Off Peak: [...], Shoulder: [...]}   # copied from the region profile; the owner may edit
    register_tolerance_kwh: 0.05
    register_tolerance_pct: 0.1
    total_tolerance_kwh: 0.05
    min_fit_periods: 3
  backtest_tolerance_pct: 1.0
```

`config/private.example.toml` (committed; the real `private/config.toml` is DVC-tracked):

```toml
salt = "<random 32 hex chars, generated at init>"
[premises]
nmi = "<NMI>"
postcode = "<postcode>"
network_tariff = "<network tariff code printed on your electricity bill>"
gas_mirn = "<MIRN>"
[current_plans]
electricity = "<offer snapshot id>"
gas = "<offer snapshot id>"
```

### 9.3 Provenance written by every stage

Each stage writes a `_provenance.json` (kind `stage_provenance`) into every output directory. It contains:
- stage name and output name
- the stage's code entry: `stage_sha256`, source hash, Python and package versions (not the tool commit or the
  package version)
- inputs with their hashes and dataset IDs (resolved through the catalog); private config is an input,
  recorded by hash only
- params values and hash
- output file hashes and row counts, and check results

It holds no timestamps or run IDs, so reruns give byte-identical files. The run manifest (§9.4) links runs to
outputs.

`dvc.lock` is the authoritative hash record. `_provenance.json` makes outputs self-describing when they are
looked at outside git.

### 9.4 The `energatron run` wrapper

1. Refuse to run if the workspace's tracked config is dirty, or if the tool is an editable checkout with
   uncommitted changes. `--draft` overrides this. Stage outputs aren't stamped: the draft flag is recorded in
   the run manifest only, and a decision may cite a recommendation only once a non-draft run manifest names it.
2. Refresh `.energatron/code_fingerprint.json`, then run `dvc repro`. No run ID reaches the stages.
3. Write `data/runs/<run_id>.json`: which stages ran, were restored from DVC's run cache, or were up to date, input dataset IDs, offer fetch batches
   considered, and the recommendation ID. Then `dvc add data/runs`.
4. `git add dvc.lock data/**/*.dvc && git commit -m "run: <run_id> <REC-id>"`, and optionally tag
   `rec/<REC-id>`.
5. No backup here: agents can't reach iCloud (§6). The command ends with `W_BACKUP_PENDING`, and the owner
   runs `icloud_backup.py backup`. `land` does the same.

**Lookup path:** `REC-…` → `git log --grep REC-…` in `demq-elektric` → `git checkout <commit>` → read the tool
commit from `.energatron/code_fingerprint.json` → `git -C ../energatron-5000 checkout <tool commit>` →
`uv sync && dvc checkout` → the exact code and data state → `dvc repro` reproduces the same `REC-…`. (An
editable dependency isn't pinned in `uv.lock`, which is why the fingerprint records the tool commit.)

---

## 10. Domain design

### 10.1 NEM12 parsing and merging

**Parsing**
- Streaming parser for record types 100/200/300/400/500/900. It tolerates a missing `100`, repeated `200`
  headers and blank lines.
- Interval length comes from the `200` record (5, 15 or 30 min). Each `300` must hold 1440/interval values.
- Output is long format: `suffix, meter_date, meter_start, interval_minutes, kwh, quality, quality_flag,
  reason_code, update_dt, dataset_id` (no NMI: landing's premises check ties each dataset to the premises; no
  `interval_end_nem`: the end is `meter_start` plus `interval_minutes`).
- For `V` days, the `400` ranges are applied to each interval.

**Merging**
- Merge key: `(suffix, meter_start)`; the merge keeps whole days, which is equivalent because `UpdateDateTime`
  is per day.
- Precedence: newest `UpdateDateTime` wins, then dataset precedence (D3 §3.3). Superseded datasets are skipped.
- Emit a conflicts report of intervals whose values differ between downloads. These are expected where
  estimates were later replaced by actuals.

**Checks:** values per day; 5-min sums match SAPN SUMMARY monthly totals; gap list; share of intervals by
quality type.

### 10.2 Time basis (high impact on TOU costs)

**The mismatch**
- **Meter clock (to verify):** NEM12 interval data is understood to use NEM time (fixed UTC+10, no daylight
  saving), with interval-ending timestamps. Every day in the files has exactly 288 values, even on SA DST change
  days, which is consistent with a no-DST clock.
- **Tariff clock:** CDR plans declare `"timeZone": "LOCAL"`, i.e. the SA wall clock (ACST UTC+9:30 / ACDT
  UTC+10:30).
- The meter clock therefore runs **30 min ahead** of SA local time in winter and **30 min behind** it in summer.
  A 10:00 local tariff boundary is 10:30 meter-time in winter and 09:30 in summer. Ignoring this moves energy
  across peak/shoulder boundaries.
- Hourly roll-ups must happen *after* converting to local time; otherwise every "hour" straddles two local
  hours. With 5-min data the conversion is lossless.

**Empirical test (`validate_timebase`)**
- The retailer usage export gives monthly peak / off-peak / shoulder kWh for the same meter.
- Recompute that split under three hypotheses:
  - H1: NEM time → convert to local
  - H2: timestamps are already the local wall clock
  - H3: timestamps are local standard time
- One hypothesis should match almost exactly. Set `meter.timebase` to it; from then on, the stage asserts the
  setting is still the best fit.
- A retailer row read at date D covers the meter dates after the previous read up to and including D. A period
  counts only when its total reconciles with the interval sum. A period fits a hypothesis when every register
  fits; the verdict is `confirmed` (the configured basis strictly beats the others, with enough periods),
  `unconfirmed` (too little evidence, or a tie, such as winter-only data) or `contradicted` (another basis
  strictly beats it). Only `contradicted` fails the stage. Periods that fit no hypothesis (for example after a
  change of the retailer's TOU windows) are reported as a warning. `localise` runs only on `confirmed`. The
  region's time zone comes from `tzdata` (P2 spec §9.4, §10). The legacy notebook's hand-shifted bill-period boundaries hint at an offset
  of this kind.

**DST handling:** local days of 23 h and 25 h (first Sunday of October and April) are unit-tested. Supply
charges count *local* dates.

### 10.3 Usage profile metrics (`marts/usage_profile`)

*Made concrete in rev 11: the P3 spec (§3, §5–§9) defines the reference window (the last `reference_months` covered
months), complete days and full hour cells (daylight-saving days are left out of hourly metrics), aligned demand
blocks, quality classes, the TOU split by date-ranged window sets (the offer-window split is P4), and the HTML
report. The list below is the intent.*

- Daily kWh series; monthly totals; average daily kWh by month and by season (DJF/MAM/JJA/SON).
- Average hourly profile: 24 hours × season × weekday/weekend, with percentile bands.
- Day × hour heatmap; year-on-year comparison by month (the 2-year history allows one comparison, and it grows
  as downloads accumulate).
- TOU split per month, for the network windows and for each candidate offer's windows.
- Base load (low percentile of 01:00–05:00); peak demand (max 30-min kW per month, for demand tariffs); load
  factor.
- Data quality: coverage and the share of actual vs substituted/estimated intervals per month.
- Rendered as an HTML report in `data/reports/usage/` (private), built from the marts so notebooks aren't needed.

### 10.4 Offer ingestion

**Source:** the AER-hosted CDR Energy Product Reference Data API (public, no accreditation).
- List: `GET https://cdr.energymadeeasy.gov.au/{brand}/cds-au/v1/energy/plans?fuelType=ALL&page-size=1000`
  with header `x-v: 1`
- Detail: `GET …/energy/plans/{planId}` with header `x-v: 3`

**Verified on 2026-10-23 with brand `globird`:**
- 505 plans nationally, including 32 SAPN electricity plans and 4 SA gas plans (distributor "AGN
  Metro/Barossa/…").
- Plan detail fields: `planId`, `lastUpdated`, `effectiveFrom`, `fuelType`, `geography` (distributors,
  postcodes), `pricingModel`, and `tariffPeriod[]` (seasonal date ranges; TOU windows with days and *inclusive*
  minute end times such as `14:59`; `dailySupplyCharge`).
- Also `discounts[]` (e.g. CONDITIONAL `percentOfBill`, `fixedAmount` sign-up credit), `fees[]`,
  `solarFeedInTariff[]` and `eligibility[]`.
- Gas plans use block rates (`volume` per `P1D`) and seasonal periods.

**`energatron fetch-offers`**
1. For each brand in `offers.brands`, fetch the plan list. Seed the brand list from the AER's published
   base-URI list.
2. Filter **locally**: distributor, postcode from private config, RESIDENTIAL, currently effective.
3. Fetch the details and land each detail JSON verbatim (`offer_cdr`), together with the list response and a
   fetch log (URL, headers, status, `fetched_at`) as `offer_cdr_index`.
4. Unchanged plans dedupe by hash, so a repeated fetch only adds plans that changed.

**Human cross-check:** retailer pages such as GloBird's "All tariffs – residential" list the same plans and link
to Energy Made Easy Basic Plan Information pages. For example, GLOHOME Flexible Rate SAPN is `GLO1202240MRE` on
the page and `GLO1202240MRE1@EME` in CDR. Before switching, land the BPID or fact-sheet PDF for the chosen plan
as `offer_manual` evidence and attach it to the decision.

**Curated offer rules** (YAML shipped in the tool repo, public facts only, improved by pull request; your own
additions go in the workspace's `config/offer_rules.local/`) cover what CDR carries only as free text:
- combo eligibility, e.g. GloBird "Combo" gas plans require a GloBird electricity plan at the same premises
- which part of a percentage discount depends on dual fuel
- membership requirements and fees
- timing of sign-up credits

Each rule cites the offer snapshot IDs it was derived from and has a `verified_on` date. Rules match on a
planId pattern and a date range.

**Offer diff report:** when a new snapshot of a plan lands, show field-level changes against the previous
snapshot (e.g. "daily supply +3c, peak −1c").

### 10.5 Tariff model and cost engine

**Model**
- A pydantic `Tariff` model is mapped from CDR `electricityContract` / `gasContract` and from `offer_manual`.
- It carries an `unsupported: list[str]` field. Offers are **never silently mispriced**: an offer with an
  unsupported feature is either ranked with an "approximate" flag or excluded with the reason recorded.

**Supported in v1**
- daily supply; single rate with blocks
- TOU: day sets, windows, seasonal `tariffPeriod`s including periods that wrap the year end (e.g. `10-01` →
  `05-31`)
- controlled load (not used at this premises today); demand charges (max kW in window per billing period)
- FiT (no export today)
- conditional and guaranteed discounts (`percentOfBill`, `percentOfUsage`, `fixedAmount`); fees (membership,
  paper bill); one-off credits

**Engine behaviour**
- **Billing periods:** simulate monthly periods (per CDR `billFrequency`), so block allowances, demand charges
  and periodic fees are applied per period, as on real bills.
- **Windows:** convert CDR inclusive end-minutes to half-open intervals, and check that each offer's windows
  cover every minute of every day exactly once.
- **Money:** use `Decimal` and round line items to cents.
- **GST:** CDR prices appear to be GST-exclusive (GloBird's CDR peak rate is lower than its advertised
  GST-inclusive rate). **Verify against one BPID**, then encode as a single tested convention.
- **Conditional discounts:** applied per `pricing.assume_conditions_met`; reports always show both "with" and
  "without".

**Output:** `marts/costs/elec/`, with line items per (offer snapshot, scenario, billing period).

### 10.6 Gas

- **Inputs:** monthly bill PDFs (landed) plus one YAML transcription per bill (`gas_bill_record`). At ~12
  bills a year, manual entry is cheaper than maintaining a parser; a pdfplumber template can come later if it
  proves worthwhile. `energatron gas-bill new <pdf_dataset_id>` scaffolds the YAML.
- **YAML fields:** retailer, bill ID, issue date, period start/end, read type (actual/estimate), start/end
  read, m³, heating value (MJ/m³), pressure correction factor, MJ, line items (supply days × rate, each step
  block MJ × rate), discounts, GST, total, `pdf_dataset_id`.
- **Validation:** MJ and total recompute from the line items within ±$0.05. An estimated read followed by an
  actual read is combined into one period.
- **Gas profile:** MJ/day per period, allocation to calendar months, seasonal curve. Later, optionally,
  weather-normalise with Bureau of Meteorology (BoM) heating-degree-days for Adelaide.
- **Gas engine:** for each historical bill period, apply the candidate plan's seasonal `tariffPeriod`. The
  step-1 allowance is `volume × days in period`; GloBird's plan terms state exactly this.

### 10.7 Bundles (electricity + gas combos)

- Electricity and gas are priced independently. Bundles then apply their rules: eligibility (same retailer and
  premises), extra discounts, shared membership fees.
- Candidate set:
  - (a) the best independent electricity plan + the best independent gas plan, from any retailers
  - (b) every same-retailer bundle, with its bundle rules applied
- Rank by combined annual cost, and show each bundle's saving or premium against (a).

### 10.8 Projection and savings methodology

- **Reference year:** the latest 12 complete months of local-time interval data; for gas, the latest 12 bills.
- **Projected annual cost** = the candidate snapshot applied to reference-year usage, i.e. "what last year would
  have cost at today's prices". It is standard and easy to explain.
- **Year 1 vs ongoing:** one-off credits and limited benefit periods count only in year 1; both figures are
  reported.
- **Savings** = cost(the current plan's latest snapshot) − cost(candidate), both computed by the engine on the
  same usage, so the comparison is like-for-like. The current plan IDs live in private config.
- **Sensitivity:**
  - the alternative 12-month window (~2 years of history exist)
  - usage scaled ±10%
  - conditional discounts on and off
  - A recommendation is marked "robust" only if the top choice holds across these.
- **Price-change horizon:** market offers are variable, and network tariffs reset each 1 July, so every
  recommendation states `valid_as_of` and `recheck_by`.

### 10.9 Backtesting (engine credibility)

- Land historical retailer bills (the transactions export plus invoice PDFs) and the tariff that applied at the
  time, as an `offer_manual` snapshot. The rates in the legacy notebooks are a starting point, but they are
  undated and unsourced.
- `backtest` computes engine(historical tariff, metered usage for the bill period) and compares it with the
  actual invoice; target ±1%, reported per bill. Gas gets the same treatment.
- This also confirms the time-basis and GST conventions independently.

---

## 11. Traceability walkthrough

**Scenario:** GloBird updates GLOHOME on 2026-11-05.

1. `energatron fetch-offers` → the detail JSON hash has changed → new dataset `offer_cdr-20261105T…-ab12cd34` with
   snapshot ID `GLO1202240MRE1@EME~20261105T…Z~ab12cd34`. The previous snapshot stays in `raw/` forever, and
   the diff report shows what changed.
2. `energatron run` → only the offer stages and downstream stages rerun; meter stages come from cache.
   `REC-20261105-…` lists the offer snapshot ID for every ranked option, plus the usage dataset IDs, the run ID
   and the commit.
3. The wrapper commits `run: run-… REC-…` with the updated `dvc.lock` and `data/raw/offer_cdr.dvc`, then runs
   `dvc push`.
4. You switch plans → `energatron decide REC-… --elec <snapshot> --gas <snapshot> --switch-date …` → `DEC-…`, with
   the plan's BPID PDF landed as evidence.
5. As post-switch bills land, an `outcomes` report compares actual against projected cost for `DEC-…`.

**"Why did we pick this?", months later:** open `DEC-…` → `REC-…` → the exact offer snapshot JSON, usage
datasets and code commit → `git checkout` + `uv sync` + `dvc checkout` → `dvc repro` reproduces the same `REC-…`.

---

## 12. Runbooks (target CLI)

```bash
# One-time: create a workspace (yours is set up by hand in P0; this is the path for others, from P8)
uv tool install git+https://github.com/<you>/energatron-5000
energatron init ~/my-energy-workspace          # asks for region, backup remote, private config values
energatron init ~/energatron-demo --demo       # synthetic data only, nothing personal

# Everything below runs inside the workspace: cd ~/WorkSpace/Electricity/demq-elektric

# After a tool update: regenerate dvc.yaml, params.yaml and empty catalogs, then commit them
uv run energatron pipeline sync

# New SAPN meter download
mv ~/Downloads/*_SAPN_DETAILED.csv data/landing/
uv run energatron land data/landing/          # hash, rename, catalog, dvc add, commit "land: <ids>"
uv run energatron run                         # repro → manifest → commit; ends with W_BACKUP_PENDING
# then, in the owner's terminal: uv run ../energatron-5000/scripts/icloud_backup.py backup

# New gas bill
uv run energatron land data/landing/<bill>.pdf --source gas_bill
uv run energatron gas-bill new <pdf_dataset_id>      # scaffolds YAML in data/landing/
uv run energatron land data/landing/<bill>.yaml --source gas_bill_record
uv run energatron run

# Refresh offers (manually, or weekly via launchd)
uv run energatron fetch-offers                # lands only changed snapshots
uv run energatron offers diff --since 2026-10-01
uv run energatron run

# Record a decision
uv run energatron decide REC-YYYYMMDD-xxxxxxxx --elec <snapshot_id> --gas <snapshot_id> --switch-date YYYY-MM-DD

# Restore drill (owner's terminal, from ~/WorkSpace/Electricity; at P0 acceptance, then quarterly).
# scripts/restore_drill.py clones the tool and the newest bundle into a scratch folder, recreates the DVC
# remote, runs dvc pull, checks dvc status and dvc repro, and runs energatron verify once it exists.
uv run energatron-5000/scripts/icloud_backup.py drill
```

---

## 13. Testing and CI

- **Unit tests:**
  - NEM12 edge cases: missing `100`, repeated `200`, blank lines, `V` + `400`, 5/15/30-min intervals,
    malformed rows
  - merge precedence; time basis on DST days; TOU window conversion; seasonal periods that wrap the year
  - block allowances (`P1D` × days); discount application order; GST
- **Property tests (hypothesis):** energy is conserved through roll-ups and localisation; every interval falls
  in exactly one TOU bucket per offer.
- **Golden tests:** hand-computed bills for 2–3 synthetic tariffs, in cents.
- **Determinism test:** running the pipeline twice on fixtures gives identical output hashes and the same
  recommendation ID.
- **Fixtures** are generated by code (`tests/fixtures/make_nem12.py` etc.) and are never derived from real
  files.
- **Demo end-to-end test:** `energatron init --demo` followed by `energatron run`, in CI. It exercises the onboarding
  path other people will use, and it gives agents a safe sandbox. (In P0–P7 this is a fixture workspace; it
  becomes `energatron init --demo` in P8.)
- **CI (GitHub Actions), tool repo:** ruff, pyright, pytest, the demo end-to-end test, and the "no data files,
  no `*.dvc`, no `dvc.lock`" check.
- Real-data checks (meter, time basis, backtest) are pipeline stages that run locally. CI never sees real data.

---

## 14. Designed to expand: other households, AI agents, web app

**Principle:** build for one household now, but get the few hard-to-reverse decisions right from the start.
Everything else waits until it's needed.

### 14.1 Decisions made now because they're hard to reverse

| Decision | Why now | Cost today |
|---|---|---|
| **Separate tool repo and workspace** | Published git history can't be taken back. If your `dvc.lock`, `*.dvc` and `run:` commits lived in the code repo, opening it up later would mean rewriting history. Others also need a clean repo to clone. | One extra directory |
| **Layered package: pure core → services → adapters** | The CLI, agent tools and a web API all become thin wrappers over the same use cases, so no logic is duplicated or trapped in the CLI | Discipline only |
| **Stage logic independent of DVC** | Stages are plain functions (`run_stage(spec, inputs, params, outputs, *, code, recorded_code)`; every output is a directory with `_provenance.json`); `dvc.yaml` just calls `energatron stage <name>`. A web app can run the same functions from a job queue, and DVC stays replaceable | None |
| **Storage behind two small interfaces** | `Catalog` (v1: JSONL files) and `BlobStore` (v1: local files under DVC). A hosted version can swap in Postgres and S3 without touching the core | A few lines |
| **No region-specific values in code** | Distributor names, the time zone and TOU windows (which `pipeline sync` copies into `params.yaml`), network-tariff mapping and CDR brand list live in region profiles; the registry decides which parser recognises a file. The meter-file formats P2 reads are core modules, not registry plugins, until a second format arrives. The first TOU window set of `analysis` is copied from the profile the same way (P3). SA is just the first profile | None |
| **`kind` and `schema_version` on every persisted artifact** (Parquet carries both as metadata) | Other people's workspaces will lag behind the latest package; `energatron migrate` upgrades them | Two fields per schema |
| **Machine-readable CLI** | Every command supports `--json`, has stable exit codes and never prompts when not attached to a terminal. Agents can drive it today, and the web API reuses the same response models | Small |
| **Per-user privacy defaults** | `energatron init` creates `private/config.toml`, derives the denylist from it and installs the hooks, so others get the guardrails automatically | Part of P0/P8 |
| **License chosen up front** | Changing the license after others have contributed needs their consent | Done: Apache-2.0 |
| **One instruction source for all agents** | Separate instruction files for Claude and Codex drift apart; symlinks set up on day 1 keep them identical (§14.5) | Two symlinks and a check script |

### 14.2 Target architecture

```
  adapters        CLI (now)  ·  MCP server for agents (later)  ·  HTTP API → web UI (later)
                      │
  services        land · fetch_offers · run · compare · explain · decide · migrate        (use cases)
                  stages: stage bodies + registry
                      │
  core (pure)     models · NEM12 parsing · time basis · usage metrics · tariff model
                  cost engine · ranking · IDs · validation                                 (no I/O)
                      │
  ports           Catalog · BlobStore · PipelineRunner · OfferProvider · SourceParser      (interfaces)
                  VersionControl · Clock
                      │
  implementations now:   JSONL catalog · files + DVC · DVC runner · CDR client · SAPN NEM12
                  later: Postgres · S3 · job queue · more regions and meter formats
```

Dependencies only point down. `core` does no file, network or subprocess I/O: it takes and returns in-memory
data (pandas frames, pydantic models). That keeps it testable, and runnable anywhere, including in a browser.

### 14.3 Tool repo and workspace

- **Tool repo:** the package, the workspace template, region profiles, curated offer rules (shared knowledge
  that others can improve by pull request), docs, `AGENTS.md`, the synthetic demo-data generator, CI. It never
  contains real data, `*.dvc` or `dvc.lock`.
- **Workspace (one per household; yours is `demq-elektric`):** template-generated `dvc.yaml`, `params.yaml`,
  `.dvc/`, `data/`, `private/`, agent instructions and skills, and a dependency on the tool: an editable
  `../energatron-5000` for you, a git tag for others. Its git history is your traceability record.
- **Code traceability across the split:** every run records the package version, the tool-repo commit and the
  code fingerprint (§9.2). Because your dependency is an editable install, `energatron run` refuses official
  runs while the tool checkout has uncommitted changes; the recorded commit is then exact.
- **Template updates:** existing workspaces get template changes through `copier update` or a built-in
  `energatron upgrade`. Decide which in P8.

### 14.4 Other households via GitHub (P8)

- **Install and start:** `uv tool install git+https://github.com/<you>/energatron-5000`, then
  `energatron init <dir>`. It asks for the region, the backup remote (any DVC remote: an iCloud, Google Drive or
  Dropbox folder, an external disk, S3, SSH) and the private config values.
- **Try it without personal data:** `energatron init --demo` builds a workspace with synthetic meter data, bills
  and frozen offer snapshots. The same demo is the CI end-to-end test and a safe sandbox for agents.
- **Other regions:** NEM12 is the national meter-data format, so downloads from other distributors should
  mostly parse. A new region needs a profile (distributors, time zone, network tariffs, gas distributors) and a
  synthetic test fixture. SA comes first; other regions come by contribution.
- **Support without exposing data:** `energatron diagnose --redacted` produces a bug report with identifiers and
  values stripped. `CONTRIBUTING.md` tells people never to attach their own data to issues.
- **Releases:** SemVer, CHANGELOG, schema migrations, CI-built releases. Add a docs site (mkdocs) once the
  README is no longer enough.

### 14.5 AI agents: Claude-centric, readable by Codex

Claude Code is the primary agent. Codex, and other tools that follow the shared conventions, read the same
instructions and skills, so there is no second copy to maintain.

| Piece | Canonical (Claude) | How Codex gets it |
|---|---|---|
| Project instructions | `CLAUDE.md` in each repo, plain Markdown | `AGENTS.md` is a symlink to `CLAUDE.md`; Codex reads `AGENTS.md` natively |
| Skills | `.claude/skills/<name>/SKILL.md` (Agent Skills format: `name` + `description` frontmatter) | `.agents/skills/<name>` symlinks; Codex scans `.agents/skills` and follows symlinks |
| Permissions and hooks | `.claude/settings.json` (e.g. deny reading `private/**`) | Not shared; extra protection for Claude only |
| Subagents | `.claude/agents/` (e.g. a privacy reviewer) | Not shared; convenience only |
| Hard guarantees | git pre-commit hooks + CI | Apply to every agent and to humans |

**Rules that keep this working**
- Nothing essential sits behind Claude-only syntax. `CLAUDE.md` has no `@import` lines for required content;
  detailed docs are referenced by plain path ("before changing the tariff engine, read `agent_docs/…`").
- Keep `CLAUDE.md` short: Codex loads at most 32 KiB of project instructions by default.
- Skills call the `energatron` CLI and describe steps in plain language. They never name Claude-specific tools,
  so either agent can follow them.
- `scripts/check_agent_links.py` (pre-commit) fails if `AGENTS.md` or any `.agents/skills/` link is missing or
  broken.
- Where symlinks don't survive (some Windows checkouts), Codex users can add
  `project_doc_fallback_filenames = ["CLAUDE.md"]` to `~/.codex/config.toml`.
- The `--json` CLI makes every command usable by any agent with no extra code.

**Skills, added as their phase lands**
- Tool repo: `new-doc` (creates `agent_docs/YYYY-MM/YYYY-MM-DD_<name>.md`, P0), `privacy-check`,
  `add-source-parser`, `add-region`, `release`.
- Workspace: `land` (P1), `usage-report` (P3), `refresh-offers` (P4), `transcribe-gas-bill` (P5),
  `explain-recommendation`, `monthly-check-in` (fetch offers → run → summarise what changed since the last
  recommendation) and `record-decision` (P6).

**Later (P9)**
- **Local MCP server** (`energatron mcp`) exposing the service layer as tools: `list_datasets`, `land_file`,
  `fetch_offers`, `run_pipeline`, `usage_summary`, `compare_offers`, `explain_recommendation`,
  `record_decision`. Claude Code and Codex can both use MCP servers, so this stays shared too.
- **Agent proposes, validators decide.** An agent can transcribe a gas bill PDF into the `gas_bill_record`
  YAML, or draft offer rules from free-text eligibility. The deterministic checks (totals recompute, TOU windows
  cover the day exactly) accept or reject the result, and rule changes go through review. The catalog records
  `extracted_by` (`manual`, `parser vN`, or `agent:<model id>`), so the extraction method is traceable.
- **Privacy:** whatever a tool returns is sent to the model provider. Tools therefore return aggregates and IDs
  by default, never raw interval rows or identifiers, and `private/` is never exposed. Users opt in to
  anything more.

### 14.6 Web app (P10; P11 only if justified)

1. **Local web UI first** (`energatron serve` on localhost): FastAPI over the service layer plus a simple frontend,
   reading the same workspace. The frontend may use a web language (e.g. TypeScript), in its own directory; it
   only calls the API and holds no analysis logic (§4 language rule). Data never leaves the machine, so there's no custody problem, and nothing is
   rebuilt.
2. **Hosted multi-user service** only if it's clearly worth it. It needs:
   - auth and per-tenant isolation (tenant prefix on every catalog record and blob path)
   - Postgres and S3 behind the existing ports, and a job queue running the same stage functions
   - encryption at rest, data export and deletion, Australian Privacy Act obligations, a security review
   - uploads only: automatic usage retrieval through CDR would need accreditation

   Write a separate design doc before building it.
- **Alternative to evaluate at that point:** run the pure core in the browser (Pyodide/WebAssembly), so data
  never leaves the device. Whether the AER API can be called directly from a browser (CORS) is unverified; if
  not, a small server-side cache of offer snapshots is enough.
- **Carries over unchanged:** core models and engine, IDs, the recommendation schema (pydantic → JSON Schema /
  OpenAPI), stage functions, validation reports.

### 14.7 Deliberately not built yet

MCP server, HTTP API, frontend, plugin entry points (a simple registry dict is enough), DB-backed catalog, job
queue, auth, docs site, package publishing, and region profiles beyond SA. Each has a seam above, so it can be
added without refactoring the core.

---

## 15. Implementation phases

| Phase | Work | Acceptance criteria |
|---|---|---|
| **P0 Bootstrap & guardrails** | *Done 2026-10-23:* folders reorganised into `energatron-5000/` and `demq-elektric/` (§7). **Tool repo** (`energatron-5000/`): `git init`; layered package skeleton (core/services/ports/adapters); Apache-2.0 `LICENSE` + `NOTICE`; `CLAUDE.md` + `AGENTS.md` symlink (privacy rules, doc naming, layering); `new-doc` skill + `.agents/skills` link; pre-commit (privacy guard, agent-link check); CI with the no-data check. **Workspace** (`demq-elektric/`, set up by hand; it becomes the `energatron init` template in P8): `git init`; uv project pinning Python, with the tool as an editable `../energatron-5000` dependency; DVC as dev dep; `dvc init`; iCloud remote via `--local`; `.gitignore`; `CLAUDE.md` + `AGENTS.md` symlink; `.claude/settings.json` deny rules; pre-commit (nbstripout, privacy guard, a local DVC `post-checkout` hook); `private/config.toml` + `energatron denylist`; `dvc add legacy private`; `energatron backup` (dvc push and a verified bundle), run by the owner through `scripts/icloud_backup.py`. **Parent folder:** `CLAUDE.md`, `AGENTS.md` and `.claude/settings.json`, symlinks made by `link_parent.py` | Tool repo contains no data, `*.dvc` or `dvc.lock` (CI-enforced); the guard blocks a test commit containing the NMI; Claude Code and Codex both load the same instructions and the `new-doc` skill; the restore drill passes |
| **P1 Landing & catalog** | `energatron land` with detectors for `sapn_interval`, `sapn_summary`, `retailer_usage`, `retailer_txn`, PDFs and offers; IDs; catalogs; idempotency; `dvc add`; `catalog_index`; `energatron pipeline sync`, `stage`, `fingerprint` (P1 spec) | Both current SAPN files and all legacy SAPN/retailer files are landed (one dataset per distinct file); the legacy duplicate is detected by hash; re-landing is a no-op |
| **P2 Meter pipeline** | `parse_nem12`, `merge_interval`, `validate_meter`, `validate_timebase`, `localise` | Interval counts correct; monthly totals match SAPN SUMMARY; merge conflicts reported; time-basis hypothesis chosen with evidence; DST tests pass |
| **P3 Usage profile** | Metrics + HTML usage report | Report builds from marts; TOU daily averages reconcile with the legacy notebook for the same window, with differences explained by the time-basis and merge fixes (made concrete as R1–R4 in the P3 spec §12) |
| **P4 Offers + electricity engine** | CDR client, `fetch-offers`, `offer_manual` schema, normaliser, offer rules, engine, `backtest`, offer diff | Every current SAPN residential plan from configured brands either normalises or is excluded with a reason; GST convention verified against a BPID; backtest within ±1% (or deviations explained) |
| **P5 Gas** | Gas bill landing + YAML scaffold, `gas_bills`, `gas_profile`, gas offer normalisation, gas engine | Every bill recomputes within ±$0.05 from its own line items; engine reproduces bills on the plan in force at the time within ±1% |
| **P6 Bundles, recommendation, decisions** | Bundle rules, ranking, sensitivity, REC JSON + HTML, `energatron run`, `energatron decide`, outcomes report | The recommendation cites offer snapshot IDs, dataset IDs, run ID and commit; a clean-checkout re-run reproduces the same recommendation ID; decision entries link back |
| **P7 Operations** | README runbooks; optional weekly launchd `fetch-offers`, with a notification when a cheaper plan appears; quarterly restore-drill reminder (the drill ends with `energatron verify`) | One full cycle (new SAPN download → recommendation) completed using only the README |
| **P8 Open-source readiness** *(later)* | Workspace template + `energatron init` / `--demo` / `upgrade` / `migrate`; region profiles; `diagnose --redacted`; README quickstart, CONTRIBUTING, release workflow | A new user on a clean Mac gets from README to a demo report in under 15 minutes; the demo end-to-end test runs in CI |
| **P9 Agent interface** *(later)* | Local MCP server over the services; workspace skills/commands; agent-assisted bill transcription with validators and `extracted_by` | An agent completes a "monthly check-in" using only the tools, without reading `private/` or raw intervals |
| **P10 Local web UI** *(later)* | `energatron serve`: FastAPI + frontend over the services | All CLI read paths are available in the browser; no new logic outside adapters |
| **P11 Hosted service** *(only if justified)* | Postgres/S3 implementations of the ports, job queue, auth, tenancy, privacy compliance | Separate design doc and security review before any build |

P8–P11 are not scheduled. They are listed so that P0–P7 leave the right seams.

---

## 16. Open questions

1. **License:** decided 2026-10-23 — Apache-2.0 (`LICENSE` + `NOTICE` in the tool repo).
2. **Older SAPN downloads:** the legacy viewer notebook references 2022–2025 downloads that aren't in this
   folder. If you still have them, landing them extends history back to late 2022.
3. **Gas details:** which retailer, what bill format (PDF?), are the monthly reads actual or estimated, and what
   is the MIRN (for private config)?
4. **Network tariff code** on your electricity bill. It decides which plan variants you're eligible for (e.g.
   GloBird's "Flexible Rate" vs "Flexible RESELE").
5. **Conditional discounts:** do you reliably meet pay-on-time / direct-debit conditions? The default assumes
   yes and also reports "without".
6. **Brands to track:** default is every retailer serving SAPN residential customers in the AER list.
7. **Solar, battery or EV** within the next year or two? If so, FiT/export modelling and EV plans (e.g. free
   overnight windows) move up the priority list.
8. **iCloud Advanced Data Protection:** decided 2026-10-23 — already on.
9. **Workspace git history:** decided 2026-10-23 — kept local, backed up as `git bundle` files in iCloud (rev 5).
10. **Tool repo visibility:** decided 2026-10-23 — private on GitHub until P8.

---

## 17. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| iCloud evicts or sync-corrupts the remote | Lost backup | Remote outside repo, write-once files, "Keep Downloaded", quarterly restore drill; Time Machine as a second copy |
| Personal data leaks to GitHub | Privacy | Tool/workspace split, directory tracking, renamed files, no private params, denylist guard, nbstripout, CI file-type check, private workspace repo |
| Wrong meter time basis | Mispriced TOU offers | `validate_timebase` against retailer TOU export; backtest against bills |
| CDR data incomplete or wrong (free-text conditions, retailer data errors) | Wrong ranking | `unsupported` flags, curated offer rules, BPID cross-check before switching |
| Tariff edge cases (GST, inclusive end-minutes, wrapping seasons, block periods) | Silent cost errors | Golden and property tests; window-partition validation; backtests |
| Usage changes (new appliances, occupancy) | Projection drift | Sensitivity windows; outcomes report after switching |
| DVC project direction after acquisition | Tooling churn | Plain files + md5 layout; easy to migrate to git-annex/DataLad |
| Disk nearly full (~97%) | Failed writes; iCloud eviction pressure | APFS reflink cache; data is small (MBs); free space before P0 |
| Schema changes break other people's workspaces | Painful upgrades | `kind` + `schema_version` everywhere; `energatron migrate`; SemVer; demo end-to-end test in CI |
| Agent tools send personal data to a model provider | Privacy | Tools return aggregates and IDs by default; `private/` never exposed; users opt in |
| A hosted web app makes you custodian of other people's energy data | Legal and privacy burden | Local-first web UI first; hosted only after a separate design and security review |

---

## 18. References

- AER — Energy product reference data (CDR): https://www.aer.gov.au/energy-product-reference-data
- AER CDR plan list endpoint (example, verified 2026-10-23):
  https://cdr.energymadeeasy.gov.au/globird/cds-au/v1/energy/plans
- GloBird — All tariffs (residential): https://www.globirdenergy.com.au/offers-rates/all-tariffs/all-tariffs-resident/
- AEMO — MDFF Specification NEM12 & NEM13 v2.4:
  https://aemo.com.au/-/media/files/electricity/nem/retail_and_metering/metering-procedures/2021/mdff-specification-nem12-nem13-v24.pdf
- lakeFS acquires DVC (Nov 2025): https://lakefs.io/blog/lakefs-acquires-dvc/
- How iCloud Drive works in macOS Sonoma (eviction / dataless files):
  https://eclecticlight.co/2024/03/18/how-icloud-drive-works-in-macos-sonoma/
- DVC documentation: https://dvc.org/doc
- AGENTS.md convention: https://agents.md
- Model Context Protocol (agent tools): https://modelcontextprotocol.io
- Copier (project templates with updates): https://copier.readthedocs.io
- Codex — custom instructions with AGENTS.md: https://developers.openai.com/codex/guides/agents-md
- Codex — skills (`.agents/skills`, symlinks followed): https://learn.chatgpt.com/docs/build-skills.md
