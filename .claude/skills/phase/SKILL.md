---
name: phase
description: Plan and build a whole phase of energatron-5000/TASKS.md in one go - draft and self-approve its spec, add its sub-tasks, then do, self-review and commit each one (the next, examine and done steps) until only owner steps remain, and hand those over as step-by-step instructions. Use only when the user types /phase, optionally with a phase ID such as P9.
disable-model-invocation: true
argument-hint: "[phase id, e.g. P9]"
---

# /phase: schedule and build a whole phase

The tracker is `energatron-5000/TASKS.md` (`TASKS.md` when the session runs inside the tool repo). Typing `/phase` is
the owner's standing instruction for this one phase:
- approve the phase spec the agent drafts, after a self-review, with its open questions decided as recommended;
- start each sub-task without being asked (the "never starts the next one unasked" rule is lifted for this phase);
- treat each finished sub-task as approved, as if the owner had typed `/done`.

Everything else in the **Rules for every sub-task** still applies: privacy, the standard checks, tests first, specs
binding, no global installs, no push, never `--no-verify`. Text after the command is the phase ID.

## 1. Pick the phase

Read **State**, **Hand-over** and **Current sub-task** in `TASKS.md`, then plan §15.

- A sub-task is 🔄: finish it first (section 3), then carry on.
- The user named a phase: take it. Otherwise take the phase that has ⬜ sub-tasks; if none has, the first phase in plan
  §15 with no section in `TASKS.md` yet.
- The phase has no section yet: add `### P<n>: <title> ⬜` before `## Log`, with a one-paragraph intro (where it comes
  from, that D1 binds all code, what stays the owner's) and three task headings, as the P8 archive shows:
  - `#### P<n>.1 P<n> spec ⬜` with `P<n>.1.1 Draft the P<n> spec` (Refs: the plan's sections for the phase, D1, the
    earlier specs it builds on; Files: the new spec, plan and D1 revisions, `TASKS.md`; Done when: the spec is
    approved and the build and acceptance sub-tasks are added from it).
  - `#### P<n>.2 Build ⬜` and `#### P<n>.3 Acceptance ⬜`, each with an italic placeholder line.
- Say in one line which phase you're taking and why.

## 2. The spec (P<n>.1.1)

Claim it as `/next` step 2 does. Then:

1. Read what the Refs name; read the code and the workspace (never `private/`) only as far as the spec needs facts.
2. Write `agent_docs/<YYYY-MM>/<YYYY-MM-DD>_p<n>-<name>-spec.md` (the `new-doc` conventions), shaped like the latest
   phase spec:
   - header: Created, Status, Deliverable, Builds on (with the plan's and D1's current revisions), For, Privacy note
   - §1 what it fixes: what the phase builds, what is not in it, facts it relies on (structure only)
   - §2 order of work: a table of sub-tasks (`P<n>.2.x` build, `P<n>.3.1` agent acceptance, `P<n>.3.2` owner steps if
     any), each with what it builds and the sections it follows; a coherent sub-task is about one session's work
   - one section per part, precise enough to build from: modules, models and their kinds (v1), commands, errors and
     warnings, locks, tests
   - kinds, codes and warnings added; plan and D1 changes (as the next revisions); acceptance checks split into an
     agent part and an owner part; open questions, each decided with the recommendation
3. Self-review it against plan §15's acceptance criterion, D1, the earlier specs and the code as it is. Fix what you
   find. A question the plan left open is decided in the spec; reversing a decision the plan or an approved spec
   already records is not: stop and ask the owner in two lines.
4. Status: `Approved <date> (by the agent, on the owner's /phase instruction of <date>), after a self-review; Q1-Qn as
   written. Its §<k> edits are in the plan as rev <r> and in D1 as rev <d>`.
5. Apply the plan and D1 revisions: a line in each Revisions header and the edits in place.
6. Replace the placeholders in `TASKS.md` with the sub-tasks from §2, each with `Refs`, `Files` and `Done when`
   (owner sub-tasks also get a `- Why:` line saying it is the owner's).
7. Close it with the `/done` steps (section 3, step 4).

## 3. Each build and agent-acceptance sub-task, in order

1. **Claim** it: `/next` step 2.
2. **Build** it: `/next` steps 3-5. Tests first; the standard checks and the privacy scan must pass. Too big for one
   session: finish a coherent part and add the rest as a new ⬜ sub-task right below it.
3. **Self-review** against the `/examine` brief, in its order. If your tool can start a reviewer that didn't write the
   work, prefer it; otherwise review it yourself. Verify every finding; fix each `blocker` and `should fix`, and the
   nits worth fixing; re-run the checks. Run what you built the way a user would at least once (a real command in a
   scratch folder, not only the tests) when it touches the CLI.
4. **Close** it with the `/done` steps 2-6: gates, `On /done:` steps, the `TASKS.md` updates (tick, `Handover:` line,
   **State**, **Hand-over**, **Current sub-task** reset, **Log** line ending in "self-review"), the privacy scan, and
   a commit of exactly the **Files** paths plus `TASKS.md`.
5. **Deviations** from the spec go into the spec (a sentence saying it was built so, in which sub-task) or into D1, in
   the same or the next commit, and into the `Handover:` line.
6. Print one line per closed sub-task: its ID, what it added, the commit subject, the test count.

**Agent acceptance** (`P<n>.3.1`) runs the spec's agent checks. In the real workspace only read-only commands and dry
runs, unless the sub-task says otherwise; nothing is committed there unless the sub-task says so. A tool bug the
checks find is fixed, tested and committed as its own `P<n>.3.1: <fix>` commit before the acceptance is recorded.

## 4. When to stop

Stop and report in at most 10 lines when:
- only owner sub-tasks (or none) are left in the phase;
- a decision is the owner's (section 2 step 3), or a spec can't be followed as written (`/next` step 4);
- a check fails and you can't fix the cause, or a command is refused or held by a permission prompt. Never retry a
  refused or held command and never route around it: record it in `TASKS.md` as left to the owner.

## 5. Hand over the owner's part

When the remaining sub-task is the owner's, end with numbered, step-by-step instructions for it:
- one step per action, each with the exact commands (from the workspace or tool repo folder), what they should print,
  and what to do if they don't;
- the owner-only steps the spec lists (push, backups, drills, schedules, visibility, releases), marking optional ones;
- anything left to the owner in section 4;
- last: "Tell me the results; I'll record P<n>.3.2 and close P<n>."

When the owner reports back, record it as `/done` would: tick the owner sub-task with what they did (and what they
waived), tick the phase, archive its sub-task lines verbatim in `agent_docs/<YYYY-MM>/<YYYY-MM-DD>_tasks-archive-p<n>.md`
with a pointer in their place, rewrite **Hand-over** for whatever comes next, add the **Log** line and commit.
