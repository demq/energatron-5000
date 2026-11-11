# P1 landing & catalog spec (D3)

- **Created:** 2026-10-23
- **Status:** Approved 2026-10-23 (owner, `/done`); its §17 edits are in the plan as rev 9
- **Deliverable:** D3 of [`2026-10-23_handover-next-planning-stage.md`](2026-10-23_handover-next-planning-stage.md) §8
- **Builds on:** [`2026-10-23_data-pipeline-plan.md`](2026-10-23_data-pipeline-plan.md) rev 8 ("the plan"),
  [`2026-10-23_core-architecture-spec.md`](2026-10-23_core-architecture-spec.md) ("D1", binding for all code) and
  [`2026-10-23_p0-bootstrap-spec.md`](2026-10-23_p0-bootstrap-spec.md) ("D2")
- **For:** the agent or person implementing P1, and D4, which turns §2 into sub-tasks
- **Privacy note:** this file lives in the public-ready tool repo. It contains no NMI, meter serial, account
  numbers, MIRN, address, postcode or usage figures, and nothing added to it may contain them either. Formats
  below were read from the workspace files by structure only (digits masked); examples use placeholders.

---

## 1. What this spec fixes

P1 turns files in an inbox into immutable, catalogued, DVC-tracked datasets with neutral names, and builds the
first pipeline stage, `catalog_index`. This spec gives every model, rule and command P1 needs, so P1 can be
built without design decisions.

"Must" means required. D1 already fixes the ports, IDs, error model, CLI contract, stage contract, provenance
and code fingerprint; this spec only fills in what D1 left to P1.

**What P1 builds**
- core: `SourceType`, `CatalogRecord`, `Coverage`, `Detection`, `SourceInfo`, `StoredBlob`, the pipeline and
  provenance models (D1 §2), `Params`, `RegionProfile`, `check_premises()` and `mask_filename()`
- adapters: `JsonlCatalog`, `FsBlobStore`, the six P1 parsers and their registry, `DvcRunner`, the code
  fingerprint and closure (D1 §11)
- services: `land`, `pipeline_sync`, the stage runner and registry, `catalog_index`
- CLI: `land`, `pipeline sync`, `stage`, `fingerprint`
- `build_context()` loses its P0 placeholders (D2 §5.9)

**Facts about the workspace files** (read on 2026-10-23, structure only):
- **SAPN DETAILED** (NEM12):
  - The filename's from/to dates are the *requested* window. The content can start later and ends the day
    before `to`. So coverage comes from the `300` dates, not the filename.
  - The generation timestamp in the filename is later than every `UpdateDateTime` in the file.
  - The `200` record holds the NMI *without* its checksum digit; the filename holds it *with* the digit. The
    meter serial is field 7 of the `200`.
  - The repeated `200` headers are identical, each preceded by a blank line. LF line endings, no BOM.
- **SAPN SUMMARY** CSV: no header row, CRLF, 11 fields per row: NMI with checksum digit, meter serial, `kWh`, a
  quality flag, start and end dates as `DD/MM/YYYY`, four numbers, `kW`. The SUMMARY PDF has the same filename
  pattern.
- **Retailer usage export:** CRLF, a header row `Account Code,Read Datetime,<one column per TOU register>,Total`.
  Each register column is named `E1 (<meter serial> - <TOU label>)`. One row per monthly read, naive ISO
  datetimes at midnight. Empty values occur in the newest row. The two exports in the workspace have the same
  read datetimes and differ in a few rows. Nothing in either file or name gives an export date.
- **Retailer transactions export:** CRLF, header `Description,Date,Amount`. Descriptions are
  `<Invoice|Payment|Adjustment> - <text>`, and invoice descriptions carry invoice numbers. No NMI, serial or
  account number.
- **Duplicates:** the legacy SAPN DETAILED file is byte-identical to one in `data/landing/`. The eight landable
  files hold seven distinct datasets.

**Changes from the plan, D1 and D2** (also listed in §17):
1. The catalog record drops the plan's `landed_by_commit`. The landing commit contains the hash of the catalog,
   so the catalog can't name that commit. The commit message names the dataset IDs instead, and
   `git log --grep <dataset_id>` finds it (§7.4). A git-ignored marker carries the IDs across an interrupted
   landing, so the healing commit names them too (§7.5).
2. The catalog record gains `file_mtime` and `detector.evidence`. Every parser records `source_ts_from` in
   `extra` (§4).
3. `source_ts` for the retailer exports is the latest datetime in the content, and for bills the PDF creation
   date. The plan says "export date" and "issue date", which these files don't carry in a form P1 can read
   (§3.3).
4. One parser per source type in v1, so `detector.name` equals the source type. D1 §4.3 is unchanged (§5).
5. `marts/catalog/` leaves out `original_filename` and `notes` (§9).
6. The command D1 §9.2 leaves to P1 is `energatron pipeline sync` (§10). Its `--check` runs as a new workspace
   pre-commit hook, `pipeline-sync`, which adds one hook to D2 §11.3.
7. `land` re-tracks every raw source directory on every run, which heals an interrupted landing (§7.5).
8. A multi-file `land` lands what it can and exits 8 (`E_PARTIAL`) when some files are rejected. A privacy
   rejection aborts the whole command instead (§7.2).
9. `land` takes `--supersedes` and `--note`, which extends D1 §4.8's example signature without changing it.
10. `params` v1 holds no settings yet; each phase that adds keys bumps its version (§10.2). DVC reads
    `params.yaml` itself, so an older file can't just be upgraded in memory. `pipeline sync` therefore
    rewrites it to the current version, ahead of `energatron migrate` (P8), which D1 §5 names for editable
    config.
11. **D1 interface change:** two ports gain methods, so services reach the raw zone only through ports (D1 §3):
    `BlobStore.keys(prefix)`, and `Catalog.missing_sources()` with `Catalog.initialise(source)` (§3.4). It's the
    same kind of change D2 made with `backup_status()`, and it waits for D1's next edit in the same way.

---

## 2. Order of work

Each step needs the ones before it. D4 makes them sub-tasks.

| # | Step | Sections |
|---|---|---|
| 1 | Core models, codes, `check_premises`, `mask_filename`; golden schema fixtures | §3, §4, §6, §11 |
| 2 | Fixture generators | §12 |
| 3 | `FsBlobStore`, `JsonlCatalog` with the §3.4 methods; `build_context()` uses them (the `runner` placeholder stays until step 7) | D1 §4.1–4.2, §3.4 |
| 4 | Parsers, registry, region profile, detection resolution | §5, §10.3 |
| 5 | `land` service and CLI | §7, §8 |
| 6 | Stage runner, registry, `catalog_index`, `dvc.yaml` emit, `stage` CLI | D1 §9, §9 |
| 7 | Closure, fingerprint, `DvcRunner`, `fingerprint` CLI; the last placeholder leaves `build_context()`; the D1 §11.5 and §14 verifications | D1 §11, §14 |
| 8 | `pipeline sync`, the workspace hook and `CLAUDE.md` update | §10 |
| 9 | Acceptance in the real workspace; then the owner backs up | §15 |

---

## 3. Source types and the raw zone

### 3.1 `SourceType` (`core/models/catalog.py`)

A `StrEnum` with every source type in plan §8.2, all from P1:

`elec_bill`, `gas_bill`, `gas_bill_record`, `offer_cdr`, `offer_cdr_index`, `offer_manual`, `retailer_txn`,
`retailer_usage`, `sapn_interval`, `sapn_summary`.

- All ten exist from P1 even where no parser does yet. So every raw directory, empty catalog and `dvc.yaml`
  input exists from the start, and adding a parser later changes no pipeline file.
- `PUBLIC_SOURCES = frozenset({offer_cdr, offer_cdr_index})`: public data, exempt from the premises check (§6).
- Adding a source type is a tool change: `pipeline sync` then creates its catalog, and the golden `dvc.yaml`
  changes (D1 §9.5).

### 3.2 Layout under `data/raw/`

```
data/raw/
├── .gitignore                  written by dvc add: one /<source> line per source
├── <source>.dvc                one pointer per source directory (git-tracked)
└── <source>/
    ├── _catalog.jsonl          append-only, one catalog_record per line (D1 §4.1)
    └── <YYYY>/<dataset_id><ext>
```

- **Blob key** (D1 §4.2): `data/raw/<source>/<YYYY>/<dataset_id><ext>`.
  - `<YYYY>` is the first four characters of the ID's timestamp, `compact_source(source_ts)`.
  - `<ext>` is `SourceInfo.extension`, lower-case, one of `.csv .pdf .json .yaml`.
