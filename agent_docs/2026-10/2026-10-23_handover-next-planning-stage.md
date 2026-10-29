# Hand-over: next planning stage (detailed specs for P0–P1)

- **Created:** 2026-10-23
- **From:** the first planning session (architecture and decisions)
- **For:** a fresh agent session (Claude Code or Codex) plus the project owner
- **Main reference:** [`2026-10-23_data-pipeline-plan.md`](2026-10-23_data-pipeline-plan.md) (rev 4), called
  "the plan" below
- **Privacy note:** this file lives in the public tool repo. It contains no NMI, meter serial, account numbers,
  address, postcode or usage figures, and nothing added to it may contain them either.
- **Update (2026-10-23, later the same day):** progress, the current state and the next step are now tracked in
  [`../../TASKS.md`](../../TASKS.md). Start each session with `/next`. The loop is described at the top of that
  file. D4 (the work breakdown) will live there rather than in a separate doc.

---

## 1. Start here

1. Launch the agent from `~/WorkSpace/Electricity/` (the parent folder), so both sub-folders are in scope.
2. Read this file in full. Then read these sections of the plan: §1 (summary), §5 (privacy boundary), §6
   (storage), §7 (layout), §8 (data model and IDs), §9 (pipeline), §14 (extensibility), §15 (phases). Read
   §10 (domain design) only as needed; it matters more for P2 onwards.
3. This is a **planning** stage. Don't create git repos, install tools globally, move files or write package
   code unless the owner says so (see §9 Guardrails).
4. A copy-paste kickoff prompt is in §11.

If you are Claude Code, saved memory notes (keyed to `~/WorkSpace/Electricity`) repeat the main conventions.
Codex doesn't see them, so **this file is the source of truth** until P0 creates the `CLAUDE.md` / `AGENTS.md`
files.

---

## 2. Project in brief

A local data pipeline that does three things:
- analyses household electricity usage from SA Power Networks (SAPN) 5-minute interval downloads, and gas
  usage from monthly bills
- prices retail offers against that usage: electricity, gas, and electricity + gas combos
- recommends the best value, with projected savings

Every input (meter download, bill, offer) is a dated, catalogued, immutable dataset. Every recommendation
traces back to the exact offer snapshots, usage datasets and code commit that produced it, and decisions are
logged against recommendation IDs.

There are two repos:
- **`energatron-5000`:** a public-ready tool that other households can eventually use, possibly through AI
  agents and later a web app
- **`demq-elektric`:** the owner's private workspace

---

## 3. Current state (2026-10-23)

```
~/WorkSpace/Electricity/              plain folder; must never be git-initialised
├── energatron-5000/                  tool repo (NOT a git repo yet)
│   └── agent_docs/2026-10/
│       ├── 2026-10-23_data-pipeline-plan.md
│       └── 2026-10-23_handover-next-planning-stage.md   (this file)
└── demq-elektric/                    workspace (NOT a git repo yet)
    ├── data/landing/                 2 SAPN "DETAILED" interval downloads (2-year windows, generated 2025-10 and 2026-10)
    └── legacy/
        ├── notebooks/                3 old Jupyter notebooks (outputs contain personal data)
        └── Electricity-Analysis/     SAPN SUMMARY csv+pdf, a duplicate SAPN DETAILED file, derived hourly JSON,
                                      retailer usage + transactions exports, plots
```

- **Nothing is backed up yet.** Until P0 sets up the DVC remote in iCloud Drive, this disk holds the only
  copies the project knows about.
- **Not created yet:** git repos, GitHub repos, any `CLAUDE.md` / `AGENTS.md`, Python package, DVC.
- **Machine:**
  - macOS 27, Apple silicon
  - **disk about 97% full** (~31 GB free)
  - Installed: system Python 3.15, uv 0.12.19, git 2.55, gh 2.101, Homebrew, pdftotext
  - Not installed: dvc, pre-commit, duckdb, jupyter

---

## 4. Decisions already made — don't reopen unless the owner asks

