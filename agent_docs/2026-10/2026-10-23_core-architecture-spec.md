# Core architecture spec (D1)

- **Created:** 2026-10-23
- **Status:** Approved by the owner, 2026-10-23; rev 2 approved 2026-10-30; rev 3 approved 2026-10-30 (with the P2 spec); rev 4 approved 2026-11-03 (with the P3 spec); rev 5 approved 2026-11-04 (with the P4 spec); rev 6 approved 2026-11-05 (with the P5 spec); rev 7 approved 2026-11-10 (with the P6 spec); rev 8 approved 2026-11-10 (with the P7 spec); rev 9 approved 2026-11-14 (with the P8 spec)
- **Revisions:** 2026-10-30 rev 2 (P1.1.1): the changes D2 §1 and D3 §1 made to this spec, D4's decisions
  on `verify`, what P0 recorded, the owner's decisions of 2026-10-29 on redaction and the envelope's
  `command`, and the third review's nits, all edited in place (listed in §1)
  2026-10-30 rev 3 (P2.1.1): the changes the P2 spec (`2026-10-30_p2-meter-pipeline-spec.md`) §16 lists:
  the format readers in core, no `SourceParser.parse`, `ServiceContext.region`, `StageRun.zone()` and the
  `tzdata` dependency, the five P2 warnings, edited in place (listed in §1)
  2026-11-03 rev 4 (P3.1.1): the additive changes the P3 spec (`2026-11-03_p3-usage-profile-spec.md`) §15 lists:
  the usage core modules, `local_day_minutes`, the two P3 warnings and a stage with no params, edited in place
  (listed in §1)
  2026-11-04 rev 5 (P4.1.1): the additive changes the P4 spec (`2026-11-04_p4-offers-electricity-engine-spec.md`)
  §15 lists: the offer and engine modules, the CDR provider's header allow-list and paging, the four P4 warnings and
  the money rounding rule, edited in place (listed in §1).
  2026-11-05 rev 6 (P5.1.1): the additive changes the P5 spec (`2026-11-05_p5-gas-spec.md`) §14 lists: the gas
  modules, stages, parser and command, and the two P5 warnings, edited in place (listed in §1)
  2026-11-10 rev 7 (P6.1.1): the changes the P6 spec (`2026-11-10_p6-recommendation-spec.md`) §13 lists: the P6
  modules, stages and commands, `StageRun.stage_sha256`, the recommendation payload, run manifest and decision schema,
  and the three P6 warnings, edited in place
  2026-11-10 rev 8 (P7.1.1): the additive changes the P7 spec (`2026-11-10_p7-operations-spec.md`) §12 lists: the P7
  modules and commands, the notifier and launchd adapters in wiring, the six P7 warnings and two git-ignored workspace
  files, edited in place
  2026-11-14 rev 9 (P8.1.1): the changes the P8 spec (`2026-11-14_p8-open-source-readiness-spec.md`) §12 lists: the
  P8 modules and commands (`init`, `demo`, `upgrade`, `migrate`, `diagnose`), the `setup` and frozen-offer adapters,
  `.tmpl` templates without `dvc.yaml`, the locks and `W_TEMPLATE_MODIFIED`, edited in place
- **Deliverable:** D1 of [`2026-10-23_handover-next-planning-stage.md`](2026-10-23_handover-next-planning-stage.md) §8
- **Builds on:** [`2026-10-23_data-pipeline-plan.md`](2026-10-23_data-pipeline-plan.md) rev 6, called "the plan"
- **For:** the agent or person implementing P0 and P1, and every later phase spec
- **Privacy note:** this file lives in the public-ready tool repo (a private GitHub repo until P8). It contains no
  NMI, meter serial, account numbers, MIRN, address, postcode or usage figures, and nothing added to it may
  contain them either.

---

## 1. What this spec fixes

These are the decisions that are expensive to change once code and data exist: package layout and import rules,
port interfaces, persisted-artifact versioning, IDs and hashing, the error model, the CLI contract, the stage
contract, provenance and the code fingerprint. Phase specs (P1 onwards) build on them and must not redefine them.

"Must" means required. Where a rule is enforced by a test or a CI check, §13 names it.

**Changes from the plan, approved by the owner** (listed again in §15; applied in plan rev 7):
1. Two ports added: `VersionControl` (`dvc add`, git commit, backup) and `Clock`.
2. Stage contract: named `outputs` replace the single `out_dir`, and every stage output is a directory holding
   its files plus `_provenance.json`.
3. Dataset IDs start with the full source type (`sapn_interval-…`, not `sapn-…`).
4. Every persisted document carries `kind` alongside `schema_version`.
5. A workspace is marked by `.energatron/workspace.toml`, and mutating commands take a workspace lock.
6. Provenance holds no timestamps or run IDs, so stage outputs are byte-identical across reruns.
7. git and DVC are called as subprocesses. The `energatron` package doesn't depend on DVC.
8. Stage code reads no environment variables. `energatron run` doesn't pass a run ID to stages, and `--draft`
   doesn't stamp stage outputs: the run ID and the draft flag live only in the run manifest (§9.4).