- `WorkspacePaths` (`core/models/workspace.py`) gains `raw_dir(source)`, `catalog_path(source)` and
  `blob_key(source, dataset_id, ext)`. The stage registry, `JsonlCatalog`, `FsBlobStore`, `land` and
  `pipeline sync` all use them, so the layout is written down once.
- Nothing else lives in a source directory. `land` checks this while planning (§7.2).

### 3.4 Port additions (a D1 change, §1 change 11)

```python
class BlobStore(Protocol):
    ...                                                   # D1 §4.2 methods
    def keys(self, prefix: str) -> list[str]: ...         # every stored file under prefix, sorted

class Catalog(Protocol):
    ...                                                   # D1 §4.1 methods
    def missing_sources(self) -> list[SourceType]: ...    # sources with no catalog yet, sorted
    def initialise(self, source: SourceType) -> bool: ... # create an empty catalog; False if one exists
```

- `keys(prefix)`: the prefix follows D1 §4.2's key rules and ends with `/`. The result holds every regular file
  under it, recursively, as keys. That includes the catalog file, which shares the directory; `.DS_Store` is
  left out. A symlink under the prefix → `E_CATALOG_CORRUPT`, `details.reason: "symlink"`.
- `initialise(source)`: `JsonlCatalog` creates `raw_dir(source)` and an empty `_catalog.jsonl` atomically
  (temp file, `os.link`, as D1 §4.2). An existing file is left alone, whatever it holds.
- Callers hold the workspace lock, as for the other methods.

### 3.3 `source_ts` per source type

`source_ts` is when the source produced the data. It goes into the dataset ID (D1 §6.3) and orders datasets of
one source. It is taken from the file, never from the clock, so it is the same whenever the file is landed.

| Source | `source_ts` | Zone | `extra.source_ts_from` |
|---|---|---|---|
| `sapn_interval` | generation timestamp in the SAPN filename; else the `100` record's DateTime; else the latest `UpdateDateTime` | naive (SAPN gives no zone) | `filename`, `nem12_header`, `content` |
| `sapn_summary` | generation timestamp in the SAPN filename; else (CSV) the latest end date at 00:00, (PDF) the PDF creation date | naive; the PDF date as given | `filename`, `content`, `pdf_metadata` |
| `retailer_usage` | latest `Read Datetime` | naive | `content` |
| `retailer_txn` | latest `Date` | naive | `content` |
| `elec_bill`, `gas_bill` | PDF creation date (`/CreationDate` or XMP `xmp:CreateDate`) | aware if the PDF gives an offset, else naive | `pdf_metadata` |
| `offer_cdr` | `data.lastUpdated` | aware (UTC in CDR) | `content` |
| `gas_bill_record`, `offer_manual` | set by the P5 and P4 specs | — | — |
| `offer_cdr_index` | `fetched_at` of the batch, set by the P4 spec | aware, UTC | — |

- Datasets of one source can share a `source_ts`, e.g. two retailer exports taken in the same month. Their IDs
  still differ by hash. **Precedence between them** (for P2 and later specs): newer `source_ts`, then newer
  `file_mtime`, then later catalog position.
- A bill whose PDF carries no creation date can't be landed in P1 (§5.6). The P5 spec adds text extraction.

---

## 4. The catalog record (`catalog_record`, version 1)

### 4.1 Model (`core/models/catalog.py`)

```json
{
  "kind": "catalog_record",
  "schema_version": 1,
  "dataset_id": "sapn_interval-20261023T100345-3fa9c2e1",
  "source": "sapn_interval",
  "sha256": "<64 hex>",
  "bytes": 1234567,
  "stored_path": "data/raw/sapn_interval/2026/sapn_interval-20261023T100345-3fa9c2e1.csv",
  "original_filename": "<kept here only; the catalog is DVC-tracked and private>",
  "landed_at": "2026-10-23T00:32:10Z",
  "file_mtime": "2026-10-23T00:05:41Z",
  "source_ts": "2026-10-23T10:03:45",
  "coverage": {"start": "2024-10-23", "end": "2026-10-22"},
  "detector": {"name": "sapn_interval", "version": 1, "evidence": ["filename", "content"]},
  "extra": {"interval_minutes": 5, "nmi_suffixes": ["E1"], "days": 731, "...": "..."},
  "supersedes": null,
  "notes": ""
}
```

| Field | Type | Rule |
|---|---|---|
| `dataset_id` | str | equals `dataset_id(source, source_ts, sha256)` (D1 §6.3) |
| `source` | `SourceType` | equals the catalog file's source |
| `sha256`, `bytes` | str, int | of the exact stored bytes; `bytes` > 0 |
| `stored_path` | str | equals `blob_key(source, dataset_id, ext)`, `ext` one of §3.2 |
| `original_filename` | str | the input's base name, no directory; 1–255 characters |
| `landed_at` | aware datetime, UTC | one value per `land` command, from `ctx.clock` |
| `file_mtime` | aware datetime, UTC, or null | the input's modification time when landed; ordering hint only (§3.3) |
| `source_ts` | datetime, naive or aware | §3.3; persisted as given (`YYYY-MM-DDTHH:MM:SS`, plus an offset when aware) |
| `coverage` | `Coverage` or null | `start` ≤ `end`, both inclusive dates |
| `detector` | `{name, version, evidence}` | `name` = the parser's `source`; `version` = its `version`; `evidence` from `Detection`, or `["option"]` when `--source` chose the parser |
| `extra` | object | the parser's `SourceInfo.extra`; non-personal facts only (§5) |
| `supersedes` | dataset ID or null | an existing dataset of the same source (§7.2) |
| `notes` | str | one line, at most 500 characters; private free text |

- `Persisted` rules apply (D1 §5): `extra="forbid"`, frozen, any change bumps the version.
- `CatalogRecord.from_json_line(line)` (core) parses, upgrades and validates one line. Invalid JSON or a failed
  validation → `E_CATALOG_CORRUPT` with `details.line`, never the line's text.
- A record is written as `canonical_json(record.model_dump(mode="json")) + b"\n"`.

### 4.2 `Coverage`, `Detection`, `SourceInfo`

As D1 §4.3 defines them, with these rules:
- `Coverage(start: date, end: date)`, frozen.
- `Detection.evidence` is ordered `filename`, then `content`, and never empty.
- `SourceInfo.extra` values are plain JSON. The parser keeps out every identifier, filename and figure (kWh, $).
  Counts, dates, units, interval lengths, TOU labels and public plan IDs are fine.
- `SourceInfo.identifiers` keys are `nmi`, `meter_serial` and `account`. Values are as found in the file,
  stripped. They go to `check_premises()` and are then dropped (D1 §4.3).

---

## 5. Parsers and detection

Each parser is a module in `adapters/sources/` implementing D1 §4.3's `SourceParser`, listed in
`adapters/sources/registry.py: PARSERS`. They are pure: `detect` sees the file name and the first 64 KiB,
`inspect` the whole file. Text formats are decoded as UTF-8 with a leading BOM dropped; LF and CRLF are both
accepted.

`detect` returns:
- `None` when neither the name nor the content looks like this source
- `Detection(evidence=("filename",))` when the name matches but the content doesn't
- otherwise a detection whose evidence includes `content`, plus `filename` when the name matches too

`inspect` validates the whole file and raises `E_INVALID_INPUT` with `details.reason` (a fixed snake_case word,
never file content) on the first problem.

### 5.1 Resolution (`services/land.py: resolve_parser`)

> 2026-11-11: step 3 now lets `ConfirmingParser`s settle a tie from the whole file; see the bill PDF text spec
> (`../2026-11/2026-11-11_bill-pdf-text-spec.md`) §3.

- **With `--source S`:**
  - no parser for `S` in `ctx.parsers` → `E_USAGE` ("no parser for S in this version")
  - the parser's detection has no `content` evidence → `E_INVALID_INPUT`, `details.reason: "content_mismatch"`
  - otherwise use it; the catalog records `evidence: ["option"]`
- **Without `--source`:** run every parser's `detect`.
  1. Rank the detections that have `content` evidence: `filename` + `content` above `content` alone.
  2. Exactly one at the top rank → use it.
  3. Several at the top rank → `E_AMBIGUOUS_SOURCE`, `details.candidates` sorted (e.g. three parsers all
     accept a bare PDF).
  4. None with content, but some filename-only → `E_INVALID_INPUT`, `details.reason: "content_mismatch"`,
     `details.source`.
  5. No detection at all → `E_UNKNOWN_SOURCE`.

