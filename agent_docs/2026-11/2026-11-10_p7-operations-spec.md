# P7 operations spec

- **Created:** 2026-11-10
- **Status:** Approved 2026-11-10 (by the agent, on the owner's instruction of 2026-11-10 to add and build P7 without
  stopping for review or `/done`), after a self-review; Q1-Q8 as written. Its §12 edits are in the plan as rev 15, in
  D1 as rev 8 and in D2 as rev 2
- **Deliverable:** P7.1.1 in `TASKS.md`
- **Builds on:** [`2026-10-23_data-pipeline-plan.md`](../2026-10/2026-10-23_data-pipeline-plan.md) rev 14 ("the plan"),
  [`2026-10-23_core-architecture-spec.md`](../2026-10/2026-10-23_core-architecture-spec.md) rev 7 ("D1", binding for all
  code), the P0 spec ([`2026-10-23_p0-bootstrap-spec.md`](../2026-10/2026-10-23_p0-bootstrap-spec.md), "D2"), the P1
  spec ("D3") §16 and the P6 spec ([`2026-11-10_p6-recommendation-spec.md`](2026-11-10_p6-recommendation-spec.md))
- **For:** the agent implementing P7; `/done` of P7.1.1 writes P7's sub-tasks from §2
- **Privacy note:** contains no identifiers or usage figures, and nothing added to it may. Examples use placeholders.

---

## 1. What this spec fixes

P7 makes the tool something the owner can operate from its README alone: a monthly cycle (download → land → run →
read the recommendation → back up), a weekly unattended offers check that notifies when a cheaper plan appears, and
reminders for the quarterly restore drill and other upkeep. Plan §15's P7 row: "README runbooks; optional weekly
launchd `fetch-offers`, with a notification when a cheaper plan appears; quarterly restore-drill reminder (the drill
ends with `energatron verify`). Acceptance: one full cycle (new SAPN download → recommendation) completed using only
the README." "Must" means required. D1 and the earlier specs stay binding; §12 lists every change this spec makes.

**What P7 builds**
- `energatron status`: one read-only summary of the workspace (pipeline sync, last run, recommendation, offers age,
  inbox, decisions, backup, restore drill) with **reminders** as warnings. No lock; agents may run it (§4).
