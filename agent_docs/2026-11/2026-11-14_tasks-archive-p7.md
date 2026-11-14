# Tasks archive: P7 operations

- **Created:** 2026-11-14
- **Status:** Archive. These lines were moved here verbatim from `TASKS.md` when P7.3.2 closed phase P7.
- **Privacy note:** this file lives in the public-ready tool repo. It contains no identifiers or usage figures,
  and nothing added to it may contain them either.

---

Built from the P7 spec (`agent_docs/2026-11/2026-11-10_p7-operations-spec.md`, "the P7 spec") in its §2 order; D1 rev 8
binds all code. The owner asked (2026-11-10) for P7's tasks to be added and built as far as possible without stopping
for review or `/done`: the agent plays the owner's part (approvals, reviews, commits), as for P4-P6. Pushing, global
installs, iCloud, `private/`, installing the launchd agent and deleting inbox files stay the owner's. The spec's
decisions (Q1-Q8): reminder thresholds are constants; `watch` runs the pipeline after a fetch that landed something and
never drafts; plan identity decides "a cheaper plan"; notifications carry no figures; `schedule` is an adapter command;
`icloud_backup.py` records the drill; `landing clear` deletes only bytes already landed; the owner installs the schedule.
- P7.2.2 adds warnings to `core/errors.py`, in every stage's closure: the next workspace `run` reruns all stages once.