9. `landed_at` and every other instant energatron generates is UTC (the plan's example shows a local offset).
10. Stage bodies and the stage registry live in `services/stages/`, not `adapters/pipeline/`. That adapter
    package holds only the DVC runner and the code fingerprint.
11. A git-ignored, machine-specific `.energatron/local.toml` sits beside `workspace.toml`.
12. Per-stage code fingerprints (owner decision): a stage goes stale only when code it actually uses changes:
    - its modules, its declared package data, the third-party packages it imports, and the Python version
    - not on a tool commit, a change to another stage or a dev-tool bump
    - stages no longer depend on `uv.lock` or on the whole fingerprint file (§11)

**Rev 2 (2026-10-30), edited in place.** The phase specs say they change this spec; rev 2 makes the text
agree with them:
- From D2 (P0 bootstrap spec) §1:
  - `VersionControl.backup_status()` and the backup models (change 12, §4.4)
  - a privacy-guard exit 2 maps to `E_COMMIT_REJECTED` (change 13, §4.4)
  - the VCS adapter is P0 (change 3, §2), P0 creates no catalogs (change 4, §9.2), D2's codes and warnings
    (change 7, §7), and `land` and `run` don't back up (change 11, §4.4)
- From D3 (P1 landing & catalog spec) §1 and §17:
  - `BlobStore.keys()`, `Catalog.missing_sources()` and `Catalog.initialise()` (§4.1, §4.2)
  - `land`'s `supersedes` and `note` (§4.8)
  - `energatron pipeline sync` creates new empty catalogs (§9.2), detects a drifted `dvc.yaml` (§9.5) and
    rewrites an older `params.yaml` ahead of `migrate` (§5)
- D4's decisions: `verify` is built in P1 and also checks every catalogued blob; where it lives and how it
  gets the live code entries (§11.4, §14).
- What P0 recorded: the warning model is `ResultWarning` (§7); the models P0 created ahead of their phase, and
  `catalog_record` registered in P0 (§2, §5).
- Owner decisions of 2026-10-29: `redact_json` also redacts numbers, with the matching rules spelled out
  (§8.7); a parse failure's envelope names only a known command (§8.5).
- The third review's nits: §5 "contain"; `ReproLogEntry` (§2); the venv first on the `dvc repro` child's
  `PATH` (§4.5, §9.5); `numpy.random` (§9.4, §13); "only through `stage_sha256`" (§9.5); a symlinked
  directory higher up (§11.1); `verify`'s error code and single process (§11.4); a killed stage (§13).
- The rev 2 review: what `BlobStore.stat()` hashes and what a missing key does (§4.2, §11.4, §13); the
  `stage_result` and `fingerprint_result` kinds (§5); "ASCII letter or digit" (§8.7).

**Rev 3 (2026-10-30), edited in place.** The P2 spec (§16 there) changes this spec; rev 3 makes the text agree
with it:
- §2: `core/` gains `text.py` (the pure helpers moved out of `adapters/sources/_common.py`), `sapn_summary.py`,
  `retailer_usage.py` and `models/meter.py`, beside `nem12.py` and `timebase.py`.
- §4.3: no `SourceParser.parse`. The registry chooses the parser that recognises a file; the code that reads a
  format's values is in core and serves both `inspect` and the stages.
- §4.8: `ServiceContext` gains `region: RegionProfile`, set by `build_context()`.
- §7: the five P2 warnings.
- §9.2, §9.4, §12: `StageRun.zone(key)`, its `importlib.resources` use as the second named exception, zone
  objects only from it, and `tzdata` as a runtime dependency.
- §13: the `test_stage_purity` additions.
- §14: the two P2 items are settled.

**Rev 4 (2026-11-03), edited in place.** The P3 spec (§15 there) adds to this spec; nothing is redefined:
- §2: `core/` gains `usage.py` (the metric functions; it replaces the `usage.py` placeholder under P3+),
  `charts.py`, `usage_report.py` and `models/usage.py`; `core/timebase.py` gains `local_day_minutes`.
- §7: the two P3 warnings.
- §9.1: a stage may declare no params (`params=()`); the P3 integration test confirms the runner, `prepare_stage`
  and the emitter accept it.

**Rev 5 (2026-11-04), edited in place.** The P4 spec (§15 there) adds to this spec; nothing is redefined:
- §2: `core/` gains `tariff.py`, `cdr_tariff.py`, `offer_rules.py`, `engine.py`, `retailer_txn.py` and
  `models/costs.py` (`ranking.py` moves to P6); `services/` gains `fetch_offers.py`, `offer_diff.py` and the stages
  `normalise_offers`, `cost_elec` and `backtest`; `adapters/` gains `offers/cdr.py` and the parsers `cdr_index.py`
  and `offer_manual.py`.
- §4.6: `Fetched` unchanged; the CDR provider's header allow-list, paging and errors.
- §7: the four P4 warnings.
- §12: the money rounding rule.

**Rev 6 (2026-11-05), edited in place.** The P5 spec (§14 there) adds to this spec; nothing is redefined:
- §2: `core/` gains `gas_tariff.py`, `cdr_gas_tariff.py`, `gas_engine.py`, `gas_bill.py` and `models/gas.py`;
  `services/` gains `gas_bill.py` and the stages `gas_bills`, `gas_profile`, `normalise_gas_offers`, `cost_gas` and
  `backtest_gas`; `adapters/` gains the parser `gas_bill_record.py` and the `gas-bill` command.
- §7: the two P5 warnings.

---

## 2. Package layout

Phase tags show when each module is first needed. P0 creates every package (with `__init__.py`) so the
import contracts in §3 are valid from the first commit, even where a package is still empty.

```
src/energatron/
├── __init__.py              __version__ = importlib.metadata.version("energatron-5000")          P0
├── __main__.py              python -m energatron → adapters.cli.app.main()                        P0
├── core/                    pure: no file, network, subprocess, environment or clock access
│   ├── errors.py            EnergatronError hierarchy, error codes, ResultWarning (§7)            P0
│   ├── persisted.py         Persisted base model, kind registry, migrations (§5)                  P0
│   ├── canonical.py         canonical_json, sha256_hex, tree_hash (§6)                            P0
│   ├── ids.py               every ID constructor and parser (§6)                                  P0
│   ├── privacy.py           derive_denylist(private_config), redact(), redact_json() (§8.7)       P0
│   ├── models/
│   │   ├── workspace.py     WorkspacePaths, WorkspaceConfig                                       P0
│   │   ├── private.py       PrivateConfig (the shape only; values are never logged)               P0
│   │   ├── vcs.py           BackupReport, BackupState, BackupStatus (§4.4)                        P0
│   │   ├── catalog.py       SourceType, CatalogRecord, Coverage, Detection, Detector, SourceInfo  P0*
│   │   ├── storage.py       StoredBlob (§4.2)                                                     P0*
│   │   ├── pipeline.py      StageStatus, ReproReport, ReproLogEntry (§4.5)                        P0*
│   │   ├── provenance.py    StageProvenance, CodeFingerprint, StageCode, stale_stages() (§10, §11) P1
│   │   └── offers.py        Fetched (§4.6)                                                        P0*
│   ├── text.py          decode, lines, csv_rows, is_decimal, parse_datetime, fail (from _common.py) P2
│   ├── nem12.py  sapn_summary.py  retailer_usage.py  timebase.py  models/meter.py                 P2
│   ├── usage.py  charts.py  usage_report.py  models/usage.py                                      P3
│   ├── tariff.py  cdr_tariff.py  offer_rules.py  engine.py  retailer_txn.py  models/costs.py      P4
│   ├── gas_tariff.py  cdr_gas_tariff.py  gas_engine.py  gas_bill.py  models/gas.py               P5
│   ├── ranking.py  bundle_rules.py  recommendation_report.py  models/recommend.py                 P6
│   └── operations.py  (reminders, the cheaper-plan test, notifications, the launchd plist)          P7
│   └── workspace_template.py  toml_text.py  (P8 spec §3.2, §7)                                   P8
├── ports/                   typing.Protocol definitions only (§4)
│   └── catalog.py  blobs.py  sources.py  vcs.py  pipeline.py  offers.py  clock.py                P0
│       setup.py  (`WorkspaceSetup`: `init`'s git, uv, DVC and pre-commit steps)                  P8
├── services/                use cases; local file I/O only at paths passed in
│   ├── context.py           ServiceContext (§4.8)                                                 P0
│   ├── files.py             atomic writes; JSON, JSONL and Parquet helpers (§12)                  P0
│   ├── land.py                                                                                    P1
│   ├── stages/__init__.py   empty: it runs on every stage import, so it holds no code (§11)       P1
│   ├── stages/runner.py     StageSpec, StageRun, prepare_stage(), run_stage() (§9)                P1
│   ├── stages/registry.py   STAGES, get_stage(); imports every stage module, imported by none     P1
│   ├── stages/catalog_index.py                                                                    P1
│   ├── verify.py            verify() (§11.4)                                                      P1
│   ├── fetch_offers.py  offer_diff.py  stages/{normalise_offers,cost_elec,backtest}.py            P4
│   ├── gas_bill.py  stages/{gas_bills,gas_profile,normalise_gas_offers,cost_gas,backtest_gas}.py   P5
│   ├── run.py  decide.py  stages/{sensitivity,bundles,recommend,outcomes}.py                      P6
│   ├── status.py  landing.py  watch.py                                                           P7
│   ├── init.py  demo.py  upgrade.py  migrate.py  diagnose.py  (P8 spec §4-§8)                    P8
│   └── compare.py  explain.py                                                                    P9+
├── adapters/
│   ├── wiring.py            composition root: build_context() (P0), code_fingerprint() (P1)       P0
│   │                        notifier(), launchd() (P7)
│   ├── notify.py  schedule/launchd.py  (P7 spec §7, §8)                                       P7
│   ├── setup/  offers/frozen.py  (P8 spec §4, §5.3: set-up subprocesses, the demo's offers)    P8
│   ├── workspace.py         find the workspace root; read workspace and private config files      P0
│   ├── storage/             jsonl_catalog.py, fs_blobs.py                                         P1
│   ├── sources/             registry.py, plus one module per source type                          P1
│   ├── vcs/                 git_dvc.py                                                            P0
│   ├── pipeline/            dvc_runner.py, fingerprint.py, closure.py (§11)                       P1
│   ├── offers/              cdr.py (CdrProvider); sources/ gains cdr_index.py, offer_manual.py    P4
│   │                        sources/ gains gas_bill_record.py; cli/commands/ gains gas_bill.py    P5
│   └── cli/                 app.py, output.py (envelope, redaction), commands/                    P0
├── regions/                 package data: sa.yaml                                                 P1
├── offer_rules/             package data: curated public offer rules                              P4
├── bundle_rules/            package data: curated dual-fuel bundle rules (P6 spec §5.1)           P6
└── templates/workspace/     package data: `.tmpl` files `energatron init` writes (P8 spec §3)    P8
```

\* Created in P0 ahead of its phase, because the ports' signatures name these models: the shapes §4 and D3
§3.1 and §4 give. `Detector` is the catalog record's `detector` (D3 §4.1). P1 completes `catalog.py`,
`storage.py` and `pipeline.py`; P4 completes `offers.py` if its spec changes `Fetched`. The phase specs add
modules this tree doesn't list (D2 §5.9, D3 §2).

---

## 3. Layers and import rules

| Layer | May import from energatron | Third-party allowed | I/O allowed |
|---|---|---|---|
| `core` | nothing outside `core` | stdlib (pure modules), pydantic, pandas, numpy | None. No files, network, subprocesses, environment variables or clock |
| `ports` | `core` (models used in signatures) | none; stdlib `typing`, `collections.abc` and `datetime` only | None |
| `services` | `ports`, `core` | + pyarrow, PyYAML (for `params.yaml`) | Local files only at paths passed in by the caller. No network or subprocesses. Wall-clock time only through the `Clock` port |
| `adapters` | everything | anything in the dependencies | Any |

Rules inside `adapters`:
- **Driven adapters** (`storage`, `sources`, `vcs`, `pipeline`, `offers`) are independent of each other.
- **Driving adapters** (`cli` now; `mcp` and `http` later) call services. They reach driven adapters only
  through `adapters/wiring.py`, which builds the `ServiceContext`.
- `adapters/workspace.py` may be imported by `wiring` and by driving adapters.

Why services may touch files at all: stage outputs are working files that the runner manages. A hosted runner
can download the inputs to a local scratch directory, run the same function and upload the results. Raw data
is the system of record, so it goes through the `BlobStore` and `Catalog` ports.

**Enforcement:** `.importlinter` at the tool repo root, run by `lint-imports` in pre-commit and CI. Verified on
2026-10-23: with `include_external_packages = True`, a forbidden contract catches stdlib modules such as
`subprocess`.

```ini
[importlinter]
root_package = energatron
include_external_packages = True

[importlinter:contract:layers]
name = Layers point down
type = layers
layers =
    energatron.adapters
    energatron.services
    energatron.ports
    energatron.core

[importlinter:contract:core-pure]
name = Core does no I/O
type = forbidden
source_modules = energatron.core
forbidden_modules =
    os
    subprocess
    shutil
    tempfile
    socket
    urllib
    http
    pyarrow
    httpx
    typer
    yaml
    duckdb
    dvc

[importlinter:contract:services-no-net]
name = Services do no network or subprocess I/O
type = forbidden
source_modules = energatron.services
forbidden_modules =
    subprocess
    socket
    urllib
    http
    httpx
    typer
    dvc

[importlinter:contract:driven-independent]
name = Driven adapters are independent
type = independence
modules =
    energatron.adapters.storage
    energatron.adapters.sources
    energatron.adapters.vcs
    energatron.adapters.pipeline
    energatron.adapters.offers

[importlinter:contract:cli-via-wiring]
name = CLI reaches driven adapters only through wiring
type = forbidden
source_modules = energatron.adapters.cli
forbidden_modules =
    energatron.adapters.storage
    energatron.adapters.sources
    energatron.adapters.vcs
    energatron.adapters.pipeline
    energatron.adapters.offers
allow_indirect_imports = True
```

Import rules can't catch `open()`, `Path.write_text()` or `datetime.now()` in core. An AST test covers those
(§13).

---

## 4. Ports

General rules:
- Ports are `typing.Protocol` classes in `energatron.ports`. Their signatures use only core models, stdlib
  types and `bytes`.
- Every model a port takes or returns lives in `core/models/` (§2), even where this section shows it next to
  the port. Port models are frozen pydantic models. They aren't `Persisted` (§5) unless something writes them to
  disk, in which case they gain `kind` and `schema_version`.
- Implementations raise only `EnergatronError` subclasses (§7). Third-party exceptions are wrapped, with the
  original kept as `__cause__`.
- No port method returns or logs personal identifiers, except `SourceParser.inspect()`, whose
  `SourceInfo.identifiers` field exists only for the landing privacy check (§4.3).

### 4.1 `Catalog`

```python
class Catalog(Protocol):
    def get(self, dataset_id: str) -> CatalogRecord | None: ...
    def find_sha256(self, sha256: str) -> CatalogRecord | None: ...
    def records(self, source: SourceType | None = None) -> Iterator[CatalogRecord]: ...
    def append(self, record: CatalogRecord) -> None: ...
    def missing_sources(self) -> list[SourceType]: ...    # sources with no catalog yet, sorted
    def initialise(self, source: SourceType) -> bool: ... # create an empty catalog; False if one exists
```

- `dataset_id` and `sha256` are each unique across **all** sources. `append` raises `E_ID_COLLISION` if either
  already exists. Callers check with `find_sha256` first; this is the second line of defence.
- Records are immutable. There's no update or delete; a correction is a new record that names what it
  `supersedes`.
- `records()` yields sources in sorted order, then records in append order within each source.
- **v1 `JsonlCatalog`:**
  - one file per source: `data/raw/<source>/_catalog.jsonl`
  - one compact canonical JSON object per line (§6), followed by `\n`
  - append: open in append mode, write, flush, `fsync`
  - old schema versions are upgraded in memory on read (§5)
  - a malformed line raises `E_CATALOG_CORRUPT`; the catalog is never repaired automatically
  - parsing lives in core (`CatalogRecord.from_json_line`), so stage code can read catalog files without this
    adapter
  - `initialise(source)` creates the source's raw directory and an empty `_catalog.jsonl` atomically (a temp
    file, then `os.link`, as in §4.2). An existing file is left alone, whatever it holds (D3 §3.4).
- `missing_sources()` and `initialise()` let `energatron pipeline sync` create the empty catalogs §9.2 needs
  without services touching the raw zone directly (§3).
- Callers hold the workspace lock (§8.8). The catalog itself doesn't lock.

### 4.2 `BlobStore`

```python
class BlobStore(Protocol):
    def put(self, key: str, data: bytes) -> StoredBlob: ...
    def get(self, key: str) -> bytes: ...
    def exists(self, key: str) -> bool: ...
    def stat(self, key: str) -> StoredBlob: ...
    def keys(self, prefix: str) -> list[str]: ...   # every stored file under prefix, sorted

class StoredBlob(BaseModel):          # core/models/storage.py
    key: str
    sha256: str
    bytes: int
```

- Keys are workspace-relative POSIX paths under `data/raw/`: no leading `/`, no `..`, no backslashes. An
  invalid key is a programming error (`E_INTERNAL`). The key layout is set per source by the P1 spec.
- `put` is write-once:
  - same key, same bytes → no-op, returns the existing blob
  - same key, different bytes → `E_BLOB_EXISTS`
- **v1 `FsBlobStore` write:**
  1. temp file in the target directory
  2. write and `fsync`
  3. `os.link(temp, final)`, which fails if `final` already exists, so nothing is overwritten even in a race
  4. unlink the temp file
  5. `chmod 0444`
  6. `fsync` the directory
- `keys(prefix)`: the prefix follows the key rules above and ends with `/`. The result holds every regular file
  under it, recursively, as keys, the catalog file included; `.DS_Store` is left out. A symlink under the
  prefix → `E_CATALOG_CORRUPT`, `details.reason: "symlink"` (D3 §3.4).
- `stat(key)` reads the stored file and hashes its bytes on every call; it never trusts a cached value, the
  file's mtime or the catalog, so its `sha256` and `bytes` describe what is on disk now.
- A key with no stored file: `get` and `stat` raise `E_CATALOG_CORRUPT` with `details.reason:
  "missing_blob"` (callers pass only catalogued keys, so a missing file means a damaged raw zone), and
  `exists` returns `False`. A caller that treats a missing blob as a finding, such as `verify` (§11.4), calls
  `exists` first. `test_fs_blobs` (P1.2.2) covers both.
- Whole-file `bytes` is deliberate: inputs are MBs, and landing needs the bytes in memory anyway to hash, sniff
  and inspect them.

### 4.3 `SourceParser`

```python
class SourceParser(Protocol):
    source: SourceType            # e.g. "sapn_interval"
    version: int                  # recorded in the catalog as detector.version; bump on any behaviour change
    def detect(self, filename: str, head: bytes) -> Detection | None: ...
    def inspect(self, filename: str, data: bytes) -> SourceInfo: ...
    # no parse(): the code that reads a format's values is in core (below)

class Detection(BaseModel):           # core/models/catalog.py, like SourceInfo
    source: SourceType
    evidence: tuple[Literal["filename", "content"], ...]   # what matched

class SourceInfo(BaseModel):
    source_ts: datetime           # aware or naive; the P1 spec sets the rule per source type
    coverage: Coverage | None     # first and last date covered, when the source has a range
    extra: dict[str, JSONValue]   # non-personal facts, e.g. interval_minutes, registers, days
    identifiers: dict[str, list[str]]   # e.g. {"nmi": [...]}; privacy check only (below)
    extension: str                # extension for the stored file, e.g. ".csv"
```