- `energatron landing list | clear`: what the inbox holds and whether each file is already landed; `clear` deletes
  only files whose bytes are already in the raw zone (D3 §16's deferred "clearing the inbox", §5).
- `energatron watch`: the weekly job. Fetches offers, runs the pipeline when something new landed, compares the new
  recommendation with the one before and produces notifications: a cheaper plan, a failure, the reminders (§6).
- Notifications through macOS Notification Center (`osascript`), sent only by the CLI with `--notify` (§7).
- `energatron schedule install | remove | status`: a per-user launchd agent that runs `watch --notify` weekly. Owner
  -run: agents use only `status` and `install --dry-run` (§8).
- The restore drill's record: `icloud_backup.py` writes `.energatron/drill_state.json` after a passed drill, so
  `status` can say when the quarterly drill is due (§9).
- README runbooks, checked by a test that every `energatron` command line they show parses (§10).

**Not in P7** (stay later): `energatron init` and a workspace template (P8), an `offer_pdf` source type (P6 §15 Q6),
notifications other than macOS, scheduling anything but `watch`, scheduled backups (backups stay owner-run, D2 §4).

**Facts it relies on** (checked 2026-11-10, structure only)
- `uv lock --check` passes in the workspace; `icloud_backup.py` already runs `uv run --frozen energatron …` there.
- `uv` lives outside launchd's default `PATH` (`/usr/bin:/bin:/usr/sbin:/sbin`), so the job needs an absolute path
  to it and a `PATH` that reaches it.
- The recommendation is replaced on every run at `data/reports/recommendations/recommendation.json`; the run manifests
  in `data/runs/` keep the history of IDs (P6 spec §7, §8.2). `option_key` is built from snapshot IDs, so a re-fetched
  but unchanged plan gets a new key; plan identity is `plan_id` per fuel.
- The restore drill (`scripts/restore_drill.py`) leaves the live workspace untouched by design and records nothing.

---

## 2. Order of work

`/done` of P7.1.1 turns the rows into sub-tasks.

| # | Sub-task | Builds | Sections |
|---|---|---|---|
| 1 | P7.2.1 `landing` | `services/landing.py`, `landing_result` v1, `landing list` and `landing clear` | §5 |
| 2 | P7.2.2 `status` and the drill record | `core/operations.py` (reminders), `drill_state` v1, `status_result` v1, `services/status.py`, the command, the new warnings; `icloud_backup.py` writes the drill record | §3, §4, §9, §11 |
| 3 | P7.2.3 `watch` and notifications | the comparison and messages in `core/operations.py`, `services/watch.py`, `watch_result` v1, `adapters/notify.py`, the command, `test_p7_watch` | §6, §7 |
| 4 | P7.2.4 `schedule` | the plist in `core/operations.py`, `adapters/schedule/launchd.py`, `schedule_result` v1, the command | §8 |
| 5 | P7.2.5 README runbooks | `README.md`, `CHANGELOG.md`, `tests/architecture/test_readme.py` | §10 |
| 6 | P7.3.1 Acceptance (agent part) | in the real workspace: `.gitignore`, `CLAUDE.md` commands, the `monthly-check-in` skill on `status`; the README cycle on the data at hand; `watch` once; `schedule install --dry-run` | §13 |
| 7 | P7.3.2 Owner: the cycle with a new download, the schedule and a drill | owner steps: a fresh SAPN download through the README, `schedule install`, `icloud_backup.py backup --drill` | §13 |

No P7 change touches a stage or `Params`, but the six warning codes go into `core/errors.py`, which every stage
imports: P7.2.2 changes every stage's code entry once, and the next `energatron run` in a workspace reruns all stages
(same outputs).

---

## 3. Shared rules

### 3.1 Thresholds (`core/operations.py` constants)

| Constant | Value | Meaning |
|---|---|---|
| `DRILL_INTERVAL_DAYS` | 91 | a restore drill is due when none is recorded or the last is older than this (plan §6: quarterly) |
| `OFFERS_STALE_DAYS` | 14 | offers are stale when none were fetched or the last fetch is older than this (two missed weekly runs) |

Constants, not params (Q1): they decide reminders, not results, so they must not enter `params.yaml` and rerun stages.

### 3.2 Local dates

"Today" is `clock.now()` in the region's zone (`ctx.region.local_tz`), as `decide` does; UTC when the zone is null.
Ages in days are whole local days between the two local dates.

### 3.3 Reminders (warnings)

A reminder is a `ResultWarning` whose code is one of these (new, except `W_BACKUP_PENDING`). `core/operations.py`
`reminders(facts) -> list[ResultWarning]` computes them from a plain `Facts` value, in this order:

| Code | When | Message says |
|---|---|---|
| `W_LANDING_NEW` | the inbox holds files not landed yet | run `energatron land data/landing/` |
| `W_RUN_BEHIND` | not synced, a stale code entry, datasets not in the last clean run, or no clean run | run `energatron run` (after `pipeline sync` when not synced) |
| `W_OFFERS_STALE` | §3.1 | run `energatron fetch-offers` (or schedule `watch`) |
| `W_RECHECK_DUE` | the recommendation's `recheck_by` is today or earlier | refresh offers and run |
| `W_BACKUP_PENDING` | `refs_backed_up` is false | the owner runs `icloud_backup.py backup` |
| `W_DRILL_DUE` | §3.1 | the owner runs `icloud_backup.py backup --drill` |
| `W_LANDING_LANDED` | the inbox holds files already landed | `energatron landing clear` may delete them |

`details` hold counts and dates only (`files`, `days_since`, `recheck_by` …), never file names or figures.

---

## 4. `energatron status` (`services/status.py`)

`status(ctx, *, code_stale: list[str], tool_dirty: bool) -> StatusResult`. The CLI computes the live code fingerprint
(services can't, D1 §11.4), compares it with the recorded one (`stale_stages`) and passes the stale stage names and
`tool_dirty`. No lock (D1 §8.8: read-only), writes nothing, never touches a backup location; agents may run it.

What it reads, each missing input giving `null` or zero, never an error:
- `pipeline_sync.check` (an `E_PIPELINE_NOT_SYNCED` becomes `synced: false` with its `drift`; any other error
  propagates);
- `data/runs/*.json` (`run_manifest`): the newest by `run_id`, and the newest non-draft one (the "last clean run");
- `data/reports/recommendations/recommendation.json`;
- the catalog: every dataset ID (against the last clean run's `datasets`), and the newest `landed_at` among
  `offer_cdr_index` records (the last fetch);
- `data/landing/` through `services/landing.py` (§5);
- `data/decisions/DEC-*.json`: count and newest ID;
- `vcs.backup_status()`;
- `.energatron/drill_state.json` (§9). An invalid one is treated as missing, with a log warning.

An invalid manifest or recommendation → `E_INVALID_INPUT` (`details.file`), as `run` does.

**`status_result` v1**
```
today: date
pipeline: {synced: bool, drift: [str], code_stale: [str], tool_dirty: bool}
last_run: {run_id, draft, finished_at, recommendation_id | null, action | null, robust | null} | null
last_clean_run_id: str | null
datasets_not_in_last_clean_run: int
recommendation: {recommendation_id | null, as_of | null, recheck_by | null, action, robust,
                 top: {electricity: str | null, gas: str | null}} | null   # "<brand> <display_name>"
offers: {last_fetch_at: UTC datetime | null, days_since: int | null}
landing: {files: int, landed: int, new: int}
decisions: {count: int, latest: str | null}
backup: {refs_backed_up: bool, commits_not_backed_up: int | null, last_backup_at: UTC datetime | null}
drill: {last_drill_at: UTC datetime | null, days_since: int | null, due: bool}
reminders: [str]   # the codes of §3.3, in order; the same warnings are in the envelope
```
No figure: no dollar amount or usage appears (plan and brand names are public). Human output: one line per block, then
one line per reminder.

---

## 5. `energatron landing` (`services/landing.py`)

D3 §16 deferred "an option that removes inbox files already in the catalog" to P7 at the latest.

- **Inbox files:** the regular files directly under `data/landing/` and in its subfolders, skipping names that start
  with `.` and symlinks (never followed). Sorted by relative path.
- **Landed:** a file's sha256 is in the catalog (`find_sha256`) **and** the blob behind that record exists with the
  same sha256 (`blobs.stat(key).sha256`). Anything else is `new`.
- **Names in output** are masked component by component with `mask_filename` and the denylist (D3 §8.2): SAPN names
  start with the NMI.

`landing_list(ctx) -> LandingResult` takes no lock. `landing_clear(ctx, *, dry_run) -> LandingResult` takes the lock
(the CLI) and deletes each landed file (re-checking its hash just before), never a `new` one, then removes subfolders
left empty (never `data/landing/` itself). `clear` without `--dry-run` needs `--yes` or an interactive confirmation
(`E_CONFIRMATION_REQUIRED` otherwise), because it deletes the owner's files.

**`landing_result` v1:** `action` (`list` | `clear`), `dry_run`, `files: [{name (masked), status: landed|new,
dataset_id | null, removed: bool}]`, `landed`, `new`, `removed` (counts). Exit 0.

---

## 6. `energatron watch` (`services/watch.py`)

`watch(ctx, *, tool: ToolState, code_stale: list[str], dry_run: bool) -> WatchResult`; the command takes the lock for
the whole call and computes `tool` and `code_stale` as `run` and `status` do.

**Steps**
1. `before` = the current recommendation (may be missing).
2. `fetch_offers(ctx, dry_run=dry_run)`. A raised error (every list failed) is recorded as `fetch_error` (its code) and
   the job goes on to step 4 without running. A partial fetch is fine; its `W_FETCH_FAILED` warnings pass through.
3. Run when not `dry_run`, no `fetch_error`, and either the fetch landed something or the status facts say the run is
   behind (`W_RUN_BEHIND`'s condition: an earlier manual `land` without a `run` is picked up too).
   Every successful fetch lands a new fetch log (`offer_cdr_index`, which decides the current plans), so in practice
   `watch` runs after every fetch; stages whose inputs didn't change stay cached.
   `run(ctx, tool=tool, draft=False)`; an `EnergatronError` (dirty config, dirty tool, a failed check, not synced) is
   recorded as `run_error` (its code), never retried and never turned into a draft run.
4. `after` = the current recommendation; `status` facts are recomputed and give the reminders.
5. **Cheaper plan** (`core/operations.cheaper_plan(before, after)`): true when `after.action == "switch"` and either
   `before` is missing, `before.action != "switch"`, or the top option's plans differ: `(electricity.plan_id,
   gas.plan_id)` of `options[0]` in each (Q3). A new snapshot of the same plans isn't news.
6. **Notifications** (`core/operations.notifications(...)`), at most three, each `{title, message}` without any
   figure:
   - cheaper plan: title "energatron: cheaper plan", message "<brand> <display_name> (electricity) + <brand>
     <display_name> (gas) tops REC-…; see the recommendation page." (only the fuels the option has);
   - failure: title "energatron: weekly check failed", message "<fetch|run> failed with <code>; run `energatron
     status` in the workspace.";
   - reminders: title "energatron: reminders", message the reminders' short names joined ("restore drill due, backup
     pending …"), only for `W_DRILL_DUE`, `W_BACKUP_PENDING`, `W_RECHECK_DUE`, `W_LANDING_NEW`.
   `W_RUN_BEHIND` and `W_OFFERS_STALE` after a successful watch are about the run it just did, so they are left out of
   the notification (they stay warnings).

**`watch_result` v1**
```
dry_run: bool
fetch: {fetch_batch_id, landed: int, failed_retailers: int} | null    # null when the fetch raised
fetch_error: str | null                                               # an error code
ran: bool
run_id: str | null
run_error: str | null
before: str | null        # recommendation IDs
after: str | null
cheaper_plan: bool
reminders: [str]
notifications: [{title, message}]
```
Exit 0, unless `fetch_error` or `run_error`: then `E_PARTIAL` (exit 8) with the result as `data`, as `fetch-offers`
does, so a log or a scheduler sees the failure. With `--notify` the CLI sends every notification after the service
returns (outside the service, §7); without it, nothing is sent. `--dry-run` fetches lists only (as `fetch-offers
--dry-run`), never runs, writes nothing, and still builds the notifications it would send.

---

## 7. Notifications (`adapters/notify.py`)

- `wiring.notifier()` returns a `Notifier` callable `(title, message) -> bool`. Services never notify (D1 §3: no
  subprocesses); the `watch` command calls it (a driving adapter reaching a driven one through wiring).
- macOS: `osascript -e 'on run argv' -e 'display notification (item 2 of argv) with title (item 1 of argv)' -e 'end
  run' <title> <message>`: the text travels as arguments, never spliced into the script. A non-zero exit or a missing
  `osascript` logs a warning and returns false; it never fails the command.
- Other platforms: logs and returns false.
- Test seam: `ENERGATRON_NOTIFY_LOG=<path>` makes the notifier append `{"title", "message"}` JSON lines to that file
  instead (as `ENERGATRON_CDR_ROOT` redirects the CDR provider).
- The human output says how many notifications were sent; JSON output is unchanged by sending.

---

## 8. `energatron schedule` (`adapters/schedule/launchd.py`)

A per-user launchd agent; macOS only (elsewhere every subcommand → `E_USAGE` "launchd is macOS only"). Not a service:
it configures this machine, not the workspace (Q5). Owner-run (`install`, `remove`); agents may run `status` and
`install --dry-run` (D2 §4's split, as for backups).

- **Label:** `org.energatron.watch.<h>`, `h` the first 8 hex digits of the sha256 of the workspace root's absolute
  path: one agent per workspace, and the path itself is not in the label.
- **Plist** (`core/operations.launchd_plist(...)`, stdlib `plistlib`, pure): `Label`; `ProgramArguments` = `[<uv>,
  "run", "--frozen", "energatron", "watch", "--notify", "--json"]`; `WorkingDirectory` = the workspace root;
  `EnvironmentVariables.PATH` = the uv directory, `/opt/homebrew/bin`, `/usr/local/bin`, `/usr/bin`, `/bin`,
  `/usr/sbin`, `/sbin` (deduplicated, in that order); `StartCalendarInterval` = `{Weekday, Hour, Minute}`;
  `StandardOutPath` and `StandardErrorPath` = `.energatron/logs/watch.log`; `RunAtLoad` false; `ProcessType`
  `Background`. launchd runs a missed calendar job once at the next wake.
- **`install [--weekday 0-6] [--hour 0-23] [--minute 0-59] [--dry-run]`** (defaults: Monday 09:00): `<uv>` from
  `shutil.which("uv")` (none → `E_USAGE`); creates `.energatron/logs/`; writes `~/Library/LaunchAgents/<label>.plist`
  atomically; `launchctl bootout gui/<uid>/<label>` (ignored when not loaded), then `launchctl bootstrap gui/<uid>
  <plist>`. A failing `bootstrap` → `E_SUBPROCESS`. Re-running replaces the agent. `--dry-run` writes and loads
  nothing.
- **`remove [--dry-run]`**: `bootout` (ignored when not loaded) and deletes the plist. Nothing installed → exit 0.
- **`status`**: whether the plist exists, whether `launchctl print gui/<uid>/<label>` succeeds (loaded), and the
  schedule read back from the plist.
- **`schedule_result` v1:** `action` (`install` | `remove` | `status`), `dry_run`, `label`, `plist` (the path with the
  home folder as `~`), `installed`, `loaded`, `weekday | null`, `hour | null`, `minute | null`, `log` (workspace
  -relative). Paths never show the home folder's name.

---

## 9. The restore drill's record

- `drill_state` v1 (`core/models/vcs.py`), `.energatron/drill_state.json`, git-ignored and machine-specific like
  `backup_state.json`: `{kind, schema_version, at}` with `at` the UTC time the drill passed.
- `scripts/icloud_backup.py` writes it after `restore_drill.py` exits 0, whether from `drill` or `backup --drill`; a
  failed drill writes nothing. The script stays stdlib-only and writes the same canonical shape the model reads (a
  test checks that the model accepts it). The drill itself still leaves the workspace untouched.
- The runbook's drill section says that the record is written and that `energatron status` reads it.

---

## 10. README runbooks

`README.md` becomes the operating manual. Sections:
1. What it is; status (P0-P7 built; P8 brings `init` and a demo); the privacy model (kept).
2. Install and set up: uv, the tool checkout, a workspace next to it (set up by hand until P8: point to D2 §11),
   `pipeline sync`, the private config (`denylist`).
3. **The monthly cycle**: download the SAPN detailed CSV → `data/landing/` → `land --dry-run`, `land` → `run` →
   open `data/reports/recommendations/index.html` (and the usage report) → `status` → `landing clear` → backup.
4. Gas bills (`land`, `gas-bill new`, transcribe, `land`), offers (`fetch-offers`, `offers diff`), deciding
   (`decide --dry-run`, `decide`; `current_plans` afterwards).
5. The weekly check: `watch --dry-run`, `watch`, `schedule install|status|remove`, where its log is, what the
   notifications mean.
6. Backups and the quarterly restore drill (owner-run, `icloud_backup.py`), and how `status` reminds.
7. After a tool update: `pipeline sync`, commit, `run`. Troubleshooting: a table of the error codes a cycle meets
   (`E_PIPELINE_NOT_SYNCED`, `E_DIRTY`, `E_LOCKED`, `E_CHECK_FAILED`, `E_PARTIAL`, `E_NETWORK`, `E_FOREIGN_PREMISES`, `E_DENYLIST_STALE`) and what
   to do.
8. Agents (the workspace skills), development (the checks), licence.

Every command in it is runnable as written from the workspace (`uv run energatron …`). `tests/architecture/
test_readme.py` extracts each `uv run energatron …` line from the README's fenced blocks and checks that the command
path exists in the typer app and that every `--option` it uses is declared for that command (placeholders in `<…>`
are allowed as arguments). `CHANGELOG.md` gets the P7 entry.

---

## 11. Kinds, codes and warnings added in P7

- Kinds: v1 of `drill_state`, `status_result`, `landing_result`, `watch_result`, `schedule_result`. Golden schemas.
- Codes: none new (`E_USAGE`, `E_SUBPROCESS`, `E_CONFIRMATION_REQUIRED`, `E_PARTIAL` cover P7).
- Warnings: `W_LANDING_NEW`, `W_LANDING_LANDED`, `W_RUN_BEHIND`, `W_OFFERS_STALE`, `W_RECHECK_DUE`, `W_DRILL_DUE`.
- CLI: `status`, `landing list|clear`, `watch`, `schedule install|remove|status`, each with `--json`.
- Workspace files: `.energatron/drill_state.json` and `.energatron/logs/` (both git-ignored).

**Tests:** `test_operations` (every reminder on and off at its boundary, `cheaper_plan` cases incl. a new snapshot of
the same plans, notification texts hold no digits other than the REC ID and are capped, plist keys and `PATH`);
`test_status` (each block from a fixture workspace, missing inputs, invalid drill state ignored, no lock taken);
`test_landing` (landed vs new incl. a hash match whose blob is missing, masking, dry run, `--yes`, empty folders);
`test_watch` (fetch error, run error, no change → no run, behind → run, cheaper plan, dry run writes nothing; services
faked at the port level); `test_notify` (argv not script, the log seam, failure returns false); `test_launchd` (fake
`launchctl`: install, reinstall, remove, status, failure codes, `~` in output); `test_icloud_backup` (drill record on
exit 0 only, accepted by the model); `test_p7_watch` (integration: a scratch workspace, a CDR stand-in, `watch
--notify --json` via `main()` with the notify log: fetch, run, cheaper plan notified; a second `watch` with nothing
new: no run, no cheaper plan); CLI one-document checks (D1 §13).

---

## 12. Plan, D1 and D2 changes

To be applied on `/done` of P7.1.1, as plan rev 15, D1 rev 8 and D2 rev 2.

**Plan**
- §6: the drill's record and the `status` reminder (§9).
- §12: the runbooks gain `status`, `landing`, `watch` and `schedule`; the README is the runbook (§10).
- §15 P7: made concrete as this spec's §13.

**D1 (rev 8)**
- §2: core gains `operations.py`; services gain `status.py`, `landing.py`, `watch.py` (`compare.py`, `explain.py`,
  `migrate.py` move to P8+); adapters gain `notify.py` and `schedule/launchd.py`; CLI commands `status`, `landing`,
  `watch`, `schedule`.
- §4.8 / wiring: `notifier()` and `launchd()` join `wiring.py`; `ServiceContext` is unchanged.
- §7: the six P7 warnings.
- §12: `.energatron/drill_state.json` and `.energatron/logs/` are git-ignored workspace files.

**D2 (rev 2)**
- §4 and the runbook: `icloud_backup.py` records a passed drill in `.energatron/drill_state.json` (§9).

---

## 13. P7 acceptance checks

In the real workspace, with the tool committed. `TASKS.md` gets counts and pass/fail only; figures go in a workspace
note.

**Agent part (P7.3.1)**
1. The workspace `.gitignore` lists `.energatron/drill_state.json` and `.energatron/logs/`; the workspace `CLAUDE.md`
   lists the P7 commands and who may run them; the `monthly-check-in` skill starts from `energatron status`.
2. The README cycle on the data at hand, using only README commands: `status`, `land --dry-run` on the inbox
   (everything already landed), `run` (cached, same `REC-…`), `status` (no `W_RUN_BEHIND`), `landing list` (and
   `landing clear --dry-run`; the actual clear is the owner's).
3. `watch --json` once (real CDR): the fetch lands what changed; a run when needed; `before`/`after` and
   `cheaper_plan` reported; exit 0. A minimal-`PATH` run (`env -i HOME=… PATH=/usr/bin:/bin <uv> run --frozen
   energatron watch --dry-run --json`) works as launchd will run it.
4. `schedule install --dry-run --json` shows the plist it would install; `schedule status` reports not installed.
5. `dvc status` clean; privacy guard 0 violations; the pending backup is reported.

**Owner part (P7.3.2)**
6. **Plan §15:** one full cycle from a new SAPN download to a recommendation, following only the README.
7. `schedule install` (the weekly job is optional; the owner decides) and `schedule status` showing it loaded.
8. `icloud_backup.py backup --drill` passes; `energatron status` then shows the drill recorded and not due.

---

## 14. Open questions / decisions for the owner

Each is decided here so P7 can be built; the recommendation is what the spec says.
1. **Reminder thresholds are constants** (§3.1), not `params`: they don't change results, and a params change reruns
   stages.
2. **`watch` runs the pipeline itself** after a fetch that landed something (plan §15 says "launchd `fetch-offers`";
   a notification about a cheaper plan needs a fresh recommendation). It never runs a draft: a dirty workspace makes it
   report `run_error` and notify.
3. **Plan identity, not snapshot identity**, decides "a cheaper plan appeared" (§6 step 5): a repriced top plan is
   not news, a different top plan is. The saving threshold is the recommendation's own (`switch` vs `stay`).
4. **Notifications carry no figures** (§6): plan names and the REC ID only; the page holds the numbers.
5. **`schedule` is an adapter command, not a service** (§8): it configures this machine; an MCP or web front end has
   no business installing launchd agents.
6. **The drill record is written by `icloud_backup.py`**, not by the drill (§9), so the drill keeps leaving the
   workspace untouched.
7. **`landing clear` deletes only bytes already in the raw zone**, re-hashed just before deleting, and asks first.
8. **The owner installs the schedule.** Agents never install launchd agents or send backups; they run `schedule
   status` and `install --dry-run`.
