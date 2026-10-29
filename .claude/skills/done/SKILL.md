---
name: done
description: Approve and close the in-progress sub-task in energatron-5000/TASKS.md - re-run the checks, tick it off, write the hand-over notes and commit in each repo it touched. Use only when the user types /done.
disable-model-invocation: true
argument-hint: "[hand-over note] [push] [anyway]"
---

# /done: approve, record, commit

The user typing `/done` is the owner's approval of the 🔄 sub-task in `energatron-5000/TASKS.md` (`TASKS.md` when
the session runs inside the tool repo).

Text after the command is an extra hand-over note, except for two words:
- `push`: push the tool repo after committing
- `anyway`: close the sub-task despite open review findings

## 1. Find it

The 🔄 sub-task. If there is none, say so in one line and stop.

## 2. Gates

Stop with a one-line reason if any of these fails:
- No review findings are marked `open` with severity `blocker` or `should fix`, unless the user wrote `anyway`.
- The sub-task's `Done when` check passes when re-run.
- The **Standard checks** from the **Rules** pass when re-run.

## 3. Extra steps

Do the `On /done:` steps listed under the sub-task, if any.

## 4. Update `TASKS.md`

- **The sub-task:** 🔄 → ✅. Add one indented line under it:
  `- Handover: <what was built; one decision taken; one thing to watch>`. At most two lines; fold in the
  user's note.
- **Its task:** ✅ once every one of its sub-tasks is ✅ or ⏩. Do the same for the phase.
- **State:**
  - Current: none
  - Last done: `<id>`
  - Last commit: the commit subject, per repo
  - Checks: a summary of the results
  - Review: none
  - Next: the first ⬜ sub-task
- **Hand-over:** rewrite it for the next sub-task:
  - where things stand
  - what to read first
  - the facts it needs
  - open questions for the owner
  - watch-outs

  Drop what no longer matters, and keep it under about 40 lines.
- **Current sub-task:** reset to "none".
- **Log:** add a line at the top:
  `- <YYYY-MM-DD> · <id> · <one line> · <commit subject, or "not committed: no repo yet">`.
- **Archive:** if a phase just finished and the file is over about 400 lines, move that phase's sub-task lines
  verbatim into `agent_docs/<YYYY-MM>/<YYYY-MM-DD>_tasks-archive-<phase>.md`. Leave a one-line pointer in their
  place.

## 5. Privacy scan

Run the privacy scan from the **Rules** over:
- every file to be committed
- `TASKS.md`
- the commit message

Stop if it finds anything.

## 6. Commit

Commit in each repo that has changes from this sub-task: the **Files** list, plus `TASKS.md` in energatron-5000.

- **Stage exactly those paths.**
  - Never use `git add -A` or `git add .`.
  - Never stage the contents of `data/`, `private/` or `legacy/`, or `.venv/` and caches. DVC-managed data
    reaches git only as `*.dvc` files and `dvc.lock`.
- **Message:** `<id>: <imperative one-liner>`, a blank line, then a body with what changed and the spec refs.
  Add your tool's usual co-author trailer, if it has one.
- **Hooks:**
  - Let them run. Never use `--no-verify`, amend, rebase, reset or force.
  - If a hook rejects the commit, fix the cause and commit again, or stop and report.
- **A folder that isn't a git repo yet** (before P0 creates it): skip the commit and say so.
- **demq-elektric:** run `uv run energatron backup --status --json` after the commit (never `energatron backup`
  itself: backups are owner-run). If `refs_backed_up` is false, end the reply with the pending-backup line (P0 spec
  §4), then "Run `icloud_backup.py backup` in your terminal.":
  - `last_backup_head` is null: "Backup pending: nothing is backed up yet."
  - `commits_not_backed_up` above 0: "Backup pending: N commits not in iCloud."
  - otherwise: "Backup pending: refs changed since the last backup (for example a new tag)."
- **Pushing:** only if the user wrote `push`, and only the tool repo.

## 7. Reply

At most 5 lines:
- "**<id> is committed:** <what it added>", with the commit subject(s) and the check results
- the next sub-task's ID, and what it will do
- "Clear the context, then `/next`."

If nothing ⬜ is left, say that the stage is complete and what comes next.