#### P7.1 P7 spec ✅
- ✅ **P7.1.1** Draft the P7 operations spec.
  - Refs: plan §6, §12, §15; D1 rev 7 §2, §7, §8, §12; D2 §4; D3 §16; the P6 spec.
  - Files: the new spec; plan rev 15; D1 rev 8; D2 rev 2; the backup runbook.
  - Done when: the owner approves it (here: the agent, on the owner's instruction of 2026-11-10, after a self-review).
  - Handover: spec approved 2026-11-10; plan rev 15, D1 rev 8, D2 rev 2. Decision: `watch` fetches, runs when something
    landed or the run is behind, and notifies by plan identity. Watch: `status` must take no lock and write nothing.

#### P7.2 Build ✅
- ✅ **P7.2.1** `energatron landing list|clear`.
  - `services/landing.py` (inbox files, landed by sha256 and blob, masked names, clear with re-hash and empty folders),
    `landing_result` v1, the command (`clear` needs `--yes` unless `--dry-run`).
  - Refs: the P7 spec §5; D3 §8.2, §16; D1 §8.
  - Files: `src/energatron/services/landing.py`, `src/energatron/adapters/cli/commands/landing.py`, `app.py`, tests,
    golden schema.
  - Done when: the standard checks pass; a hash match whose blob is missing counts as new and is never deleted.
  - Handover: `services/landing.py` (inbox files incl. subfolders, landed = catalogued sha256 whose blob still matches, masked names, `clear` re-hashes before deleting and removes empty subfolders), `landing_result` v1, `energatron landing list|clear` (`clear` locks and needs `--yes`; `list` takes no lock); self-review. Watch: a synthetic all-letter NMI is masked only through the private config (a real one is all digits).
- ✅ **P7.2.2** `energatron status` and the drill record.
  - `core/operations.py` (thresholds, `Facts`, `reminders`), `drill_state` v1, `services/status.py`, `status_result`
    v1, the command (no lock), the six warnings; `icloud_backup.py` writes `.energatron/drill_state.json` after a
    passed drill.
  - Refs: the P7 spec §3, §4, §9, §11; D1 §7, §8.8, §11.4.
  - Files: `src/energatron/core/{operations.py,errors.py,models/vcs.py,models/workspace.py}`,
    `src/energatron/services/status.py`, `src/energatron/adapters/cli/commands/status.py`, `app.py`,
    `scripts/icloud_backup.py`, tests, golden schemas.
  - Done when: the standard checks pass; each reminder is on and off at its boundary in a test.
  - Handover: `core/operations.py` (thresholds, `Facts`, `reminders`; also the cheaper-plan test, notification texts and the launchd plist, tested here, used by P7.2.3-P7.2.4), six warnings, `drill_state` v1, `services/status.py` (`snapshot` for `watch`), `status_result` v1, `energatron status` (no lock), `wiring.code_state`; `icloud_backup.py` records a passed drill; self-review. Decision: the stage closure includes `core/errors.py`, so the next workspace run reruns every stage once (spec §2 corrected). Watch: human output lists reminders as stderr warnings only.
- ✅ **P7.2.3** `energatron watch` and notifications.
  - `cheaper_plan` and `notifications` in `core/operations.py`, `services/watch.py`, `watch_result` v1,
    `adapters/notify.py` and `wiring.notifier()`, the command (`--notify`, `--dry-run`, lock), `test_p7_watch`.
  - Refs: the P7 spec §6, §7, §11.
  - Files: the modules above, `src/energatron/adapters/wiring.py`, tests.
  - Done when: the standard checks pass; `test_p7_watch` sees the cheaper-plan notification, then a quiet second run.
  - Handover: `services/watch.py` (fetch; run when anything landed or behind; `cheaper_plan` by plan identity; notifications; `E_PARTIAL` on a failed fetch or run), `watch_result` v1, `adapters/notify/macos.py` (`osascript` with argv, `ENERGATRON_NOTIFY_LOG` seam) and `wiring.notifier()`, `energatron watch [--notify] [--dry-run]` (lock), `test_p7_watch`; notify and schedule packages in the import contracts; self-review. Decision: every fetch lands a new fetch log, so `watch` runs after every successful fetch (spec §6 note). Watch: the adapter is `adapters/notify/macos.py`, not `notify.py`.
- ✅ **P7.2.4** `energatron schedule install|remove|status`.
  - `launchd_plist` in `core/operations.py`, `adapters/schedule/launchd.py` and `wiring.launchd()`, `schedule_result`
    v1, the command (macOS only).
  - Refs: the P7 spec §8.
  - Files: the modules above, tests.
  - Done when: the standard checks pass; install, reinstall, remove and status work against a fake `launchctl`.
  - Handover: `adapters/schedule/launchd.py` (`Launchd`: plist written atomically, `bootout` then `bootstrap` into `gui/<uid>`, `print` for loaded, `~` in shown paths) and `wiring.launchd()`, `energatron schedule install|remove|status` (macOS only, no lock; `install` creates `.energatron/logs/`), `schedule_result` v1 in `services/schedule.py` (kind only, spec §8 edited); self-review. Watch: dry-run human output prints the plist with `~` for the home folder.
- ✅ **P7.2.5** README runbooks.
  - `README.md` as the operating manual, `CHANGELOG.md`, `tests/architecture/test_readme.py`.
  - Refs: the P7 spec §10; plan §12.
  - Files: `README.md`, `CHANGELOG.md`, `tests/architecture/test_readme.py`.
  - Done when: the standard checks pass; every `uv run energatron …` line in the README parses against the app.
  - Handover: README rewritten as the operating manual (set-up, the monthly cycle, gas bills, offers, deciding, the weekly check and schedule, backups and the drill, after a tool update, reminders and error codes, agents, development), `test_readme` (every `uv run energatron` line parses: command path, declared options, required argument), CHANGELOG P7 entry, plan §12's `offers diff` line corrected (no `--since`); self-review. Watch: the workspace skill `monthly-check-in` still says `offers diff --since` (fix in P7.3.1). P7.2 complete.

#### P7.3 Acceptance ✅
- ✅ **P7.3.1** P7 acceptance in the workspace (agent part).
  - Refs: the P7 spec §13 checks 1-5.
  - Files: `demq-elektric` (`.gitignore`, `CLAUDE.md`, `.claude/skills/monthly-check-in/SKILL.md`, the `watch`
    commits, a workspace note); `TASKS.md`.
  - Done when: §13 checks 1-5 pass or are explained in the workspace note; the pending backup is reported.
  - Handover: P7 acceptance (agent part): workspace `.gitignore`, `CLAUDE.md` and `monthly-check-in` updated; README cycle (clean `run`, 20 ran, `status` no longer behind; 11 invoice PDFs rejected as ambiguous, left for the owner); `watch --notify` on the live CDR (22 landed, ran, same top plans, no cheaper plan); minimal-PATH dry run ok; `schedule install --dry-run` ok, not installed; dvc clean, guard 0. Two tool fixes from real use: README `--source` note, a duplicate warning in `watch`. Watch: backup pending (17 commits); drill not recorded until the next one.
- ✅ **P7.3.2** Owner: a full cycle from a new SAPN download using only the README, the schedule and a drill.
  - Why: owner steps (the SAPN portal login, launchd on the owner's account, iCloud).
  - Refs: the P7 spec §13 checks 6-8; the README.
  - Files: none in the tool repo; the workspace gets `land` and `run` commits.
  - Done when: a new download reaches a recommendation via the README alone; `schedule status` shows the agent loaded
    (if the owner wants the weekly job); `backup --drill` passes and `energatron status` shows the drill not due.
  - Handover: owner cycle done: new SAPN download and the 11 invoices landed, the tool pushed, a full `run` (`REC-20261110-982097a0`, switch, robust), `backup --drill` passed (12 steps) and recorded; `status` shows no reminders. Decision: no weekly schedule; the agent was removed (`schedule remove`). Watch: set Keep Downloaded on the iCloud Backups folder (`W_BACKUP_DATALESS`).

#### P7.4 Follow-ups from acceptance ✅
- ✅ **P7.4.1** Bill PDFs: the issue date and the kind from the PDF's text, so a folder of bills lands in one command.
  - Why: the owner's retailer PDFs have no creation date (`no_creation_date`), and every bill needed `--source`; the
    owner decided on 2026-11-11 to read the bills' text.
  - Refs: the bill PDF text spec; D3 §5.1, §5.6; the P5 spec Q2.
  - Files: `adapters/sources/pdf_text.py`, `bill_pdf.py`, `sapn_summary.py`, `ports/sources.py`, `services/land.py`,
    `pyproject.toml`, `uv.lock`, tests, docs; the workspace `uv.lock`.
  - Done when: the standard checks pass; `land data/landing/ --dry-run` in the workspace lands every invoice PDF as
    `elec_bill`, dated by its issue date, without `--source`.
  - Handover: `pdf_text.py` (pypdf: text, kind, issue date), `ConfirmingParser` settling the PDF tie, `elec_bill`/`gas_bill` v2 (issue date first, `no_date`), `sapn_summary.confirm`; spec approved, plan rev 16. Decision: pypdf over pdfplumber; `--source` still overrides. Watch: the owner lands the 11 invoices.
- ✅ **P7.4.2** The restore drill and `status` say to push the tool repo before a drill.
  - Why: the owner's drill failed at step 4 because the tool commit the last run used was never pushed.
  - Refs: the P7 spec §3.3; D2 §12; the iCloud runbook.
  - Files: `scripts/restore_drill.py`, `adapters/pipeline/fingerprint.py`, `adapters/wiring.py`, `core/errors.py`,
    `core/operations.py`, `services/status.py`, `adapters/cli/commands/status.py`, tests, README, CHANGELOG, docs.
  - Done when: the standard checks pass; a drill on an unpushed commit says to push; `status` in the workspace warns
    `W_TOOL_UNPUBLISHED` while the tool repo is ahead of origin.
  - Handover: drill step 4 checks the commit is in the clone and says to push; `W_TOOL_UNPUBLISHED` (`commit_published` on remote-tracking branches, no network; `status` only); README, runbook, P7 spec §3.3. Decision: not in `watch`. Watch: the next `run` reruns all stages (errors.py changed).
