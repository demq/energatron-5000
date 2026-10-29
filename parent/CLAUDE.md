# ~/WorkSpace/Electricity

This folder holds two independent git repos. It is not a repo: never run `git init` here. Nothing in it is
written by hand; `python3 energatron-5000/scripts/link_parent.py` generates it from the repos.

- `energatron-5000/`: the tool. Public-ready code and design docs; a private GitHub repo until P8.
- `demq-elektric/`: the owner's private workspace. Data is DVC-managed and backed up to iCloud.

Before working in either repo, read its `CLAUDE.md` (the same file as its `AGENTS.md`).

## How work happens

Progress, rules and hand-over notes live in `energatron-5000/TASKS.md`. Start every session by reading its
State, Hand-over and Current sub-task sections.

| The owner types | The agent |
|---|---|
| `/next` (Codex: `$next`) | resumes the in-progress sub-task or takes the next one, does it, reports, never commits |
| `changes: …` | applies the changes and re-runs the checks |
| `/examine` | has a fresh reviewer check the uncommitted work and records findings |
| `/done` | the owner's approval: checks, ticks, hand-over, commits, then says whether a workspace backup is pending (the owner runs backups) |

The context is cleared between sub-tasks, so everything the next session needs goes into `TASKS.md`.

## Rules that apply everywhere

- Nothing personal goes into `energatron-5000`: no NMI, meter serial, account or invoice number, MIRN,
  address, postcode, kWh or $ figure, not even in commit messages. Use placeholders such as `<NMI>`.
- Workspace files may be read for analysis; their contents are never copied into the tool repo.
- Never read `demq-elektric/private/`.
- Don't delete or move anything in `demq-elektric/` unless the current sub-task says to.
- Commits happen only in `/done`. Never `--no-verify`, amend, rebase or force-push.
- Ask the owner before pushing, before any global install, and before changing a decision recorded in
  the plan or an approved spec.
