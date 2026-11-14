# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

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
