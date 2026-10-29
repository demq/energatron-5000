# Tasks archive: the spec stage (S)

- **Created:** 2026-10-24
- **Status:** Archive. `/done` moved these lines here verbatim from `TASKS.md` when S.5.1 closed the spec
  stage.
- **Privacy note:** this file lives in the public-ready tool repo. It contains no identifiers or usage figures,
  and nothing added to it may contain them either.

---

### S: Spec stage (hand-over §8) ✅

#### S.1 Before-P0 answers ✅
- ✅ **S.1.1** Ask the owner the before-P0 questions (hand-over §7) and verify the Python version for DVC
  (hand-over §6).
  - Handover: answers recorded in plan rev 5 and §16. Python 3.14 verified in a scratch venv with a full DVC
    init/add/push/pull round trip; 3.15 is blocked by missing `pygit2` wheels. Watch: the disk-space question
    is still open.

#### S.2 D1: core architecture spec ✅
- ✅ **S.2.1** Draft the core architecture spec: layers and import rules, ports, schema versioning, IDs and
  hashing, errors, CLI, the stage contract, provenance, the code fingerprint.
  - Refs: hand-over §8 D1; plan §8.4, §9, §14.1–14.2.
  - Files: `agent_docs/2026-10/2026-10-23_core-architecture-spec.md`.
  - Done when: the owner approves it.
  - On /done: add the next plan revision (rev 7) with the edits D1 §15 lists.
  - Handover: D1 approved after three reviews; plan rev 7 applies its §15 edits. Decision: per-stage code
    fingerprints with DVC's run cache on. Watch: eight D1 nits are still open (Hand-over); P1 must verify D1 §11.5.

#### S.3 D2: P0 bootstrap spec ✅
- ✅ **S.3.1** Draft the P0 bootstrap spec, `agent_docs/<YYYY-MM>/<YYYY-MM-DD>_p0-bootstrap-spec.md`.
  - Covers everything in hand-over §8 D2, built on the rev 5 answers:
    - the Step 0 safety copy into a dated folder beside `Backups/demq-elektric-dvc/`
    - git bundles in `Backups/demq-elektric-git/`: naming, verification, retention and restore
    - the explicit backup trigger (no git remote means no pre-push hook)
    - the uv setup for both repos: `.python-version` 3.14, dependency groups, and every tool as a dev
      dependency run through `uv run` (ruff, pyright, import-linter, pre-commit, DVC, jupyter, nbstripout).
      Check whether pyright needs a system Node; if it does, pick an option that installs through uv.
    - the commands that create the private GitHub repo for the tool
    - how agent sessions get iCloud access, or which steps the owner runs by hand
    - `CLAUDE.md` drafts for the three locations: they describe this `/next` → `/examine` → `/done` loop and
      point to `TASKS.md`
    - the parent folder's `CLAUDE.md` and `AGENTS.md`, sourced from the tool repo and installed by
      `scripts/link_parent.py` like the skill wrappers; `check_agent_links.py` runs that script with `--check`
  - Refs: hand-over §6–§8; plan §5–§7, §12, §14.5, §15 P0; D1.
  - Done when: the owner approves it.
  - On /done: add the next plan revision with the changes D2 lists.
  - Handover: D2 approved (14 review findings fixed), plus `icloud_backup.py` and its runbook; plan rev 8.
    Decision: the owner runs every backup. Watch: two D1 changes wait for D1's next edit (Hand-over).

#### S.4 D3: P1 landing & catalog spec ✅
- ✅ **S.4.1** Draft the P1 landing & catalog spec, `agent_docs/<YYYY-MM>/<YYYY-MM-DD>_p1-landing-catalog-spec.md`.
  - Covers everything in hand-over §8 D3:
    - the catalog record model and `source_ts` per source type
    - detector rules
    - the landing algorithm and commit message
    - the `energatron land` contract
    - the synthetic NEM12 fixture generator
    - the test list
  - Refs: hand-over §5, §8 D3, §10; plan §3.2, §8, §12, §15 P1; D1 §4.1–4.3, §6–§8.
  - Done when: the owner approves it.
  - On /done: add the next plan revision with the changes D3 lists.
  - Handover: D3 approved (19 review findings fixed); plan rev 9. Decision: partial landing (exit 8), with a
    foreign file aborting all. Watch: D3 adds three port methods to D1 (Hand-over, "Waiting for D1").

#### S.5 D4: work breakdown ✅
- ✅ **S.5.1** Propose the thin vertical slice (land → parse → merge → daily-totals report) for the owner to
  decide on. Then write the P0–P2 tasks and sub-tasks into this file, in order.
  - Each sub-task gets Refs, Files and a Done when, and is sized for one session.
  - P2 starts with its own spec sub-task.
  - This replaces the separate `…_p0-p2-work-breakdown.md` from hand-over §8: D4 lives here.
  - Refs: hand-over §8 D4; plan §13, §15; D1–D3.
  - Done when: the owner approves the breakdown.
  - On /done: check the spec-stage exit criteria (hand-over §8) and mark the spec stage ✅.
  - Handover: D4 is the P0–P2 task list in `TASKS.md` (38 sub-tasks, ⛔ = owner steps), approved with its four
    D2/D3 deviations. Decision: slices inside each phase, `sapn_interval` first (P1.4). Watch: first commits P0.3.1.
