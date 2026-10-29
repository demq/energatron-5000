---
name: examine
description: Review the uncommitted work of the in-progress sub-task in energatron-5000/TASKS.md against its spec, its Done-when check and the project rules, and record the verdict in TASKS.md. Changes no other file. Use only when the user types /examine, optionally with a focus.
disable-model-invocation: true
argument-hint: "[focus]"
---

# /examine: review the last step's work

This command only reviews. It changes no file except the **Current sub-task** section and the **State** block of
`energatron-5000/TASKS.md` (`TASKS.md` when the session runs inside the tool repo). Text the user typed after the
command is the focus of this review.

## 1. Find the work

- Read the 🔄 sub-task in `TASKS.md`: its `Refs` and `Done when`, plus the **Current sub-task** record (summary,
  files, checks).
- No 🔄 sub-task: say so in one line and stop.

## 2. Collect the change

- In each repo the **Files** list names, run `git status` and `git diff`, and include new untracked files.
- Before the repos exist: the listed files themselves.

## 3. Choose who reviews

- Prefer a reviewer that didn't write the work. If your tool can start a sub-agent, give it the brief in step 4,
  the file list and the `Refs`, and wait for its findings.
- Otherwise, review it yourself against the same brief. A session that starts fresh after a context clear
  counts as a fresh reviewer.

## 4. The brief: check these, in this order

1. **Privacy**
   - No NMI, meter serial, account/customer/invoice number, MIRN, address, postcode, kWh figure or $ figure in
     any energatron-5000 file, in `TASKS.md` or in the planned commit message.
   - No data files (`*.csv *.parquet *.pdf *.json *.xlsx`) outside `tests/fixtures/` and `config/`.
   - No `*.dvc` or `dvc.lock` in the tool repo.
   - No absolute paths that contain the username.
   - Run the privacy scan from the **Rules**.
2. **Done when:** the sub-task's check is met, and the standard checks pass when re-run.
3. **Spec:**
   - The work follows its `Refs`.
   - For code, it also follows the core architecture spec: layers, ports, IDs, error codes, the CLI envelope.
   - Every deviation is recorded.
4. **Correctness:** bugs, the edge cases the spec names, error handling, determinism.
5. **Tests:**
   - They prove the spec's rules rather than just running the code.
   - Fixtures are synthetic only.
6. **Scope:** no unrelated changes, leftovers, debug output or stray files.
7. **Guardrails:** nothing deleted or moved in `demq-elektric/` without the owner's OK, and nothing installed
   globally.
8. **Simplicity:** a clearly simpler way to do the same thing.

For each finding, give:
- a severity: `blocker`, `should fix` or `nit`
- `path:line`
- one sentence on what is wrong, and one on the fix

Verify each finding before reporting it, and drop any you can't confirm.

## 5. Record the result

- Under **Current sub-task**, write `Review result:` followed by:
  - the date
  - the reviewer (sub-agent or session)
  - the verdict: `pass`, `pass with nits` or `changes requested`
  - the findings, numbered, each marked `open`
- Set **State** → `Review: <verdict>, <n> open`.
- Change nothing else.

## 6. Report

At most 12 lines: the verdict first, then the findings, most severe first, one line each.

Last line: "Next: `changes: fix 1, 3` here, `/next` in a fresh session to fix the open findings, or `/done`."