- `head` is the first 64 KiB of the file. `detect` must not need more.
- Parsers are pure: bytes in, models out. They live in `adapters/sources/`, and the registry
  (`adapters/sources/registry.py: PARSERS: tuple[SourceParser, ...]`) chooses the parser that **recognises** a
  file. A new region or meter format means adding a module and a registry entry for detection.
- The code that reads a format's values lives in core (`core/nem12.py`, `sapn_summary.py`,
  `retailer_usage.py`), because stages can't import `adapters/` (§3). It serves both `inspect` and the stages,
  which name their source directories in their `SPEC` and don't go through the registry. That is acceptable
  while each source has one format; a second format of one source reworks it (P2 spec §3.1).
- `SourceInfo.identifiers` is compared with private config during landing, then discarded. It is never
  persisted, logged or returned in command output.
- The P1 spec defines each parser's detection rules and how landing resolves no match or several matches.

### 4.4 `VersionControl` (added by this spec)

```python
class VersionControl(Protocol):
    def head(self) -> str | None: ...                              # full hex SHA; None before the first commit
    def dirty_paths(self, paths: Sequence[str] = ()) -> list[str]: ...
    def track(self, paths: Sequence[str]) -> list[str]: ...        # dvc add; returns the pointer files written
    def commit(self, paths: Sequence[str], message: str) -> str: ...  # returns the new commit SHA
    def backup(self) -> BackupReport: ...                          # dvc push, then a verified git bundle
    def backup_status(self) -> BackupStatus: ...                   # is a backup pending? local only

class BackupReport(BaseModel):        # core/models/vcs.py
    dvc_files_pushed: int             # files `dvc push` uploaded; 0 when the remote was already complete
    bundle_name: str                  # file name of the new, verified bundle (or the newest one); no directory
    bundles_kept: int                 # bundles left after retention
    bundles_removed: int              # bundles retention deleted
    bundle_created: bool              # False when the refs equal the newest bundle's heads (D2 §8.1)
    dataless_files: int               # iCloud placeholders under the backup locations (D2 §8.1)

class BackupStatus(BaseModel):        # core/models/vcs.py
    head: str | None                  # None before the first commit
    last_backup_head: str | None      # None when there's no backup marker
    last_backup_at: datetime | None   # aware, UTC
    refs_backed_up: bool              # the current refs equal those of the last backup
    commits_not_backed_up: int | None # None with no marker, or when the old head no longer exists
```

- **Commit SHAs** are full hex object names: 40 characters in a SHA-1 repo, 64 in a SHA-256 repo. Code never
  assumes the length. Validators accept `^[0-9a-f]{40}([0-9a-f]{24})?$`.
- `dirty_paths` lists tracked-but-modified and untracked files under `paths` (the whole repo if empty),
  workspace-relative.
- `commit` stages exactly the given paths (never `git add -A`) and runs the normal git hooks.
  - A hook rejection raises `E_COMMIT_REJECTED` (category `state`), naming the hooks that failed but not their
    output. Any hook can reject a commit, e.g. nbstripout or ruff.
  - If the privacy guard is among the failed hooks with exit code 1 (a hit), it raises `E_DENYLIST_HIT`
    (category `privacy`) instead. A guard exit code 2 is a setup error, such as a stale denylist, not evidence
    of personal data, so it stays `E_COMMIT_REJECTED`. D2 §8.6 gives the exact mapping. The privacy guard's
    own messages must not echo denylist entries either.
  - Nothing to commit → returns the current `head()`.
- `backup` pushes DVC data, then writes, verifies and stores a git bundle. A bundle that fails
  `git bundle verify` raises `E_STORAGE`. Paths, file naming and retention are set in the P0 spec (D2 §8).
  `BackupReport` holds counts and file names, never absolute paths.
  - Only the owner runs it (`energatron backup`, D2 §4): agents never touch iCloud.
- `backup_status` reads a local marker (`backup_state`, D2 §6.5) and the current refs. It touches no backup
  location, so agents and `/done` may call it (`energatron backup --status`, D2 §8.5). It is a port method
  because the check runs git, which services may not (§3).
- **v1 `GitDvcVersionControl`** runs `git` and `dvc` as subprocesses from the workspace venv. It doesn't
  import the DVC Python API, so the package doesn't depend on DVC; DVC is a dev dependency of the workspace.
- **Why a port:** landing and running end with "track, commit", and backups need the same tools. Services
  can't import adapters, and a hosted version would replace all of it with a database transaction plus object
  storage.
- `land` (P1) and `run` (P6) don't call `backup()`. After a commit they return the warning `W_BACKUP_PENDING`,
  with `details.commits_not_backed_up` from `backup_status()` (D2 §1 change 11, D3 §7.3).

### 4.5 `PipelineRunner`

```python
class PipelineRunner(Protocol):
    def status(self) -> list[StageStatus]: ...                     # stale stages only, in dvc.yaml order
    def repro(self, targets: Sequence[str] = ()) -> ReproReport: ...

class StageStatus(BaseModel):         # core/models/pipeline.py
    name: str
    changed_deps: list[str]           # workspace-relative paths, sorted
    changed_outs: list[str]           # workspace-relative paths, sorted
    changed_params: list[str]         # dotted params keys, sorted
    changed_cmd: bool

class ReproReport(BaseModel):         # core/models/pipeline.py; each list in dvc.yaml order
    ran: list[str]                    # stages whose command executed
    restored: list[str]               # stages DVC restored from its run cache without executing
    cached: list[str]                 # every other stage in scope: up to date, nothing done
```

- v1 `DvcRunner`:
  - `status` combines two sources and writes nothing:
    - `dvc status --json`, parsed
    - the live code entries (§11.1) compared with those in `.energatron/code_fingerprint.json`. `dvc status`
      can't see a code change until the file is refreshed, so the runner adds `stages.<name>.stage_sha256` to
      `changed_params` for each stage whose live entry differs from the recorded one, or has none.
    - Only the changed stage itself is listed; DVC reports its downstream stages once it has rerun.
  - `repro`:
    1. refreshes `.energatron/code_fingerprint.json` (§11), so a repro through energatron never runs against
       stale code entries
    2. reads `dvc.lock`
    3. runs `dvc repro [targets]`, with DVC's run cache on and `ENERGATRON_REPRO_LOG` set to a new empty file
       under `.energatron/tmp/` (git-ignored). The child process:
       - runs with the workspace root as its working directory, stdin from `/dev/null`, and stdout and stderr
         merged into one captured pipe
       - gets the parent's environment minus `ENERGATRON_JSON`, with `ENERGATRON_WORKSPACE` set to the root. So
         no child prints a JSON envelope, and `energatron run --json` still writes exactly one document (§8.4).
       - gets `PATH` with the running interpreter's directory (`Path(sys.executable).parent`, the workspace
         venv's `bin`) first. So `dvc.yaml`'s `energatron stage` commands run the same installed tool as the
         parent, whatever the caller's `PATH` holds. `dvc` itself is taken from the same directory, else
         from `PATH`, as `GitDvcVersionControl` does.
       - its output is forwarded line by line to the `energatron.adapters.pipeline` logger: INFO normally,
         and the last 50 lines at ERROR when the repro fails. That logger writes to stderr through the
         redacting formatter (§8.7).
    4. reads the log. `energatron stage` appends two lines to the file named by `ENERGATRON_REPRO_LOG`, when that
       variable is set. `main()` writes both, so a stage that fails before `run_stage` is still recorded (a
       missing params key, a registry import error, `E_NOT_A_WORKSPACE`):
       - `start`: as soon as the argv pre-scan (§8.5) finds the `stage` command and its name, before typer
         parses anything or the workspace is located
       - `end`: in `main()`'s outermost `finally`, with the exit code `main()` is about to return

       Lines are compact canonical JSON of kind `repro_log_entry`:
       `{"kind": "repro_log_entry", "schema_version": 1, "stage": "<name>", "event": "start" | "end",
       "exit_code": <int> | null}`. Only the CLI adapter reads the variable; stage code never sees it (§9.4).
       - `ran` = the stages with an `end` record whose exit code is 0
    5. reads `dvc.lock` again. `restored` = the stages whose entry is new or different but which aren't in `ran`.
    6. `cached` = every other stage the repro covered: the targets and everything upstream of them, or every
       stage when there are no targets.
    7. deletes the log in a `finally`, so a failed repro leaves nothing behind either
  - **When `dvc repro` exits non-zero**, the log names the failed stage: the one with a non-zero `end` record, or
    with a `start` record and no `end` (killed). `details` always holds `stage`, `exit_code`, `ran` and
    `restored` so far.
    - stage exit code 7 → `E_CHECK_FAILED`, the same category as running the stage directly (§7)
    - any other stage exit code, or none (killed) → `E_STAGE_FAILED`, `exit_code` as recorded or `null`
    - no failed stage in the log → DVC itself failed: `E_SUBPROCESS` with DVC's exit code
  - **Why the run cache stays on:** when a stage's deps, params and code entry return to values DVC has seen
    before, DVC restores the matching outputs instead of recomputing them. Examples are reverting a param or
    checking out an older workspace commit. Reporting `restored` separately keeps `ran` meaning "executed".
  - Outputs DVC recreates with an unchanged `dvc.lock` entry (e.g. after they were deleted) count as `cached`:
    their content equals what `dvc.lock` already records.
  - `dvc repro` has no JSON output, which is why the runner relies on the log and the `dvc.lock` diff, never on
    DVC's human-readable output.

### 4.6 `OfferProvider` (used from P4; signatures fixed now)

```python
class OfferProvider(Protocol):
    name: str                                                      # "cdr"
    def list_plans(self, brand: str) -> list[Fetched]: ...         # one Fetched per page
    def get_plan(self, brand: str, plan_id: str) -> Fetched: ...

class Fetched(BaseModel):             # core/models/offers.py
    url: str
    request_headers: dict[str, str]    # includes x-v
    status: int
    fetched_at: datetime               # aware, UTC
    response_headers: dict[str, str]   # a fixed allow-list; never cookies
    body: bytes                        # verbatim
```

- Providers never filter or interpret anything; `services/fetch_offers.py` does. Bodies are landed verbatim.
- `Fetched` is unchanged by P4. The CDR provider's response-header allow-list (`content-type`, `date`, `etag`,
  `last-modified`, `x-v`, `x-fapi-interaction-id`), its paging (`links.next`, at most 50 pages) and its errors are
  fixed by the P4 spec §3.1.
- Network failures raise `E_NETWORK`. HTTP status codes of 400 and above raise `E_NETWORK` with `status` in
  `details`.

### 4.7 `Clock`

```python
class Clock(Protocol):
    def now(self) -> datetime: ...     # aware, UTC
```

The adapter `SystemClock` is used in production; tests use a `FixedClock`. Services never call `datetime.now()`
directly.

### 4.8 `ServiceContext`

```python
@dataclass(frozen=True)
class ServiceContext:
    paths: WorkspacePaths              # absolute root for this run; never persisted
    config: WorkspaceConfig
    private: PrivateConfig | None      # None if private/config.toml is missing
    catalog: Catalog
    blobs: BlobStore
    parsers: Sequence[SourceParser]
    region: RegionProfile              # loaded by build_context(); pipeline sync copies its zone and windows (P2 spec §5.3)
    vcs: VersionControl
    runner: PipelineRunner
    clock: Clock
    offers: Mapping[str, OfferProvider] = field(default_factory=dict)   # P4
```

- Services are plain functions that take `ctx: ServiceContext` first, e.g.
  `land(ctx, paths, *, source=None, supersedes=None, note="", dry_run=False) -> LandResult` (D3 §7).
- Services that need private config raise `E_PRIVATE_CONFIG_MISSING` when `ctx.private` is `None`.
- Each service's result model sits next to the service and carries `kind` and `schema_version` (§5). The CLI
  prints it, and the MCP and HTTP adapters will return the same models.

---

## 5. Persisted artifacts: `kind`, `schema_version` and migrations

Every JSON or YAML document, every JSONL line and every TOML file that energatron reads or writes as one of its
own models contains two fields. Writers put them first where the format keeps order, but readers don't rely
on it:

```json
{"kind": "catalog_record", "schema_version": 1, "...": "..."}
```