### 5.2 `sapn_interval` (`adapters/sources/sapn_nem12.py`, version 1)

- **Filename:** `^(?P<nmi>[A-Z0-9]{10}[0-9])_(?P<from>\d{8})_(?P<to>\d{8})_(?P<gen>\d{14})_SAPN_DETAILED\.csv$`,
  extension case-insensitive.
- **Content:** the first non-blank line is either `100,NEM12,…`, or a `200` record with at least 10 fields whose
  field 8 is `KWH` (any case) and field 9 is `5`, `05`, `15` or `30`.
- **`inspect`** checks, in order:
  - every non-blank line's record type is `100 200 300 400 500 900`; a `100` may only be first; the last
    non-blank line is `900` (else `truncated`)
  - every `200` has at least 10 fields and the same interval length; the unit is `KWH`
  - every `300` follows a `200`, has exactly `2 + 1440 / interval + 5` fields, a valid `YYYYMMDD` date and a
    14-digit `UpdateDateTime`
  - every `400` follows a `300` whose QualityMethod starts with `V`, and its interval range lies in
    `1 … 1440 / interval`
  - no `(NMI, suffix, date)` appears twice (else `duplicate_day`)
  - a `100` record's DateTime (field 3) is 12 digits, `YYYYMMDDhhmm`, a valid time (seconds are 00), else
    `bad_header`
  - a filename whose `from` or `to` isn't a valid date, or whose `gen` isn't a valid `YYYYMMDDhhmmss` time →
    `bad_filename`
- **`SourceInfo`:**
  - `source_ts` per §3.3; `coverage`: the first and last `300` date over all suffixes
  - `extra`: `interval_minutes`, `nmi_suffixes` (sorted), `uom` (`"KWH"`), `days` (distinct `300` dates),
    `days_v` (dates with a `V` record), `header_records` (number of `200`s), `requested_window`
    (`{start, end}` from the filename, or null), `source_ts_from`
  - `identifiers`: `nmi` from every `200` (10 characters) and from the filename (11); `meter_serial` from every
    non-empty `200` field 7
  - `extension`: `.csv`
- Values and quality detail are P2's (`parse`); P1 only counts.

### 5.3 `sapn_summary` (`adapters/sources/sapn_summary.py`, version 1)

- **Filename:** as §5.2 with `SAPN_SUMMARY\.(csv|pdf)`.
- **Content:** CSV, when the first non-blank line has 11 fields, field 3 is `kWh`, fields 5 and 6 match
  `\d{2}/\d{2}/\d{4}` and field 11 is `kW`. PDF, when the file starts with `%PDF-`.
- **`inspect`, CSV:** every non-blank row has those 11 fields; dates parse as `DD/MM/YYYY` with start ≤ end;
  fields 7–10 are decimals.
  - `coverage`: earliest start to latest end
  - `extra`: `format: "csv"`, `rows`, `requested_window`, `source_ts_from`
  - `identifiers`: `nmi` from field 1 and the filename; `meter_serial` from field 2
- **`inspect`, PDF:** the file ends with `%%EOF` (trailing whitespace allowed), else `truncated`.
  - `coverage`: null. The filename window overstates the content (§1), and P1 doesn't read PDF text. The
    window stays in `extra.requested_window`.
  - `source_ts` without a matching name: the PDF creation date, read as in §5.6
  - `extra`: `format: "pdf"`, `requested_window`, `source_ts_from`
  - `identifiers`: `nmi` from the filename; none otherwise, as P1 doesn't read PDF text
- `extension`: `.csv` or `.pdf`. Stages that read summaries (P2) select `extra.format == "csv"`.

### 5.4 `retailer_usage` (`adapters/sources/retailer_usage.py`, version 1)

v1 recognises the one portal export format in the workspace. Another retailer's format is a new parser
version, or a per-format parser once a spec adds a `name` to `SourceParser` (§16).

- **Filename evidence:** `(?i)usage_all\.csv$`.
- **Content:** the first line matches
  `^Account Code,Read Datetime(,[A-Z][0-9A-Z] \([^,()]+ - [^,()]+\))+,Total$`.
- **`inspect`:** every row has the header's field count; `Read Datetime` parses as `%Y-%m-%dT%H:%M:%S`; the
  register columns and `Total` are empty or decimal; at least one row.
  - `source_ts`: the latest `Read Datetime`; `coverage`: earliest to latest read date (the dates the reads
    were taken, not the periods)
  - `extra`: `rows`, `tou_labels` (the labels from the register columns, sorted, as written, e.g.
    `"Off Peak"`), `nmi_suffixes`, `source_ts_from: "content"`
  - `identifiers`: `account` from every row's `Account Code`; `meter_serial` from every register column name
  - `extension`: `.csv`

### 5.5 `retailer_txn` (`adapters/sources/retailer_txn.py`, version 1)

- **Filename evidence:** `(?i)transactions_all\.csv$`.
- **Content:** the first line is exactly `Description,Date,Amount`.
- **`inspect`:** every row has 3 fields; `Date` parses as `%Y-%m-%dT%H:%M:%S`; `Amount` is a decimal; at least one
  row.
  - `source_ts`: the latest `Date`; `coverage`: earliest to latest date
  - `extra`: `rows`, `source_ts_from: "content"`. Description text never reaches `extra`: it holds invoice
    numbers.
  - `identifiers`: none, so the premises check warns (§6)
  - `extension`: `.csv`

### 5.6 `elec_bill`, `gas_bill` (`adapters/sources/bill_pdf.py`: two parser instances, version 1)

> 2026-11-11: version 2 dates a bill by its printed issue date and confirms its kind from the text, so a bill no
> longer needs `--source`; see the bill PDF text spec (`../2026-11/2026-11-11_bill-pdf-text-spec.md`) §3, §4.

- **Filename evidence:** none. **Content:** the file starts with `%PDF-`. So a bill always needs `--source`: a
  bare PDF also matches `sapn_summary` content-only, and the three tie (§5.1).
- **`inspect`:** the file ends with `%%EOF`, else `truncated`. A trailer or dictionary holding `/Encrypt` →
  `E_INVALID_INPUT`, `details.reason: "encrypted"`. `source_ts` comes from
  `adapters/sources/pdf_meta.py: creation_date(data) -> datetime | None`, and `None` →
  `E_INVALID_INPUT`, `details.reason: "no_creation_date"`.
  - `coverage`: null; `extra`: `source_ts_from: "pdf_metadata"`; `identifiers`: none; `extension`: `.pdf`
