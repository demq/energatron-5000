# P10 local web UI spec

- **Created:** 2026-11-14
- **Status:** Approved 2026-11-14 (by the agent, on the owner's /phase instruction of 2026-11-14), after a
  self-review; Q1-Q8 as written. Its §10 edits are in the plan as rev 19 and in D1 as rev 11
- **Deliverable:** P10.1.1 in `TASKS.md`
- **Builds on:** [`2026-10-23_data-pipeline-plan.md`](../2026-10/2026-10-23_data-pipeline-plan.md) rev 18 ("the plan"),
  [`2026-10-23_core-architecture-spec.md`](../2026-10/2026-10-23_core-architecture-spec.md) rev 10 ("D1", binding for all
  code), the P7 spec §4 (`status`), the P8 spec §8 (`diagnose`) and the P9 spec §3 (the read commands) and §5 (the MCP
  adapter)
- **For:** the agent implementing P10; `/done` of P10.1.1 writes P10's sub-tasks from §2
- **Privacy note:** contains no identifiers or usage figures, and nothing added to it may. Examples use placeholders.

---

## 1. What this spec fixes

P10 lets the owner look at a workspace in a browser: where things stand, the recommendation and the plans behind it,
usage, datasets, offer changes, the inbox, the workspace's health, and the HTML reports the pipeline already builds.
Plan §15's P10 row: "`energatron serve`: FastAPI + frontend over the services. Acceptance: all CLI read paths are
available in the browser; no new logic outside adapters." D1 and the earlier specs stay binding; §10 lists every change
this spec makes.

**What P10 builds**
- One shared definition of every CLI read path, which the CLI and the web adapter both call (§3).
- `adapters/http/`: a FastAPI app with a JSON API (one route per read path, answering the CLI's own envelope) and
  server-rendered HTML pages over the same results, behind loopback-only access checks (§4-§6).
- `energatron serve`: runs the app on `127.0.0.1` until stopped (§7).

**Not in P10** (later, or the owner's): write paths in the browser (`land`, `fetch-offers`, `run`, `decide`,
`gas-bill write`, `landing clear`); serving to anything but the loopback interface; a hosted or multi-user service, auth
beyond the per-launch token, Postgres, S3 or a job queue (P11, only after its own design doc and security review); a
JavaScript or TypeScript frontend and any Node toolchain (§12 Q1); charts beyond those already in the generated reports;
live updates.

**Facts it relies on** (checked 2026-11-14, structure only)
- Every read command is thin: open the workspace (`cli/common.open_workspace`: denylist, then `wiring.build_context`),
  sometimes ask `wiring` for live code state (`status`, `explain`, `diagnose`), call one service, return a
  `ServiceResult`. A few hold small glue in the command module: `diagnose` builds `ToolInfo`, `schedule status` reads the
  launchd agent, `pipeline sync --check` builds an empty `PipelineSyncResult`, `gas-bill check` raises
  `E_CHECK_FAILED` from the result.
- Read paths that take no lock and write nothing (D1 §8.8 and the commands as built): `status`, `datasets`, `usage`,
  `compare`, `explain`, `offers diff`, `landing list`, `backup --status`, `diagnose`, `fingerprint --check`,
  `pipeline sync --check`, `schedule status`, `version`, `gas-bill check`, `gas-bill text`. `denylist --check` takes the
  lock, so it is not one of them.
- The envelope (`CliResult`, `cli_result` v1) and its redaction (`redact_json`) live in `adapters/cli/output.py`.
- The pipeline's reports are self-contained HTML with no `<script>`: `data/reports/usage/index.html` and
  `data/reports/recommendations/index.html`.
- No HTTP stack is a dependency today; the CDR client uses the stdlib. FastAPI 0.162 (Starlette 1.x) and uvicorn 0.54
  support Python 3.14 and pydantic 2.14 (PyPI, 2026-11-14). FastAPI's `TestClient` needs `httpx`.

---

## 2. Order of work

`/done` of P10.1.1 turns the rows into sub-tasks.

| # | Sub-task | Builds | Sections |
|---|---|---|---|
| 1 | P10.2.1 Shared read paths | `adapters/envelope.py` (the envelope moved out of `cli/output.py`), `adapters/reads.py` (every read path's arguments and function), the CLI read commands calling it, the command classification test | §3 |
| 2 | P10.2.2 JSON API | dependencies, `adapters/http/{app,security,api}.py`, the API routes, access checks, the import contracts, parity and security tests | §4, §5, §8 |
| 3 | P10.2.3 Pages | `adapters/http/pages.py` and `style.py`, the page routes, the reports, the gas bill check form, page tests | §6, §8 |
| 4 | P10.2.4 `energatron serve` | `adapters/http/server.py`, `cli/commands/serve.py`, `serve_result` v1, the integration test, README "Web UI", CHANGELOG | §7, §8 |
| 5 | P10.3.1 Acceptance (agent part) | §11's agent checks | §11 |
| 6 | P10.3.2 Owner: the UI in a browser | a look through every page in the owner's workspace; push | §11 |

No core, service, stage or persisted-model change: stage code entries don't move and the next `run` reruns nothing.

---

## 3. Shared read paths (`adapters/envelope.py`, `adapters/reads.py`)

### 3.1 The envelope

`CliResult` and `CliError` move from `cli/output.py` to `adapters/envelope.py`, unchanged (`cli_result` v1, same golden
schema), with two functions both driving adapters use:
- `success(command, result, error=None) -> CliResult` and `failure(command, error) -> CliResult`: the envelope as
  `write_success` and `write_failure` build it today (D1 §8.5).
- `redacted(envelope, denylist) -> dict`: `redact_json` over the dumped envelope (D1 §8.7).

`cli/output.py` imports them; its behaviour and the CLI's output don't change.

`open_workspace`'s non-CLI part moves to `adapters/reads.open_context(paths) -> (ServiceContext, denylist)`: the manual
denylist first, then `wiring.build_context`, then the derived entries (as `cli/common.open_workspace` does it today,
which then calls it).

### 3.2 The read paths

`adapters/reads.py` holds one `ReadPath` per read command:
- `name` (the API route segment, e.g. `offers/diff`), `command` (the envelope's `command`, as the CLI names it: `offers`
  for `offers diff`, `backup` for `backup --status`), `argv` (the CLI words, e.g. `("offers", "diff")` or
  `("backup", "--status")`);
- `args`: a pydantic model (`extra="forbid"`, frozen) with the CLI's options as fields, bounds and defaults taken from
  the same service constants the typer options use (`datasets_service.MAX_LIMIT`, …);
- `run(context, args) -> ServiceResult`: the command's body after `open_workspace`, moved here from the command module
  (the `wiring` calls, `ToolInfo`, the launchd state, the empty `PipelineSyncResult`, the `E_CHECK_FAILED` raise).

| Name | Command (CLI) | Args |
|---|---|---|
| `status` | `status` | none |
| `datasets` | `datasets` | `source?`, `limit` |
| `usage` | `usage` | `months` |
| `compare` | `compare` | `fuel?`, `limit`, `brand?` |
| `explain` | `explain [REC_ID]` | `rec_id?`, `options` |
| `offers/diff` | `offers diff` | `plan?` |
| `landing` | `landing list` | none |
| `backup/status` | `backup --status` | none |
| `diagnose` | `diagnose` | none |
| `fingerprint/check` | `fingerprint --check` | none |
| `pipeline/check` | `pipeline sync --check` | none |
| `schedule/status` | `schedule status` | none |
| `version` | `version` | none (needs no workspace) |
| `gas-bill/check` | `gas-bill check SOURCE` | the record's bytes (§5.2) |
| `gas-bill/text` | `gas-bill text PDF_DATASET_ID` | `pdf_dataset_id` (§5.3) |

Each CLI read command becomes: `begin`, `open_workspace`, `reads.<path>.run(context, args)`, `CommandOutput` with its
text. Arguments the CLI validates by hand today (`--fuel`, `--source`) are validated by the args model and raise the same
`E_USAGE`. (Built so in P10.2.1: the CLI keeps its own checks and messages for those two, so its output is unchanged;
the args model's `parse` validates loose values, such as an HTTP query, raising `E_USAGE` with `details.parameter` the
field's name and a generic message.) Nothing in `reads.py` computes a figure: it only calls services and wiring, as the commands did.

`READS` (the table) is ordered as above. `test_reads` (§8) fails when a CLI command (or subcommand) is neither in `READS`
nor in the test's list of commands that write, lock or serve, so a new command has to be classified.

---

## 4. The app (`adapters/http/app.py`)

`create_app(root, *, token, port, allow_documents) -> FastAPI` builds the app for one workspace root (found by `serve`
before it starts, D1 §8.3). FastAPI's generated docs are off (`docs_url`, `redoc_url` None: they load scripts from a
CDN); the OpenAPI document is at `/api/openapi.json`, behind the access checks.

- **In process.** Routes call `reads` directly (D1 §3: driving adapters call services and reach driven adapters only
  through wiring). Unlike MCP (P9 spec §5.4), there is no child process per request: a page makes several reads and no
  model provider sits between.
- **Fresh per request.** Each request opens the workspace again (`reads.open_context`), so a changed private config or
  denylist applies at once and nothing is cached between requests.
- **One at a time.** A process-wide lock serialises the reads (single user; `wiring` and logging aren't shared-state
  safe). Route functions are plain `def` (FastAPI runs them in its thread pool).
- **Read-only.** Nothing writes under the workspace and no route takes the workspace lock. A read that fails because
  another command is writing (e.g. a half-written mart) answers its error, as the CLI would.
- **Errors.** An `EnergatronError` from a read becomes the failure envelope; any other exception is `E_INTERNAL`
  (logged with its traceback only under `--debug`, through the redacting formatter), never a stack trace in a response.

---

## 5. JSON API (`adapters/http/api.py`)

### 5.1 Routes

`GET /api/v1/<name>` for every read path in §3.2 but `gas-bill/check` and `gas-bill/text`. Query parameters are the
args model's fields (`rec_id`, `limit`, …), validated from `request.query_params` against it; an unknown or repeated
parameter or an invalid value → `E_USAGE` naming the parameter, never echoing the value. `GET /api/v1` lists the routes (name, command, parameters).

The body is exactly the envelope the CLI prints with `--json` for the same arguments, redacted the same way: same
`command`, `data`, `warnings` and `error`. HTTP status by the envelope's exit code:

| Exit | Status |
|---|---|
| 0 | 200 |
| 1 internal | 500 |
| 2 usage | 400 |
| 3 input | 422 |
| 4 state | 409 |
| 5 privacy | 403 |
| 6 external | 502 |
| 7 check | 422 |
| 8 partial | 207 (no read path returns it today) |

`Content-Type: application/json`; the JSON is `ensure_ascii=False`, `allow_nan=False`, as the CLI writes it.

### 5.2 `POST /api/v1/gas-bill/check`

The body is the record, YAML or JSON (as `gas-bill check -` reads stdin), at most 1 MiB (`E_USAGE`, `reason:
"too_large"`, beyond). The answer is `gas-bill check`'s envelope: 200 when every check passes, 422 with
`E_CHECK_FAILED` otherwise. It writes nothing.

### 5.3 `GET /api/v1/gas-bill/text/{pdf_dataset_id}` (opt-in)

Only with `serve --allow-documents`, as for MCP (P9 spec §6.3): a bill's text holds personal detail beyond the denylist.
Without the flag the route doesn't exist (404, the same fixed body as any unknown path).

### 5.4 Access checks (`adapters/http/security.py`)

One middleware, applied to every route, API and pages alike (§6), in order:

1. **Host.** The `Host` header must be `127.0.0.1:<port>`, `localhost:<port>` or `[::1]:<port>` for the bound address
   family. Anything else → 400 with a fixed body. This stops DNS rebinding.
2. **Token.** `serve` makes a per-launch token (`secrets.token_urlsafe(32)`). A request carries it as the cookie
   `energatron_session_<port>` (cookies are shared across ports, so two servers mustn't share a name), or as `Authorization: Bearer <token>` (scripts, tests). A `GET` with `?token=<token>` sets the
   cookie (`HttpOnly`, `SameSite=Strict`, `Path=/`, no expiry: it dies with the browser session) and answers 303 to the
   same path without the parameter. Missing or wrong → 401 with a fixed page saying to open the address `energatron
   serve` printed. Tokens are compared with `hmac.compare_digest`. This keeps other accounts on the machine out.
3. **Cross-site writes.** A `POST` must have no `Origin` or the server's own origin (`http://<Host>`), and no
   `Sec-Fetch-Site` other than `same-origin` or `none`; else 403. No CORS headers are ever sent.
4. **Methods.** Only `GET` and `HEAD` everywhere, and `POST` on the two gas bill check routes; else 405.

Every response carries `Cache-Control: no-store`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`,
`X-Frame-Options: DENY`, `Cross-Origin-Resource-Policy: same-origin`, `Cross-Origin-Opener-Policy: same-origin` and a
`Content-Security-Policy`:

| Responses | Policy |
|---|---|
| pages and API | `default-src 'none'; style-src 'self'; img-src 'self' data:; form-action 'self'; frame-ancestors 'none'; base-uri 'none'` |
| reports | `default-src 'none'; style-src 'unsafe-inline'; img-src data:; frame-ancestors 'none'; base-uri 'none'; form-action 'none'; sandbox` |

Fixed bodies never echo the request (path, header or parameter).

---

## 6. Pages (`adapters/http/pages.py`, `style.py`)

Server-rendered HTML, one stylesheet, no JavaScript. Each page route runs its reads, takes each **redacted envelope
document** (§3.1), and renders it; the page renderers see only those JSON documents.

### 6.1 Pages

| Path | Reads | Shows |
|---|---|---|
| `/` | `status`, `version` | where things stand, one section per `status` part (pipeline, last run, recommendation, offers, inbox, decisions, backup, drill), the reminders, links to every page |
| `/recommendation` | `explain`, `compare` | action, robustness, recheck date, options, sensitivity, what to check, the run and inputs, then the ranked plans per fuel; query `fuel`, `limit`, `brand`, `options` passed through |
| `/usage` | `usage` | electricity per suffix (figures, then months) and gas (reference, months); query `months` |
| `/datasets` | `datasets` | counts per source, then the list; query `source`, `limit` |
| `/offers` | `offers/diff` | the changes per plan; query `plan` |
| `/inbox` | `landing` | inbox files, landed or new |
| `/health` | `backup/status`, `fingerprint/check`, `pipeline/check`, `schedule/status`, `diagnose` | each as its own section |
| `/gas-bill` | `gas-bill/check` | a form with one text area; `POST /gas-bill` (form-encoded, parsed with the stdlib) shows each check, the totals and the verdict; nothing is written |
| `/gas-bill/text/{id}` | `gas-bill/text` | the text in a `<pre>`; opt-in like §5.3 |
| `/reports/usage`, `/reports/recommendation` | none | the generated report file (§6.3) |

Every page has the same header (workspace pages as links, the tool version) and, under each section, a link to the API
route and its parameters that produced it ("JSON"). A failed read is shown in its section as its code and message (and
hint), with the page's status that of the first failure; the other sections still render.

### 6.2 Rendering rules

- Every value is escaped with `html.escape` (quotes included); nothing from a result goes into a tag, attribute name,
  URL scheme or style. Links are built only from the fixed route table and `urllib.parse.quote`d query values.
- The renderers format; they don't compute: no arithmetic, sorting, filtering, ranking or ID logic. Values are shown as
  the result holds them (money as its decimal string, dates as ISO strings, null as "n/a"). Tables keep the result's
  order. An import contract keeps `pages.py` and `style.py` away from `energatron.services`, `energatron.core`,
  `energatron.adapters.wiring` and `energatron.adapters.reads` (§10).
- Plain, readable HTML: `<!doctype html>`, `lang="en"`, a `<title>` per page, tables with `<th scope>`, one stylesheet at
  `/style.css` (a constant in `style.py`), light and dark through `prefers-color-scheme`, no external resource.

### 6.3 Reports

`/reports/usage` and `/reports/recommendation` serve `data/reports/usage/index.html` and
`data/reports/recommendations/index.html` as they are, passed through `redact()` (the last line of defence, D1 §8.7),
with their own CSP (§5.4's table). A missing file → 404 with a page saying to run the pipeline. No other file under
`data/` is served: the paths are fixed, not taken from the request.

---

---

## 7. `energatron serve` (`adapters/http/server.py`, `cli/commands/serve.py`)

`energatron serve [--host 127.0.0.1|localhost|::1] [--port N] [--open] [--allow-documents] [--workspace PATH] [-v]
[--debug] [--json]`.

- Finds the workspace first (D1 §8.3); outside one, the usual error and exit 2. It takes no lock (D1 §8.8) and writes
  nothing under the workspace.
- `--host` defaults to `127.0.0.1`; any other value than the three loopback names → `E_USAGE` (`parameter: "--host"`):
  P10 never listens beyond the machine. `--port` defaults to 8765; `0` picks a free port. The socket is bound and
  listening before anything is printed; a port in use → `E_USAGE` (`reason: "port_in_use"`).
- When listening, it prints its result once (D1 §8.4's second exception, beside `mcp`): `serve_result` v1 in the
  envelope with `--json`, else `Serving <workspace name> at <url> (Ctrl-C to stop).` `serve_result` v1: `host`, `port`,
  `url` (with `?token=`), `allow_documents`. Then it serves until SIGINT or SIGTERM and exits 0, printing nothing more on
  stdout.
- `--open` opens the URL in the default browser (`webbrowser`).
- uvicorn runs on the bound socket, `log_config=None`, access log off (a URL can hold the token), its loggers on the
  redacting stderr handler at WARNING (INFO with `-v`).

---

## 8. Tests

- `test_reads` (unit): every CLI command path is either in `READS` or listed by the test as writing, locking or serving;
  each `READS` entry's `argv` parses with the CLI; each args model's bounds and defaults equal the typer option's; the
  CLI read commands still print the same envelopes (the existing CLI tests stay green, unchanged).
- `test_http_api` (unit, `TestClient`, the `make_read_workspace` fixture and the fake VCS): for every API route, the
  body equals the CLI's `--json` envelope for the same arguments (one parametrised parity test, with arguments for
  each); status codes per §5.1 (a usage error, an `E_INVALID_INPUT`, `E_CHECK_FAILED` from the gas bill check); an
  unknown query parameter → 400 naming it without its value; the body size limit; `gas-bill/text` only with
  `allow_documents`; `/api/v1` lists every route; `/docs` and `/redoc` are 404.
- `test_http_security` (unit): wrong or missing `Host` → 400; no token, a wrong token, a wrong bearer → 401; `?token=`
  → 303 with the cookie's attributes and a `Location` without the token; the cookie then works; a cross-site `POST`
  (foreign `Origin`, `Sec-Fetch-Site: cross-site`) → 403; `PUT`/`DELETE` → 405; every response has §5.4's headers and
  no `Access-Control-*`; fixed bodies don't contain a probe string put in the path, a header or a parameter.
- `test_http_pages` (unit): every page renders (200) on the read workspace and on an empty workspace (missing marts:
  the "run the pipeline" text, still 200); every link on every page answers 200 or 404 for a missing report; a string
  with `<script>` and quotes in a result is escaped; a failing read shows its code in its section; a denylist entry
  planted in a result (a plan name) never appears in a page, an API body or a report; the gas bill form shows the
  failing check names.
- `test_serve_cli` (unit): `--host 0.0.0.0` → `E_USAGE`; a busy port → `E_USAGE` `port_in_use`; outside a workspace →
  exit 2 with one envelope.
- `test_serve` (integration): `energatron serve --port 0 --json` as a real child against a scratch workspace: the first
  stdout line is one `serve_result` envelope; `GET /` with the token's cookie and `/api/v1/status` with the bearer
  answer 200; SIGINT ends it with exit 0 within 10 s and stdout held exactly one line; the workspace's files are
  unchanged.
- Import contracts (§10) and the architecture tests stay green; `test_readme` checks the README's `serve` lines.

---

## 9. Kinds, codes and warnings added in P10

- Kinds: `serve_result` v1 (golden schema). `cli_result` v1 moves module, unchanged.
- Codes and warnings: none new (`E_USAGE`, `E_INTERNAL` and the read paths' own codes cover P10).
- CLI: `serve`. Locks: none (`serve` takes none; its reads take none).
- Dependencies: `fastapi` and `uvicorn` (plain, no `[standard]` extras) as runtime dependencies; `httpx` as a dev
  dependency (`TestClient`), all through `uv add`.

---

## 10. Plan and D1 changes

To be applied on `/done` of P10.1.1, as plan rev 19 and D1 rev 11.

**Plan**
- §14.6 item 1 made concrete: `energatron serve` is read-only, loopback-only with a per-launch token, FastAPI over the
  shared read paths in process, with server-rendered pages and no script frontend (the language rule's TypeScript
  stays for a later script frontend, if one is ever needed); the JSON API answers the CLI's envelope.
- §14.7: the local HTTP API and UI are built (P10); hosted pieces stay unbuilt.
- §12: a `serve` line among the runbooks.
- §15: the P10 row's acceptance is made concrete as this spec's §11; "P9 started 2026-11-14; P10-P11 are not
  scheduled" becomes "P9 and P10 started 2026-11-14; P11 is not scheduled".

**D1 (rev 11)**
- §2: adapters gain `envelope.py`, `reads.py` and `http/` (`app.py`, `security.py`, `api.py`, `pages.py`, `style.py`,
  `server.py`); `cli/commands/` gains `serve.py` (P10).
- §3: the `http` driving adapter calls services in process through `reads` and `wiring`. Two contracts: `http-via-wiring`
  (`energatron.adapters.http` imports no driven adapter, like `cli-via-wiring`), and `http-pages-format-only`
  (`energatron.adapters.http.pages` and `.style` import neither `energatron.services`, `energatron.core`,
  `energatron.adapters.wiring` nor `energatron.adapters.reads`).
- §8.4: `energatron serve` is the second exception: one result once it listens, then nothing on stdout while it serves.
- §8.8: `serve` takes no lock.
- §12: adapters' dependencies gain `fastapi` and `uvicorn` (P10). The languages rule: the P10 UI is server-rendered HTML
  from the Python adapter, with no script and no build step; the separate-directory, own-lockfile rule applies to a
  script frontend if one comes later.
- §14: "MCP and HTTP adapters" settled by the P9 and P10 specs.

---

## 11. P10 acceptance checks

**Agent part (P10.3.1)**, with the tool committed:
1. In a fresh demo workspace (`init --demo --tool-source <clone>`): `energatron serve --port 0 --json` as a child; every
   page and every API route (with arguments) through the token: pages 200, API bodies equal to the CLI's `--json`
   envelopes (the `status` and `diagnose` live fields aside: compared by `kind` and keys), no denylist entry of the
   demo in any body, and the reports served. Security spot checks with a plain HTTP client: a foreign `Host` → 400, no
   token → 401, a cross-site `POST` → 403, `/docs` → 404. SIGINT → exit 0. The workspace is unchanged (`git status`, HEAD).
2. In the owner's workspace, the same fetches: every page 200 (a `schedule/status` section may show its own error),
   denylist matches 0 over every body (counts only), the workspace unchanged (`git status`, HEAD), no lock held after.
3. The README's "Web UI" commands are the ones `test_readme` checks.

**Owner part (P10.3.2)**
4. **Plan §15:** in the workspace, `uv run energatron serve --open`; look through every page and both reports in the
   browser; say what is missing or unclear (findings become a follow-up sub-task, not a reopening of P10).
5. Push the tool repo; CI green.

---

## 12. Open questions / decisions for the owner

Each is decided here so P10 can be built; the recommendation is what the spec says.
1. **No script frontend, no Node.** Pages are server-rendered by the Python adapter: read-only views need no client
   logic, nothing needs a global install, and the UI ships inside the package so `uvx` users get it. The plan's "may use
   a web language" and D1's frontend rule stay for a later script frontend.
2. **In process, not a child per request.** D1 §3 already names `http` as a driving adapter that calls services; MCP's
   reason for children (one contract for agents with and without MCP) is met here by answering the CLI's own envelope
   from the same `reads` code, checked by the parity test.
3. **Read-only.** The acceptance asks for read paths; write paths in the browser need confirmation flows, lock
   handling and CSRF beyond §5.4, and are later work.
4. **Loopback, per-launch token, Host and Origin checks.** A localhost port is reachable by other accounts on the
   machine and by web pages through DNS rebinding; the token and the Host check close both, at the cost of opening the
   printed URL once.
5. **FastAPI and uvicorn as runtime dependencies**, not an extra: `serve` is part of the tool and `uvx --from git+…`
   installs no extras by default. Plain uvicorn keeps out uvloop, httptools and watchfiles.
6. **The API answers the CLI's envelope** (`cli_result` v1), not a new kind: one document per read path for CLI, MCP and
   HTTP clients alike.
7. **Bill text stays opt-in** (`--allow-documents`), as in P9.
8. **Reports served as built**, under a sandboxing CSP and `redact()`, rather than re-rendered.