- Parquet files carry the same two fields as file metadata: `energatron.kind` and
  `energatron.schema_version` (string values).
- `kind` is snake_case and never changes. `schema_version` is an integer, starting at 1, per kind.
- Files in external formats (NEM12 CSV, PDFs, CDR JSON, `dvc.yaml`) have no `kind`. For those, the parser
  version recorded in the catalog does the same job.

**Rules**
- Models subclass `Persisted` (`core/persisted.py`), configured with `extra="forbid"` and `frozen=True`. The
  class variables `KIND` and `SCHEMA_VERSION` must match the fields.
- **Any** change to a persisted model bumps its `schema_version` and adds a migration: adding, removing,
  renaming or retyping a field, or changing what a field means. With `extra="forbid"`, an older reader fails
  loudly on a newer file instead of misreading it.
- **Reading:**
  - version equals current → validate
  - version lower → upgrade in memory through the migration chain, then validate
  - version higher → `E_SCHEMA_TOO_NEW` ("written by a newer energatron; upgrade the tool")
- **Migrations** are pure functions in core, registered per kind and version:

```python
@migration("catalog_record", from_version=1)
def _catalog_record_v1_to_v2(doc: dict[str, JSONValue]) -> dict[str, JSONValue]:
    ...                                # must return a document with schema_version == 2
```

`upgrade(doc)` applies the chain. `energatron migrate` (P8) uses the same functions to rewrite editable files.

**Rewrite policy**

| Artifact class | Examples | When its schema changes |
|---|---|---|
| Immutable | raw blobs, catalog lines, landed YAML (`gas_bill_record`, `offer_manual`), run manifests, decisions | Never rewritten; upgraded on every read |
| Rebuildable | everything under `staged/` … `reports/`, `_provenance.json` | Regenerated: a code change changes the fingerprint, so `dvc repro` reruns the stages |
| Editable config | `params.yaml`, `.energatron/workspace.toml`, `private/config.toml`, `config/offer_rules.local/` | Upgraded on read; `energatron migrate` (P8) rewrites them in a commit. `params.yaml` is the exception, below |

`params.yaml` can't wait for `migrate`: DVC reads the declared keys from the file itself, so a key that exists
only in memory would fail `dvc repro`. `energatron pipeline sync` (P1) therefore rewrites an older
`params.yaml` to the current version, keeping every value, and `pipeline sync --check` treats an older file as
drift (D3 §10.1, §10.2). energatron's own readers still upgrade it in memory.

**Kinds needed in P0–P1:**
- P0 (D2 §6): `workspace_config`, `local_config`, `private_config`, `backup_state`, `cli_result` (§8.5), and the
  results `version_result`, `denylist_result`, `backup_result`, `backup_status_result`. P0 also registers
  `catalog_record`, created early with the `Catalog` port (§2).