- **`creation_date(data)`** is pure and stdlib-only (`re`, `zlib`, `codecs`). It searches these places in order,
  stopping at the first date that parses:
  1. the raw bytes
  2. every stream whose dictionary has `/Filter /FlateDecode` (a single filter, or an array holding only it),
     inflated with `zlib.decompressobj()`. This reaches an Info dictionary inside a compressed `/ObjStm`
     (PDF 1.5+) and a compressed XMP packet. A stream that fails to inflate is skipped. At most 64 MiB is
     inflated in total; past that it stops searching.
  
  In each place it looks for `/CreationDate` and then for XMP `xmp:CreateDate`:
  - `/CreationDate` may hold a literal string `(…)`, with `\` escapes, or a hex string `<…>`. Bytes that start
    with the UTF-16BE BOM `FE FF` are decoded as UTF-16BE, other bytes as Latin-1.
  - The value is `D:YYYY[MM[DD[HH[mm[SS]]]]][Z | ±hh'mm' | ±hh]`; the `D:` prefix is optional. Missing parts
    default to 01, 01, 00, 00, 00. An offset makes the datetime aware, `Z` makes it UTC, and no offset leaves it
    naive.
  - `xmp:CreateDate` is the ISO 8601 value of the attribute or element, with an optional offset.
- The P5 spec adds text extraction (pdfplumber), the issue date and identifiers, as version 2.

### 5.7 `offer_cdr` (`adapters/sources/cdr_plan.py`, version 1)

For a plan-detail response landed by hand. P4's `fetch-offers` lands through the same service.
- **Filename evidence:** none. **Content:** the first non-space byte is `{`, and the head contains `"planId"`
  and `"lastUpdated"`.
- **`inspect`:** valid JSON; `data` is an object with string `planId` (no `~` or whitespace), `lastUpdated`
  (RFC 3339) and `fuelType` (`ELECTRICITY`, `GAS` or `DUAL`).
  - `source_ts`: `lastUpdated`; `coverage`: null
  - `extra`: `plan_id`, `fuel_type`, `offer_snapshot_id` (D1 §6.3, over `data`)
  - `identifiers`: none; `extension`: `.json`
- **Snapshot dedupe** (plan §8.4): if the catalog already holds an `offer_cdr` record with the same
  `extra.offer_snapshot_id`, the item is a no-op with status `same_snapshot`, even though the bytes differ.

### 5.8 Sources without a P1 parser

`gas_bill_record` (P5), `offer_manual` (P4) and `offer_cdr_index` (P4, landed only by `fetch-offers`). `--source`
with one of them → `E_USAGE`. Their catalogs exist and stay empty.

---

## 6. Premises check (`core.privacy.check_premises`)

```python
def check_premises(
    source: SourceType, identifiers: Mapping[str, Sequence[str]], private: PrivateConfig
) -> list[ResultWarning]: ...  # or raises E_FOREIGN_PREMISES / E_INVALID_INPUT
```

Comparisons are case-insensitive, on stripped values. The NMI is compared on its first 10 characters.

1. **Public sources** (`PUBLIC_SOURCES`): no check, no warning.
2. **NMI:** an 11-character value whose last digit isn't `nmi_checksum()` of the first 10 → `E_INVALID_INPUT`,
   `details.reason: "nmi_checksum"`. Any NMI that differs from `premises.nmi` → `E_FOREIGN_PREMISES`. When at
   least one NMI is present and all match, the premises is confirmed.
3. **Meter serials and accounts** not in `premises.meter_serials` or `accounts.electricity` ∪ `accounts.gas`
   → `W_UNKNOWN_IDENTIFIER` with `details.identifier` (`meter_serial` or `account`), once per kind. The hint
   says to add the value to `private/config.toml` and run `energatron denylist`, because the denylist doesn't
   cover it yet. A meter exchange gives exactly this case, so with a confirmed NMI it isn't an error.
4. **No NMI in the file:** the premises is confirmed if any serial or account is known.
   - Values of a kind are present, that kind's private list is non-empty, and nothing matches in any kind →
     `E_FOREIGN_PREMISES`.
   - Otherwise (no identifiers, or only kinds whose private list is empty) → `W_PREMISES_UNCHECKED`.

Errors and warnings name kinds, never values. `details` holds `identifier` and the input's `position`.
`ResultWarning` is the `{code, message, details}` warning model P0 puts in `core/errors.py` (D1 §7), whatever
name P0 gives it. It must not be called `Warning`, which would shadow the Python builtin.

---

## 7. Landing (`services/land.py`)

```python
def land(
    ctx: ServiceContext,
    paths: Sequence[Path],
    *,
    source: SourceType | None = None,
    supersedes: str | None = None,
    note: str = "",
    dry_run: bool = False,
) -> LandResult: ...
```

The CLI holds the workspace lock (D1 §8.8) for the whole call, dry runs included.

### 7.1 Preconditions (any failure: nothing is read or written)

1. `ctx.private` is `None` → `E_PRIVATE_CONFIG_MISSING`.
2. `pipeline_sync.check(ctx)` (§10.1) fails → `E_PIPELINE_NOT_SYNCED`.
3. `vcs.dirty_paths(["data/raw"])` lists anything other than `data/raw/*.dvc` and `data/raw/.gitignore` →
   `E_DIRTY`, `details.paths`. (Dirty pointer files are allowed: they are what an interrupted land leaves, §7.5.)
4. `vcs.dirty_paths(["dvc.yaml", "params.yaml", ".pre-commit-config.yaml"])` is not empty → `E_DIRTY`, with
   `details.hint` "commit these first".
   - pre-commit stashes unstaged changes before it runs hooks, so the `pipeline-sync` hook in `land`'s commit
     checks the *committed* `dvc.yaml`.
   - An uncommitted `pipeline sync` would pass step 2 but fail the commit; this step stops that earlier.
5. A path that doesn't exist, or lies under `data/raw/` → `E_USAGE`, `details.position`.
6. `supersedes` naming an unknown dataset → `E_USAGE`.
7. `note` longer than 500 characters or containing a line break → `E_USAGE`.

### 7.2 Plan (reads only)

A whole-command error raised here also leaves nothing written.

1. **Expand** each path:
   - a file stays; a directory gives its regular files, not recursively, sorted by name
   - inside a directory, names starting with `.` are skipped and counted (`skipped_hidden`); a hidden file
     named explicitly is read like any other
   - a symlink, given or found, is a rejected item (`E_INVALID_INPUT`, `reason: "symlink"`)
   - items are numbered `position` 1, 2, … in this order
   - no items at all → `E_USAGE` ("no input files"); `supersedes` with more than one item → `E_USAGE`
2. **Raw zone:** for every source, `blobs.keys(raw_dir(s) + "/")`. Leaving out `catalog_path(s)` and every
   catalogued `stored_path` gives the source's *unclaimed keys*, which step 4 uses.
3. **Items, one at a time in position order.** Each item goes through 3.1–3.6 before the next one starts, so
   "earlier in the batch" always means an item whose outcome is known.
   1. **Read:** the bytes, their `sha256` and the `file_mtime`. An empty file, or one over 256 MiB →
      `E_INVALID_INPUT`.
   2. **Already landed:**
      - `catalog.find_sha256` hits → `already_landed`, with that record's ID
      - the same hash as an earlier item *accepted* in this batch → `duplicate_in_batch`, naming that item's ID
      - a copy of an earlier *rejected* item is processed in its own right. Detection depends on the file name,
        so a well-named copy can succeed where a badly named one failed.
   3. **Detect** (§5.1) and **inspect** (§5).
   4. **Premises** (§6).
      - `E_FOREIGN_PREMISES` aborts the whole command with that error, before anything is written: a foreign
        file usually means the wrong folder.
      - `E_INVALID_INPUT` (`nmi_checksum`) rejects only the item: a damaged name or file, not a privacy
        problem.
   5. **ID:** `dataset_id(source, info.source_ts, sha256)`.
      - `catalog.get` finds it (with another hash), or an earlier accepted item has it → `E_ID_COLLISION`
      - for `offer_cdr`: a catalog record or an earlier accepted item with the same `extra.offer_snapshot_id` →
        `same_snapshot` (§5.7)
   6. **Supersedes:** the target's source must equal the item's, and no record may already supersede it.
      Otherwise → `E_USAGE` for the whole command (there is only one item).

   An item that fails 3.1, 3.3, 3.4 (checksum) or 3.5 is **rejected** with its error; the others go on.
4. **Stray files:** take each source's unclaimed keys and remove the keys of this batch's accepted items.
   - Anything left → `E_CATALOG_CORRUPT` for the whole command, with `details.reason: "uncatalogued_file"`,
     `details.source` and `details.count`. `dvc add` would otherwise capture an uncatalogued file.
   - An unclaimed key that matches an accepted item's key is an orphan blob from an interrupted run. It is
     adopted: §7.3's `put` finds the same bytes and does nothing.
5. **Dry run:** stops here and returns the plan. A dry run runs every check above, so its statuses and errors
   are the ones the real run would give.

### 7.3 Write

1. **Pending marker:** when there are new items, write `.energatron/tmp/land_pending.json` (kind
   `land_pending`, version 1: `{"kind", "schema_version", "dataset_ids": [sorted]}`), atomically, before the
   first blob.
   - It holds this run's new IDs plus the IDs of a marker left by an interrupted run.
   - `.energatron/tmp/` is git-ignored (D2 §11.1).
2. **Order:** accepted items are written sorted by
   `(source, compact_source(source_ts), file_mtime, position)`, with a null `file_mtime` after every non-null
   one. So catalog order follows data age (§3.3).
3. For each: `blobs.put(blob_key(…), data)`, then `catalog.append(record)`. `landed_at` is one
   `ctx.clock.now()`, taken at the start of the command.
4. **Track:** `vcs.track([raw_dir(s) for s in SourceType])`, every source, every time (§7.5).
5. **Commit:**
   - The paths are whatever `vcs.dirty_paths(["data/raw"])` now lists. §7.1 step 3 guarantees these are only
     `data/raw/*.dvc` and `data/raw/.gitignore`, whether this run or an interrupted one left them dirty.
   - None → no commit. Otherwise `vcs.commit(paths, message)` (§7.4), naming this run's new IDs plus every ID
     in the marker that `catalog.get` finds.
6. **Marker cleanup:** after a successful commit, or when step 5 found nothing dirty, delete the marker.
7. When a commit was made: warning `W_BACKUP_PENDING`, with `details.commits_not_backed_up` from
   `vcs.backup_status()` (local only, D2 §8.5).

### 7.4 Commit message

The commit message is the only place where a dataset ID meets a commit, so it must name every ID that the
commit lands.

```
land: sapn_interval-20261023T100345-3fa9c2e1
```

or, with several:

```
land: 3 datasets

retailer_usage-20251001T000000-0a1b2c3d
sapn_interval-20251023T221530-4e5f6a7b
sapn_interval-20261023T100345-3fa9c2e1
```

- IDs are sorted, one per body line. They come from §7.3 step 5, so a healed landing names its IDs too.
- Dirty pointer files but no IDs at all (the marker was lost): `land: resync raw pointers`.
  - **The gap:** datasets committed this way can't be found with `git log --grep`. They are still found through
    the catalog, by `landed_at` and the commit date. Only deleting `.energatron/tmp/` by hand mid-recovery
    causes it.
- Nothing else goes in the message: no file names, kWh figures or notes. The workspace's `privacy-guard-msg`
  hook checks it anyway.

### 7.5 Failures and recovery

| Failure after | State left | What the next `land` does |
|---|---|---|
| the marker, or `blobs.put` | the marker; maybe a stored blob with no catalog line | re-landing the same file adopts the blob (§7.2 step 4); `put` is a no-op and the line is appended |
| `catalog.append` | the marker; catalogued, not tracked | the item is `already_landed`; step 4 re-tracks every source; step 5 commits, naming the marker's IDs |
| `track`, or a rejected commit (`E_COMMIT_REJECTED`, `E_DENYLIST_HIT`) | the marker; dirty pointer files | allowed by §7.1 step 3; committed in step 5, naming the marker's IDs |

- So re-running the same `land` after any failure finishes the job. A failed commit's error says so in
  `details.hint`.
- A blob without a catalog line that nobody re-lands fails the next land at §7.2 step 4 (`E_CATALOG_CORRUPT`)
  until the owner decides. Nothing deletes it automatically.
- `land` never moves or deletes its inputs. The owner clears `data/landing/` (§16).

---

## 8. `energatron land` (CLI contract)

### 8.1 Synopsis

```
energatron land PATH... [--source SOURCE] [--supersedes DATASET_ID] [--note TEXT]
                        [--dry-run] [--yes] [--json] [--workspace PATH] [-v] [--debug]
```

- Takes the lock (D1 §8.8). `--yes` is accepted and unused: `land` never prompts.
- Paths may lie outside the workspace (e.g. `~/Downloads`).

### 8.2 Result (`land_result`, version 1)

```json
{
  "kind": "land_result",
  "schema_version": 1,
  "dry_run": false,
  "items": [
    {"position": 1, "input": "data/landing/<n>_<n>_<n>_<n>_SAPN_DETAILED.csv", "bytes": 1234567,
     "sha256": "<64 hex>", "status": "landed", "source": "sapn_interval",
     "dataset_id": "sapn_interval-20261023T100345-3fa9c2e1",
     "stored_path": "data/raw/sapn_interval/2026/sapn_interval-20261023T100345-3fa9c2e1.csv",
     "coverage": {"start": "2024-10-23", "end": "2026-10-22"}, "error": null},
    {"position": 2, "input": "data/landing/notes.txt", "bytes": 120, "sha256": "<64 hex>",
     "status": "rejected", "source": null, "dataset_id": null, "stored_path": null, "coverage": null,
     "error": {"code": "E_UNKNOWN_SOURCE", "category": "input", "message": "No parser recognises this file.",
               "details": {"position": 2}}}
  ],
  "landed": ["sapn_interval-20261023T100345-3fa9c2e1"],
  "commit": "<full hex SHA>",
  "pointer_files": ["data/raw/sapn_interval.dvc"],
  "skipped_hidden": 1
}
```

- `status`: `landed` (in a dry run: would land), `already_landed`, `duplicate_in_batch`, `same_snapshot`,
  `rejected`. `dataset_id` is set for every status but `rejected`; for the no-op statuses it is the existing ID.
- `input`: the path relative to the workspace, or `<outside>/<name>`, with every component passed through
  `core.privacy.mask_filename()`:
  1. `redact()` with the denylist
  2. every maximal run of `[A-Za-z0-9]` containing four or more digits becomes `<n>`
  
  So SAPN names keep their shape but lose the NMI and dates, and a retailer name loses its numbers. The
  `position` tells same-shaped names apart.
- `bytes` and `sha256` are null for an item rejected before it was read (a symlink).
- `landed`: new IDs, sorted. `commit`: the new commit, or null. `pointer_files`: what was committed.
- Never in the result, errors or logs: original file names, identifiers, `notes`, or any content.
- Warnings (`W_BACKUP_PENDING`, `W_PREMISES_UNCHECKED`, `W_UNKNOWN_IDENTIFIER`) go to the envelope's `warnings`,
  as P0's services return them; per-item ones carry `details.position`.

### 8.3 Exit codes (D1 §7)

| Outcome | Exit | Error |
|---|---|---|
| every item landed or a no-op | 0 | — |
| one input file (after expansion), rejected | its own category: 3 (`E_UNKNOWN_SOURCE`, `E_AMBIGUOUS_SOURCE`, `E_INVALID_INPUT`) or 4 (`E_ID_COLLISION`) | that item's error; `data` null |
| several input files, at least one rejected | 8 | `E_PARTIAL`; `data` holds the result, and the accepted items are landed and committed |
| any item from a foreign premises | 5 | `E_FOREIGN_PREMISES`; nothing written |
| a precondition (§7.1), no input files, or a bad `--supersedes` target (§7.2 steps 1 and 3.6) | 2 (`E_USAGE`) or 4 (`E_PRIVATE_CONFIG_MISSING`, `E_PIPELINE_NOT_SYNCED`, `E_DIRTY`) | as listed there; nothing written |
| an uncatalogued file in the raw zone (§7.2 step 4), or a malformed catalog line | 4 | `E_CATALOG_CORRUPT`; nothing written |
| `blobs.put` finds different bytes under a key | 4 | `E_BLOB_EXISTS`; re-running heals what was written (§7.5) |
| `dvc add` or git failed | 6 | `E_SUBPROCESS`; re-running heals (§7.5) |
| the commit was rejected | 4 or 5 | `E_COMMIT_REJECTED` or `E_DENYLIST_HIT`; `details.hint` says re-run `land` after fixing |

A directory argument with no regular files contributes no items. If no argument gives any, the command fails
with `E_USAGE`.

### 8.4 Human output

One line per item: `<position> <status> <source> <dataset_id>` (or the error code), then
`landed N, unchanged M, rejected K; commit <sha7>`, then the warnings, on stderr.

---

## 9. The `catalog_index` stage (`services/stages/catalog_index.py`)

```python
SPEC = StageSpec(
    name="catalog_index",
    inputs={f"catalog.{s}": f"data/raw/{s}/_catalog.jsonl" for s in sorted(SourceType)},   # built by the registry
    params=(),
    outputs={"catalog": "data/marts/catalog"},
    impl=_run,
)
```

- The inputs are built from `SourceType` by the registry, as D1 §9.2 requires. The stage reads catalog files
  only, never blobs.
- **Body:** for each input in sorted order, parse every line with `CatalogRecord.from_json_line`. A line that
  fails is left out of the table and counted for `records_valid`. `E_SCHEMA_TOO_NEW` isn't caught: it fails the
  stage, because the tool is older than the data.
- **Output `catalog/datasets.parquet`** (kind `catalog_table`, version 1, `sort_by=["dataset_id"]`), one row
  per valid record:

  | Column | Type |
  |---|---|
  | `dataset_id`, `source`, `sha256`, `stored_path` | string |
  | `bytes` | int64 |
  | `landed_at`, `file_mtime` | timestamp[us, UTC]; `file_mtime` nullable |
  | `source_ts` | string, as persisted (naive or aware) |
  | `source_ts_compact` | string, the ID's timestamp part, so mixed naive and aware values sort |
  | `coverage_start`, `coverage_end` | date32, nullable |
  | `detector_name` | string; `detector_version` int32; `detector_evidence` list<string> |
  | `extra_json` | string: `canonical_json(extra)` |
  | `supersedes`, `superseded_by` | string, nullable |
  | `is_current` | bool: not superseded |

  `original_filename` and `notes` are left out: marts are what agents and reports read (plan §14.5).
- **Checks** (D1 §9.2), each with counts in `details`, never values:
  - `records_valid`: every line parsed; `details.invalid` per source with line numbers
  - `source_matches_file`: each record's `source` equals its file's source
  - `dataset_id_unique`, `sha256_unique`: across all sources
  - `id_consistent`: `dataset_id` and `stored_path` recompute (§4.1)
  - `supersedes_valid`: each target exists with the same source, has at most one successor, and there are no
    cycles
- Empty catalogs give a valid zero-row table, and every check passes.
- Provenance (D1 §10): each catalog input's `dataset_ids` are the IDs of its valid lines.

---

## 10. `pipeline sync`, `params` and the region profile

### 10.1 `energatron pipeline sync [--check] [--dry-run]` (`services/pipeline_sync.py`)

Brings the workspace's generated pipeline files in line with the installed tool. Run it once when P1 is set up,
and again after any tool change that alters `STAGES` or `SourceType`.

1. `dvc.yaml` = `services/stages/dvc_yaml.py: emit_dvc_yaml(STAGES)`, the same text `energatron stage
   --emit-dvc-yaml` prints (D1 §9.5). It starts with
   `# Generated by energatron pipeline sync. Do not edit; re-run the command instead.` Written atomically, only if
   the bytes differ.
2. `params.yaml`:
   - missing → written as the current version's defaults (in P1: `kind: params`, `schema_version: 1`)
   - older version → upgraded through the migration chain and rewritten, keeping every existing value. DVC
     reads declared params straight from the file, so a key that exists only in memory would fail
     `dvc repro`. Comments are lost; the result says so with `params_rewritten`.
   - current → validated only; newer → `E_SCHEMA_TOO_NEW`
   - written with `yaml.safe_dump(sort_keys=False)`, `kind` and `schema_version` first
3. For every source in `catalog.missing_sources()`: `catalog.initialise(source)` (§3.4).
4. `vcs.track` the raw directories created in step 3.
5. No commit, as with `energatron denylist` (D2 §7.2). The result lists what to commit.

- Takes the lock. Result kind `pipeline_sync_result`:
  - `dvc_yaml_written: bool`
  - `params_written: bool` (created), `params_rewritten: bool` (upgraded)
  - `catalogs_created: list[str]` (sources)
  - `pointer_files: list[str]`, including `data/raw/.gitignore` when it changed
  - `dry_run: bool`
- **`--check`:** no lock, no writes. Fails with `E_PIPELINE_NOT_SYNCED` when any of these holds:
  - `dvc.yaml` is missing, or its bytes differ
  - `params.yaml` is missing, or holds an older version
  - `catalog.missing_sources()` isn't empty

  `details.drift` lists `dvc.yaml`, `params.yaml` and/or `catalogs`. This is what detects a drifted `dvc.yaml`
  (D1 §9.5). It reads the working tree; inside a commit hook, pre-commit has already stashed unstaged changes,
  so it sees what is being committed.
- **Workspace hook** (added to D2 §11.3, before `agent-links`):

  ```yaml
      - id: pipeline-sync
        name: pipeline in sync with the tool
        entry: uv run --frozen energatron pipeline sync --check
        language: system
        pass_filenames: false
        always_run: true
        stages: [pre-commit]
  ```

  `land` checks the same thing first, and also requires `dvc.yaml`, `params.yaml` and
  `.pre-commit-config.yaml` to be committed (§7.1 steps 2 and 4). That covers the known ways the hook could
  reject a land commit. If it rejects one anyway, `land` fails with `E_COMMIT_REJECTED`, and re-running after
  the fix heals the state (§7.5).

The golden `dvc.yaml` after P1 (`tests/fixtures/golden/dvc.yaml`) is:

```yaml
# Generated by energatron pipeline sync. Do not edit; re-run the command instead.
stages:
  catalog_index:
    cmd: energatron stage catalog_index
    deps:
    - data/raw/elec_bill/_catalog.jsonl
    - data/raw/gas_bill/_catalog.jsonl
    - data/raw/gas_bill_record/_catalog.jsonl
    - data/raw/offer_cdr/_catalog.jsonl
    - data/raw/offer_cdr_index/_catalog.jsonl
    - data/raw/offer_manual/_catalog.jsonl
    - data/raw/retailer_txn/_catalog.jsonl
    - data/raw/retailer_usage/_catalog.jsonl
    - data/raw/sapn_interval/_catalog.jsonl
    - data/raw/sapn_summary/_catalog.jsonl
    params:
    - .energatron/code_fingerprint.json:
      - stages.catalog_index.stage_sha256
    outs:
    - data/marts/catalog
```

It is emitted with `yaml.safe_dump(sort_keys=False)`; the golden file pins the exact bytes.

### 10.2 `params` (`core/models/params.py`, version 1)

```yaml
kind: params
schema_version: 1
```

- v1 holds no settings: the only P1 stage declares no params. `extra="forbid"`, as for every `Persisted`.
- Each phase that adds keys (P2: `meter.*`, P3: `analysis.*`, …) bumps the version and adds a migration that
  inserts its defaults.
- `params.yaml` is editable config, which D1 §5 upgrades on read. That isn't enough here, because DVC reads
  declared keys from the file itself. So `pipeline sync` rewrites an older file to the current version (§10.1
  step 2), and `pipeline sync --check` (and with it `land` and the commit hook) treats an older file as drift.
  Readers in energatron still upgrade in memory, as D1 §5 says.
- `prepare_stage` (D1 §9.1) validates the whole document through this model before it picks the declared keys.

### 10.3 Region profile (`regions/sa.yaml`, kind `region_profile`, version 1)

```yaml
kind: region_profile
schema_version: 1
name: sa
parsers: [elec_bill, gas_bill, offer_cdr, retailer_txn, retailer_usage, sapn_interval, sapn_summary]
```

- `build_context()` loads `regions/<workspace_config.region>.yaml` from package data and sets
  `ctx.parsers` to the `PARSERS` whose `source` is listed, in `PARSERS` order.
  - An unknown region → `E_INVALID_INPUT`, `details.field: "region"` (D2 §6.1).
  - A listed source with no parser → `E_INTERNAL`.
- Time zone, distributors and network tariffs join the profile with the P2 and P4 specs, as new versions.

---

## 11. Kinds, codes and warnings added in P1

**Persisted kinds** (each with a golden `tests/fixtures/schemas/<kind>/v1.json`): `catalog_record`,
`land_result`, `land_pending` (§7.3), `pipeline_sync_result`, `params`, `region_profile`, `catalog_table`
(Parquet metadata only), plus D1's `stage_provenance`, `code_fingerprint` and `repro_log_entry`.

| Code | Category (exit) | Raised by |
|---|---|---|
| `E_PIPELINE_NOT_SYNCED` | `state` (4) | `pipeline sync --check`, `land` precondition |
| `W_BACKUP_PENDING` | warning | `land` after a commit; `run` from P6 (D2 §8.4) |
| `W_PREMISES_UNCHECKED` | warning | §6 rule 4 |
| `W_UNKNOWN_IDENTIFIER` | warning | §6 rule 3 |

Every other code P1 raises exists in D1 §7 or D2.

---

## 12. Synthetic fixture generators

Fixtures are generated by code at test time and never derived from real files (plan §5, §13). The generators
live in `tests/fixtures/`, use only the stdlib, and are deterministic: the same arguments give the same bytes.
No generated file is committed; only goldens (`dvc.yaml`, schema documents) are.

**Synthetic identifiers** (`tests/fixtures/synthetic.py`): NMI `ZZTEST0001` (no `I` or `O`, as in real NMIs),
its checksum digit computed with `nmi_checksum()`, meter serial `SYN0000001`, account `900000000001`, and a
matching `PrivateConfig` builder. A test asserts the NMI's checksum digit, so the filename helper never builds an
invalid name by accident.

### 12.1 `tests/fixtures/make_nem12.py`

```python
@dataclass(frozen=True)
class QualityRange:                 # one 400 record
    start: int                      # 1-based interval index
    end: int
    method: str                     # e.g. "A", "S14", "E52", "F17"
    reason: str = ""

@dataclass(frozen=True)
class Nem12Spec:
    start: date
    days: int
    nmi: str = SYNTHETIC_NMI        # 10 characters, as in the 200 record
    meter_serial: str = SYNTHETIC_SERIAL
    suffixes: tuple[str, ...] = ("E1",)
    interval_minutes: int = 5       # 5, 15 or 30
    header_100: bool = False
    repeat_200_every: int | None = None    # repeat the 200 header every n days, SAPN-style
    blank_line_before_repeat: bool = True  # SAPN puts a blank line before each repeated 200
    v_days: Mapping[date, tuple[QualityRange, ...]] = field(default_factory=dict)
    update_dt: Callable[[date], datetime] | None = None   # default: the next day at 05:00:00
    seed: int = 0
    trailer_900: bool = True
    line_ending: str = "\n"

def make_nem12(spec: Nem12Spec) -> bytes: ...
def sapn_filename(kind: Literal["DETAILED", "SUMMARY"], nmi10: str, window: tuple[date, date],
                  generated: datetime, ext: str = "csv") -> str: ...   # adds the checksum digit
def sa_dst_dates(year: int) -> tuple[date, date]: ...   # first Sunday in April, first Sunday in October
def dst_spec(year: int, **kw) -> Nem12Spec: ...         # covers both changes, three days either side
```

- Values: `random.Random(seed)`, a base load plus a daily shape, three decimals, never negative. Tests read
  expected facts from the spec (coverage, days, `V` days), not from the values.
- Each `V` day gets its `400` records and QualityMethod `V`; other days get `A` with empty reason fields. The
  `300` field layout matches the real files (§1): 2 + values + 5.
- Malformed variants are made by the tests, by editing the generated text (drop the `900`, cut a `300`, add an
  unknown record type, mix interval lengths, duplicate a day), so the generator itself stays simple.
- A test pins `sa_dst_dates(2025)` as 6 April and 5 October 2025.

### 12.2 `tests/fixtures/make_sources.py`

| Function | Produces |
|---|---|
| `make_sapn_summary_csv(rows, *, nmi11, serial)` | the 11-field CRLF format of §5.3 |
| `make_retailer_usage_csv(months, *, account, serial, labels=("Off Peak", "Peak", "Shoulder"), blank_last=False)` | §5.4 |
| `make_retailer_txn_csv(entries)` | §5.5 |
| `make_pdf(*, creation_date: str \| None, xmp: str \| None = None, date_encoding: Literal["literal", "hex", "utf16"] = "literal", info_in_objstm=False, xmp_compressed=False, encrypted=False, truncated=False)` | a minimal valid one-page PDF. `info_in_objstm` puts the Info dictionary in a Flate-compressed `/ObjStm` (PDF 1.5 style); `xmp_compressed` deflates the XMP packet; `encrypted` adds an `/Encrypt` entry to the trailer |
| `make_cdr_plan_detail(*, plan_id, last_updated, fuel_type="ELECTRICITY", pretty=False)` | a plan-detail response with `data`, `links`, `meta`; `pretty` changes only whitespace, for the snapshot dedupe test |

---

## 13. Tests

Written first and run red, per the **Rules**. The D1 §13 tests marked "from P1" all apply; this table adds
P1's own. All values are synthetic (§12).

| Test | Covers |
|---|---|
| `tests/unit/core/test_catalog_record.py` | each §4.1 field rule; `dataset_id` and `stored_path` must recompute; naive and aware `source_ts` round-trip unchanged through a line; `from_json_line` errors carry the line number, not its text; written lines are canonical and end in `\n` |
| `tests/unit/core/test_premises.py` | each §6 rule: public sources skipped; wrong NMI → `E_FOREIGN_PREMISES`; bad checksum digit → `E_INVALID_INPUT`; known NMI with an unknown serial → only `W_UNKNOWN_IDENTIFIER`; no NMI with a known serial → confirmed; no NMI, unknown serial, non-empty list → `E_FOREIGN_PREMISES`; nothing to check → `W_PREMISES_UNCHECKED`; no value in any error or warning |
| `tests/unit/core/test_mask_filename.py` | SAPN names lose NMI and dates; a denylist entry inside a name is redacted; names without long digit runs are unchanged |
| `tests/unit/adapters/sources/test_<parser>.py`, one per §5 parser | `detect`: filename + content, content only, filename only, neither; `detect` gets only the first 64 KiB of a larger file; `inspect`: `source_ts` for every row of §3.3, coverage, `extra`, identifiers, extension; each `details.reason` from a malformed variant; `extra` never holds an identifier (asserted against the synthetic values) |
| `tests/unit/adapters/sources/test_sapn_nem12.py`, in addition | the NEM12 cases of plan §13, each built from `make_nem12` or by editing its output: no `100` record; a `100` record first, and one elsewhere (`bad_header`); repeated `200`s with blank lines (`header_records` counts them); `V` days with `400`s (`days_v`), and a `400` after an `A` day; 5-, 15- and 30-minute files, each with its field count, and a mixed file rejected; CRLF line endings and a BOM accepted; malformed rows: a `300` one field short or long, a bad date, a missing `UpdateDateTime`, an unknown record type, a missing `900` (`truncated`), a duplicate day; two suffixes (`E1`, `B1`), with `days` counting dates once; a renamed file (no filename evidence) takes `source_ts` from the `100` record, else the latest `UpdateDateTime`; a bad filename `gen` → `bad_filename` |
| `tests/unit/adapters/sources/test_pdf_meta.py` | `creation_date` from: a plain literal date, an escaped literal, a hex date, a UTF-16BE date, an Info dictionary in a compressed `/ObjStm`, plain and compressed XMP; offsets `Z`, `+10'30'`, `-05'00'` and none (naive); a truncated date filled with defaults; a stream that fails to inflate is skipped; no date → `None`; the inflate limit stops the search; and through the bill parser, an encrypted PDF → `encrypted` |
| `tests/unit/adapters/sources/test_registry.py` | one parser per source; every parser's `source` is a `SourceType`; the region profile filters `ctx.parsers` |
| `tests/unit/services/test_resolve_parser.py` | each §5.1 rule, including `--source` for a type with no parser and the three-way PDF tie |
| `tests/unit/services/test_land.py` | with fake ports and a `FixedClock`: every status; each §7.1 precondition, including an uncommitted `dvc.yaml`, `params.yaml` or `.pre-commit-config.yaml` → `E_DIRTY`; items handled one at a time: a copy of a rejected, badly named item, given later under a SAPN name, lands, and a copy of an accepted item is `duplicate_in_batch` naming its ID; a bad NMI checksum digit rejects only that item; the write order of §7.3, a null `file_mtime` sorting after the rest; a dry run writes nothing, returns the same statuses, and reports an uncatalogued raw file as `E_CATALOG_CORRUPT` exactly as the real run does; an orphan blob matching an accepted item is adopted; the pending marker holds the new IDs before the first `put`, the commit names them, and the marker is gone after a commit; a foreign item aborts with nothing written; mixed results → `E_PARTIAL` with the accepted items committed; one rejected input → its own error; `E_ID_COLLISION`; supersedes rules; the commit message in each §7.4 form; `track` gets every source; no commit when nothing is dirty; `W_BACKUP_PENDING` only after a commit; hidden files skipped and counted; symlinks rejected; the dumped result and captured logs contain no original file name, synthetic identifier or note |
| `tests/integration/test_land_cli.py` | in a scratch workspace with real git and DVC: `pipeline sync`, then `land` of two SAPN fixtures plus a byte copy of one → 2 landed, 1 `duplicate_in_batch`; one commit holding exactly the pointer files; blob modes match what §14 check 3 recorded (`0444` from `FsBlobStore` alone is covered by D1's `test_fs_blobs.py`); `dvc status` is clean; landing again → all `already_landed`, exit 0, HEAD unchanged; `--json` gives one envelope |
| `tests/integration/test_land_recovery.py` | faults injected after the marker, after `blobs.put`, after `catalog.append` and in `track`, and a commit rejected by a failing hook: in each case the re-run commits, naming the interrupted IDs, and leaves a clean tree and no marker; with the marker deleted by hand, the re-run commits `land: resync raw pointers`; a stray file in a source directory → `E_CATALOG_CORRUPT` with nothing written; after an uncommitted `pipeline sync`, `land` stops at `E_DIRTY` before writing |
| `tests/unit/adapters/test_locking.py`, in addition | `land` and `pipeline sync` fail with `E_LOCKED` while another process holds the lock; `pipeline sync --check` succeeds while it is held, as it does inside `land`'s commit hook |
| `tests/unit/adapters/test_fs_blobs.py`, `test_jsonl_catalog.py`, in addition | `keys(prefix)`: sorted, recursive, includes the catalog file, leaves out `.DS_Store`, a symlink → `E_CATALOG_CORRUPT`, an invalid prefix → `E_INTERNAL`; `missing_sources()` and `initialise()`: create an empty catalog, return `False` and change nothing when one exists |
| `tests/unit/services/stages/test_catalog_index.py` | every check passing and failing; zero-row table from empty catalogs; columns and types as §9; `superseded_by` and `is_current`; no `original_filename` or `notes`; a newer-version line → `E_SCHEMA_TOO_NEW` |
| `tests/unit/services/test_pipeline_sync.py` | creates missing catalogs and `params.yaml`; writes `dvc.yaml` only when it differs; with a test-registered `params` v2 and its migration, an older file is rewritten with the new default and its existing values kept, and `--check` reports it as drift; `--check` reports each kind of drift; `--dry-run` writes nothing; no commit |
| `tests/integration/test_pipeline_sync.py` | in a scratch workspace: the written `dvc.yaml` equals the golden file; `dvc repro` runs `catalog_index` (after `fingerprint`); the `pipeline-sync` hook rejects a commit after `dvc.yaml` is edited by hand |
| `tests/unit/adapters/test_wiring.py` | no `_NotYet` placeholder is left in `build_context()` (D2 §5.9); unknown region → `E_INVALID_INPUT` |
| `tests/unit/fixtures/test_generators.py` | same spec → same bytes; repeated `200`s with blank lines; `V` days with `400`s; field counts for 5, 15 and 30 minutes; synthetic NMI checksum; `sa_dst_dates(2025)` |
| `tests/integration/test_dvc_assumptions.py` | the §14 checks, with D1 §11.5's checks in `test_dvc_runner.py` |

---

## 14. To verify in P1 (DVC 3.67.1)

In addition to D1 §11.5. If any fails, stop and ask the owner; don't work around it silently.
1. A `dvc.yaml` stage can depend on `data/raw/<source>/_catalog.jsonl` while `data/raw/<source>` is tracked by
   its own `.dvc` file, and a new line in that catalog makes the stage stale.
2. `dvc add` of all ten raw directories in one call, with none changed, rewrites no pointer file.
3. The mode of a landed blob after `dvc add`, and after `dvc checkout` into a fresh clone. If DVC leaves
   checked-out files writable, record it: `0444` is then only an accidental-edit guard at landing, and the
   catalog's `sha256` plus DVC's hashes remain the integrity record. No code change.
4. `dvc add` on a directory holding `0444` files and empty `<YYYY>/` directories succeeds.

---

## 15. P1 acceptance checks

From plan §15, made concrete. Run in `demq-elektric/` by the agent; the backup is the owner's.

1. **Set-up:** `uv run energatron pipeline sync`, then `fingerprint` (D1 §11.3) and `dvc repro`. Commit
   `dvc.yaml`, `dvc.lock`, `params.yaml`, `.energatron/code_fingerprint.json`, `data/raw/*.dvc`,
   `data/raw/.gitignore`, `data/marts/.gitignore`, the `pipeline-sync` hook and the `CLAUDE.md` commands
   (§17) as one P1 set-up commit. `land` requires the hook config and the pipeline files to be committed
   (§7.1 step 4).
2. **Current SAPN files:** `uv run energatron land data/landing/ --json` → 2 `landed`, exit 0, one commit.
3. **Legacy files:**
   `uv run energatron land legacy/Electricity-Analysis/data/ legacy/Electricity-Analysis/raa/ --json`
   - the duplicate SAPN DETAILED file → `already_landed`, with the ID from step 2 (the hash check of plan §15)
   - the SAPN SUMMARY CSV and PDF, both usage exports and the transactions export → 5 `landed`
   - the derived hourly JSON in `data/` → `rejected`, `E_UNKNOWN_SOURCE`; so the command exits 8 with
     `E_PARTIAL`. That is expected: derived files are archived in `legacy`, not landed.
   - the transactions export gives `W_PREMISES_UNCHECKED`; nothing gives `E_FOREIGN_PREMISES`
4. **Totals:** 7 datasets: 2 `sapn_interval`, 2 `sapn_summary`, 2 `retailer_usage`, 1 `retailer_txn`.
5. **Re-landing is a no-op:** both commands again → every landed item `already_landed`, no commit, HEAD
   unchanged. (Step 3 still exits 8 for the JSON file.)
6. **Pipeline:** `fingerprint` and `dvc repro` → `catalog_index` reruns, every check passes, and
   `marts/catalog/datasets.parquet` has 7 rows. Commit `dvc.lock`.
7. **Clean state:** `dvc status` and `git status` are clean; `pipeline sync --check` passes; the privacy guard
   passes on `--tracked`.
8. **Backup:** `energatron backup --status` shows a backup pending; the agent asks the owner to run
   `icloud_backup.py backup`, and records the result.

The agent records the results in the workspace's `agent_docs/` when they mention data, and only counts and
pass/fail in `TASKS.md`.

---

## 16. Deferred

- **Clearing the inbox:** `land` never deletes inputs. A later spec (P7 at the latest) may add an option that
  removes inbox files already in the catalog. Until then the owner clears `data/landing/` by hand.
- **Per-format parsers:** a `name` on `SourceParser`, so two parsers can share a source type (another
  retailer's export, a second distributor). A D1 change, when the first second format arrives.
- **Blob integrity check:** a read-only `energatron catalog check` comparing every stored blob with its catalog
  `sha256`, or folding it into `verify` (D1 §11.4). D4 decides.
- **A read-only `energatron catalog list --json`** for agents (P9).
- `SourceParser.parse`, staged schemas → P2. Bill text extraction → P5. `offer_manual`, `offer_cdr_index` and the
  `fetch-offers` reuse of `land` → P4. `gas_bill_record` → P5.

---

## 17. Plan, D1 and D2 changes (for the next plan revision, rev 9)

To be added to the plan's Revisions header on `/done`, with these edits in place:
- **§8.2 (`source_ts` column):** `sapn_interval` and `sapn_summary`: the filename generation timestamp, with
  content fallbacks; retailer exports: the latest datetime in the content; bills: the PDF creation date.
- **§8.3 (catalog):**
  - the example drops `landed_by_commit`, and adds `file_mtime` and `detector.evidence`; `extra` per the P1 spec
  - the landing commit is found with `git log --grep <dataset_id>`; a pending marker carries the IDs across an
    interrupted landing
  - the NMI check becomes the premises check (NMI, meter serials, accounts)
  - a multi-file `land` lands what it can (exit 8); a foreign file aborts it
  - `offer_cdr` re-fetches with the same snapshot ID are no-ops
- **§9.2:**
  - `marts/catalog/` leaves out original file names and notes
  - the `params.yaml` example begins with `kind: params` and `schema_version: 1`. P1's v1 holds no keys; each
    block in the example arrives with its phase's spec, as a new version, and `pipeline sync` rewrites the file
- **§7 (workspace layout):** `data/raw/<source>/<YYYY>/<dataset_id><ext>`; `params.yaml` and `dvc.yaml` are
  written by `energatron pipeline sync`; the workspace pre-commit adds the `pipeline-sync` hook;
  `.energatron/tmp/land_pending.json` (git-ignored) exists only while a landing is unfinished.
- **§12 (runbooks):** after a tool update, `uv run energatron pipeline sync`; the `land` commit is
  `land: <ids>`.
- **§15, P1 row:** add `pipeline sync`, `stage`, `fingerprint`; the acceptance count is the datasets the
  owner's files hold, with the legacy duplicate detected by hash.

D1 and D2 are not edited; these notes stand beside them, and the first two join the list in `TASKS.md`
**Hand-over** under "Waiting for D1's next edit":
- D1 §4.1 and §4.2 (interface change, §3.4): `BlobStore.keys(prefix)`, `Catalog.missing_sources()` and
  `Catalog.initialise(source)`.
- D1 §5 (rewrite policy): `params.yaml` is rewritten by `pipeline sync` when its version is older, before P8's
  `migrate` (§10.2).
- D1 §4.8: `land`'s signature gains `supersedes` and `note`.
- D1 §9.2: the command that creates new empty catalogs is `energatron pipeline sync`.
- D2 §11.3: the workspace `.pre-commit-config.yaml` gains the `pipeline-sync` hook (§10.1).
- D2 §10.1 (workspace `CLAUDE.md`, Commands): add `uv run energatron land <path>…` and
  `uv run energatron pipeline sync`, in P1.

---

## 18. Open questions for the owner

None. The choices most worth a look in review:
1. Partial landing with exit 8 versus all-or-nothing (§7.2, §8.3).
2. Retailer exports ordered by content date, then file time, since they carry no export date (§3.3).
3. `land` leaves inbox files in place (§16).
