# iCloud backup runbook (owner)

- **Created:** 2026-10-23
- **For:** the owner. Agents read it to know what to ask the owner for; they never run these commands.
- **Spec:** [`2026-10-23_p0-bootstrap-spec.md`](2026-10-23_p0-bootstrap-spec.md) §3, §4, §8 and §12
- **Privacy note:** this file is in the public-ready tool repo. Nothing personal goes in it. The script prints
  counts only, so its output can be pasted into an agent session.

---

## Why you run these by hand

Agent sessions can't reach iCloud Drive: macOS blocks the app that hosts them, and you've chosen not to grant
it access (decided 2026-10-23). So every step that touches `iCloud Drive/Backups/` runs in a terminal you
open yourself, through one script:

```bash
cd ~/WorkSpace/Electricity
uv run energatron-5000/scripts/icloud_backup.py <command>
```

| Command | What it does | When |
|---|---|---|
| `check` | Checks that this terminal can read and write `Backups/`, and counts files that are only in the cloud | first, and whenever something fails |
| `step0` | One-off safety copy of the workspace's `data/landing/` and `legacy/`, with a verified `SHA256SUMS` | once, before P0 starts |
| `backup` | `energatron backup`: `dvc push`, then a verified git bundle | after every workspace commit |
| `backup --drill` | the backup, then the restore drill | at P0 acceptance, then quarterly |
| `drill` | the restore drill alone (`scripts/restore_drill.py`) | as above |

The Backups folder is the one that holds `backup.bundle_dir` in the workspace's `.energatron/local.toml`
(the DVC remote sits beside it). `iCloud Drive/Backups` is used only when that isn't set; `--backups-dir DIR`
(before the command) overrides both.

Each prints one `OK`, `WARN` or `FAIL` line per check. Exit code 0 means done, 1 a check or the backup failed,
2 not set up, no access, or the workspace busy. Nothing is ever deleted; bundle retention happens inside
`energatron backup` and only touches bundle files.

## Once: give your terminal access

1. Open the terminal app you'll use for this. Pick one that doesn't host agent sessions, because granting
   access to an app grants it to every agent running in it too.
2. Run `check`.
   - `OK this terminal can read and write iCloud Drive`: done.
   - `FAIL this terminal can't use iCloud Drive`: System Settings → Privacy & Security → **Files and Folders**
     → that app → turn on **iCloud Drive**. If macOS asks "would like to access files in your iCloud Drive",
     click Allow. Quit and reopen the terminal, then run `check` again.

## Once, now: Step 0 safety copy

Nothing is backed up yet, so do this before P0 starts.

1. Run `step0`. It copies into `Backups/demq-elektric-step0-<today>.partial/`, checks every file against its
   source's sha256, writes `SHA256SUMS`, and only then renames the folder to `demq-elektric-step0-<today>/`.
   Expect `OK copy verified`.
2. In Finder, right-click `iCloud Drive/Backups` → **Keep Downloaded**. That covers the step 0 folder and the
   two P0 adds later.
3. Wait until Finder shows no cloud badge on `Backups/`.
4. Run `check`. Expect `OK demq-elektric-step0-<date>: N files, all downloaded`.
5. Tell the agent: "step 0 done: copy verified, all downloaded". That's all that goes into `TASKS.md`.

Keep that folder. It may be deleted only after P1 has landed every file and a restore drill has passed.

To check the copy without the script: `cd` into the folder and run `shasum -a 256 -c --quiet SHA256SUMS`;
no output means every file matches.

## After every workspace commit: backup

From P0 step 6 on, `/done` ends with a line like **"Backup pending: N commits not in iCloud."** (or "nothing
is backed up yet", or "refs changed since the last backup"). Agents work that out from a local marker,
`.energatron/backup_state.json`, without touching iCloud.

1. Run `backup`. Expect:
   - `OK DVC files pushed: N`
   - `OK new bundle written and verified (K kept, R removed)`, or `no new commits; bundle unchanged`
2. Nothing else is needed. The next agent session sees the backup through the marker
   (`uv run energatron backup --status` in the workspace).

Several commits can share one backup: every bundle holds the full history. The risk while a backup is pending
is losing those commits if the disk dies, so don't let it run for days.

## At P0 acceptance, then quarterly: restore drill

Run `backup --drill`. The drill clones the tool and the newest bundle into a scratch folder, pulls every DVC
file from iCloud, and checks the clone matches the live workspace. Expect every line `PASS` or `SKIP`, then
exit 0. Paste the summary into the agent session; it has counts only.

After a passed drill the script records the time in the workspace's `.energatron/drill_state.json` (git-ignored).
`uv run energatron status` reads it and warns `W_DRILL_DUE` when no drill is recorded or the last one is over 91
days old (P7 spec §9).

## When something fails

| Output | What to do |
|---|---|
| `FAIL this terminal can't use iCloud Drive` | grant access as above, then quit and reopen the terminal |
| `FAIL ... only in the cloud (Keep Downloaded is off)`, or `WARN ... only in the cloud` | set Keep Downloaded on `Backups/`, wait for the download, run `check` |
| `WARN ... leftover .partial copy` | an earlier run was interrupted. A bundle's `.partial` file goes at the next `backup` after a day; a `step0` `.partial` folder you delete in Finder, then run `step0` again |
| `FAIL ... already exists; nothing was written` (step0) | step 0 already ran today. Nothing to do |
| `FAIL ... symlink(s) found` (step0) | a symlink sits in `data/landing/` or `legacy/`. Ask the agent what it is; nothing was copied |
| `FAIL copy failed` (step0) | delete the `.partial` folder it names in Finder, run `check`, then `step0` again |
| `FAIL E_BACKUP_NOT_CONFIGURED` | the workspace's `.energatron/local.toml` or DVC remote is missing; ask the agent to fix P0 step 6 |
| `FAIL E_STORAGE` | a backup location failed mid-way and nothing was renamed into place. Run `check`, then `backup` again |
| `FAIL E_LOCKED` | an agent command is running in the workspace. Wait for it, then run `backup` again |
| `FAIL the workspace isn't set up yet` | expected before P0 step 6 |