- P1 (D3 §11): `params`, `region_profile`, `land_result`, `land_pending`, `pipeline_sync_result`,
  `catalog_table` (Parquet metadata only), `stage_provenance`, `code_fingerprint`, `repro_log_entry` (§4.5),
  `verify_result` (§11.4), and the results `stage_result` (`energatron stage`'s `StageResult`, §9.3 step 8)
  and `fingerprint_result` (`energatron fingerprint [--check]`, §11.3).
- The P0 and P1 specs define their fields, except `repro_log_entry` (§4.5), `stage_provenance` (§10),
  `code_fingerprint` (§11) and `verify_result` (§11.4), which this spec defines, and `stage_result` and
  `fingerprint_result`, whose fields the sub-tasks that build the commands set: P1.3.3 for `stage` (from
  §9.3 step 8) and P1.3.2 for `fingerprint`. Each is version 1.
Later kinds include `run_manifest`, `recommendation`, `decision`, `offer_rule`, `offer_manual`,
`gas_bill_record` and `tariff`.

---

## 6. Identifiers and hashing

### 6.1 Hash primitives (`core/canonical.py`)

- `sha256_hex(data: bytes) -> str`: 64 lowercase hex characters. File hashes are computed over the exact bytes,
  with no normalisation.
- `canonical_json(value) -> bytes`:

```python
json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
```

  - `value` must already be plain JSON data (dict, list, str, int, float, bool, None). Pydantic models are
    converted first with `model_dump(mode="json")`, which turns `Decimal` and `datetime` into strings. Passing a
    `Decimal` or `datetime` directly raises `TypeError`, so every conversion is explicit.
  - Strings are hashed as given, with no Unicode normalisation.
  - Floats use Python's shortest round-trip representation, so `0.10` and `0.1` hash the same, while `1` and
    `1.0` hash differently. The worst case is a spurious new offer snapshot, never a missed one.
- `content_hash(value) = sha256_hex(canonical_json(value))`.
- `tree_hash(entries: Iterable[tuple[str, str]]) -> str`:
  - `entries` are `(relative POSIX path, sha256 hex)` pairs, one per file
  - sort by path in code-point order, then hash the UTF-8 concatenation of `f"{path}\0{sha256}\n"`
  - deciding which files to include is the caller's job (§10 and §11)

### 6.2 Timestamp formatting (`core/ids.py`)

- `compact_utc(dt)`: an aware datetime, converted to UTC and formatted `%Y%m%dT%H%M%SZ`. A naive datetime raises
  an error.
- `compact_source(dt)`: aware → converted to UTC and formatted `%Y%m%dT%H%M%S`; naive → formatted as given. Used
  only in dataset IDs. The catalog keeps the full `source_ts`, including any offset.

### 6.3 ID formats

| ID | Constructor | Pattern |
|---|---|---|
| Dataset | `dataset_id(source, source_ts, sha256)` → `f"{source}-{compact_source(source_ts)}-{sha256[:8]}"` | `^[a-z][a-z0-9_]*-\d{8}T\d{6}-[0-9a-f]{8}$` |
| Offer snapshot | `offer_snapshot_id(plan_id, last_updated, data)` → `f"{plan_id}~{compact_utc(last_updated)}~{content_hash(data)[:8]}"`, where `data` is the parsed `data` object of the plan-detail response (`links` and `meta` excluded) | `^[^~\s]+~\d{8}T\d{6}Z~[0-9a-f]{8}$` |
| Fetch batch | `fetch_batch_id(now)` → `f"fetch-{compact_utc(now)}"` | `^fetch-\d{8}T\d{6}Z$` |
| Run | `run_id(now, head)` → `f"run-{compact_utc(now)}-{head[:7]}"`; `head` is the full workspace HEAD SHA at run start (sliced, never `git rev-parse --short`, whose length varies) | `^run-\d{8}T\d{6}Z-[0-9a-f]{7}$` |
| Recommendation | `rec_id(as_of, payload)` → `f"REC-{as_of:%Y%m%d}-{content_hash(payload)[:8]}"` | `^REC-\d{8}-[0-9a-f]{8}$` |
| Decision | `decision_id(day, existing)` → `f"DEC-{day:%Y%m%d}-{n:02d}"`, where `n` = 1 + the number of existing decisions on that workspace-local date | `^DEC-\d{8}-\d{2}$` |

**Rules**
- **Full source type as the dataset prefix:** every source gets a distinct prefix with no mapping table.
  Example: `sapn_interval-20261023T100345-3fa9c2e1`.
- **Collisions:** a computed dataset ID that already exists with a different `sha256` raises `E_ID_COLLISION`.
  IDs are never lengthened silently. The same `sha256` means "already landed", which is a no-op.
- **Recommendation payload:** content only: input dataset and snapshot IDs, params hash, the `recommend`
  stage's `stage_sha256` (§11), ranking. No wall-clock times, run IDs or commit SHAs, so a re-run reproduces the
  same ID. The P6 spec §7.3 fixes the exact payload: the `recommendation` document without its ID.
- `plan_id` must not contain `~` or whitespace (validated on construction).
- IDs are opaque. Code reads meaning out of an ID only through the `parse_*` functions in `core/ids.py`.
- IDs never contain personal data. They are built from source types, timestamps, hashes and public plan IDs.
- Git forbids `~` in ref names (verified with `git check-ref-format`), so an offer snapshot ID must never appear
  in a branch or tag name. Tags use recommendation IDs only (`rec/REC-…`, verified valid).

---

## 7. Error model

```python
class EnergatronError(Exception):
    code: ClassVar[str]               # stable, E_UPPER_SNAKE
    category: ClassVar[Category]
    def __init__(self, message: str, **details: JSONValue) -> None: ...
```

- `message`: one human-readable sentence. `details`: JSON-serialisable values.
- Neither may contain identifiers, personal values or original filenames (SAPN filenames start with the NMI).
  Refer to inputs by their position or by a redacted name instead.
- Every code is defined in `core/errors.py`, and each belongs to exactly one category.
- Adapters wrap third-party exceptions, e.g. `CalledProcessError` → `E_SUBPROCESS`, `httpx.HTTPError` →
  `E_NETWORK`, `OSError` on the backup path → `E_STORAGE`.
- Any other exception is a bug: the CLI reports `E_INTERNAL`, and prints the traceback to stderr only with
  `--debug`.
- **Warnings** are returned in results as `{"code": "W_…", "message", "details"}`. Python's `warnings` module is
  not used for this.
  - The model is `ResultWarning` in `core/errors.py`, not `Warning`, which would shadow the Python builtin
    (D3 §6). Its codes are the `WarningCode` enum there.
  - Warnings in P0–P1: `W_PRIVATE_FIELDS_MISSING`, `W_BACKUP_DATALESS` (D2), `W_FINGERPRINT_METADATA` (§11.3),
    `W_BACKUP_PENDING` (§4.4), `W_PREMISES_UNCHECKED`, `W_UNKNOWN_IDENTIFIER` (D3 §11).
  - Warnings added in P2: `W_MERGE_SAME_UPDATE`, `W_METER_GAPS`, `W_METER_UNVALIDATED`,
    `W_TIMEBASE_UNCONFIRMED`, `W_TIMEBASE_UNEXPLAINED` (P2 spec §12).
  - Warnings added in P3: `W_USAGE_MIXED_WINDOWS`, `W_USAGE_PARTIAL_DAYS` (P3 spec §11).
  - Warnings added in P4: `W_FETCH_FAILED`, `W_OFFERS_MISSING_SNAPSHOT`, `W_BACKTEST_OUTSIDE_TOLERANCE`,
    `W_BACKTEST_NO_TARIFF` (P4 spec §12).
  - Warnings added in P5: `W_GAS_GAP`, `W_GAS_PARTIAL_YEAR` (P5 spec §11).
  - Warnings added in P6: `W_SENSITIVITY_UNAVAILABLE`, `W_REC_NO_CURRENT_PLAN`, `W_REC_CURRENT_NOT_PRICED` (P6 spec
    §11).
  - Warnings added in P7: `W_LANDING_NEW`, `W_LANDING_LANDED`, `W_RUN_BEHIND`, `W_OFFERS_STALE`, `W_RECHECK_DUE`,
    `W_DRILL_DUE` (P7 spec §11).
  - Warning added in P8: `W_TEMPLATE_MODIFIED` (P8 spec §11).

| Exit | Category | Meaning | Codes needed in P0–P1 |
|---|---|---|---|
| 0 | — | Success, including a no-op (e.g. already landed) | — |
| 1 | `internal` | Bug | `E_INTERNAL` |
| 2 | `usage` | Bad arguments, not in a workspace, confirmation needed | `E_USAGE`, `E_NOT_A_WORKSPACE`, `E_CONFIRMATION_REQUIRED` |
| 3 | `input` | An input file was rejected | `E_UNKNOWN_SOURCE`, `E_AMBIGUOUS_SOURCE`, `E_INVALID_INPUT` |
| 4 | `state` | The workspace state blocks the action | `E_LOCKED`, `E_DIRTY`, `E_ID_COLLISION`, `E_BLOB_EXISTS`, `E_CATALOG_CORRUPT`, `E_SCHEMA_TOO_NEW`, `E_FINGERPRINT_STALE`, `E_PIPELINE_STALE`, `E_PRIVATE_CONFIG_MISSING`, `E_COMMIT_REJECTED`, `E_BACKUP_NOT_CONFIGURED`, `E_DENYLIST_STALE` (D2), `E_PIPELINE_NOT_SYNCED` (D3) |
| 5 | `privacy` | A privacy rule blocked the action | `E_FOREIGN_PREMISES`, `E_DENYLIST_HIT` |
| 6 | `external` | A subprocess, network or backup-storage failure | `E_SUBPROCESS`, `E_NETWORK`, `E_STORAGE`, `E_STAGE_FAILED` |
| 7 | `check` | A stage's quality check failed, or `verify` found a difference (§11.4) | `E_CHECK_FAILED` |
| 8 | `partial` | A multi-item command where some items failed; per-item results are in the output | `E_PARTIAL` |

---

## 8. CLI contract

### 8.1 Basics
- Built with typer. Entry point: `energatron = "energatron.adapters.cli.app:main"`.
- Command names, option names and JSON field names are an API. Before P8 they may change, with a CHANGELOG
  entry. From P8 on, renames need a deprecation period.

### 8.2 Common options
Every command accepts these, placed after the command name:
- `--json`
- `--workspace PATH`
- `-v` / `--verbose`
- `--debug`

Mutating commands also accept `--dry-run` and `--yes`. The environment variables `ENERGATRON_JSON=1` and
`ENERGATRON_WORKSPACE` do the same as `--json` and `--workspace`.

### 8.3 Finding the workspace
1. `--workspace`
2. `ENERGATRON_WORKSPACE`
3. Otherwise, walk up from the current directory to the first directory that contains
   `.energatron/workspace.toml`.

Not found → `E_NOT_A_WORKSPACE`. Only `version`, `--help` and `init` (P8) work outside a workspace.

### 8.4 Output streams
- **stdout** carries the result only: human text, or exactly one JSON document followed by a newline.
- **stderr** carries logs, progress, human-mode warnings and tracebacks.

### 8.5 JSON envelope (kind `cli_result`, version 1)

```json
{
  "kind": "cli_result",
  "schema_version": 1,
  "command": "land",
  "ok": true,
  "exit_code": 0,
  "energatron_version": "0.1.0",
  "data": {"kind": "land_result", "schema_version": 1},
  "warnings": [],
  "error": null
}
```

- On failure: `"ok": false`, and `"error": {"code", "category", "message", "details"}`. `data` holds per-item
  results for exit code 8 and is otherwise `null`.
- `data` is the service's result model, dumped with `model_dump(mode="json")`.
- Paths in output are workspace-relative POSIX paths.
- With `--json`, every outcome produces the envelope, including argument errors. `main()` runs the typer app with
  `standalone_mode=False` and maps click exceptions to `E_USAGE`.
- **Output mode is decided before parsing.** `--json` comes after the command name, so on an argument error
  typer never gets to set it. `main()` therefore pre-scans `sys.argv` first:
  - JSON mode is on if `--json` appears anywhere before a `--` separator, or `ENERGATRON_JSON=1` is set
  - debug mode is on if `--debug` appears the same way
  - typer still declares both options, so they show in `--help` and parse normally

  When typer fails to parse, `main()` uses the pre-scanned mode to write the envelope. `command` is then the
  first argument that doesn't start with `-`, but only if it is one of the app's command names; otherwise
  `null`. The raw token is never echoed: a mistyped command could be a file name or a value (§7).

### 8.6 Human output, prompts and dry runs
- **Human mode:** short text. Colour only when stdout is a TTY and `NO_COLOR` is unset.
- **Prompts** happen only when stdin and stderr are both TTYs and `--json` is not set. Otherwise, a command that
  needs confirmation fails with `E_CONFIRMATION_REQUIRED` unless `--yes` is given. Nothing reads stdin
  interactively when it isn't a TTY.
- **`--dry-run`** runs every check and reports what would happen. It writes nothing: no files, no catalog
  lines, no commits.

### 8.7 Redaction
Everything written to stdout and stderr first passes through `core.privacy.redact()`. It replaces every denylist
entry (derived from private config, plus `private/denylist.txt`) with `<redacted>`. This is the last line of
defence: outputs must not contain identifiers in the first place.

Matching follows the privacy guard's rules (D2 §7.4): an entry is literal text, matched ignoring case, and a
whole-word entry matches only where no ASCII letter or digit, `_` or `-` touches it (`[0-9A-Za-z_-]`, not
a Unicode `\w`).
- **Whitespace:** an entry is normalised with `value.split()`. Each run of whitespace inside it matches any run
  of whitespace, and whitespace at either end is ignored.
- **Overlapping or adjacent matches** become one `<redacted>`: every character inside any entry's match is
  redacted, and each run of them is replaced once, so no tail of an entry is left behind.
- Redaction repeats until the text stops changing, because a replacement can leave a whole-word entry
  standing alone.

How it's applied depends on the stream:
- **JSON envelope:** redacted as data, never as text. `redact_json(value, denylist)` walks the dumped envelope
  and redacts every string value, every object key and every number. The result is then serialised. A
  replacement can't break the JSON, and an entry can't hide behind JSON escaping.
  - **Numbers** (owner decision 2026-10-29): an int or float, not a bool, whose JSON text holds an entry
    becomes that text redacted, a string. An all-digit identifier can be written as a number. A number that
    holds no entry stays a number.
  - Keys that redact to the same text are all kept; the later ones get a ` (2)`, ` (3)` … suffix.
- **Human stdout:** `redact()` over the text before it is written.
- **stderr:** the logging handler's formatter calls `redact()` on each formatted record. Tracebacks shown with
  `--debug` go through the same path.

### 8.8 Locking
Commands that change the workspace take an exclusive, non-blocking lock on `.energatron/lock`, using the
`filelock` package. If another command holds it → `E_LOCKED`. The lock file is git-ignored.

| Takes the lock | Never takes the lock |
|---|---|
| `land`, `fetch-offers`, `fingerprint` (it writes the file), `backup`, `run`, `decide`, `migrate`, `upgrade`, `demo`, `verify` (§11.4), and any later command that writes under `data/`, `.energatron/` or `private/` or commits | `stage`, `version`, read-only commands (`status`, `diagnose`, `fingerprint --check`, `offers diff`, `explain`, `compare`), `init` (no workspace yet), `stage --emit-dvc-yaml` |

- `energatron stage` never locks. `dvc repro` runs it as a child process, normally of `energatron run`, which
  already holds the lock; a child that tried to take it would fail with `E_LOCKED`. Its only writes under
  `.energatron/` are its two records in the repro log (§4.5).
- `verify` writes only into a scratch directory under `.energatron/tmp/`, but it takes the lock so the outputs
  it compares can't change while it runs.
- Before P6, the supported sequence `uv run energatron fingerprint && uv run dvc repro` (§11.3) runs its `dvc repro`
  without the energatron lock. DVC's own repo lock still prevents two repros at once. Running `land` at the same
  time is the owner's job to avoid; this is a single-user tool.
- A `--dry-run` takes the lock like the real command, so its checks don't read state that another command is
  changing.

### 8.9 Logging
- stdlib `logging`, with one logger per module (`energatron.<module>`).
- Level: WARNING by default, INFO with `-v`, DEBUG with `--debug`.
- Log IDs and counts, never personal values.

---

## 9. Stage contract

### 9.1 The function

```python
def run_stage(
    spec: StageSpec,                  # looked up by the caller with registry.get_stage(name)
    inputs: Mapping[str, Path],       # input name → existing file or directory
    params: Mapping[str, JSONValue],  # exactly the stage's declared dotted keys
    outputs: Mapping[str, Path],      # output name → directory the stage will create
    *,
    code: StageCode,                  # this stage's live code entry (§11)
    recorded_code: str | None,        # stages.<name>.stage_sha256 from the fingerprint file; what DVC tracked
) -> StageResult: ...
```

`run_stage` takes the spec, not a name, so `runner.py` never imports the registry, and with it every other
stage's code (§11).

A stage may declare no params (`params=()`; rev 4): then `params` is empty and the dvc entry lists only the code entry.

```python
def prepare_stage(
    spec: StageSpec,
    *,
    root: Path,                                  # absolute workspace root
    params_file: Path,                           # root / "params.yaml"
    input_overrides: Mapping[str, Path] | None = None,    # from --input name=path
    output_overrides: Mapping[str, Path] | None = None,   # from --output name=path
) -> PreparedStage: ...                          # inputs, params and outputs, ready for run_stage
```

**Everything that decides what a stage receives lives in `runner.py`.** That module is in every stage's module
set (§11.1), so its code is in every stage's hash. It covers reading `params.yaml`, picking out the declared
dotted keys, and resolving input and output paths.
- `params.yaml` is parsed with `yaml.safe_load`, so PyYAML is in every stage's `packages`.
- A declared key missing from `params.yaml`, or an unknown override name → `E_USAGE`.
- Default paths are the spec's workspace-relative paths under `root`.
- The CLI adapter only collects the command-line values, calls `prepare_stage` and then `run_stage`. It holds
  no logic that shapes a stage's inputs or params.

**Why `outputs` replaces the plan's single `out_dir`:** some stages write to two zones. For example,
`merge_interval` writes to `interim/` and to `reports/validation/`. Making every output a directory gives each
one a home for its `_provenance.json`.

### 9.2 Registry (`services/stages/runner.py`, `services/stages/registry.py`)

```python
@dataclass(frozen=True)
class StageSpec:                      # runner.py; each stage module defines its own SPEC
    name: str
    inputs: Mapping[str, str]         # input name → default workspace-relative path
    params: tuple[str, ...]           # dotted keys into params.yaml, e.g. "meter.timebase"
    outputs: Mapping[str, str]        # output name → workspace-relative directory
    impl: Callable[[StageRun], None]  # the stage body
    package_data: tuple[str, ...] = ()   # package-relative files or directories it reads, e.g. "regions/sa.yaml"

STAGES: dict[str, StageSpec] = {...}  # registry.py, built from each stage module's SPEC
```

- Every stage also tracks its own code entry, `stages.<name>.stage_sha256` in
  `.energatron/code_fingerprint.json`, as a DVC param (§9.5, §11). That entry covers the stage's modules,
  package data, third-party packages and Python version.
  - Stages don't depend on `uv.lock` or on the fingerprint file as a whole.
  - So a tool commit, a change to another stage's code or a dev-tool bump makes no stage stale.
- A stage that needs private config declares it as an ordinary input named `private_config`
  (`private/config.toml`). DVC then records only its hash. Private values are never params.
- **Inputs from several sources.** DVC deps can't be globs, and a missing dep fails `dvc repro`. So:
  - a stage that reads every source declares one input per source type, named `<prefix>.<source>`, e.g.
    `catalog.sapn_interval` → `data/raw/sapn_interval/_catalog.jsonl`. The registry builds these entries from
    `SourceType` (core), not by hand.
  - every input must exist, so a source with nothing landed yet has an empty `_catalog.jsonl`. P0 creates
    none. `energatron pipeline sync` (P1), which also writes `dvc.yaml`, creates one for every source type
    that has none, through `Catalog.missing_sources()` and `Catalog.initialise()` (§4.1; D3 §10.1). It runs
    when P1 is set up and after any tool change that adds a source type.
  - empty catalogs are normal: a stage treats an empty file as "no datasets of this type".
- `StageRun` gives the body:
  - `input(name) -> Path`
  - `inputs(prefix) -> Mapping[str, Path]`: every input named `<prefix>.<…>`, keyed by the part after the dot,
    in sorted order
  - `params`
  - `package_data(path) -> Path`: a file under `spec.package_data`. Any other path → `E_INTERNAL`, so a stage
    can't read package data its code entry doesn't cover.
  - `zone(key) -> ZoneInfo`: the time zone `key`, read from the `tzdata` distribution, never from the system
    database (§9.4; P2 spec §6). A malformed or unknown key → `E_INVALID_INPUT`.
  - `output(name) -> Path`: a temporary directory to write into
  - `check(name, passed, **details)`: a check either passes or fails. Something worth flagging that shouldn't
    fail the stage is a warning, not a check.
  - `warn(code, message, **details)`
  - `stage_sha256`: the live code entry `run_stage` was given (rev 7; read by `recommend` for its payload)

### 9.3 What `run_stage` does
1. The caller has already looked up the spec: `registry.get_stage(name)` raises `E_USAGE` for an unknown name.
2. Compares `recorded_code` with `code.stage_sha256`. A difference, or a missing entry → `E_FINGERPRINT_STALE`
   ("run `energatron fingerprint`, or use `energatron run`"). Only this stage's entry counts.
3. Checks that every input exists and that the `params` keys equal `spec.params` exactly. Otherwise →
   `E_USAGE`.
4. Creates a temporary directory per output, as a hidden sibling of the target, and runs `impl`. If `impl`
   raises, the temporary directories are removed, and the exception propagates; a non-energatron exception
   becomes `E_INTERNAL`.
5. Writes `_provenance.json` into each output (§10).
6. Promotes each output atomically:
   1. rename the existing target, if any, aside
   2. rename the temporary directory into place
   3. delete the old one (rebuildable outputs; DVC's cache holds every committed version)
7. If any check failed, raises `E_CHECK_FAILED` **after** promoting. The validation report is then on disk to
   read, and DVC doesn't record the outputs in `dvc.lock`, because the command exits non-zero.
8. Returns `StageResult`: per output, the files with their `sha256`, byte counts and row counts, plus the checks
   and warnings.

### 9.4 Determinism rules for stage code
- No wall-clock reads, randomness (`random`, `numpy.random`, `uuid4`, `secrets`), environment-dependent
  values (locale, cwd, hostname, username) or absolute paths in outputs.
- Read only the declared inputs and params. Stage code reads **no** environment variables, including
  `ENERGATRON_*`. Those configure the CLI adapter only, which never passes them to a stage.
  - So `energatron run` (P6) passes no run ID to stages, and `--draft` doesn't stamp their outputs (this
    changes plan §9.4).
  - The run ID and the draft flag live only in the run manifest, `data/runs/<run_id>.json`.
  - A recommendation can be cited by a decision only once a non-draft run manifest names it. A draft and a
    clean run on the same inputs give the same `REC-…` ID, which is correct, because the content is the same.
  - Outputs from different code already differ: provenance records the stage's `stage_sha256`. The fingerprint
    file records `tool_dirty`, and `energatron run` refuses a dirty tool without `--draft`.
  - The P6 spec §8.2 fixes the manifest fields.
- **Code and data reach a stage only through what its code entry sees.** A stage's code entry is computed from
  the import statements and declared package data of its module set (§11.1), so anything reached another way
  would be invisible to it. Within every module of a stage's module set:
  - imports are static: no `importlib` (`import_module`, `util`, `resources`, `metadata`), `__import__`,
    `pkgutil` or `runpy`
  - no code loaded at run time: no `exec`, `eval` or `compile` builtins, no `entry_points()`, and no `pickle`,
    `marshal`, `shelve` or `pandas.read_pickle`
  - package data is read only through `StageRun.package_data()` (§9.2): no `__file__`, `__path__`, `__spec__`
    or `__loader__`
  - two named exceptions in `importlib`, both in every module set and listed by name in the test:
    `energatron/__init__.py` may call `importlib.metadata.version` to set `__version__` (which no other module
    in a module set may read), and `runner.py` may use `importlib.resources`: in `StageRun.package_data()` to
    locate the package, and in `StageRun.zone()` to open a zone file of the `tzdata` distribution
  - time zones are converted only with zone objects from `StageRun.zone()`, passed as objects: no `ZoneInfo(...)`
    construction, no `zoneinfo.available_timezones`, no `pytz` or `dateutil.tz`, and no string as a time zone
- **No process-wide state changes**, anywhere in `energatron`:
  - library settings: `pd.set_option`, assignments to `pd.options.<…>`, `np.seterr`, `np.set_printoptions`,
    `decimal.getcontext()` (any use), `locale.setlocale`, `warnings.filterwarnings` / `simplefilter`,
    `sys.setrecursionlimit`
  - the process environment: `os.chdir`, `os.putenv`, `os.unsetenv`, and any write to `os.environ`
    (assignment, `del`, `pop`, `popitem`, `update`, `setdefault`, `clear`)
  - the import system: any write to `sys.path` or `sys.modules`
  - global random state: `random.seed`, `np.random.seed`
  - The `energatron stage` process imports the registry (every stage) and the adapters, none of which are in the
    stage's module set. A setting changed by any of them at import time or before `run_stage` would change the
    stage's output with no change to its code entry.
  - Where a stage needs a setting, it uses the scoped form inside its body: `pd.option_context`,
    `np.errstate`, `decimal.localcontext`, `warnings.catch_warnings`.
  - Third-party libraries that change state when imported are outside this rule. `energatron verify` (§11.4)
    is the net for those.
- Sort rows by the table's declared key before writing. Iterate directories in sorted order. Use
  `groupby(sort=True)`.
- **Parquet** is written only through `services.files.write_parquet(df, path, *, kind, schema_version,
  sort_by)`: pyarrow, zstd compression, the metadata keys from §5. Verified on 2026-10-23: rewriting the same
  table gives byte-identical files.
- **JSON reports** are written only through `services.files.write_json`: sorted keys, `indent=2`, trailing
  newline.

### 9.5 CLI and `dvc.yaml`
- `energatron stage <name>` takes the inputs and outputs from the registry defaults, relative to the workspace
  root. `--input name=path` and `--output name=path` override them for tests. Provenance still records each
  input's declared default path, with the hash of the file actually read (§10).
- `prepare_stage` (§9.1) reads `params.yaml` and passes only the declared keys. The CLI adapter computes the
  stage's live `StageCode` and reads `recorded_code` from the fingerprint file. They affect outputs only
  through `stage_sha256`: `run_stage` refuses to run when the two differ (§9.3), and `_provenance.json` records
  the live `StageCode`, every field of which `stage_sha256` covers (§10, §11.1).
- `energatron stage --emit-dvc-yaml` prints `dvc.yaml` generated from `STAGES`:
  - `cmd: energatron stage <name>`
  - `deps`: the declared inputs only
  - `params`: the declared `params.yaml` keys, plus `stages.<name>.stage_sha256` from
    `.energatron/code_fingerprint.json`:

    ```yaml
    params:
      - meter.timebase
      - .energatron/code_fingerprint.json:
          - stages.merge_interval.stage_sha256
    ```
  - `outs`: the output directories, with default caching
- `cache: false` is never emitted.
- The workspace's `dvc.yaml` is written from this output (P1).
  - In the tool repo, a test compares the output with a golden file, `tests/fixtures/golden/dvc.yaml`. Changing
    the golden file is a deliberate part of any change to `STAGES`.
  - The workspace template has no `dvc.yaml`: `energatron init` runs `pipeline sync`, which writes it (P8 spec §3.1).
  - `energatron pipeline sync --check` detects a workspace `dvc.yaml` that has drifted from the generated one;
    it runs as a workspace pre-commit hook and as a `land` precondition (D3 §10.1).
- DVC runs inside the workspace venv, and `DvcRunner` puts the venv's `bin` first on the child's `PATH`
  (§4.5), so `energatron` resolves to the workspace's install. The supported ways to run the pipeline are
  listed in §11 under **Who uses it**.

---

## 10. Provenance (`_provenance.json`, kind `stage_provenance`, version 1)

One file in every stage output directory:

```json
{
  "kind": "stage_provenance",
  "schema_version": 1,
  "stage": "merge_interval",
  "output": "interval",
  "code": {"stage_sha256": "<64 hex>", "source_sha256": "<tree hash>", "python": "3.14.7",
           "packages": {"numpy": "<v>", "pandas": "<v>", "pyarrow": "<v>", "pydantic": "<v>", "...": "<v>"},
           "files": ["__init__.py", "core/__init__.py", "...", "services/stages/merge_interval.py"]},
  "inputs": [
    {"name": "staged", "type": "dir", "path": "data/staged/elec_interval", "sha256": "<tree hash>",
     "dataset_ids": ["sapn_interval-20261023T100345-3fa9c2e1"]}
  ],
  "params": {"values": {"meter.timebase": "nem_aest"}, "sha256": "<content_hash(values)>"},
  "files": [{"path": "data.parquet", "sha256": "<64 hex>", "bytes": 0, "rows": 0}],
  "checks": [{"name": "key_unique", "status": "pass", "details": {}}]
}
```

- **Nothing run-specific:** no timestamps, run IDs or absolute paths. Reruns with the same stage code, inputs and
  params therefore produce byte-identical files. The run manifest (P6) links runs to outputs.
- **`code`:** the stage's `StageCode` (§11). There's no tool commit, version or dirty flag here.
  - A tool commit that leaves the stage's code alone must not change its output bytes. Otherwise every
    downstream stage would see a changed dependency and rerun.
  - The commit is found through the workspace commit's fingerprint file (§11).
- **`inputs[].path`:** always the declared default from `spec.inputs`, even when `--input` overrode it. An
  override may sit outside the workspace, and an absolute path would break the rules above and §12.
- **`inputs[].sha256`:** the file hash for a file input. For a directory input, the `tree_hash` (§6.1) over every
  file in it, excluding `.DS_Store`.
- **`inputs[].dataset_ids`:** sorted and unique.
  - For a `data/raw/<source>` input, or that directory's `_catalog.jsonl`: every `dataset_id` in the catalog
    file (none for an empty one).
  - For an input that is another stage's output: the union of the `dataset_ids` in that output's
    `_provenance.json`, so lineage accumulates transitively.
  - For other inputs (e.g. `private_config`): `[]`.
- **`params.values`:** the declared `params.yaml` keys in full, because `params.yaml` holds non-personal settings
  only (plan §5). The code entry is under `code`, not here.
- **`files`:** every file in the output directory except `_provenance.json`, sorted by path. `rows` comes from
  the Parquet metadata, or is `null` for other files.
- **`checks[].status`:** `pass` or `fail` (§9.2). Warnings are reported in the `StageResult`, not here.

---

## 11. Code fingerprint (`.energatron/code_fingerprint.json`, kind `code_fingerprint`, version 1)

Git-tracked in the workspace. It holds one code entry per stage, and each stage tracks **only its own entry's
`stage_sha256`** as a DVC param (§9.5). A stage therefore goes stale only when code it actually uses changes.
The other fields are recorded for traceability and trigger nothing.

```json
{
  "kind": "code_fingerprint",
  "schema_version": 1,
  "energatron_version": "0.1.0",
  "install": "editable",
  "tool_commit": "<full hex SHA>",
  "tool_dirty": false,
  "stages": {
    "catalog_index": {
      "stage_sha256": "<64 hex>",
      "source_sha256": "<tree hash>",
      "python": "3.14.7",
      "packages": {"numpy": "<v>", "pandas": "<v>", "pyarrow": "<v>", "pydantic": "<v>", "...": "<v>"},
      "files": ["__init__.py", "core/__init__.py", "...", "services/stages/catalog_index.py"]
    }
  }
}
```

### 11.1 A stage's code entry (`StageCode`)

Computed by `adapters/pipeline/fingerprint.py: compute_fingerprint() -> CodeFingerprint`, using
`adapters/pipeline/closure.py` for step 1. The package directory is `Path(energatron.__file__).parent`, resolved.

1. **Module set:** the stage's module (`spec.impl.__module__`) plus every `energatron` module it imports,
   directly or indirectly. `closure.py` finds them by parsing source with `ast`, never by importing:
   - every `import` and `from … import` statement counts, wherever it sits: in function bodies,
     `if TYPE_CHECKING:` blocks, everywhere. Over-counting is safe; missing one is not.
   - relative imports are resolved against the importing module
   - `from pkg import name` adds `pkg.name` when that is a module, and `pkg` itself either way
   - `from pkg import *` adds `pkg` and every module under it, recursively
   - every parent package's `__init__.py` is added, because it runs on import
   - this is why `services/stages/__init__.py` stays empty and `runner.py` never imports `registry.py`.
     Otherwise every stage would depend on every other stage's code.
   - a module that has no `.py` source on disk → `E_INTERNAL`
2. **`files`:** the module set's files plus the stage's declared `package_data` (directories expanded
   recursively), as package-relative POSIX paths, sorted.
   - Skipped: `__pycache__`, `*.pyc`, `*.pyo`, `.DS_Store`.
   - A symlink anywhere on the way to these files → `E_INTERNAL`: a file itself, a directory under a declared
     package-data directory, or any directory between the resolved package directory and a file (e.g. a
     symlinked `services/stages/`). Each path component below the package directory is checked, not only
     the last. Python and `package_data()` would read through the link, so hashing the path alone would miss
     an edit to its target.
3. **`source_sha256`:** `tree_hash` (§6.1) over `(path, sha256_hex(bytes))` for each entry in `files`.
4. **`packages`:** the third-party distributions the stage's code can run, with their installed versions:
   - start from the top-level third-party modules the module set imports. Stdlib modules
     (`sys.stdlib_module_names`) are covered by `python` instead.
   - map each module to its distributions with `importlib.metadata.packages_distributions()`. When a module
     name maps to several distributions (a namespace package), every one of them is included.
   - a module that maps to no installed distribution → `E_INTERNAL`, naming the module. So energatron code has
     no optional third-party imports, and nothing it imports goes uncounted.
   - one exception: `_typeshed` exists only for type checkers and isn't in `sys.stdlib_module_names`. It is
     skipped when imported inside an `if TYPE_CHECKING:` block; anywhere else it raises `E_INTERNAL`, as above.
   - add each distribution's requirements (`importlib.metadata.requires()`), recursively, **including**
     requirements behind an `extra ==` marker whenever that distribution is installed. Libraries use optional
     dependencies without any energatron import: pandas uses bottleneck and numexpr when they are present, and
     pydantic loads plugins through entry points. So "installed" decides, not the extra; over-counting is safe.
   - distributions that aren't installed are skipped. Installing one later adds it, and the stage goes stale.
   - a stage that imports only pandas doesn't depend on typer or httpx, and no stage depends on a dev tool
5. **`python`:** `platform.python_version()`.
6. **`stage_sha256`:** `content_hash({"source_sha256": …, "packages": …, "python": …})`.

### 11.2 The other fields

- `energatron_version`: `importlib.metadata.version("energatron-5000")`.
- `install`: from the distribution's `direct_url.json` (PEP 610), and every case is mapped:

  | `direct_url.json` | `install` | `tool_commit` | `tool_dirty` |
  |---|---|---|---|
  | `dir_info` with `editable: true` | `editable` | `git -C <package dir> rev-parse HEAD` | `git -C <package dir> status --porcelain -- .` is non-empty |
  | `dir_info`, not editable | `local_dir` | `null` | `false` |
  | `vcs_info` | `vcs` | `vcs_info.commit_id` | `false` |
  | `archive_info` (a wheel or sdist from a URL or file) | `archive` | `null` | `false` |
  | no file (installed from a package index) | `index` | `null` | `false` |
  | anything else, or an unreadable file | raises `E_INTERNAL` | — | — |

- A `null` commit is acceptable: the code entries identify the code in every case. The commit only makes the
  plan §9.4 lookup path fast.
- **Lookup path still holds:** the file is refreshed before every repro, so each workspace commit names the tool
  commit that was current when its `dvc.lock` was written.
  - A stage that didn't rerun across later tool commits has the same `stage_sha256` at that commit.
  - So checking out the recorded commit reproduces its output.
- The file is written with sorted keys, `indent=2` and a trailing newline, and only if its content changed.

### 11.3 Who uses it

- `energatron fingerprint` writes it.
- `energatron fingerprint --check` writes nothing.
  - It fails with `E_FINGERPRINT_STALE` when any `stages.<name>.stage_sha256` differs from the live one, or a
    stage is missing or extra. `details.stages` lists those stages.
  - When only the recording fields differ (`tool_commit`, `tool_dirty`, `energatron_version`, `install`), it
    succeeds with a warning, `W_FINGERPRINT_METADATA`.
- `DvcRunner.repro()` refreshes it before every `dvc repro` (§4.5), so `energatron run` (P6) is always current.
- `energatron stage` refuses to run when its own entry differs from the live one (§9.3), so an output can never
  be produced by code other than what its tracked entry names.

**Supported ways to run the pipeline**
- From P6: `energatron run`.
- Before P6: `uv run energatron fingerprint && uv run dvc repro`.
- A bare `dvc repro` without the fingerprint step is not supported after a code change. DVC sees no changed
  param, so it silently skips the stages the change affects; any stage that does run fails with
  `E_FINGERPRINT_STALE`. With no code change, as in the restore drill, a bare `uv run dvc status` or
  `uv run dvc repro` is fine.

### 11.4 Safety net: `energatron verify`

Per-stage tracking can only miss a dependency through code or state it can't see. The rules in §9.4 (static
imports, no run-time code loading, declared package data, no process-wide state changes) and the tests in §13
close the known routes.

