# Tasks archive: P8 open-source readiness

- **Created:** 2026-11-14
- **Status:** Archive. These lines were moved here verbatim from `TASKS.md` when P8.3.2 closed phase P8.
- **Privacy note:** this file lives in the public-ready tool repo. It contains no identifiers or usage figures,
  and nothing added to it may contain them either.

---

Started on the owner's instruction (2026-11-14). Built from the P8 spec (`agent_docs/2026-11/2026-11-14_p8-open-source-readiness-spec.md`, "the P8 spec") in its §2 order; D1
rev 9 binds all code. The spec's decisions (approved by the agent on the owner's instruction of 2026-11-14): a built-in
`upgrade` over hash-tracked managed files; hook scripts copied into templated workspaces; a frozen, code-generated demo
offer set; `diagnose` always redacted; no new region profiles; the workspace pins the tool `init` ran from. Making the
GitHub repo public, pushing and publishing a release stay the owner's.
- P8.2.3 adds `W_TEMPLATE_MODIFIED` to `core/errors.py`: every stage's code entry changes once (the next `run` reruns all).

#### P8.1 P8 spec ✅
- ✅ **P8.1.1** Draft the P8 open-source readiness spec.
  - Refs: plan §14 (§14.1-§14.4, §14.7), §15; D1 (the latest revision); the P0 spec (the hand-built workspace that
    becomes the `init` template); D3 §16; the P7 spec and the README; `CHANGELOG.md`.
  - Files: the new spec (`agent_docs/2026-11/<date>_p8-open-source-readiness-spec.md`); plan and D1 revisions if the
    spec changes them; `TASKS.md` (P8.2 build and P8.3 acceptance sub-tasks from the spec's build order).
  - Done when: the owner approves the spec; the P8.2 and P8.3 sub-tasks are added from it.
  - Handover: P8 spec approved by the agent on the owner's instruction (self-review), Q1-Q10 as written; plan rev 17, D1 rev 9.
    Decision: built-in `upgrade`, no copier. Watch: template files end in `.tmpl` (the guard rejects `.json` in package data).

#### P8.2 Build ✅
- ✅ **P8.2.1** Workspace template and `energatron init`.
  - `templates/workspace/*.tmpl`, `core/workspace_template.py` (`render`, `TEMPLATE_VERSION`), `template_state` and
    `init_result` v1, `adapters/setup/` (git, uv, dvc, pre-commit subprocesses), `services/init.py`, the `init`
    command (prompts only on a terminal), golden schemas.
  - Refs: the P8 spec §3, §4, §11; D2 §6, §10, §11; D1 §8.3, §8.6.
  - Files: `src/energatron/templates/workspace/`, `src/energatron/core/workspace_template.py`,
    `src/energatron/core/models/`, `src/energatron/adapters/setup/`, `src/energatron/adapters/wiring.py`,
    `src/energatron/services/init.py`, `src/energatron/adapters/cli/`, `.importlinter`,
    `tests/unit/core/test_workspace_template.py`, `tests/architecture/test_template_scripts.py`,
    `tests/unit/services/test_init.py`, `tests/integration/test_init_cli.py`, `tests/fixtures/schemas/`.
  - Done when: the standard checks pass; `init --no-sync` makes a workspace that `status` accepts and whose first commit
    the privacy guard passes.
  - Handover: template (`.tmpl`, seed/managed), `render`, `template_state`/`init_result` v1, `WorkspaceSetup` port + `adapters/setup`, `init`
    (21 s with a real `uv sync`); 3655 tests. Decision: the guard allows `.energatron/template.json`. Watch: `--demo` is P8.2.2's.
- ✅ **P8.2.2** The demo: `energatron demo` and `init --demo`.
  - `services/demo.py` (generators, the build), `adapters/offers/frozen.py`, `demo_result` v1, the `demo` command and
    `init --demo`, `test_demo`, `test_frozen_provider`.
  - Refs: the P8 spec §5, §11.
  - Files: `src/energatron/services/demo.py`, `src/energatron/adapters/offers/frozen.py`, `src/energatron/adapters/wiring.py`,
    `src/energatron/services/init.py`, `src/energatron/adapters/cli/`, `tests/integration/test_demo.py`,
    `tests/unit/adapters/test_frozen_provider.py`, `tests/fixtures/schemas/`.
  - Done when: the standard checks pass; `test_demo` reaches a recommendation, both reports and a passing `verify`.
  - Handover: `services/demo.py` (14 months NEM12, usage export on NEM time, 12 gas bills, 5 frozen plans), `FrozenProvider`, `demo`,
    `init --demo` (in-process with `--no-sync`); 3661 tests. Decision: no reference-window change needed. Watch: `test_demo` takes ~33 s.
- ✅ **P8.2.3** `energatron upgrade` and `energatron migrate`.
  - `services/upgrade.py`, `services/migrate.py`, `core/toml_text.py`, `upgrade_result` and `migrate_result` v1,
    `W_TEMPLATE_MODIFIED`, the commands.
  - Refs: the P8 spec §6, §7, §11; D1 §5.
  - Files: `src/energatron/services/{upgrade,migrate}.py`, `src/energatron/core/{toml_text.py,errors.py}`,
    `src/energatron/core/models/`, `src/energatron/adapters/cli/`, `tests/unit/core/test_toml_text.py`,
    `tests/unit/services/test_{upgrade,migrate}.py`, `tests/fixtures/schemas/`.
  - Done when: the standard checks pass.
  - Handover: `services/upgrade.py` (hash-tracked managed files, `--force`), `services/migrate.py` + `core/toml_text.py`, both commands,
    `W_TEMPLATE_MODIFIED`; 3681 tests. Decision: their tests are integration tests (real git). Watch: every stage reruns once (errors.py).
- ✅ **P8.2.4** `energatron diagnose` and the region-profile tests.
  - `services/diagnose.py`, `diagnose_result` v1, the command (`--redacted` accepted); `test_region_profiles`.
  - Refs: the P8 spec §8, §9, §11.
  - Files: `src/energatron/services/diagnose.py`, `src/energatron/core/models/`, `src/energatron/adapters/`,
    `tests/unit/services/test_diagnose.py`, `tests/architecture/test_region_profiles.py`, `tests/fixtures/schemas/`.
  - Done when: the standard checks pass; a fixture workspace's report holds none of its synthetic identifiers.
  - Handover: `services/diagnose.py` (versions, config kinds, counts, codes; failed blocks → `problems`), `diagnose [--redacted]`,
    `test_region_profiles`; 3688 tests. Decision: program versions keep the number only. Watch: on the real workspace it ran clean.
- ✅ **P8.2.5** Docs, CI demo job and release workflow.
  - README quickstart and "Your own workspace", `CONTRIBUTING.md`, `SECURITY.md`, the bug-report issue template,
    `CHANGELOG.md`, the `demo` CI job, `.github/workflows/release.yml`.
  - Refs: the P8 spec §10; plan §14.4.
  - Files: `README.md`, `CONTRIBUTING.md`, `SECURITY.md`, `CHANGELOG.md`, `.github/`, `tests/architecture/test_readme.py`.
  - Done when: the standard checks pass; `test_readme` covers the new commands.
  - Handover: README quickstart (uvx … `init --demo`), own workspace, after-update, diagnose; CONTRIBUTING (regions), SECURITY, issue
    template, CI `demo` job (macOS), `release.yml`; 3689 tests. Watch: the quickstart's git URL works only once the repo is public.

#### P8.3 Acceptance ✅
- ✅ **P8.3.1** P8 acceptance (agent part).
  - The P8 spec §13 checks 1-4: the quickstart in a fresh folder (timed), the demo workspace's checks, `diagnose`,
    `migrate --dry-run` and `upgrade --dry-run` on the real workspace (nothing committed there).
  - Refs: the P8 spec §13.
  - Files: `TASKS.md` (counts and pass/fail only); a dated workspace note for anything that mentions data.
  - Done when: checks 1-4 pass.
  - Handover: quickstart via `uvx --from git+file://…` to a recommendation in 60-69 s (warm cache); demo `status`/`verify`/`diagnose`
    clean, `upgrade --dry-run` all unchanged after a fix (`/private` in the template ignore). Real workspace: `diagnose` 0 denylist hits, 0 figures;
    `upgrade --dry-run` `not_templated`; its config-rewrite dry run was blocked by a local permission hook (owner to run). Nothing committed there.
- ✅ **P8.3.2** Owner: publish.
  - Why: owner steps (push, visibility, release).
  - The P8 spec §13 checks 5-8: push, CI green, the repo public, the quickstart from GitHub on a clean Mac in under 15
    minutes, optionally the first tagged release.
  - Refs: the P8 spec §13.
  - Files: none.
  - Done when: checks 5-7 pass.
  - Handover: owner 2026-11-14: config-rewrite dry run clean in the workspace, history audit 0 violations, pushed, CI `check` and `demo`
    green, repo public, private vulnerability reporting on. Check 7 (clean-Mac 15-minute run) waived by the owner; no release tagged.
