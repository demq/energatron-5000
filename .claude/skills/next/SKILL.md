---
name: next
description: Start or resume the next sub-task in energatron-5000/TASKS.md, do it, record a work report in TASKS.md and stop for review without committing. Use only when the user types /next, optionally with a sub-task ID.
disable-model-invocation: true
argument-hint: "[sub-task id]"
---

# /next: do one sub-task

The tracker is `energatron-5000/TASKS.md`, or `TASKS.md` when the session runs inside the tool repo. It holds the
rules, the state and the hand-over notes. Follow its **Rules for every sub-task** throughout. Text the user typed
after the command is a sub-task ID.

## 1. Pick the sub-task

Read `TASKS.md`: **State**, **Hand-over**, **Current sub-task**, then the task list.

- **A sub-task is 🔄:** resume it. Say in one line where it stands.
  - If **Current sub-task** has review findings marked `open`, the work is to fix them.
  - Mark each finding `fixed` as you fix it.
- **Otherwise:** take the sub-task the user named, or else the first ⬜ sub-task in file order.
  - Never take a ✅, ⛔ or ⏩ sub-task unless the user names it.
  - Italic placeholder lines are not sub-tasks.
- **No ⬜ sub-task left:** summarise what the finished stage delivered, in at most 10 lines. Say which phase
  comes next according to plan §15, and stop.

## 2. Claim it before anything else

- Say: "Picking up <id>: <title>."
- Edit `TASKS.md`:
  - the sub-task's marker: ⬜ → 🔄
  - **State** → `Current: <id>, started <YYYY-MM-DD>`
  - **Current sub-task**: replace "none" with the ID and title

## 3. Read only what the sub-task needs

- the **Hand-over** notes
- the documents and sections the sub-task's `Refs` name
- the files it names

The `CLAUDE.md` of each repo you touch applies once that file exists.

## 4. Do the work, within the sub-task's scope

- **Code:** tests first. Run them red, implement, run them green. The core architecture spec (D1) is binding.
- **Docs:** dated paths, `agent_docs/YYYY-MM/YYYY-MM-DD_<name>.md`. Never write identifiers or usage figures
  into energatron-5000.
- **Can't be done as the spec says** (an interface would change, the data surprises you, a decision is
  missing)? Stop and say so in two lines. Don't redesign.
- **Too big for one session?** Finish a coherent part, add the rest as a new ⬜ sub-task right below this one
  (with Refs, Files and Done when), and say so.

## 5. Check

Run the sub-task's `Done when` check and the **Standard checks** from the **Rules**, including the privacy scan.
Note the results.

## 6. Record the work under **Current sub-task**

This record is exact and technical, because the next session works from it.

- **Summary:** what was done.
- **Files:** new and changed files, grouped by repo.
- **Checks:** what ran, with the results.
- **Different from the spec:** each deviation, or "nothing".
- **Open questions:** each question for the owner, or "none".
- **Review** (whether `/examine` should run):
  - Write "recommended: <why>" when any of these is true:
    - more than about 300 changed lines, or more than about 10 files
    - anything touching privacy, git hooks, backups, the DVC remote, git history, IDs or persisted schemas
    - anything touching files under `demq-elektric/data`, `private` or `legacy`
    - any deviation from the spec, or any open question
    - the sub-task produced a spec document (the owner reads every spec)
  - Otherwise write "not needed: <why>".
- **Review result:** "not run".

## 7. Stop. Don't commit.

Report in at most 14 lines, in plain words. Say what the change lets someone do before naming functions or
files.

- First line: "**<id> is built and not committed yet.**" If only part of it is done: "**<id> is partly built.**"
- **What changed:** one or two sentences.
- **How it's checked:** the checks and their results.
- **Different from the spec:** at most three items, or "nothing".
- **Questions for you:** the open questions, or "none".
- **Review:** recommended or not, and why, in one line.
- **Files:** paths only.

Last line: "Next: `/examine`, `changes: …`, or `/done`."