| # | Decision | Plan § |
|---|---|---|
| 1 | Two sibling repos under `~/WorkSpace/Electricity/`: tool `energatron-5000` (public-ready, code + design docs only) and workspace `demq-elektric` (private). The parent stays a plain folder. | §7, §14.3 |
| 2 | Distribution name `energatron-5000`; Python import package and CLI `energatron`. The workspace depends on the tool as an editable `../energatron-5000`; other households use a git tag. | §7 |
| 3 | Git + DVC, no server. The DVC "local remote" is a folder in iCloud Drive (`~/Library/Mobile Documents/com~apple~CloudDocs/Backups/demq-elektric-dvc/`), marked "Keep Downloaded". Repos and DVC cache stay outside iCloud. | §4, §6 |
| 4 | Privacy boundary: the tool repo holds code only. The workspace git holds hashes and non-personal config (private GitHub repo or `git bundle`). Everything personal is DVC-managed. Data is tracked by directory and renamed to neutral dataset IDs on landing. Private config is a DVC `deps` entry, never `params`. Never set `cache: false`. A denylist pre-commit guard is derived from private config. | §5 |
| 5 | License: **Apache-2.0**. | §16 |
| 6 | Agents: Claude-centric, readable by Codex. `CLAUDE.md` is canonical (plain Markdown, no essential `@imports`, under 32 KiB); `AGENTS.md` is a symlink to it. Skills live in `.claude/skills/<name>/SKILL.md`, with `.agents/skills/<name>` symlinks. Hard guarantees come from git hooks + CI, not agent hooks. | §14.5 |
| 7 | Architecture: pure core (no I/O) → services (use cases) → adapters (CLI now; MCP and HTTP later). Ports: `Catalog`, `BlobStore`, `PipelineRunner`, `OfferProvider`, `SourceParser`. Stage logic is independent of DVC: `dvc.yaml` calls `energatron stage <name>`. A code fingerprint file is a stage dependency. `schema_version` on every persisted artifact. `--json` on every CLI command. | §9.2, §14.1–14.2 |
| 8 | Offers come from the AER CDR Energy Product Reference Data API, plus manual offers (quotes, PDFs). Curated offer rules ship in the tool repo; local overrides live in the workspace. | §10.4 |
| 9 | IDs: dataset `{src}-{source_ts}-{sha8}`; offer snapshot `{planId}~{lastUpdated}~{sha8}`; run `run-{utc}-{gitsha7}`; recommendation `REC-{as_of}-{sha8}` (content-derived, deterministic); decision `DEC-{date}-{nn}`. | §8.4 |
| 10 | Pricing: simulate monthly bills on actual 5-minute usage in local time. Reference year = latest 12 months. Savings are measured against the current plan's latest snapshot. Sensitivity runs on the alternative year, ±10% usage, and conditional discounts on/off. | §10.5, §10.8 |
| 11 | The meter time basis is settled by evidence (the `validate_timebase` stage against the retailer's monthly TOU export), not assumed. | §10.2 |
| 12 | Gas v1: a manual YAML transcription per bill, validated by recomputing the totals. An agent may transcribe; validators decide. | §10.6, §14.5 |
| 13 | Web: a local-first UI (`energatron serve`) comes first. A hosted service only after a separate design and security review. | §14.6 |
| 14 | Docs go in `agent_docs/YYYY-MM/YYYY-MM-DD_<name>.md`, named by creation date. Design docs go in the tool repo; anything that mentions personal data stays in the workspace. | §7 |
| 15 | Everything is Python, with environments, dependencies and tools managed by uv (`uv sync`, `uv run`, `uv add`; no pip, conda or global installs). Another language (e.g. TypeScript on Node) only where Python is the wrong tool, such as the P10 frontend: in its own directory, with no analysis logic. Added 2026-10-23 (plan rev 6). | §4, §14.6 |

---

## 5. Research already done — don't redo it

All checked on 2026-10-23.

**AER CDR Energy Product Reference Data API (public, no auth)**
- List: `GET https://cdr.energymadeeasy.gov.au/{brand}/cds-au/v1/energy/plans?fuelType=ALL&page-size=1000`
  with header `x-v: 1`
- Detail: `GET …/energy/plans/{planId}` with header `x-v: 3`
- GloBird (`globird`): 505 plans nationally, including 32 SAPN electricity and 4 SA gas (distributor "AGN
  Metro/Barossa/…").
- Plan IDs look like `GLO1202240MRE1@EME` (Victorian plans end in `@VEC`).
- Electricity detail fields:
  - `lastUpdated`, `effectiveFrom`, `electricityContract.timeZone: "LOCAL"`
  - `tariffPeriod[]`: seasonal date ranges; TOU windows given with *inclusive* end minutes, e.g. `14:59`;
    `dailySupplyCharge`
  - `discounts[]` (`percentOfBill`, `fixedAmount`), `fees[]`, `solarFeedInTariff[]`, `eligibility[]`
- Gas detail: `singleRate.rates[]` blocks (`volume` per `P1D`), with seasonal periods.
- Combo (dual-fuel) conditions appear **only as free text** (in `eligibility` and discount descriptions), which
  is why curated offer rules are needed.
- The CDR Register (`https://api.cdr.gov.au/cdr-register/v1/energy/data-holders/brands/summary`, `x-v: 1`)
  lists 84 energy brands with the retailers' own base URIs, **not** the AER brand slugs. The slug list
  therefore has to be curated in `params.yaml`.

**SAPN meter files (NEM12 format)**
- Filename: `<NMI incl. checksum>_<from YYYYMMDD>_<to YYYYMMDD>_<generated YYYYMMDDhhmmss>_SAPN_DETAILED.csv`.
  This is why files are renamed on landing.
- No `100` header. The `200` header can repeat (8 times in the newer file), and there are blank lines.
- 5-minute intervals, single `E1` register (consumption only, no export).
- `300` records: 288 values + QualityMethod (`A`, or `V` with `400` detail) + reason fields + a per-day
  **UpdateDateTime**, which is the merge precedence key.
- The two downloads overlap by 347 days.
- Legacy logic, and the bugs worth fixing when porting it, are summarised in plan §3.3.

**Agent tooling**
- Codex reads `AGENTS.md` from the repo root down to the working directory. It also tries the names in
  `project_doc_fallback_filenames` (`~/.codex/config.toml`), and the default instruction size cap is 32 KiB.
- Codex skills live in `.agents/skills/`, scanned from the working directory up to the repo root, plus
  `$HOME/.agents/skills`. Codex follows symlinks; `SKILL.md` needs `name` + `description`.

**Other**
- DVC was acquired by lakeFS in November 2025 and continues as open source for individuals and small datasets.
- With "Optimise Mac Storage" on, iCloud can evict files to dataless placeholders, hence "Keep Downloaded" on
  the remote folder.
- The AEMO NEM12 spec PDF returns HTTP 403 to automated fetches; download it by hand if it's needed.

---

## 6. Unverified assumptions — and when to verify them

| Assumption | Verify in | How |
|---|---|---|
| Which Python versions DVC, pandas and pyarrow support (system is 3.15, probably too new) | **This stage** (needed for P0) | In a scratch dir: `uv run --python 3.13 --with dvc dvc version`; try 3.14 too; pick the newest that works |
| "Keep Downloaded" works on the iCloud remote folder on this macOS version | P0 | Set it in Finder, push, check the files aren't dataless |
| NEM12 data uses NEM time (UTC+10, no DST, interval-ending) | P2 | `validate_timebase` against the retailer's monthly TOU export |
| CDR prices are GST-exclusive | P4 | Compare one plan's CDR detail with its Energy Made Easy BPID |
| How CDR planIds map to Energy Made Easy BPI page IDs (`…MRE1@EME` vs `…MRE`) | P4 | Spot-check a few plans |
| The AER API allows browser (CORS) access | P11 only | — |

---

## 7. Open questions for the owner

**Needed before P0 (ask these first):**
1. **Workspace git history:** push `demq-elektric` to a private GitHub repo, or keep it local with a
   `git bundle` backup in iCloud?
2. **Tool repo visibility:** public on GitHub from the first commit (the design allows it), or private until
   P8?
3. **iCloud Advanced Data Protection** (end-to-end encryption): on or off? If off, consider turning it on
   before personal data goes to iCloud.
4. **Backup path:** confirm `iCloud Drive/Backups/demq-elektric-dvc/`. Also, can some disk space be freed
   before P0?

**Needed later:**
5. **Before P1:** do the older (2022–2025) SAPN downloads referenced by the legacy notebooks still exist
   somewhere? Landing them extends the history.
6. **Before P4:** the network tariff code on the electricity bill; whether pay-on-time / direct-debit
   discounts are reliably met; which retailer brands to track (default: all serving SAPN residential).
7. **Before P5:** gas retailer, bill format (PDF?), actual vs estimated reads, and the MIRN (it goes in private
   config only).
8. **Any time:** solar, battery or EV plans in the next year or two? They would raise the priority of FiT,
   export and EV-plan modelling.

---

## 8. Goal of this stage: deliverables and exit criteria

Turn the plan into specs detailed enough that the next session can **implement P0 and P1 without making
design decisions**. Write each deliverable as a dated doc in `energatron-5000/agent_docs/<YYYY-MM>/`, and
review each one with the owner before starting the next.

Suggested order: D1 first, because it constrains the others.

**D1 — Core architecture spec** (`…_core-architecture-spec.md`): the one-way doors made concrete.
- Package layout and import rules: core must not import adapters. Consider enforcing this with an
  import-linter contract.
- Port interfaces with method signatures: `Catalog`, `BlobStore`, `PipelineRunner`, `OfferProvider`,
  `SourceParser`.
- Conventions:
  - `schema_version` and the migration hook
  - ID-generation functions (exact canonicalisation rules for hashing)
  - error model
  - CLI rules: the `--json` output envelope, exit codes, no prompts without a TTY
- The stage-function contract (`run_stage(name, inputs, params, out_dir)`), the `_provenance.json` schema and
  the code-fingerprint algorithm.

**D2 — P0 bootstrap spec** (`…_p0-bootstrap-spec.md`):
- **Step 0:** a one-off safety copy of `demq-elektric/data/landing/` and `legacy/` to iCloud, before anything
  else, because nothing is backed up yet.
- **Python version:** from §6.
- **Tool repo:** skeleton; `pyproject.toml` (dependencies, `energatron` entry point); ruff, pyright and pytest
  config; GitHub Actions CI; pre-commit config; `LICENSE` + `NOTICE` (Apache-2.0).
- **`privacy_guard.py`:**
  - inputs: staged diff, denylist path
  - rules: denylist strings; data-type file extensions; `*.dvc` / `dvc.lock` rejected in the tool repo
  - how the denylist is derived from `private/config.toml`
  - test cases
- **`check_agent_links.py`:** spec.
- **Agent files:** draft `CLAUDE.md` contents for all three locations (parent, tool repo, workspace), each
  within the size budget; the `new-doc` skill; the workspace's `.claude/settings.json` deny rules.
- **Workspace bootstrap commands:**
  - `git init`, uv project with editable `../energatron-5000`, DVC as dev dependency
  - `dvc init`, `dvc remote add -d --local …`, `dvc install --use-pre-commit-tool`
  - `.gitignore`, `private/config.toml` (with `salt`), `dvc add legacy private`
- **Restore-drill script** and the GitHub repo creation commands (per answers 1–2).

**D3 — P1 landing & catalog spec** (`…_p1-landing-catalog-spec.md`):
- Catalog record pydantic model; `source_ts` derivation for each source type.
- Detector rules for each source type: `sapn_interval`, `sapn_summary`, `retailer_usage`, `retailer_txn`,
  PDFs, offers — filename patterns plus content sniffing.
- Landing algorithm: hash → idempotency check → NMI check against private config → neutral rename →
  read-only → catalog append → `dvc add` → commit message format.
- `energatron land` CLI contract: arguments, `--json` output and exit codes.
- Synthetic NEM12 fixture generator spec (repeated `200`, blank lines, `V` + `400`, DST-adjacent dates).
- Test list and acceptance checks (plan §15 P1 row).

**D4 — Work breakdown** (`…_p0-p2-work-breakdown.md`):
- PR-sized tasks for P0–P2, in order, with acceptance checks.
- Consider a **thin vertical slice** early (land → parse → merge → daily-totals report) to prove the layering
  before building wide. Propose it to the owner; it isn't decided.

**Exit criteria**
- D1–D3 approved by the owner.
- The "before P0" questions are answered.
- The Python version has been verified.
- D4 exists.
- The plan's "Revisions" line notes any changes this stage made to it.

---

## 9. Guardrails

- **Planning only:** no `git init`, no global installs, no moving or deleting files, unless the owner says so.
  Throw-away checks in a scratch or temp directory (e.g. the Python/DVC check) are fine.
- **Never write identifiers or usage figures into `energatron-5000`:** NMI (with or without the checksum
  digit), meter serial, retailer account/customer/invoice numbers, MIRN, address, postcode, kWh or $ amounts.
  - Use placeholders such as `<NMI>`.
  - Before finishing, scan new docs for identifiers. Find them by reading the workspace files, but don't write
    them anywhere.
- Workspace data may be read for analysis, but excerpts must not be copied into tool-repo docs.
- **Don't delete anything** in `demq-elektric/`: there's no backup yet.
- **Never `git init`** `~/WorkSpace/Electricity/` itself.
- **Docs:** new docs are dated files. Don't rename existing ones. When changing the plan, add a line to its
  "Revisions" header.
- **Keep instructions and skills tool-neutral** (no Claude-only tool names), since Codex may run them.
- **Prefer recommendations to surveys.** The decisions in §4 are settled; build on them.

---

## 10. Watch-outs

- **Disk is ~97% full:** iCloud eviction pressure and failed writes are both possible. Raise this with the
  owner before P0.
- **System Python 3.15** is likely ahead of DVC/pyarrow support; pin the project's Python with uv.
- **Symlinks** (`AGENTS.md`, `.agents/skills`) may not survive some Windows checkouts. The documented fallback
  is Codex's `project_doc_fallback_filenames`.
- **Duplicate file:** `legacy/Electricity-Analysis/data/` has a SAPN DETAILED file with the same name as one in
  `data/landing/`. Landing must dedupe it by hash, not by name.
- **Notebook outputs:** the legacy notebooks contain personal data in their outputs. They're archived with DVC
  (`dvc add legacy`), never committed.

---

## 11. Kickoff prompt (copy-paste into a new session)

```
We're continuing the energatron-5000 / demq-elektric project in ~/WorkSpace/Electricity.
Read energatron-5000/agent_docs/2026-10/2026-10-23_handover-next-planning-stage.md in full,
then the sections of 2026-10-23_data-pipeline-plan.md it lists. This session is planning only.
First, ask me the "needed before P0" questions from §7 of the hand-over and verify the Python
version for DVC (§6). Then write deliverables D1–D4 from §8 as dated docs in
energatron-5000/agent_docs/, one at a time, checking each with me before moving on.
Follow the guardrails in §9 — especially: no personal identifiers or usage figures in energatron-5000.
```

---

## 12. Sources from the first session

- AER energy product reference data: https://www.aer.gov.au/energy-product-reference-data
- GloBird residential tariffs: https://www.globirdenergy.com.au/offers-rates/all-tariffs/all-tariffs-resident/
- AEMO MDFF NEM12/NEM13 spec v2.4:
  https://aemo.com.au/-/media/files/electricity/nem/retail_and_metering/metering-procedures/2021/mdff-specification-nem12-nem13-v24.pdf
- lakeFS acquires DVC: https://lakefs.io/blog/lakefs-acquires-dvc/
- iCloud Drive in macOS Sonoma: https://eclecticlight.co/2024/03/18/how-icloud-drive-works-in-macos-sonoma/
- Codex AGENTS.md guide: https://developers.openai.com/codex/guides/agents-md
- Codex skills: https://learn.chatgpt.com/docs/build-skills.md
- DVC docs: https://dvc.org/doc
