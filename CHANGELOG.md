# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

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