`energatron verify` catches the rest, and also checks the raw zone (D4). It is built in P1 (P1.3.4).

**Where it lives.** `services/verify.py: verify(ctx, *, live: CodeFingerprint, recorded: CodeFingerprint |
None, scratch: Path) -> VerifyResult`.
- Services can't compute code entries: that is `adapters/pipeline/fingerprint.py` (§11.1). So the CLI
  command computes the live entries with `wiring.code_fingerprint()`, reads the recorded file
  (`None` if missing) and passes both in, as it does for `energatron stage` (§9.5).
- `stale_stages(live, recorded) -> list[str]` in `core/models/provenance.py` compares them: the stages whose
  `stage_sha256` differs, or that are missing or extra, sorted. `fingerprint --check` uses the same function.
- `scratch` is `.energatron/tmp/verify/` under the root. The command deletes a leftover one from a killed
  run before calling the service; the lock makes that safe.
- `verify` imports `services/stages/registry.py`; it isn't in any stage's module set, so this changes no
  code entry (§11.1).

**What it does.** The command takes the lock (§8.8), then the service:
1. requires a current pipeline, so a stale one can't show up as false differences:
   - `stale_stages(live, recorded)` must be empty, otherwise `E_FINGERPRINT_STALE` with `details.stages`
   - `PipelineRunner.status()` must be empty, otherwise `E_PIPELINE_STALE` (category `state`), with the stale
     stage names in `details.stages`
