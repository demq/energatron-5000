# Tasks archive: P10 local web UI

- **Created:** 2026-11-14
- **Status:** Archive. These lines were moved here verbatim from `TASKS.md` when P10.3.2 closed phase P10.
- **Privacy note:** this file lives in the public-ready tool repo. It contains no identifiers or usage figures,
  and nothing added to it may contain them either.

---

From plan §15 (P10) and §14.6: `energatron serve`, a local, read-only web UI and JSON API over the services, so every
CLI read path is available in the browser with no new logic outside the adapters. Started by the owner's `/phase 10` of
2026-11-14. D1 binds all code. The owner keeps looking through the UI in a real browser, pushing and backups.

#### P10.1 P10 spec ✅
- ✅ **P10.1.1** Draft the P10 spec.
  - Refs: plan §4, §14.2, §14.6, §14.7, §15 (P10); D1 §2, §3, §7, §8, §12, §14; the P7 spec §4 (`status`), the P8
    spec §8 (`diagnose`), the P9 spec §3, §5 (read commands, the MCP adapter).
  - Files: `agent_docs/2026-11/2026-11-14_p10-local-web-ui-spec.md`, the plan and D1 (revision lines and edits),
    `TASKS.md`.
  - Done when: the spec is approved and the build and acceptance sub-tasks are added from it.
  - Handover: P10 spec approved on the owner's `/phase 10` (self-review: cookie name per port, access checks as §5.4, pages render
    from redacted documents only); plan rev 19, D1 rev 11. Watch: no script frontend, so no Node; `serve` is read-only.

#### P10.2 Build ✅
- ✅ **P10.2.1** Shared read paths and envelope.
  - `adapters/envelope.py` (`CliResult`, `CliError` moved from `cli/output.py`, `success`, `failure`, `redacted`);
    `adapters/reads.py` (`open_context`, `ReadPath`, `READS`: each read path's args model and body, moved from its
    command); the CLI read commands call it with no change in output; `test_reads` classifies every CLI command.
  - Refs: the P10 spec §3, §8 (`test_reads`); D1 §3, §8.5, §8.7.
  - Files: `src/energatron/adapters/{envelope,reads}.py`, `src/energatron/adapters/cli/{output,common}.py`,
    `src/energatron/adapters/cli/commands/` (the read commands), `tests/unit/adapters/test_reads.py`, `CHANGELOG.md`.
  - Done when: the standard checks pass with the existing CLI tests unchanged; `test_reads` passes.
  - Handover: `adapters/envelope.py` (`success`, `failure`, `redacted`, `dumps`), `adapters/reads.py` (15 `ReadPath`s, `open_context`, `parse`),
    CLI read commands call them (3899 tests). Decision: the CLI keeps its own `--source`/`--fuel` messages (spec noted). Watch: `version` runs with no context.
- ✅ **P10.2.2** The JSON API.
  - `fastapi`, `uvicorn` (runtime) and `httpx` (dev) through `uv add`; `adapters/http/{app,security,api}.py`: one route
    per read path answering the CLI's envelope, status codes, the gas bill check `POST`, the opt-in bill text, access
    checks and headers; the `http-via-wiring` contract.
  - Refs: the P10 spec §4, §5, §8 (`test_http_api`, `test_http_security`), §10 (D1 §3); D1 §7, §8.
  - Files: `pyproject.toml`, `uv.lock`, `.importlinter`, `src/energatron/adapters/http/`,
    `tests/unit/adapters/http/`, `CHANGELOG.md`.
  - Done when: the standard checks pass; every API body equals the CLI's `--json` envelope for the same arguments.
  - Handover: `adapters/http/{app,reader,security,api}.py`: 13 query routes + gas-bill check/text, access middleware, `http-via-wiring` (3950 tests).
    Decision: `httpx2` not `httpx` (Starlette 1.10), `Reader` in its own module (spec, D1 noted); HEAD answered as GET. Watch: pages reuse `Reader.answer`.
- ✅ **P10.2.3** The pages.
  - `adapters/http/{pages,style}.py` and the page routes (§6.1), the two reports, the gas bill check form; the
    `http-pages-format-only` contract.
  - Refs: the P10 spec §6, §8 (`test_http_pages`), §10 (D1 §3).
  - Files: `.importlinter`, `src/energatron/adapters/http/`, `tests/unit/adapters/http/`, `CHANGELOG.md`.
  - Done when: the standard checks pass; every page renders on the read workspace and an empty one, and every link
    answers.
  - Handover: `http/{views,pages,style}.py`: 7 read pages, gas bill form, two reports, `/style.css`; `http-pages-format-only` (3981 tests).
    Decision: one generic escaping renderer + per-page parts; routes in `views.py` (spec noted). Watch: `/health` is 409/422 where a check fails.
- ✅ **P10.2.4** `energatron serve`.
  - `adapters/http/server.py` (bound socket, uvicorn, signals), `cli/commands/serve.py`, `serve_result` v1 (golden
    schema), README "Web UI" (checked by `test_readme`), CHANGELOG.
  - Refs: the P10 spec §7, §8 (`test_serve_cli`, `test_serve`), §9; D1 §8.4, §8.8.
  - Files: `src/energatron/adapters/http/server.py`, `src/energatron/adapters/cli/{app.py,commands/serve.py}`,
    `tests/unit/adapters/` (serve CLI), `tests/integration/test_serve.py`, `tests/fixtures/schemas/`, `README.md`,
    `tests/` (readme), `CHANGELOG.md`.
  - Done when: the standard checks pass; a real `energatron serve --port 0` child serves a page with its token and
    stops with exit 0 on SIGINT.
  - Handover: `http/server.py` (bind, prepare, stop_signals, run), `serve` command, `serve_result` v1, README "Web UI" (3993 tests).
    Decision: uvicorn subclassed so SIGINT/SIGTERM exit 0 (spec noted). Watch: the token is in the printed URL only; no access log.

#### P10.3 Acceptance ✅
- ✅ **P10.3.1** P10 acceptance (agent part).
  - The P10 spec §11 checks 1-3: every page and API route in a fresh demo workspace (parity with the CLI, denylist,
    security spot checks, workspace unchanged); the same fetches in the owner's workspace, read-only; the README lines.
  - Refs: the P10 spec §11; plan §15 (P10).
  - Files: `TASKS.md`.
  - Done when: checks 1-3 pass; `TASKS.md` gets counts and pass/fail only.
  - Handover: check 1 pass (fresh demo: 13/13 API = CLI, 12/12 pages, 4/4 security, denylist 0 of 25, workspace unchanged);
    check 2 pass (owner's workspace via the tool's env: 13/13 = CLI, 12/12 pages, denylist 0 of 25, unchanged; fingerprint stale as on the CLI); check 3 pass.
    Fix `P10.3.1: load the web stack only when serve runs`: the workspace's old uv.lock broke every command. Watch: the workspace needs `uv lock`.
- ✅ **P10.3.2** Owner: the UI in a browser, push.
  - Why: judging the UI in a real browser and pushing are the owner's.
  - Refs: the P10 spec §11 checks 4-5; first `uv lock`, `uv sync` and a `uv.lock` commit in the workspace (P10.3.1).
  - Files: none.
  - Done when: the owner reports having looked through every page and both reports (with any findings), and the push
    with CI green.
  - Handover: the owner reported all steps passed (2026-11-14): workspace `uv lock`/`uv sync` and `uv.lock` commit, every page
    and both reports looked through in a browser with no findings, the push with CI green. P10 closed.
