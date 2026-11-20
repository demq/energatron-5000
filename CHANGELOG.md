# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- The workspace's check-in and gas bill skills, and the MCP `land_file` description, no longer tell an agent that a bill
  PDF needs a source: `land` recognises bills from their text, and a source is passed only after `E_AMBIGUOUS_SOURCE`.
  Workspace template version 3; run `energatron upgrade` in a workspace to refresh its skills.

### Added

- README front page: a hero banner and an estimate-versus-measured illustration (`docs/images/`, SVG, light and dark),
  feature overview, a comparison with retailer quotes and Energy Made Easy, Mermaid diagrams of the pipeline and the
  agent data flow, and a quickstart for using it through an AI agent. The runbook follows unchanged under "Manual".
- `energatron run` shows its progress on stderr: each step, and each pipeline stage as it starts and ends with its
  position (`[ 7/20]`), its time and, on failure, its exit code. `--json` output is unchanged.
- `energatron serve` (P10): the local web UI and JSON API for a workspace, read-only, on `127.0.0.1` (or `localhost`,
  `::1`) only, with a per-launch token in the printed address; `--open` opens it, `--port 0` picks a free port,
  `--allow-documents` adds a bill's text. It prints one result (`serve_result`) and serves until Ctrl-C or SIGTERM.
  README "Web UI". The CLI loads the web stack only for `serve`, so a workspace whose lock predates it still runs
  every other command.
- Web pages over the same read paths (P10): overview, recommendation and ranked plans, usage, datasets, offer changes,
  inbox, health (backup, fingerprint, pipeline files, schedule, diagnosis), a gas bill record check form, and the
  pipeline's two HTML reports. Server-rendered, no JavaScript, every value escaped and redacted; each section links to
  the JSON behind it.
- A local JSON API over every read command (P10, served by the coming `energatron serve`): `GET /api/v1/<read>` answers
  exactly the envelope `energatron <command> --json` prints, `POST /api/v1/gas-bill/check` checks a record, and the
  bill text route exists only with `--allow-documents`. Requests need the host's loopback name, a per-launch token and,
  for a `POST`, the server's own origin. New dependencies: `fastapi`, `uvicorn`.
- Every read command's body is shared by the CLI and the coming web UI (P10): `adapters/reads.py` holds each read path
  (its arguments and its work) and `adapters/envelope.py` the `cli_result` envelope. Output is unchanged.
- Workspace template v2 (P9): `energatron init` writes four agent skills (monthly check-in, explain a recommendation,
  record a decision, transcribe a gas bill) under `.claude/skills/` with `.agents/skills/` links for Codex, and
  `.mcp.json` registering `energatron mcp`; `energatron upgrade` adds them to a version 1 workspace. The privacy guard
  allows `.mcp.json`. README "Agents" explains the tools, how to register them and what they send to the model
  provider.
- `energatron mcp` (P9): a local MCP server on stdio for AI agents (Claude Code, Codex, any MCP client). Its tools
  (`status`, `list_datasets`, `list_inbox`, `usage_summary`, `compare_offers`, `explain_recommendation`,
  `offers_diff`, `land_file`, `fetch_offers`, `run_pipeline`, `record_decision` and the gas bill record tools) each
  run the matching `energatron … --json` command and return its envelope. `--allow-documents` adds
  `read_gas_bill_text`. No tool backs up, clears the inbox, schedules, upgrades or rewrites config.
- Gas bill transcription with validators (P9): `gas_bill_record` v2 says who extracted it (`extracted_by: manual` or
  `agent:<model id>`; v1 records are upgraded when read, and the catalog carries it in `extra.extracted_by`);
  `energatron gas-bill check` and `gas-bill write` recompute a record from its own reads and lines before it is
  written or landed, `gas-bill text` prints a landed bill's text, and `gas-bill new --extracted-by` fills the skeleton.
- Read commands for agents and people (P9): `energatron datasets` (the landed datasets, newest first, no file names),
  `usage` (the usage aggregates behind the costs), `compare` (the recommendation's ranked options and plans) and
  `explain` (what the current recommendation says, how sure it is and where it came from). All read-only.
- Open-source readiness (P8): `energatron init` creates a workspace from a template (git, uv, DVC, an optional local
  backup remote, the private config, the hooks, one commit) and `init --demo` fills it with synthetic data and runs it
  to a recommendation (`energatron demo`); `energatron upgrade` refreshes the template-managed files, `energatron
  migrate` rewrites config written by an older version, and `energatron diagnose` prints a redacted support report.
  README quickstart, `CONTRIBUTING.md`, `SECURITY.md`, a bug-report template, a CI job that runs the demo on macOS, and a
  release workflow for `v*` tags. The privacy guard's workspace mode allows `.energatron/template.json`.
- `energatron status` warns `W_TOOL_UNPUBLISHED` when the last run's tool commit isn't pushed, and the restore drill
  says to push the tool repo when that commit is missing from the clone; the README's drill section says so too.
- Bill PDFs are dated by the issue date printed on them and recognised from their text (electricity bill, gas bill
  or SAPN summary), so `energatron land data/landing/` lands a folder of bills without `--source`. New dependency:
  pypdf.
- Operations (P7): `energatron status` (one read-only summary with reminders), `landing list|clear`, `watch` (the
  weekly offers check that runs the pipeline and notifies on macOS when a cheaper plan appears), `schedule
  install|remove|status` (a per-user launchd agent for `watch`); `icloud_backup.py` records each passed restore drill;
  the README is now the operating manual, checked by a test.
- Project bootstrap (P0): the `energatron` package skeleton with its ports, services and adapters; the CLI
  commands `version`, `denylist` and `backup`; the privacy guard, agent-link check, parent-folder linker and
  restore-drill scripts; pre-commit hooks and CI.