2. checks every catalogued blob: for each record in `catalog.records()`, `blobs.exists(record.stored_path)`
   must be true, and then `blobs.stat(record.stored_path)`, which hashes the stored bytes (§4.2), must match
   the record's `sha256` and `bytes`. A missing or differing blob is a difference, named by its `dataset_id`.
3. for every stage, in `STAGES` order, calls `prepare_stage` with every output overridden into
   `scratch/<stage>/<output>`, then `run_stage` with `code = live.stages[name]` and `recorded_code` from
   `recorded`. Inputs are the workspace's current ones, never another stage's scratch output, so a
   difference points at the stage that produced it.
4. compares each scratch output directory with the workspace's: the same file paths, and the same hash for
   every file, `_provenance.json` included
5. deletes `scratch` in a `finally`

**Its error code.** Any difference fails with `E_CHECK_FAILED` (category `check`, exit 7): a pipeline that
doesn't reproduce is a failed check, and no new code is needed.
- `details.reason` is `"not_reproducible"`, which tells it apart from a stage's own failed check.
- `details.stages` lists the differing stages and `details.datasets` the differing blobs, both sorted.
- A stage whose own check fails inside `verify` counts as a differing stage: `dvc.lock` records only outputs
  whose checks passed, so the workspace's outputs no longer reproduce. Every other error propagates as is.
- Steps 2 to 4 run to the end before failing, so one run lists every difference.
- The result, kind `verify_result` version 1: `stages: list[str]` (the stages verified, in `STAGES` order) and
  `datasets: int` (the blobs checked).

**One process.** `verify` runs every stage in the `verify` process itself, one after another, while
`dvc repro` runs each stage in a separate `energatron stage` process. So a stage whose output depends on
process-wide state that an earlier stage, or an import, changed shows up as a difference. That is a breach of
§9.4 for the net to catch; the fix is in the stage, never in `verify`.

It is meant for occasional use, e.g. with the quarterly restore drill.

### 11.5 To verify in P1 (DVC 3.67.1)

- `params` read from a nested key of a JSON file other than `params.yaml`
- the run cache restores a stage when its code entry returns to an earlier value
- `ENERGATRON_REPRO_LOG` reaches the stage command through `dvc repro`

If any of these fails, stop and ask the owner. Don't silently fall back to whole-file deps.

---

## 12. Cross-cutting conventions

- **Files:**
  - every write is atomic: a temp file in the same directory, `fsync`, then rename
  - UTF-8 with LF line endings
  - JSON documents: sorted keys, `indent=2`, trailing newline
  - JSONL lines: compact canonical JSON (§6.1)
- **Paths in persisted artifacts and output:** workspace-relative POSIX only. Never absolute (an absolute path
  contains the macOS username), and never `..`.
- **Time:**
  - instants are timezone-aware, persisted as RFC 3339 with an offset
  - instants that energatron generates (`landed_at`, `fetched_at`) are UTC (`Z`)
  - dates are `YYYY-MM-DD`
  - local conversions use zone objects from `StageRun.zone()`, read from the `tzdata` distribution, with the
    time zone from `params.yaml` (`meter.local_tz`)
  - naive datetimes appear only where a source carries no zone; the P1 spec lists those per source type
- **Money:** `Decimal`, never `float`. Each line item is rounded to cents with `ROUND_HALF_UP`; totals are sums of
  rounded lines; GST is one rounded line per billing period (P4 spec §7.4, §7.8). Parquet stores money as
  `decimal128(18, 2)`, quantities as `decimal128(18, 6)` and rates as `decimal128(24, 10)`.
- **Energy:** kWh as float64 in Parquet.
- **Dependencies:**
  - core: pydantic, pandas, numpy
  - runtime data: `tzdata` (an explicit dependency from P2, so the zone rules are pinned in `uv.lock`)
  - services: + pyarrow, PyYAML (`stages/runner.py` reads `params.yaml`, so the parser is in every stage's
    `packages`)
  - adapters: + typer, httpx, filelock, PyYAML
  - duckdb is an optional extra (`energatron-5000[sql]`). Since duckdb 2.0, relations no longer have `fetch*()`
    methods; use `con.execute(sql).fetchone()` (verified 2026-10-23).
  - git and DVC are subprocesses, not dependencies.
- **Concurrency:** one writer per workspace, through the lock in §8.8.
- **Languages and environments** (owner rule, plan rev 6):
  - Everything is Python, run through uv: the package, stages, analysis, notebooks, scripts and dev tooling.
  - Each repo has a `.python-version` and a committed `uv.lock`. Environments are created with `uv sync`,
    commands run with `uv run`, and dependencies change only through `uv add` and `uv remove`.
  - No pip, conda, Poetry or global installs. Tools such as ruff, pyright, pre-commit, DVC and jupyter are dev
    dependencies.
  - Scripts that must run before a venv exists use the stdlib only, e.g. `scripts/link_parent.py`.
  - Another language only where Python is the wrong tool: the P10 browser frontend (e.g. TypeScript on Node).
    - It lives in its own top-level directory, with its own lockfile.
    - It calls only the HTTP API, which serialises the service result models (§4.8).
    - It holds no analysis, pricing or ID logic.
    - The Python package never needs Node at runtime.

---

## 13. Tests that enforce this spec

