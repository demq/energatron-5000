# P9 agent interface spec

- **Created:** 2026-11-14
- **Status:** Approved 2026-11-14 (by the agent, on the owner's /phase instruction of 2026-11-14), after a
  self-review; Q1-Q9 as written. Its §10 edits are in the plan as rev 18, in D1 as rev 10 and in D2 as rev 3
- **Deliverable:** P9.1.1 in `TASKS.md`
- **Builds on:** [`2026-10-23_data-pipeline-plan.md`](../2026-10/2026-10-23_data-pipeline-plan.md) rev 17 ("the plan"),
  [`2026-10-23_core-architecture-spec.md`](../2026-10/2026-10-23_core-architecture-spec.md) rev 9 ("D1", binding for all
  code), the P0 spec ([`2026-10-23_p0-bootstrap-spec.md`](../2026-10/2026-10-23_p0-bootstrap-spec.md), "D2") rev 2 §7.4,
  the P5 spec §3 (gas bill records), the P6 spec §6-§9 (recommendation, decisions), the P7 spec §4 (`status`) and the
  P8 spec §3, §6 (the workspace template and `upgrade`)
- **For:** the agent implementing P9; `/done` of P9.1.1 writes P9's sub-tasks from §2
- **Privacy note:** contains no identifiers or usage figures, and nothing added to it may. Examples use placeholders.

---

## 1. What this spec fixes

P9 lets an AI agent (Claude Code, Codex or any MCP client) work a workspace through a small set of tools instead of
reading files: see where things stand, land new files, refresh offers, run the pipeline, read the usage and the
recommendation, record a decision the owner made, and transcribe a gas bill that deterministic checks then accept or
reject. Plan §15's P9 row: "Local MCP server over the services; workspace skills/commands; agent-assisted bill
transcription with validators and `extracted_by`. Acceptance: an agent completes a monthly check-in using only the
tools, without reading `private/` or raw intervals." D1 and the earlier specs stay binding; §10 lists every change this
spec makes.

**What P9 builds**
- Four read-only commands that give an agent what it otherwise reads from files: `datasets`, `usage`, `compare`,
  `explain` (§3).
- Gas bill transcription with validators: `gas_bill_record` v2 with `extracted_by`, `gas-bill check`, `gas-bill write`,
  and `gas-bill text` (§4).
- `energatron mcp`: a local MCP server on stdio whose tools run those commands (§5, §6).
- Workspace skills and the MCP registration in the template (template v2), and the README's "Agents" section (§7).

**Not in P9** (later, or the owner's): an HTTP API or web UI (P10); remote or multi-user MCP transports (HTTP/SSE);
MCP resources, prompts or sampling; tool-repo skills (`privacy-check`, `add-source-parser`, `add-region`, `release`,
plan §14.5); agent-drafted offer rules (they stay curated YAML, reviewed by pull request); electricity bill
transcription (electricity bills are cross-checked by `backtest`, not transcribed); running a real agent session, which
is the owner's (§11).

**Facts it relies on** (checked 2026-11-14, structure only)
- Every command already prints one `cli_result` envelope with `--json`, redacted through the workspace denylist as data
  (D1 §8.5, §8.7), with stable exit codes and no prompts without a terminal (D1 §8.6).
- No existing command returns interval rows: `status`, `land`, `fetch-offers`, `offers diff`, `run`, `decide`,
  `landing list` return counts, IDs, plan names and statuses.
- The recommendation document (`data/reports/recommendations/recommendation.json`, `recommendation` v1) already holds
  the ranked options, the priced plans per fuel, the current plans, the sensitivity verdicts, the usage windows and the
  inputs; the run manifests (`data/runs/run-*.json`) name it. `services/status.py` reads both (`read_manifests`,
  `read_recommendation`).
- The usage marts hold `summary.json` (`usage_summary` v1) and monthly tables (`monthly.parquet`,
  `tou_monthly.parquet`); the gas profile holds `summary.json` (`gas_profile_summary` v1) and `monthly.parquet`.
- `gas_bill_record` v1 has `transcribed_by: owner | agent`; `core/gas_bill.check_record` recomputes a record from its
  own reads and lines (the P5 spec §6 checks); `gas-bill new` writes the skeleton into `data/landing/`.
- The privacy guard allows JSON files only in listed places (D2 §7.4); `.claude/settings.json` is one of them.
- The workspace template has one symlink (`AGENTS.md`); `upgrade` handles managed files only (P8 spec §6).

---

## 2. Order of work

`/done` of P9.1.1 turns the rows into sub-tasks.

| # | Sub-task | Builds | Sections |
|---|---|---|---|
| 1 | P9.2.1 Read commands | `services/{datasets,usage_summary,compare,explain}.py`, their result kinds (v1), the commands `datasets`, `usage`, `compare`, `explain` | §3 |
| 2 | P9.2.2 Gas bill transcription | `gas_bill_record` v2 and its migration, the parser's `extra.extracted_by`, `gas-bill new --extracted-by`, `gas-bill check`, `gas-bill write`, `gas-bill text`, two result kinds; the demo's records at v2 | §4 |
| 3 | P9.2.3 MCP server | `adapters/mcp/` (protocol, tools, runner, server), `energatron mcp [--allow-documents]`, the protocol, tool and privacy tests | §5, §6 |
| 4 | P9.2.4 Workspace skills and template | four workspace skills and `.mcp.json` in the template (v2), `upgrade` adds missing symlinks, the guard allows `.mcp.json`, README "Agents", CHANGELOG, `test_mcp_check_in` | §7, §8 |
| 5 | P9.3.1 Acceptance (agent part) | §11's agent checks; the owner's workspace gains `.mcp.json` and the tool-aware skills | §11 |
| 6 | P9.3.2 Owner: a real agent session | a monthly check-in in Claude Code through the MCP tools; push; backup | §11 |

P9.2.2 changes `core/models/gas.py`, which the gas stages import: their code entries change once, and the next `run`
reruns them (same outputs). No new error code or warning is added, so `core/errors.py` and the other stages don't move.

---

## 3. Read commands (`services/datasets.py`, `usage_summary.py`, `compare.py`, `explain.py`)

All four are read-only: no lock, no writes, no commits (D1 §8.8 already lists `explain` and `compare` as lock-free).
Each takes `--json` and the common options. A missing input gives `null` (the result says what is missing in
`missing`); a document that doesn't validate → `E_INVALID_INPUT` naming the workspace-relative path, never a value, as
`status` does. They return workspace data an agent needs (kWh and $ figures, plan names); they never return
identifiers, file names, notes, raw rows or anything under `private/`.

### 3.1 `energatron datasets [--source SOURCE] [--limit N]`

The landed datasets from the catalogs, newest `landed_at` first. `--limit` defaults to 50 (1-1000; `E_USAGE` outside).

`datasets_result` v1:
- `counts`: `{source type: records}` over every catalog (all source types, zero included).
- `total`, `shown`.
- `datasets`: `[{dataset_id, source, landed_at, source_ts, coverage: {start, end} | null, detector: "<name> v<N>",
  extracted_by: str | null, supersedes: str | null}]`. `extracted_by` is the record's `extra.extracted_by` when present
  (§4), else null.
- Never: `original_filename`, `notes`, `stored_path`, `bytes`, `file_mtime`, any other `extra` field.

### 3.2 `energatron usage [--months N]`

Aggregates from the usage and gas marts. `--months` (1-60, default 13) limits each monthly list to the newest N months.

`usage_result` v1:
- `electricity`: null when `data/marts/usage_profile/summary.json` is missing; else
  - `suffixes`: `{suffix: {first_date, last_date, covered_months, window_start, window_end, window_coverage,
    avg_daily_kwh, base_load_kw, peak_demand_kw, load_factor, tou_shares, months}}`, the summary's fields as they are;
    `months`: `[{month, kwh, avg_daily_kwh, coverage, tou: {label: kwh}}]` from `monthly.parquet` and
    `tou_monthly.parquet` (NaN → null).
  - `excluded_suffixes`, `window_sets_used`, `dst_days`, `checks`: the summary's.
- `gas`: null when `data/marts/gas_profile/summary.json` is missing; else the summary's `coverage_start`,
  `coverage_end`, `gaps` (count), `estimated_periods`, `reference_start`, `reference_end`, `reference_days`,
  `reference_bills`, `reference_mj`, `partial_year`, `checks`, and `months: [{month, mj}]` from its `monthly.parquet`.
- `missing`: the paths that weren't there.

Never: daily or interval rows, the heatmap, the hourly profile.

### 3.3 `energatron compare [--fuel electricity|gas] [--limit N] [--brand TEXT]`

The current recommendation's rankings, from `recommendation.json` alone. `--limit` (1-200, default 10) applies to each
list; `--brand` keeps plans and options whose brand contains the text (case-insensitive).

`compare_result` v1: `recommendation_id`, `as_of`, `action`, `current` (the recommendation's `current`), `options`
(the first N `options`, each as in the recommendation), and `plans: {fuel: {total, shown, current_rank: int | null,
items: [PricedTariff]}}` for the fuels asked (both by default). `current_rank` is the current plan's rank in `priced`
when it is there. `missing`: the paths that weren't there. No recommendation: `recommendation_id`, `as_of`, `action`
and `current` null, empty lists, and `missing` names the file.

### 3.4 `energatron explain [REC_ID] [--options N]`

What the current recommendation says, how sure it is and where it came from. `REC_ID`, when given, must be the current
recommendation's ID; otherwise `E_INVALID_INPUT` (`reason: "not_current"`: older recommendations are in the workspace's
git history, not on disk). `--options` (1-20, default 3) limits the options shown.

`explain_result` v1:
- `recommendation_id`, `as_of`, `recheck_by`, `action`, `robust`, `usage` (the recommendation's), `current`,
  `options` (first N), `sensitivity` (all verdicts), `checks`.
- `chosen`: for option 1, each fuel's `notes`, `approximations` and `status` (what to check before switching).
- `run`: the newest run manifest naming this recommendation: `run_id`, `draft`, `finished_at`, `workspace_head`,
  `tool_commit`, `tool_dirty`; null when none names it.
- `inputs`: `dataset_count`, `offer_snapshot_count`, `params_sha256`, `stage_sha256` (the IDs themselves via
  `datasets`; the recommendation file holds them all).
- `behind`: `{datasets_not_in_run: int, code_stale: [stage names]}`; the CLI passes the stale stages as it does for
  `status` (D1 §11.4).
- `outcomes`: `data/reports/outcomes/summary.json` (`outcomes_summary` v1) when it exists and names a decision, else
  null.
- `trace`: the workspace-relative paths a figure comes from (`options`: the bundles mart, `elec`/`gas`: the cost
  totals, `usage`: the usage marts), as `explain-recommendation` lists them today.
- `missing`: the paths that weren't there. No recommendation (and no `REC_ID` given): every field above null or empty
  and `missing` names the file.

---

## 4. Gas bill transcription (`gas_bill_record` v2, `services/gas_bill.py`)

"Agent proposes, validators decide" (plan §14.5): an agent (or the owner) fills a record; the P5 checks recompute it
from its own reads and lines and refuse it before it is written or landed; the record says who extracted it, and the
catalog carries that.

### 4.1 `gas_bill_record` v2

`transcribed_by` is replaced by `extracted_by`: `manual` (a person typed it), or `agent:<model id>` where the model id
matches `[A-Za-z0-9][A-Za-z0-9._:/-]{0,79}` (for example `agent:<model-id>` as the agent reports its own model).
Migration v1 → v2: `owner` → `manual`, `agent` → `agent:unknown`. Landed v1 records stay as they are in the raw zone
(immutable) and are upgraded when read (D1 §5). The skeleton is v2, with `extracted_by: ""`. Everything else is
unchanged.

The `gas_bill_record` parser (`adapters/sources/gas_bill_record.py`) adds `extracted_by` to `SourceInfo.extra`, so the
catalog record has `extra.extracted_by` for records landed from P9 on; its version goes up by one. Parsed sources need
no field: their `detector` (name and version) already says which parser read them (plan §14.5's "`parser vN`").

### 4.2 Commands

- `gas-bill new PDF_DATASET_ID [--extracted-by VALUE] [--force]`: as in P5, with `extracted_by` pre-filled when given
  (validated as §4.1; `E_INVALID_INPUT`, `field: "extracted_by"`).
- `gas-bill check SOURCE`: `SOURCE` is a workspace path or `-` (stdin), YAML or JSON (JSON is YAML). Read-only, no
  lock. Parses the record (`E_INVALID_INPUT` with the field, never the value, when it doesn't validate), checks that
  `pdf_dataset_id` names a landed gas bill PDF, and runs `check_record` with `validation.gas_bills.tolerance` and the
  GST rate as the `gas_bills` stage does. Exit 0 when every check passes, else `E_CHECK_FAILED` (exit 6) with the
  result in `data` and the failing check names in `details.failed`.
- `gas-bill write SOURCE [--force] [--dry-run]`: the same checks, then writes the record as canonical YAML
  (`kind`, `schema_version` first; `yaml.safe_dump`, no comments) to `data/landing/gas_bill_record-<pdf id>.yaml`. A
  failing check → `E_CHECK_FAILED` and nothing written; an existing file needs `--force` (`E_CONFIRMATION_REQUIRED`).
  Takes the lock (it writes under `data/`, D1 §8.8). Landing stays a separate `land` (its own commit).
- `gas-bill text PDF_DATASET_ID`: the text of a landed gas bill PDF (pypdf, as `adapters/sources/pdf_text.py` reads it;
  pages joined by a form feed), for transcription. Read-only. The envelope is redacted through the denylist like
  every other (D1 §8.7), but a bill holds more personal detail than the denylist covers (a street address, names): the
  MCP server exposes it only on request (§6.3).

`gas_bill_check_result` v1 (for `check` and `write`): `pdf_dataset_id`, `extracted_by`, `period_start`, `period_end`,
`checks: {name: bool}` (the P5 spec §6 per-record checks), `recomputed_total`, `printed_total`, `path` (null for
`check`, the written path for `write`), `written: bool`, `dry_run`. `gas_bill_text_result` v1: `pdf_dataset_id`,
`pages`, `text`.

### 4.3 The demo and the skill

The demo's generated records become v2 with `extracted_by: manual` (they are made by code, not by an agent). The
`transcribe-gas-bill` skill (§7) moves to `gas-bill text`, `gas-bill check`, `gas-bill write` and `land`, with
`--extracted-by agent:<model id>`.

---

## 5. The MCP server (`adapters/mcp/`, `energatron mcp`)

### 5.1 Command

`energatron mcp [--allow-documents] [--workspace PATH] [-v] [--debug] [--json]`. It finds the workspace at start
(D1 §8.3); outside one → `E_NOT_A_WORKSPACE`, printed as every command prints errors, and exit 3. Once it serves,
stdout carries only the protocol (§5.2): it is the one command whose stdout isn't a single envelope (D1 §8.4).
`--json` is accepted and changes nothing once serving. Logs go to stderr (D1 §8.9). It takes no lock; each tool call
takes what its command takes. It reads no private config itself. EOF on stdin ends it with exit 0.

At start it duplicates file descriptor 1 for the protocol and points fd 1 at stderr, so nothing a library or child
process writes can corrupt the protocol stream.

### 5.2 Protocol (`adapters/mcp/protocol.py`)

MCP over stdio: UTF-8 JSON-RPC 2.0 messages, one per line, no embedded newlines. Requests are handled one at a time,
in order. Supported protocol versions: `2025-11-25`, `2025-06-18`, `2025-03-26`, `2024-11-05`; `initialize` answers
with the client's version when it is one of them, else the newest.

| Message | Answer |
|---|---|
| `initialize` | `{protocolVersion, capabilities: {tools: {listChanged: false}}, serverInfo: {name: "energatron", version}, instructions}`; `instructions` is two sentences on what the tools are for and that figures stay local |
| `notifications/initialized`, `notifications/cancelled`, any notification | none |
| `ping` | `{}` |
| `tools/list` | `{tools: [...]}` (§6), no pagination |
| `tools/call` | §5.3 |
| another method | error `-32601` |
| unparseable line | error `-32700`, `id: null` |
| not an object, wrong `jsonrpc`, missing `method` | error `-32600` |
| `tools/call` with an unknown tool name | error `-32602` |

Protocol error messages are fixed strings; they never echo what was sent.

### 5.3 A tool call

1. Validate `arguments` against the tool's input model (pydantic, `extra="forbid"`). Invalid → a tool result with
   `isError: true` and an `E_USAGE` envelope built by the server, naming the field only.
2. Build the command line from the arguments (§6) and run `[sys.executable, "-m", "energatron", *argv, "--json",
   "--workspace", <root>]` with `cwd` the workspace root, stdin the tool's input document (only the `gas-bill` tools,
   else empty), stdout and stderr captured, and the tool's timeout (§6). The child is the CLI itself, so every result
   is the same envelope, redaction, lock and exit code as the command line.
3. The answer is `{content: [{type: "text", text: <the envelope, one JSON line>}], structuredContent: <the envelope>,
   isError: not envelope.ok}`.
4. If the child's stdout isn't exactly one valid `cli_result` envelope (a crash), or it times out (it is killed), the
   server builds an `E_SUBPROCESS` envelope (`details: {tool, reason: "no_envelope" | "timeout"}`) and answers with
   `isError: true`. The child's stderr is logged at DEBUG only, through the redacting formatter.

The server holds no state between calls besides the workspace root and `--allow-documents`.

### 5.4 Why a subprocess per call

It keeps one contract: the agent sees exactly what `energatron <command> --json` prints, so an agent without MCP
(Codex with only a shell, a script) uses the same commands and gets the same results. A crash, a leaked handle or a
child's output can't take the server down or corrupt the protocol. The adapter imports neither services nor driven
adapters (D1 §3's rule holds trivially). The cost, about a second of start-up per call, is small next to `run`.

---

## 6. The tools (`adapters/mcp/tools.py`)

One table: name, title, description (what it is for, when to use it, and for write tools that they commit), input
model, the command line it builds, annotations (`readOnlyHint`, `destructiveHint: false`, `idempotentHint`,
`openWorldHint`) and timeout. The JSON Schema in `tools/list` is the input model's `model_json_schema()`.

### 6.1 Read tools (`readOnlyHint: true`, timeout 120 s)

| Tool | Arguments | Command |
|---|---|---|
| `status` | none | `status` |
| `list_datasets` | `source?`, `limit?` | `datasets [--source] [--limit]` |
| `list_inbox` | none | `landing list` |
| `usage_summary` | `months?` | `usage [--months]` |
| `compare_offers` | `fuel?`, `limit?`, `brand?` | `compare …` |
| `explain_recommendation` | `recommendation_id?`, `options?` | `explain [REC_ID] [--options]` |
| `offers_diff` | `plan_id?` | `offers diff [--plan]` |
| `check_gas_bill_record` | `record` (object) | `gas-bill check -`, the record as stdin |

### 6.2 Write tools (`readOnlyHint: false`, `destructiveHint: false`)

| Tool | Arguments | Command | Timeout |
|---|---|---|---|
| `land_file` | `path`, `source?`, `supersedes?`, `dry_run?` | `land <path> [--source] [--supersedes] [--dry-run]` | 600 s |
| `fetch_offers` | `dry_run?` | `fetch-offers [--dry-run]` (`openWorldHint: true`) | 1800 s |
| `run_pipeline` | `draft?` | `run [--draft]` | 3600 s |
| `record_decision` | `recommendation_id`, `switch_date`, `elec?`, `gas?`, `evidence?` (list), `note?`, `dry_run?` | `decide …` | 120 s |
| `new_gas_bill_record` | `pdf_dataset_id`, `extracted_by?`, `force?` | `gas-bill new …` | 120 s |
| `write_gas_bill_record` | `record` (object), `force?`, `dry_run?` | `gas-bill write -`, the record as stdin | 120 s |

- `land_file`'s `path` must be a relative POSIX path that resolves (symlinks followed) to a file or folder inside
  `data/landing/`; anything else → `E_USAGE` (`field: "path"`) before any command runs. The tool can't land an
  arbitrary file from the machine.
- `record_decision`'s description says to call it only when the owner says they have switched or will, naming the
  plans, and to dry-run first.
- No tool runs `backup`, `landing clear`, `schedule`, `init`, `upgrade`, `migrate`, `denylist`, `stage` or `verify`:
  those stay with the owner or the command line.

### 6.3 Opt-in tool (`--allow-documents` only)

| Tool | Arguments | Command |
|---|---|---|
| `read_gas_bill_text` | `pdf_dataset_id` | `gas-bill text <id>` (read-only, 120 s) |

Without the flag the tool isn't listed and calling it is `-32602`, like any unknown tool. Plan §14.5: "Users opt in to
anything more." The README says what the flag sends to the model provider.

### 6.4 Privacy

- Results are the CLI's envelopes, redacted through the workspace denylist by the child. No tool returns interval
  rows, `private/` content, file names from `data/raw/`, catalog notes or identifiers (§3).
- What a tool returns goes to the model provider. The figures `usage_summary`, `compare_offers` and
  `explain_recommendation` return (kWh, $) are the workspace's own aggregates, the same an agent reads today from the
  marts; the README and the tool descriptions say so.

---

## 7. Workspace skills and the template (template v2)

### 7.1 Skills

Four skills, generic (no owner names, no figures), each a `SKILL.md` with `name` and `description` frontmatter, written
so either agent can follow them (plan §14.5): each step names the MCP tool and the equivalent command (`uv run
energatron … --json`), so a session without the server works the same way.

| Skill | Steps |
|---|---|
| `monthly-check-in` | `status`; `list_inbox` and `land_file` (dry run, then real) for new files; `fetch_offers`; `run_pipeline` when anything landed or the run is behind; `offers_diff`; `explain_recommendation` and `compare_offers` against the previous run; outcomes when a decision exists; report, then `status` again and the owner-only items (backup, drill, inbox) |
| `explain-recommendation` | `explain_recommendation`, then `compare_offers` for alternatives and `usage_summary` for the usage behind it |
| `record-decision` | only on the owner's word: `explain_recommendation` (current, clean run), the plans from `compare_offers`, optional evidence via `land_file`, `record_decision` dry run, then real; remind the owner of `current_plans` and the backtest tariffs |
| `transcribe-gas-bill` | `land_file` the PDF (`source: gas_bill`); `read_gas_bill_text` (when allowed) or the owner reads it; fill the record; `check_gas_bill_record` until every check passes; `write_gas_bill_record`; `land_file`; `run_pipeline` |

They never read `private/` or the raw zone, never run a backup or push, and keep figures in the workspace.

### 7.2 Template changes (`TEMPLATE_VERSION` 2)

| Written as | Class |
|---|---|
| `.claude/skills/<name>/SKILL.md` (four) | managed |
| `.agents/skills/<name>` → `../../.claude/skills/<name>` (four) | symlink |
| `.mcp.json`: `{"mcpServers": {"energatron": {"command": "uv", "args": ["run", "--frozen", "energatron", "mcp"]}}}` | seed |
| `CLAUDE.md` | seed (gains an "Agents" paragraph naming the skills and tools; existing workspaces keep theirs) |

- `upgrade` creates a template symlink that is missing (status `added`) and leaves an existing path alone (status
  `unchanged` when it is the same link, `modified` with `W_TEMPLATE_MODIFIED` otherwise; never replaced). It writes new
  seed files that are missing (`added`) and never touches existing ones. Both are committed with the managed files.
- The template's `check_agent_links.py` copy already checks `.agents/skills/` links.
- The privacy guard allows `.mcp.json` at the repo root in both modes (D2 §7.4; the tool repo may carry one for its own
  sessions), and the template's byte copy is refreshed in the same change.

### 7.3 README "Agents"

What the tools are, how to register the server (Claude Code: the workspace's `.mcp.json`, approved on first use; Codex:
`codex mcp add energatron -- uv run --directory <workspace> --frozen energatron mcp`; others: the same command), the
tool list, `--allow-documents`, what goes to the model provider, and that long calls (`run_pipeline`) may need a longer
client timeout. `test_readme` checks the command lines.

---

## 8. Tests

- `test_datasets`, `test_usage_summary`, `test_compare`, `test_explain` (unit, the fixture workspace's marts and a
  written recommendation): fields, limits and filters, missing inputs → nulls and `missing`, an invalid document →
  `E_INVALID_INPUT` naming the path, `not_current`; no original file name, note or synthetic identifier in any result.
- `test_gas_bill_record` additions: v2 validation of `extracted_by`; the v1 → v2 migration of both values; golden
  schemas.
- `test_gas_bill_check` (unit): a consistent record passes; a wrong total or MJ fails the named check (exit 6, nothing
  written); `write` writes canonical YAML that `land --dry-run` accepts; `--force`; stdin; a non-landed PDF ID.
- `test_gas_bill_text` (unit, a generated PDF).
- `test_mcp_protocol` (unit, the server over in-memory streams and a fake runner): `initialize` version negotiation;
  notifications get no answer; `ping`; `-32700`, `-32600`, `-32601`, `-32602`; invalid arguments → `isError` with
  `E_USAGE` naming the field and not the value; every answer is one line; responses keep request IDs (number and
  string).
- `test_mcp_tools` (unit): each tool's command line for given arguments; every option a tool uses is one the CLI
  declares; `land_file` path rules (absolute, `..`, outside `data/landing/`, a symlink out); `read_gas_bill_text` only
  with `--allow-documents`; schemas are valid JSON Schema objects with `additionalProperties: false`.
- `test_mcp_server` (integration): `energatron mcp` as a real child over pipes against a scratch workspace: initialize,
  `tools/list`, `status`, a failing tool (`isError`, the CLI's code), a timeout and a non-envelope crash (a test runner
  stub), stdout carries only protocol lines even when a child writes to fd 1.
- `test_mcp_check_in` (integration, the demo workspace): a scripted client does §7.1's check-in without
  `fetch_offers` (no network in tests: the demo's frozen offers are already landed): `status`, `list_inbox`, one more
  synthetic gas bill (the demo's generators, the month after its last bill) through `land_file`,
  `new_gas_bill_record`, `check_gas_bill_record` (a wrong total first: refused), `write_gas_bill_record` and
  `land_file`, then `run_pipeline`, `offers_diff`, `explain_recommendation`, `compare_offers`, `usage_summary`,
  `list_datasets` (the record's `extracted_by`), `status`. Every answer is checked: no denylist entry (the demo NMI, serial,
  postcode), no `data/raw/` file name, no key of an interval table (`meter_start`, `utc_start`, `local_start`), no path
  under `private/`.
- `test_template` additions: v2 files, classes, the four symlinks; `upgrade` from a v1 workspace adds the skills, the
  links and `.mcp.json`, leaves an edited `CLAUDE.md` alone; the guard allows `.mcp.json`.
- CLI one-document checks (D1 §13) for every new command.

---

## 9. Kinds, codes and warnings added in P9

- Kinds (v1 unless noted, golden schemas): `datasets_result`, `usage_result`, `compare_result`, `explain_result`,
  `gas_bill_check_result`, `gas_bill_text_result`; `gas_bill_record` v2.
- Codes and warnings: none new (`E_USAGE`, `E_INVALID_INPUT`, `E_CHECK_FAILED`, `E_CONFIRMATION_REQUIRED`,
  `E_SUBPROCESS`, `W_TEMPLATE_MODIFIED` cover P9).
- CLI: `datasets`, `usage`, `compare`, `explain`, `gas-bill check|write|text`, `mcp`. Locks: `gas-bill write` takes
  it; the rest don't (`mcp`'s tool calls take what their commands take).
- Workspace files: `.claude/skills/*` (managed), `.agents/skills/*` (links), `.mcp.json` (seed).

---

## 10. Plan, D1 and D2 changes

To be applied on `/done` of P9.1.1, as plan rev 18, D1 rev 10 and D2 rev 3.

**Plan**
- §14.5 "Later (P9)": made concrete: the tools are §6's (the plan's names, with `status`, `list_inbox`, `offers_diff`
  and the gas bill tools added, `usage_summary` for the plan's usage tool); each runs the CLI command as a child
  process; the opt-in is `--allow-documents`; `extracted_by` is a field of `gas_bill_record` v2 that the catalog
  carries in `extra.extracted_by` (parsed sources keep `detector` for "`parser vN`"); agent-drafted offer rules stay
  out (curated by pull request). The workspace skills ship in the template.
- §14.7: the MCP server is built (P9).
- §15: the P9 row's acceptance is made concrete as this spec's §11; "P9-P11 are not scheduled" becomes "P9 started
  2026-11-14; P10-P11 are not scheduled".

**D1 (rev 10)**
- §2: services gain `datasets.py`, `usage_summary.py`, `compare.py`, `explain.py` (P9); adapters gain `mcp/`
  (`protocol.py`, `tools.py`, `runner.py`, `server.py`); `cli/commands/` gains `datasets.py`, `usage.py`,
  `compare.py`, `explain.py`, `mcp.py`.
- §3: the `mcp` driving adapter runs each tool as an `energatron … --json` child process and imports no services or
  driven adapters; the `cli-via-wiring` contract gains a twin for `energatron.adapters.mcp`, which also may not import
  `energatron.services`.
- §5: `gas_bill_record` v2 (`extracted_by`), with its migration.
- §8.4: `energatron mcp` is the exception: once it serves, stdout carries MCP JSON-RPC lines, not one envelope.
- §8.8: `gas-bill write` takes the lock; `datasets`, `usage`, `gas-bill check`, `gas-bill text` and `mcp` don't.

**D2 (rev 3)**
- §7.4: `.mcp.json` at the repo root is an allowed JSON path in both modes.

---

## 11. P9 acceptance checks

**Agent part (P9.3.1)**, with the tool committed:
1. In a fresh demo workspace (`init --demo --tool-source <clone>`): a scripted MCP client over `energatron mcp` runs the
   whole §7.1 check-in, with `fetch_offers` as a dry run (it reads the public AER API; nothing lands, so the demo's
   frozen offers stay the only ones), and the privacy checks of `test_mcp_check_in` hold over every answer.
2. In the owner's workspace, read-only tools only through the server: `status`, `list_datasets`, `usage_summary`,
   `compare_offers`, `explain_recommendation`, `offers_diff`. No workspace denylist entry appears in any answer
   (checked by the redaction matcher, counts only); `TASKS.md` gets counts and pass/fail only.
3. The owner's workspace gains `.mcp.json` and the four skills in their tool-aware form (its existing skills are
   replaced; it isn't template-managed, so they are copied by hand), committed as one workspace commit. Nothing else
   is committed there.
4. `upgrade --dry-run` in a v1 demo workspace (made from the previous commit's template, or by editing
   `template.json` back to v1 in a scratch copy) lists the skills, links and `.mcp.json` as `added`.

**Owner part (P9.3.2)**
5. **Plan §15:** in Claude Code in the owner's workspace, approve the `energatron` MCP server and ask for a monthly
   check-in. The agent completes it through the tools (the session shows `mcp__energatron__*` calls), without reading
   `private/` (denied by `.claude/settings.json`) or raw intervals.
6. Optional: the same in Codex after `codex mcp add`.
7. Push the tool repo; CI green. Back up the workspace (`icloud_backup.py backup`).

---

## 12. Open questions / decisions for the owner

Each is decided here so P9 can be built; the recommendation is what the spec says.
1. **Tools run the CLI as a child process** rather than calling services in-process: one contract for MCP and shell
   agents, isolation from crashes and stray output; about a second per call.
2. **No MCP SDK dependency**: the stdio protocol subset needed (§5.2) is small, stdlib JSON, and fully tested; the
   official SDK would add a web stack (HTTP server, SSE) P9 doesn't use.
3. **Four read commands** (`datasets`, `usage`, `compare`, `explain`) back the plan's read tools, so the CLI keeps
   parity and Codex without MCP gets the same answers.
4. **`extracted_by` lives in `gas_bill_record` v2 and the catalog's `extra`**, not a new catalog field: the catalog
   record stays v1 for every workspace; parsed sources already record their parser in `detector`.
5. **Bill text is opt-in** (`--allow-documents`): a bill carries personal detail beyond the denylist.
6. **The MCP tools can't back up, clear the inbox, schedule, upgrade or migrate**: those stay owner-run.
7. **`land_file` is confined to `data/landing/`**, so a tool call can't land an arbitrary file.
8. **The owner's skills are replaced by the tool-aware versions** in P9.3.1 (same steps, tool names added), and the
   workspace gains `.mcp.json`; the owner's workspace stays hand-built (no `template.json`).
9. **No agent-drafted offer rules in P9**: curated rules change by pull request (plan §14.5's "rule changes go through
   review"), and no tool writes them.