| Test | Enforces |
|---|---|
| `lint-imports` (pre-commit + CI) | §3 layer, purity, independence and wiring contracts |
| `tests/architecture/test_core_purity.py` | AST scan of `core/**`. Fails on: `open`, `input` and `print` calls; calls of the attributes `read_text read_bytes write_text write_bytes open mkdir unlink rmdir rename replace touch iterdir glob rglob`; pandas `read_*` and `to_csv to_parquet to_json to_excel`; `datetime.now utcnow today`, `date.today`, `time.time` |
| `tests/architecture/test_persisted.py` | Every `Persisted` subclass has a unique `KIND`. Every version above 1 has a complete migration chain. Golden v1 documents in `tests/fixtures/schemas/<kind>/v1.json` upgrade and validate. |
| `tests/architecture/test_errors.py` | Codes are unique and match `^E_[A-Z_]+$`. Each has one category. The exit-code table matches §7. |
| `tests/architecture/test_stage_purity.py` (from P1) | AST scan of every module in every registered stage's module set (§11.1), not just `services/stages/**`. Fails on: `os.environ`, `os.getenv`, `os.getcwd`, `Path.cwd`, `Path.home`, `socket.gethostname`, `platform.node`, `getpass`, `locale`, the `random`, `uuid` and `secrets` modules, `numpy.random` (imported, or reached as an attribute such as `np.random`), `time.time`, `datetime.now utcnow today`, `date.today`; and on the routes past the code entry: `importlib`, `__import__`, `pkgutil`, `runpy`, calls of the bare builtin names `exec`, `eval` and `compile` (not attribute calls such as `re.compile` or `pd.eval`), `entry_points`, `pickle`, `marshal`, `shelve`, `read_pickle`, `__file__`, `__path__`, `__spec__`, `__loader__`, `__version__`, with the two named exceptions (§9.4). From P2 it also fails on: a `ZoneInfo(...)` construction, `zoneinfo.available_timezones`, a `pytz` or `dateutil.tz` import, and a string literal or `str` argument as `tz=` or as the first argument of `tz_localize` / `tz_convert`. |
| `tests/architecture/test_process_state.py` | AST scan of all of `energatron` for every form listed in §9.4, matched by node shape, not only by call name: calls (`pd.set_option`, `os.chdir`, `random.seed`, `os.environ.update`, …); assignments and augmented assignments whose target is under `pd.options`, `os.environ`, `sys.path` or `sys.modules`; `del` of those; and method calls that mutate `sys.path` or `sys.modules` (`append`, `insert`, `extend`, `pop`, `remove`, `clear`, `update`, `setdefault`). Each form has a synthetic-source case in the test, so a gap in the matcher fails. The scoped forms are allowed. |
| `tests/architecture/test_stages.py` (from P1) | The `dvc.yaml` generated from `STAGES` equals `tests/fixtures/golden/dvc.yaml` (§9.5). Every output is a directory. No `cache: false`. Every input is a concrete path, never a glob. Every stage tracks exactly its own `stages.<name>.stage_sha256`, and none depends on `uv.lock` or the whole fingerprint file. |
| `tests/unit/services/test_run_stage.py` (from P1) | `prepare_stage`: picks exactly the declared keys from a synthetic `params.yaml`; a missing key or unknown override name → `E_USAGE`; default paths resolve under `root`. The §9.3 rules, one test each: unknown stage → `E_USAGE` from `get_stage`; a differing or missing `recorded_code` → `E_FINGERPRINT_STALE`, while a change in another stage's entry doesn't matter; undeclared `package_data` → `E_INTERNAL`; missing input or wrong params keys → `E_USAGE`; `impl` raising leaves every target untouched and no temporary directory behind; a non-energatron exception becomes `E_INTERNAL`; a failed check promotes the outputs, then raises `E_CHECK_FAILED`; an empty catalog input is accepted; with an `--input` override outside the workspace, provenance records the declared default path and the override's hash. |
| `tests/integration/test_determinism.py` (from P1) | Run every registered stage twice on fixture inputs into fresh directories: every output file, `_provenance.json` included, is byte-identical. From P6, the whole pipeline twice gives the same output hashes and the same `REC-…` ID (plan §13). |
| `tests/unit/adapters/test_fs_blobs.py` (from P1) | `put` of the same key and bytes is a no-op returning the existing blob; different bytes → `E_BLOB_EXISTS`, and the stored file is unchanged; stored files are mode `0444`; an invalid key (leading `/`, `..`, backslash, outside `data/raw/`) → `E_INTERNAL`; `keys(prefix)` lists every regular file under the prefix, sorted, without `.DS_Store`, and a symlink under it → `E_CATALOG_CORRUPT`; `stat` returns the hash and size of the bytes on disk, also after the file was changed behind the store's back; `get` and `stat` of a missing key → `E_CATALOG_CORRUPT` with `details.reason: "missing_blob"`, and `exists` → `False`. |
| `tests/unit/adapters/test_jsonl_catalog.py` (from P1) | `append` with an existing `dataset_id` or `sha256` → `E_ID_COLLISION`, including across sources; `records()` order (§4.1); a malformed line → `E_CATALOG_CORRUPT` and the file is untouched; an old-version line is upgraded on read; `missing_sources()` lists exactly the sources without a catalog, sorted; `initialise()` creates an empty catalog and returns `True`, and leaves an existing one untouched and returns `False`. |
| `tests/unit/core/test_canonical.py`, `test_ids.py` | Fixed examples, plus hypothesis properties: `canonical_json` ignores key order and whitespace in the source JSON; every constructor's output matches its pattern and round-trips through `parse_*`. A computed dataset ID that exists with a different `sha256` → `E_ID_COLLISION`. |
| `tests/unit/core/test_privacy.py` | `redact_json` redacts string values, keys and numbers at any depth and returns valid JSON, including for denylist entries that contain `"`, `\` or non-ASCII characters; a number holding an entry becomes a redacted string, a bool is never redacted; overlapping or adjacent matches become one `<redacted>`; an entry's inner and outer whitespace is normalised (§8.7). |
| `tests/unit/adapters/test_cli_envelope.py` | With `--json`, or `ENERGATRON_JSON=1`: exactly one JSON document on stdout, for success, every error category and argument errors, including an unknown option placed before `--json`; on a parse failure `command` names a known command and is `null` for any other token. Non-TTY confirmation → `E_CONFIRMATION_REQUIRED`. Denylist strings never reach stdout or stderr. From P6: `run --json` writes exactly one document, while its `dvc repro` children print nothing to stdout (§4.5). |
| `tests/unit/adapters/test_locking.py` | A locking command fails with `E_LOCKED` while another holds the lock; `energatron stage` runs while the lock is held (§8.8). |
| `tests/unit/adapters/test_fingerprint.py` (from P1) | A stable hash for a fixed tree. `__pycache__` is ignored. A symlink among the module files, under declared package data, or as a directory higher up between the package directory and a module file → `E_INTERNAL`. Every `direct_url.json` case in §11.2 maps as the table says. `fingerprint --check` fails on a stale stage entry and only warns on a changed `tool_commit`. |
| `tests/unit/adapters/test_closure.py` (from P1) | On a synthetic package tree: relative, nested-function, `TYPE_CHECKING` and star imports are found, a star import bringing in every submodule; parent `__init__.py` files are included; editing a module inside a stage's set changes its `stage_sha256`, editing one outside doesn't; a changed declared package-data file changes it; `packages` follows requirements recursively, includes an installed distribution behind an `extra ==` marker, skips an uninstalled one, and leaves out distributions the stage doesn't import; an import that maps to no installed distribution → `E_INTERNAL`; a module name mapping to two distributions includes both; `_typeshed` under `TYPE_CHECKING` is skipped, and elsewhere → `E_INTERNAL`. |
| `tests/integration/test_stage_loads.py` (from P1) | Checks the closure against what each stage really loads. For every registered stage:<br>1. The test computes the stage's `StageCode` in the test process.<br>2. It runs the stage on fixture inputs in a fresh interpreter (`sys.executable`). The child gets the `StageCode` as JSON on stdin, rebuilds it with the core model, and passes `recorded_code = code.stage_sha256`; it never imports the fingerprint adapter.<br>3. The child snapshots `sys.modules` before its first `energatron` import. Only modules added after that are checked, so startup modules (`__main__`, the venv's `.pth` hooks such as `_virtualenv`) don't count.<br>4. It imports only `runner.py` and that stage's module.<br>Passes when every added `energatron.*` module is in the module set, and every added non-stdlib top-level module maps to at least one distribution in `packages`. |
| `tests/architecture/test_stage_closure.py` (from P1) | On the real package: no stage's module set contains `services/stages/registry.py` or another stage's module (unless it imports it on purpose, listed in the test); `services/stages/__init__.py` has no statements beyond a docstring. (The ban on dynamic imports is in `test_stage_purity`.) |
| `tests/unit/adapters/test_dvc_runner.py` (from P1) | Against a scratch DVC repo: a downstream stage that reran only because its upstream ran is in `ran`; a stage brought back by the run cache after a param revert is in `restored`; up-to-date stages are `cached`; a changed code entry makes only that stage (and its downstream) stale; `status()` reports a changed code entry before the fingerprint file is refreshed; `repro` refreshes a stale fingerprint file first. Failures: a stage failing a check → `E_CHECK_FAILED`, another stage failure → `E_STAGE_FAILED` with its exit code, a DVC error with no failed stage → `E_SUBPROCESS`, a stage failing before `run_stage` (missing params key) → `E_STAGE_FAILED`, not `E_SUBPROCESS`; a stage killed by a signal (a `start` record and no `end`) → `E_STAGE_FAILED` with `exit_code: null`; the log is gone afterwards in every case. `PATH`: with a caller `PATH` that lacks the venv, the stages still run the venv's `energatron`. Output: with `ENERGATRON_JSON=1` in the environment, `repro()` writes nothing to stdout, no child prints an envelope, and child output reaches stderr. Covers the three §11.5 checks. |
| `tests/integration/test_verify.py` (from P1) | `verify` refuses a stale fingerprint (`E_FINGERPRINT_STALE`) or a stale pipeline (`E_PIPELINE_STALE`); on a current pipeline it passes; after an output file is altered by hand it fails with `E_CHECK_FAILED`, `details.reason: "not_reproducible"`, naming that stage; a stored blob altered or removed by hand fails it naming that `dataset_id`; a leftover scratch directory is replaced, and none is left behind. |

---

## 14. Deferred to phase specs

- Settled by the P2 spec (rev 3): there is no `SourceParser.parse`; the format readers are in core and are in a
  stage's module set through ordinary imports (§4.3). `localise` gets its time zones from the `tzdata`
  distribution through `StageRun.zone()` (§9.2, §9.4). Staged table schemas and keys are in the P2 spec.
- Tariff and engine models, money rounding, the GST convention → P4 spec
- `Fetched` header allow-list, CDR paging details → P4 spec
- Recommendation payload, run manifest, decision schema → settled by the P6 spec (§7.3, §8.2, §9.2; rev 7)
- `energatron migrate` CLI → settled by the P8 spec §7 (rev 9); `params.yaml` is rewritten earlier, by
  `pipeline sync` (§5)
- MCP and HTTP adapters → P9 and P10 (they reuse the service result models and the error codes)

---

## 15. Plan changes (applied in plan rev 7)

Added to the plan's Revisions header as rev 7, with these edits in place:
- §14.1 / §14.2: ports also include `VersionControl` and `Clock`.
- §9.2 / §14.1: the stage contract is `run_stage(spec, inputs, params, outputs, *, code, recorded_code)`, and
  every output is a directory with `_provenance.json`. File outputs in the §9.2 table become directories, e.g.
  `marts/catalog.parquet` → `marts/catalog/`.
- §8.3 / §8.4: dataset IDs use the full source type, e.g. `sapn_interval-20261023T100345-3fa9c2e1`. `landed_at`
  is UTC.
- §9.3: provenance holds no timestamps or run IDs.
- §14.1 (the `schema_version` row) and §17: every persisted document carries `kind` alongside `schema_version`,
  provenance included; Parquet files carry both as metadata.
- §7: the workspace contains `.energatron/workspace.toml` (marker and non-personal settings),
  `.energatron/local.toml` (git-ignored, machine-specific), `.energatron/lock` (git-ignored) and
  `.energatron/tmp/` (git-ignored scratch: the repro log, `verify` outputs). P7 adds `.energatron/drill_state.json`
  (the last passed restore drill) and `.energatron/logs/` (the scheduled `watch` log), both git-ignored.
- §12 (restore drill) and §15 (P7's quarterly drill): after `dvc repro` reports everything up to date, run
  `uv run energatron verify` once it exists (§11.4; D4 schedules it, no later than P2).
- §4: the package calls git and DVC as subprocesses and doesn't depend on DVC.
- §7 / §14.2: stage bodies and the stage registry live in `services/stages/`. `adapters/pipeline/` holds the DVC
  runner and the code fingerprint; `energatron stage` is a CLI command.
- §9.4: `energatron run` doesn't set `ENERGATRON_RUN_ID` for stages, and `--draft` doesn't stamp stage outputs.
  The run ID and the draft flag live in the run manifest only; a decision may cite a recommendation once a
  non-draft run manifest names it.
- §6 and §9.4 step 5: the workspace has no git remote, so DVC's pre-push hook never runs `dvc push`. Drop the
  promise that it does. Backups run through the explicit trigger the P0 spec defines (`VersionControl.backup()`).
- §9.2: replace "Every stage also depends on `.energatron/code_fingerprint.json` and `uv.lock` … any code change
  reruns the stages" with per-stage code entries: each stage tracks only its own `stage_sha256` (its modules,
  package data, imported third-party packages and Python version) as a DVC param, so it reruns only when code
  it uses changes. The tool commit is still recorded in the file for the §9.4 lookup path. DVC's run cache
  stays on; run manifests list stages that ran, were restored, or were up to date.
- §9.3: provenance records the stage's code entry, not the tool commit or package version.
- §9.2: `catalog_index` depends on one `data/raw/<source>/_catalog.jsonl` per source type, not on a glob. An
  empty catalog file exists for every source type with nothing landed.
