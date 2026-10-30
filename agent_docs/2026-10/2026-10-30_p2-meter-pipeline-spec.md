# P2 meter pipeline spec

- **Created:** 2026-10-30
- **Status:** Approved 2026-10-30 (owner, `/done`), with the recommendations for Q1–Q9 as written, except Q6: no
  waiver of hand-over decision #11. Its §16 edits are in the plan as rev 10 and in D1 as rev 3
- **Deliverable:** P2.1.1 in `TASKS.md`
- **Builds on:** [`2026-10-23_data-pipeline-plan.md`](2026-10-23_data-pipeline-plan.md) rev 9 ("the plan"),
  [`2026-10-23_core-architecture-spec.md`](2026-10-23_core-architecture-spec.md) rev 2 ("D1", binding for all code)
  and [`2026-10-23_p1-landing-catalog-spec.md`](2026-10-23_p1-landing-catalog-spec.md) ("D3")
- **For:** the agent or person implementing P2; `/done` of P2.1.1 rewrites P2's sub-tasks from §2
- **Privacy note:** contains no identifiers or usage figures, and nothing added to it may. Facts about the
  workspace files (§1) were read by structure only: counts, record types, interval lengths, column names and
  quality categories. Examples use placeholders and synthetic values.

---

## 1. What this spec fixes

P2 turns the landed SAPN interval files into one merged, checked, local-time interval table, and decides with
evidence which clock the meter data uses. It builds five stages: `parse_nem12`, `merge_interval`,
`validate_meter`, `validate_timebase` and `localise`. This spec gives every model, rule, check and test they
need, so P2 can be built without design decisions. "Must" means required. D1 and D3 stay binding; where this spec
changes them, §16 lists the change.

**What P2 builds**
- core: the format readers `core/nem12.py`, `core/sapn_summary.py` and `core/retailer_usage.py`, with the text
  helpers they share in `core/text.py` (§4), used by the P1 parsers and the P2 stages; `core/timebase.py` (§9);
  `Params` v2 (§5.1); `RegionProfile` v2 (§5.2); dataset precedence (§3.3); P2's warnings (§12)
- services: the five stages in `services/stages/`; `StageRun.zone()` in `runner.py` (§6); `pipeline sync`
  fills `meter.local_tz` and the TOU windows from the region profile (§5.3)
- adapters: the three P1 parsers' `inspect` move onto the core readers, unchanged in behaviour (§4.4)
- a new explicit runtime dependency, `tzdata` (§6)

**Facts about the workspace files** (read on 2026-10-30 by structure only, through a throwaway script):
- **SAPN DETAILED** (two datasets):
  - Every `200` record has 5-minute intervals and one suffix, `E1`; one NMI across both files.
  - `300` QualityMethod is `A` or `V`; reason fields are empty on every `300`. No `500` records. No empty
    values, no negative values; every value is written with many decimal places.
  - `400` records occur only after `V` days. Their methods are `A` and `F` plus two digits; reason fields are
    empty. On every `V` day the `400` ranges cover intervals 1…288 exactly once, in order.
  - The two files overlap. On every overlapping day the `UpdateDateTime`, the values and the per-interval
    quality are identical, so the real data has no merge conflicts; the conflict path is tested on synthetic
    data only (§13).
  - The merged coverage has no missing day.
- **SAPN SUMMARY CSV** (one dataset; the PDF isn't read in P2):
  - Each row is one period: whole calendar months, except a partial first and last row. Rows don't overlap.
  - Field 4 (quality) takes the values `A` and `AF`. Fields 7–10 are numbers with three decimals.
  - **Field 7 is the period's kWh total**: on every row it equals the sum of the interval values over the row's
    meter dates (the `300` dates, start and end inclusive) within ±0.01 kWh. The meaning of fields 8–10 isn't
    established, and P2 doesn't use them.
- **Retailer usage export** (two datasets, the same read datetimes):
  - Three registers, labelled `Off Peak`, `Peak`, `Shoulder`; one row per read, at 00:00; rows in ascending
    order. One export has empty cells in its newest row. Values have up to four decimals; `Total` equals the sum
    of the registers on every complete row.
  - **A row read at date D covers the meter dates after the previous read up to and including D.** With that
    alignment most periods' `Total` equals the interval sum over those meter dates (on the meter clock), and no
    other day offset comes close.
  - Recomputing each register under the plan's three time-basis hypotheses (§9) with the TOU windows plan §3.3
    records for the retailer's SAPN tariff: one hypothesis matches every register of several periods within 0.05 kWh; the other two match none.
    Some later periods match no hypothesis at all while their `Total` still reconciles, which is what a change of
    TOU windows during the export's span looks like. So the stage must tolerate unexplained periods (§9.4).

---

## 2. Order of work

Each step needs the ones before it. `/done` of P2.1.1 turns the rows into sub-tasks P2.2.1–P2.2.7 and P2.3.1,
replacing the provisional list; the owner's backup stays P2.3.2.

| # | Sub-task | Builds | Sections |
|---|---|---|---|
| 1 | P2.2.1 Foundations | `Params` v2 and migration, `RegionProfile` v2 and migration, `sa.yaml` v2; `pipeline sync` fills and checks `meter.local_tz` and the TOU windows; `tzdata` dependency; `StageRun.zone()`; the stage-purity additions; P2 warnings; golden schemas | §5, §6, §12, §13.1 |
| 2 | P2.2.2 Format readers | `core/text.py` (the pure helpers moved out of `adapters/sources/_common.py`, §4), `core/nem12.py`, `core/sapn_summary.py`, `core/retailer_usage.py`; the three parsers' `inspect` moved onto them with their P1 tests unchanged; precedence (§3.3); fixture generator additions and the test oracle (§13.2) | §3.3, §4, §13.2 |
| 3 | P2.2.3 `parse_nem12` | the stage, its tables, the golden `dvc.yaml` | §7.1 |
| 4 | P2.2.4 `merge_interval` | the stage, the conflicts report | §7.2 |
| 5 | P2.2.5 `validate_meter` | the stage and its report | §8 |
| 6 | P2.2.6 `validate_timebase` | `core/timebase.py` and the stage | §9 |
| 7 | P2.2.7 `localise` | the stage; the end-to-end and tzdata integration tests | §10, §13 |
| 8 | P2.3.1 Acceptance | in the real workspace | §14 |

`Params` (P2.2.1) and `precedence_order` (P2.2.2) sit in modules that `runner.py` imports, and `runner.py` is in
every stage's module set (D1 §9.1, §11.1). So each of those two sub-tasks changes the code entry of every registered
stage, `catalog_index` included, and `fingerprint --check` reports them stale until `energatron fingerprint` is
re-run. That is expected, not a fault.

Each stage sub-task updates `tests/fixtures/golden/dvc.yaml` (D1 §9.5) and the fixture workspace (§13.2) for the
stage it adds, so `test_determinism` and `test_stage_loads` cover it from the start.

---

## 3. Inputs, keys and precedence

### 3.1 How a stage reads raw data

D1 §14 leaves open how a stage reaches parser code. **The answer: format reading lives in core.**
- `adapters/sources/` stays where detection, `inspect` and the registry live. Stages can't import it (D1 §3).
- The code that turns a file's bytes into records moves to pure core modules (§4). The parsers' `inspect` calls
  them; the stages call them too. So a reader is in each stage's module set through an ordinary static import
  (D1 §11.1), and a file that landed is read by the same code that accepted it.
- **`SourceParser.parse` is not added** (a D1 change, §16): nothing outside a stage would call it, and stages
  can't reach the port. D1 §2 already reserves `core/nem12.py`; this spec adds the other readers beside it.
- **Accepted trade-off (§17 Q3).** D1 §4.3 says parsers are format-specific plugins chosen through a registry, and
  plan §14.1 keeps meter-file formats in a parser registry. A stage that reads the portal's format through a core
  module, and names its source directory in its `SPEC`, doesn't go through the registry. That is acceptable while
  each source has one format (D3 §16 defers per-format parsers); when a second format arrives, the reader choice
  moves into a region-profile setting and that spec reworks these stages. D1 rev 3 rewrites the §4.3 bullet to say
  so (§16).

A stage that reads a raw source declares the source directory as one input, e.g.
`raw: data/raw/sapn_interval`. The whole directory is the DVC dependency, and D1 §10 already resolves its
`dataset_ids` from the catalog in it.
- The stage reads `<input>/_catalog.jsonl` with `core.models.catalog.parse_catalog(data, source)`.
- A record's blob is `<input>/<stored_path minus "data/raw/<source>/">`, so `--input` overrides work.
- Each blob's `sha256` is checked against its record (check `blobs_match_catalog`); a mismatch fails the stage,
  naming the `dataset_id`.
- A record whose `detector.name` isn't the expected source, or whose `detector.version` the reader doesn't know
  (P2 knows version 1 of each), fails check `detector_known`.
- Superseded datasets (some record's `supersedes` names them) are read but not used downstream (§7.2).

### 3.2 Clocks and keys

- **Meter clock:** the naive date and time the NEM12 file states. Interval *i* (1-based) of a `300` record dated
  *d* covers `[d 00:00 + (i−1)·L, d 00:00 + i·L)` on the meter clock, with *L* the interval length in minutes.
  Every staged and merged table keys intervals by `meter_start`, naive, because which real clock that is stays
  unknown until `validate_timebase` (§9). The plan's `interval_start_nem` / `interval_end_nem` become
  `meter_start` plus `interval_minutes` (§16).
- **Meter date:** the `300` record's date, `meter_date`. SUMMARY periods and retailer periods are both counted
  in meter dates (§1).
- **Register:** the `200` record's NMI suffix, column `suffix`. Consumption registers start with `E`.
- **No NMI or serial in any table** (§17 Q2). The premises check at landing already ties every dataset to the
  owner's NMI, and a stage never reads an identifier (§4). There is no NMI check in any stage, and
  none is needed: landing's premises check is what keeps another premises' NMI out. A second NMI that slipped past
  it would be merged silently unless its dates overlap an existing one's on the same suffix (then `key_unique` in
  `merge_interval` fails); a stage can't tell the two cases apart, because it never sees an identifier. The plan's merge key
  `(nmi, suffix, interval_start)` becomes `(suffix, meter_start)`.
- **Local clock:** after `localise`, each interval also has `utc_start` (aware, UTC) and `local_start` (naive wall
  clock in `meter.local_tz`) with its `utc_offset_minutes`, which tells the repeated hour of a DST fall-back
  apart.

### 3.3 Dataset precedence (`core/models/catalog.py: precedence_order`)

D3 §3.3 sets precedence between datasets of one source: newer `source_ts`, then newer `file_mtime`, then later
catalog position. P2 makes it a function:

```python
def precedence_order(records: Sequence[CatalogRecord]) -> list[str]:
    """Dataset IDs, highest precedence first."""
```

- `source_ts` compares through `compact_source(source_ts)` (D1 §6.2), so naive and aware values order.
- A null `file_mtime` ranks below every non-null one.
- Position is the record's index in `records` (catalog order); later wins.
- Pure and total: no two records tie.

---

## 4. Format readers (core)

All three are pure: bytes in, frozen models or a DataFrame out, no I/O. They decode as D3 §5 says (UTF-8, a
leading BOM dropped, LF or CRLF) and raise `E_INVALID_INPUT` with the same fixed `details.reason` words the
parsers raise today.

**Identifiers.** Each reader has two entry points. The `scan_*` function that `inspect` calls returns the facts
plus the identifiers (NMIs, meter serials, accounts) for the premises check. The `read_*` function that stages call
returns the same facts and the data, and **no identifier**: its result types have no identifier field, so a stage
cannot see one. Both are built on one private walk, so they accept exactly the same files.

**Shared helpers move to core (`core/text.py`).** The readers need the decoding, line-splitting, CSV and decimal
helpers that are in `adapters/sources/_common.py` today, which core can't import. P2.2.2 moves these to
`core/text.py`: `BOM`, `lines`, `decode`, `csv_rows`, `is_decimal`, `parse_datetime` and `fail` (which takes a
`SourceType`, already in core). `_common.py` keeps what only detection needs (`head_lines`, `first_line`,
`window_json`, `sapn_filename_facts`) and imports the moved names from core; the parsers import them from core
directly. The helpers' behaviour doesn't change, and their tests move with them.

### 4.1 `core/nem12.py`

```python
@dataclass(frozen=True)
class Nem12Facts:                   # what inspect and the stages both use, in D3 §5.2's terms; no identifiers
    interval_minutes: int
    suffixes: tuple[str, ...]       # sorted
    days: int                       # distinct 300 dates
    days_v: int
    header_records: int
    header_dt: datetime | None      # the 100 record's DateTime
    latest_update: datetime
    coverage: Coverage

@dataclass(frozen=True)
class Nem12Scan:                    # returned to inspect only
    facts: Nem12Facts
    nmis: tuple[str, ...]           # identifiers: the premises check only
    serials: tuple[str, ...]

@dataclass(frozen=True)
class Nem12Read:                    # returned to stages: no identifier field
    facts: Nem12Facts
    table: pd.DataFrame             # the staged columns of §7.1 except dataset_id
    v_coverage_faults: int          # V-day intervals covered by no 400 range, or by more than one

def scan_nem12(data: bytes) -> Nem12Scan: ...        # D3 §5.2's record checks, in its order
def read_nem12(data: bytes) -> Nem12Read: ...        # the same checks, then every interval
```

- The shared walk is today's `_scan` logic from `sapn_nem12.py`, moved. `read_nem12` runs it, then reads each
  `300` and its `400`s:
  - each value parses as a decimal (`float`); anything else → `E_INVALID_INPUT`, `reason: "bad_value"`. Negative
    values are kept: the stage checks them (§7.1).
  - a day whose QualityMethod doesn't start with `V` gives every interval its QualityMethod and ReasonCode.
  - a `V` day gives each interval the QualityMethod and ReasonCode of the `400` range that covers it. An interval
    covered by no range keeps quality `V` and counts in `v_coverage_faults`; one covered by two ranges takes the
    later range and counts too. (`inspect` v1 accepts both, so reading mustn't reject them; the stage fails its
    check instead.)
  - ReasonCode is an integer or empty; ReasonDescription and MSATSLoadDateTime are not kept (free text and an
    unused field).
  - `500` records are skipped.
- Errors carry `reason` only, never content (D3 §5).

### 4.2 `core/sapn_summary.py`

```python
@dataclass(frozen=True)
class SummaryRow:
    start: date; end: date          # inclusive meter dates
    quality: str                    # field 4, e.g. "A", "AF"
    kwh: Decimal                    # field 7, the period's total (§1); compared as float(kwh), see §8

def scan_summary_csv(data: bytes) -> SummaryScan:   # rows, plus nmis and serials: inspect only
def read_summary_csv(data: bytes) -> SummaryRead:   # rows in file order; no identifier field
```

D3 §5.3's CSV checks, moved. Fields 8–10 are validated as decimals (as today) and not returned.

### 4.3 `core/retailer_usage.py`

```python
@dataclass(frozen=True)
class UsageRow:
    read_dt: datetime               # naive
    values: Mapping[str, Decimal | None]   # TOU label as written → kWh; None for an empty cell
    total: Decimal | None

def scan_usage_export(data: bytes) -> UsageScan:  # labels, rows, plus accounts and serials: inspect only
def read_usage_export(data: bytes) -> UsageRead:  # labels (sorted), suffixes, rows; no identifier field
```

D3 §5.4's checks, moved. A register column's label is the text after ` - ` inside the parentheses, as
`extra.tou_labels` already records it.

### 4.4 The parsers stay version 1

`sapn_nem12.py`, `sapn_summary.py` and `retailer_usage.py` call the `scan_*` functions from `inspect`. Their behaviour,
`details.reason` words, `extra` and identifiers must not change, so every P1 parser test passes unchanged and
`version` stays 1. If any behaviour has to change, stop and ask: it would be a parser version bump (D1 §4.3) with
already-landed datasets recorded under version 1.

---

## 5. Configuration

### 5.1 `params` v2 (`core/models/params.py`)

```yaml
kind: params
schema_version: 2
meter:
  timebase: nem_aest              # nem_aest | local_wall | local_standard (§9.1)
  local_tz: <zone key>            # filled by pipeline sync from the region profile (§5.3)
validation:
  meter:
    total_tolerance_kwh: 0.01     # plan §9.2
  timebase:
    windows:                      # the TOU label in the retailer export → local-time windows
      <label>: ["HH:MM-HH:MM", ...]   # filled by pipeline sync from the region profile (§5.3)
    register_tolerance_kwh: 0.05
    register_tolerance_pct: 0.1
    total_tolerance_kwh: 0.05
    min_fit_periods: 3
```

- Nested frozen models, `extra="forbid"`. Tolerances are non-negative; `min_fit_periods` ≥ 1.
- `meter.local_tz` and `validation.timebase.windows` are **region-specific, so core holds no default for them**
  (plan §14.1: no region-specific values in code). Their type allows `null`, which only a migration produces
  (below); `pipeline sync` replaces every `null` from the region profile, and the commit hook and `land` treat a
  `null` as drift. A stage that reads one of them while it is `null` fails with `E_INVALID_INPUT`, `details.field`
  naming it, and the message "run `energatron pipeline sync`".
- `windows`: each entry is `HH:MM-HH:MM`, start inclusive, end exclusive, wrapping past midnight when end ≤
  start (`24:00` is not allowed; use `00:00`). Every minute boundary is a multiple of 5. **The windows must
  partition the day**: every minute of 00:00–24:00 in exactly one label; otherwise validation fails
  (`E_INVALID_INPUT`, `details.field: "validation.timebase.windows"`).
- **Migration v1 → v2** is pure and has no region. It inserts `meter.timebase: nem_aest` (NEM time is the same in
  every NEM region, §5.2), the tolerances and `min_fit_periods` above, and `null` for `local_tz` and `windows`.
- **Current-version defaults** (a missing `params.yaml`) are built by `Params.defaults(profile: RegionProfile)`,
  which takes the zone and windows from the profile it is given.
- `params.yaml` stays non-personal (plan §5): tolerances, a time zone, public tariff windows.

### 5.2 Region profile v2 (`core/models/region.py`, `regions/sa.yaml`)

```yaml
kind: region_profile
schema_version: 2
name: sa
local_tz: Australia/Adelaide
tou_windows:                        # plan §3.3: the retailer's windows for this network
  Peak: ["06:00-10:00", "15:00-01:00"]
  Off Peak: ["01:00-06:00"]
  Shoulder: ["10:00-15:00"]
parsers: [elec_bill, gas_bill, offer_cdr, retailer_txn, retailer_usage, sapn_interval, sapn_summary]
```

- The zone and windows are package data in the profile, which is where plan §14.1 puts region-specific values.
  `core` and `services` contain neither, and a test enforces it (`test_no_region_values`, §13.1).
- `local_tz` must be a key in the installed `tzdata` (checked when `build_context()` loads the profile:
  `E_INTERNAL` for package data that names an unknown zone or leaves it `null`). `tou_windows` obeys the same rules as `windows`
  in §5.1, including the partition.
- **Migration v1 → v2** is pure and has no region data, so, as for `params`, the v2 model allows `null` for
  `local_tz` and `tou_windows`, and the migration inserts `null` for both. The golden `region_profile/v1.json`
  therefore upgrades and validates, and D1 §13's `test_golden_v1_documents_upgrade_and_validate` passes with no
  exemption. A profile with a `null` value is unusable: `build_context()` rejects it (`E_INTERNAL`, package
  data), and `sa.yaml` ships v2 with both set.
- NEM time (UTC+10, no daylight saving) is the same for every NEM region, so it is a core constant
  (`core/timebase.py: NEM_UTC_OFFSET`), not a profile field.

### 5.3 `pipeline sync`, the time zone and the windows

The zone and windows belong to the region (D3 §10.3); stages can only see params (D1 §9.4). So `params.yaml`
carries copies, and `pipeline sync` fills them from `ctx.region`:
- writing or upgrading `params.yaml` sets `meter.local_tz` and `validation.timebase.windows` from the profile
- a current file whose `local_tz` differs from the profile's is rewritten with only that value changed
  (`params_rewritten: true`): the zone is the region's, not the owner's
- a `windows` value is replaced only while it is `null`: the owner may edit it (the retailer's windows can differ
  from the profile's default), and `pipeline sync` keeps an edited value
- `--check` reports a `null` value, or a differing `local_tz`, as `params.yaml` drift, so the commit hook and
  `land` catch both
- every other `meter.*` and `validation.*` value is the owner's to edit; `pipeline sync` keeps it

`pipeline_sync_result` is unchanged (v1). `pipeline sync` needs the profile, which only `wiring.py` loads today
(D3 §10.3, `ctx.parsers` is derived from it). So `ServiceContext` gains `region: RegionProfile`, set by
`build_context()`; this is a D1 §4.8 change (§16).

---

## 6. Time-zone data: `tzdata` and `StageRun.zone()`

D1 §14: `zoneinfo` may read the macOS system database, which no code entry covers. P2 reads the zone from the
`tzdata` distribution instead.
- `tzdata` becomes an explicit runtime dependency (`uv add tzdata`, project-local), so its version is pinned in
  `uv.lock`. It is **already** in every stage's code entry on this machine, because D1 §11.1 step 4 follows
  requirements recursively and pandas requires `tzdata`. The explicit dependency makes that independent of pandas'
  platform markers; it doesn't make the entry stage-specific, and nothing here needs it to be. A `tzdata` upgrade
  therefore makes every stage stale, which is correct: the zone rules are data every stage's code could read.
- `StageRun` gains `zone(key: str) -> ZoneInfo` (`runner.py`):
  - `key` must match `^[A-Za-z0-9_+-]+(/[A-Za-z0-9_+-]+)*$`, else `E_INVALID_INPUT`, `details.field:
    "meter.local_tz"`.
  - it opens the zone file through `importlib.resources.files("tzdata").joinpath("zoneinfo", *key.split("/"))` and
    returns `ZoneInfo.from_file(file, key=key)`; a missing file → the same `E_INVALID_INPUT`.
  - This is the second named use of `importlib.resources` in `runner.py`, beside `package_data()` (D1 §9.4).
- Stage code converts time zones **only** with zone objects from `run.zone()`, passed as objects to `core` and to
  pandas. `test_stage_purity` adds (§13.1): no `ZoneInfo(...)` construction and no `zoneinfo.available_timezones`,
  no `pytz` or `dateutil.tz` import, and no string literal or `str` argument as `tz=` / the first argument of
  `tz_localize` / `tz_convert` in a stage's module set. That is what keeps a stage off the system database.
  An integration test runs `localise` with a deliberately wrong system zone file and expects identical outputs
  (§13.3).

---

## 7. Parsing and merging

### 7.1 `parse_nem12` (`services/stages/parse_nem12.py`)

```python
SPEC = StageSpec(
    name="parse_nem12",
    inputs={"raw": "data/raw/sapn_interval"},
    params=(),
    outputs={"staged": "data/staged/elec_interval"},
    impl=_run,
)
```

**Body.** Read the catalog (§3.1). For each record in catalog order: check the blob, `read_nem12`, add
`dataset_id`, write `<dataset_id>.parquet`. A dataset that raises `E_INVALID_INPUT` is left out and named in
the `datasets_read` check with its `reason`. Then write `index.parquet`.

**`<dataset_id>.parquet`** (kind `elec_interval_staged`, version 1, `sort_by=["suffix", "meter_start"]`):

| Column | Type | Rule |
|---|---|---|
| `suffix` | string | `200` field 5 |
| `meter_date` | date32 | the `300` date |
| `meter_start` | timestamp[us], naive | §3.2 |
| `interval_minutes` | int16 | 5, 15 or 30 |
| `kwh` | float64 | the value as read |
| `quality` | string | the QualityMethod in force for the interval (§4.1), e.g. `A`, `F16`, `E52`, `V` |
| `quality_flag` | string | its first character |
| `reason_code` | int16, nullable | |
| `update_dt` | timestamp[us], naive | the day's `UpdateDateTime` |
| `dataset_id` | string | |

**`index.parquet`** (kind `elec_interval_index`, version 1, `sort_by=["precedence"]`), one row per catalogued
dataset: `dataset_id`, `precedence` (int32, 1 = highest, §3.3), `is_current` (bool: no record supersedes it),
`read_ok` (bool), `interval_minutes` (int16, nullable), `suffixes` (list<string>), `days` (int32),
`coverage_start`, `coverage_end` (date32, nullable).

**Checks** (counts and dataset IDs in `details`, never values):
- `blobs_match_catalog`, `detector_known` (§3.1)
- `datasets_read`: every dataset read
- `values_per_day`: every `(dataset, suffix, meter_date)` has exactly `1440 / interval_minutes` rows (the plan's
  check; true by construction, kept as a guard)
- `units`: every `200` unit is `KWH` (guard; `inspect` rejects others)
- `v_days_covered`: `v_coverage_faults` is 0 for every dataset
- `non_negative`: no `kwh` < 0
- `catalog_agrees`: each dataset's `Nem12Facts` coverage, `days`, `days_v` and `interval_minutes` equal its catalog
  `extra` and `coverage`

An empty catalog gives an empty `index.parquet` and passes every check.

### 7.2 `merge_interval` (`services/stages/merge_interval.py`)

```python
SPEC = StageSpec(
    name="merge_interval",
    inputs={"staged": "data/staged/elec_interval"},
    params=(),
    outputs={"interval": "data/interim/elec_interval_nem", "report": "data/reports/validation/merge"},
    impl=_run,
)
```

**Rule.** Candidates are the current datasets (`is_current and read_ok`). For each `(suffix, meter_date)` held by
one or more candidates, keep the whole day from the candidate with the newest `update_dt`, ties broken by
`precedence`. `UpdateDateTime` is per day (plan §3.2), so a per-day choice is the plan's per-interval rule.

**`interval/intervals.parquet`** (kind `elec_interval_nem`, version 1, `sort_by=["suffix", "meter_start"]`): the
§7.1 columns of the kept rows; `dataset_id` names the dataset each day came from.

**`report/conflicts.parquet`** (kind `merge_conflicts`, version 1, `sort_by=["suffix", "meter_date",
"dropped_dataset_id"]`): one row per `(suffix, meter_date, dropped candidate)` whose values or qualities differ
from the kept day: `suffix`, `meter_date`, `kept_dataset_id`, `dropped_dataset_id`, `kept_update_dt`,
`dropped_update_dt`, `intervals_differing` (int16), `quality_differing` (int16), `kept_kwh`, `dropped_kwh` (the
day totals, float64, `math.fsum`), `kept_flags`, `dropped_flags` (sorted distinct quality flags, string).

**`report/summary.json`** (kind `merge_summary`, version 1): `datasets_used`, `datasets_skipped` (superseded or
unread, by reason), `days` per suffix, `overlap_days` (days held by more than one candidate), `conflict_days`,
`conflict_intervals`, `same_update_conflicts` (conflict days whose `update_dt` are equal, decided by precedence
alone).

**Checks:** `key_unique` on `(suffix, meter_start)`; `one_interval_length`: all candidates share one interval
length per suffix (resampling a meter that changed its interval length is deferred, §15). A conflict is a
finding, not a failure. **Warning** `W_MERGE_SAME_UPDATE` when `same_update_conflicts` > 0.

---

## 8. `validate_meter` (`services/stages/validate_meter.py`)

```python
SPEC = StageSpec(
    name="validate_meter",
    inputs={"interval": "data/interim/elec_interval_nem", "summary": "data/raw/sapn_summary"},
    params=("validation.meter",),
    outputs={"report": "data/reports/validation/meter"},
    impl=_run,
)
```

**Summary rows.** From the current `sapn_summary` datasets with `extra.format == "csv"` (D3 §5.3), read with
`read_summary_csv`. Rows with the same `(start, end)` in several datasets: the highest-precedence dataset's row
is used; if their `kwh` differ the row's `summary_disagreement` is true. The same `(start, end)` twice in one file
fails `summary_rows_unique` (multi-register summaries are unknown, §15).

**Comparison.** A row is *covered* when every meter date `start…end` is present in the merged table for every
consumption suffix. The meter total is `math.fsum` of `kwh` over those dates and those suffixes. The summary
total is `float(SummaryRow.kwh)` (the `Decimal` is converted once, here, so the comparison is float against float).
Status: `match` when `|meter − summary| ≤ total_tolerance_kwh`, `mismatch` otherwise, `not_covered` when not covered.

**Outputs** (all in `report/`):
- `summary_compare.parquet` (kind `meter_summary_compare`, v1, sorted by `period_start`, `period_end`):
  `dataset_id`, `period_start`, `period_end`, `summary_quality`, `summary_kwh`, `meter_kwh` (null when not
  covered), `diff_kwh`, `status`, `summary_disagreement`
- `gaps.parquet` (kind `meter_gaps`, v1, sorted by `suffix`, `gap_start`): per suffix, each run of missing meter
  dates between that suffix's first and last date: `gap_start`, `gap_end`, `days`
- `quality_monthly.parquet` (kind `meter_quality_monthly`, v1, sorted by `suffix`, `month`, `quality_flag`):
  per suffix, meter month (`YYYY-MM`) and quality flag: `intervals`, `kwh`, `share` (of that month's intervals)
- `report.json` (kind `meter_report`, v1): row counts by status, `gap_days`, `intervals` per suffix,
  `first_date` / `last_date` per suffix, and the share of intervals with flag `A`

**Checks:** `monthly_totals_match` (no `mismatch`), `summary_rows_unique`.
**Warnings:** `W_METER_GAPS` (`details.gap_days`) when any gap exists; `W_METER_UNVALIDATED` when no row is
covered (no SUMMARY CSV landed, or none overlaps the data). Neither fails the stage: SUMMARY is evidence the owner
may not have, and a gap is a fact to report (plan §10.1).

---

## 9. Time basis

### 9.1 Hypotheses (`core/timebase.py`)

```python
class Timebase(StrEnum):
    NEM_AEST = "nem_aest"              # H1: meter time is UTC+10 all year
    LOCAL_WALL = "local_wall"          # H2: meter time is already the local wall clock
    LOCAL_STANDARD = "local_standard"  # H3: meter time is local standard time all year

NEM_UTC_OFFSET: Final = timedelta(hours=10)

@dataclass(frozen=True)
class Window:                          # one parsed "HH:MM-HH:MM" entry
    start_minute: int                  # minutes since local midnight, 0…1435
    end_minute: int                    # exclusive; the window wraps past midnight when end_minute <= start_minute

@dataclass(frozen=True)
class LocalTimes:                      # one entry per input row, same index
    utc_start: pd.Series               # datetime64[us, UTC]; NaT where unplaceable
    local_start: pd.Series             # naive wall clock in the zone; NaT where unplaceable
    utc_offset_minutes: pd.Series      # Int16; the zone's offset at that instant; <NA> where unplaceable
    unplaceable: pd.Series             # bool

def parse_windows(windows: Mapping[str, Sequence[str]]) -> dict[str, tuple[Window, ...]]: ...   # §5.1's rules
def to_local(meter_start: pd.Series, basis: Timebase, zone: tzinfo) -> LocalTimes: ...
def bucket(local_start: pd.Series, windows: Mapping[str, Sequence[Window]]) -> pd.Series: ...  # label per row
```

- `nem_aest`: `meter_start` at `NEM_UTC_OFFSET` gives `utc_start`; `local_start` is that instant in `zone`.
- `local_standard`: the zone's **standard offset** on a date is `utcoffset − dst` of the zone at 12:00 wall time on
  that date (`fold=0`), taken once per distinct `meter_date`. `meter_start` at that offset gives `utc_start`.
- `local_wall`: `meter_start` read as wall time in `zone`. A time that doesn't exist (spring forward) or occurs
  twice (fall back) can't be placed: `to_local` marks those rows `unplaceable`, and `validate_timebase`, which
  needs only the wall time, never calls it for this basis.
- `bucket` assigns each interval to the window containing its **local start**. With 5-minute data and window
  edges on 5-minute boundaries no interval straddles an edge. For 15- and 30-minute data an edge that isn't a
  multiple of the interval length in local time would split intervals: `validate_timebase` fails
  `windows_align` instead of guessing.
- `zone` is always the object from `StageRun.zone()` (§6).

### 9.2 `validate_timebase` (`services/stages/validate_timebase.py`)

```python
SPEC = StageSpec(
    name="validate_timebase",
    inputs={"interval": "data/interim/elec_interval_nem", "usage": "data/raw/retailer_usage"},
    params=("meter.local_tz", "meter.timebase", "validation.timebase"),
    outputs={"report": "data/reports/validation/timebase"},
    impl=_run,
)
```

The module gets its zone from `StageRun.zone()` (§6). While `meter.local_tz` or `validation.timebase.windows` is
`null` the stage fails with `E_INVALID_INPUT` (§5.1).

### 9.3 Periods

1. Read every current `retailer_usage` dataset with `read_usage_export`. For each read datetime, take the row from
   the highest-precedence dataset whose row is complete (no empty cell); rows that are incomplete everywhere are
   dropped and counted (`incomplete`).
2. Sort the read datetimes. A read not at 00:00 is dropped (`not_midnight`).
3. Each consecutive pair `(r₀, r₁)` gives a period of meter dates `r₀.date + 1 … r₁.date` (§1). The first read
   opens the series and is no period.
4. A period is **used** when every interval of those meter dates is present for every consumption suffix
   (otherwise `not_covered`), and `|Σ kwh − Total| ≤ total_tolerance_kwh` (otherwise `total_mismatch`). The
   total check is what confirms the alignment; it doesn't depend on the hypothesis.
5. The export's labels must equal the keys of `windows` (check `labels_match`, `details` lists the missing and
   extra labels; they are generic TOU names, not personal).

### 9.4 Fit and verdict

- For each used period, hypothesis *h* and label *l*: `recomputed = fsum(kwh where bucket(local start under h) =
  l)`. The register **fits** when `|recomputed − reported| ≤ max(register_tolerance_kwh, register_tolerance_pct /
  100 × reported)`. The period fits *h* when all its registers fit.
- `fits[h]` = the number of used periods that fit *h*. A period may fit several (in winter `local_wall` and
  `local_standard` coincide).
- **Verdict**, with `best = max(fits)`:
  - `unconfirmed` when no period is used, or `best < min_fit_periods`, or the configured basis has `best` fits and
    another basis has as many. The tie rule matters: with winter-only evidence `local_wall` and `local_standard`
    give identical local times, so a correct configuration of either would otherwise be called wrong and block
    the household for lack of data, not because of a fault. Warning `W_TIMEBASE_UNCONFIRMED`, with `details.fits`;
    the stage's checks pass.
  - `confirmed` when the configured basis has strictly more fits than each other basis (and `best ≥
    min_fit_periods`).
  - `contradicted` when some other basis has strictly more fits than the configured one, and `best ≥
    min_fit_periods`: check `configured_is_best` fails, `details.fits` and `details.best`. Only a basis that beats
    the configured one can fail the stage.
- **`unconfirmed` is not permission to assume.** Hand-over decision #11 says the time basis is settled by evidence,
  not assumed. `validate_timebase` reports the shortfall and passes, but `localise` builds local-time data only
  from a `confirmed` verdict (§10), so no downstream result rests on an assumed basis. A household with no
  retailer export, or only winter reads, stays at `unconfirmed` until more evidence lands. The owner decided
  (§17 Q6) that there is no waiver of decision #11.
- Used periods that fit no hypothesis are *unexplained*: listed in the report, warning `W_TIMEBASE_UNEXPLAINED`
  with their count. They are expected when the retailer's TOU windows changed inside the export (§1); a single
  window set is kept in `params` because window history would be personal (§17 Q5).
- `total_mismatch` and `not_covered` periods raise no warning of their own: they are counted in `result.json` and
  `periods.parquet`, the retailer's totals may include corrections the meter data doesn't, and a count is enough
  for the acceptance record (§14 check 6).

### 9.5 Outputs

- `report/periods.parquet` (kind `timebase_periods`, v1, sorted by `period_end`, `label`): one row per period and
  label: `period_start`, `period_end`, `status` (`used`, `not_covered`, `total_mismatch`), `dataset_id`,
  `label`, `reported_kwh`, then for each basis `kwh_<basis>` and `fit_<basis>` (null when not used)
- `report/result.json` (kind `timebase_result`, v1): `configured`, `verdict`, `fits` (per basis), `best` (sorted
  list), `periods` (counts per status), `incomplete`, `not_midnight`, `unexplained`, and the tolerances used

**Checks:** `labels_match`, `windows_align`, `configured_is_best`.

---

## 10. `localise` (`services/stages/localise.py`)

```python
SPEC = StageSpec(
    name="localise",
    inputs={"interval": "data/interim/elec_interval_nem", "timebase": "data/reports/validation/timebase"},
    params=("meter.local_tz", "meter.timebase"),
    outputs={"interval": "data/interim/elec_interval_local"},
    impl=_run,
)
```

- **Gate** (check `timebase_gate`): `timebase/result.json` names the same `configured` basis as the param, and its
  verdict is `confirmed`. Both `unconfirmed` and `contradicted` fail it, so `dvc repro` builds no local-time data
  on a basis the evidence hasn't settled (hand-over decision #11). When the gate fails, the stage writes **no
  table** and runs no other check: the output directory holds only `_provenance.json`, and the failed check raises
  `E_CHECK_FAILED` with `details.verdict`. The dependency also orders the stages (plan §9.1 change, §16).
- **Body:** `to_local` under the configured basis with `run.zone(meter.local_tz)` (§6).
- **`interval/intervals.parquet`** (kind `elec_interval_local`, v1, `sort_by=["suffix", "utc_start"]`):
  `suffix`, `utc_start` (timestamp[us, UTC]), `local_start` (timestamp[us], naive), `local_date` (date32),
  `utc_offset_minutes` (int16), `meter_start`, `meter_date`, `interval_minutes`, `kwh`, `quality`,
  `quality_flag`, `reason_code`, `dataset_id`.
- **Checks:**
  - `energy_conserved`: the same row count in and out, and `math.fsum` of `kwh` equal exactly (`fsum` is
    order-independent, so equality is exact)
  - `key_unique` on `(suffix, utc_start)`
  - `local_placeable`: no unplaceable row (only `local_wall` can produce one, §9.1). So `local_wall`, even
    confirmed, cannot pass on data spanning a spring-forward or fall-back day: such a clock can't be turned into
    instants. That is deliberate and recorded in §15; a household whose meter really runs on the wall clock needs a
    spec that decides how to place the ambiguous hours.
  - `local_day_lengths`: for every suffix and every local date strictly between its first and last local date,
    the row count equals the length of that local day in the zone (next local midnight − this one, in UTC)
    divided by the interval length. `details.dst_days` counts the short and long days seen, so the DST check of
    plan §10.2 is visible in every run.
- Supply-charge days and hourly roll-ups use `local_date` and `local_start` from here on (P3, P4).

---

## 11. The pipeline after P2

DAG (replacing plan §9.1's meter part):

```
raw/sapn_interval ─► parse_nem12 ─► merge_interval ─┬─► validate_meter    ◄── raw/sapn_summary
                                                     ├─► validate_timebase ◄── raw/retailer_usage
                                                     │          │
                                                     └─► localise ◄─┘ (gate)
```

`STAGES` order: `catalog_index`, `parse_nem12`, `merge_interval`, `validate_meter`, `validate_timebase`,
`localise`. The golden `dvc.yaml` gains, after `catalog_index` (the emitter fixes the exact bytes):

```yaml
  parse_nem12:
    cmd: energatron stage parse_nem12
    deps:
    - data/raw/sapn_interval
    params:
    - .energatron/code_fingerprint.json:
      - stages.parse_nem12.stage_sha256
    outs:
    - data/staged/elec_interval
  merge_interval:
    cmd: energatron stage merge_interval
    deps:
    - data/staged/elec_interval
    params:
    - .energatron/code_fingerprint.json:
      - stages.merge_interval.stage_sha256
    outs:
    - data/interim/elec_interval_nem
    - data/reports/validation/merge
  validate_meter:
    cmd: energatron stage validate_meter
    deps:
    - data/interim/elec_interval_nem
    - data/raw/sapn_summary
    params:
    - validation.meter
    - .energatron/code_fingerprint.json:
      - stages.validate_meter.stage_sha256
    outs:
    - data/reports/validation/meter
  validate_timebase:
    cmd: energatron stage validate_timebase
    deps:
    - data/interim/elec_interval_nem
    - data/raw/retailer_usage
    params:
    - meter.local_tz
    - meter.timebase
    - validation.timebase
    - .energatron/code_fingerprint.json:
      - stages.validate_timebase.stage_sha256
    outs:
    - data/reports/validation/timebase
  localise:
    cmd: energatron stage localise
    deps:
    - data/interim/elec_interval_nem
    - data/reports/validation/timebase
    params:
    - meter.local_tz
    - meter.timebase
    - .energatron/code_fingerprint.json:
      - stages.localise.stage_sha256
    outs:
    - data/interim/elec_interval_local
```

- A raw source directory as a dep, while `data/raw/<source>.dvc` tracks it, is the D3 §14 check 1 situation for
  a whole directory instead of its catalog file. P2.2.3's integration test confirms DVC accepts it and marks the
  stage stale when a dataset lands; if it doesn't, stop and ask (as D1 §11.5).
- A dict-valued param key (`validation.meter`, `validation.timebase`) is tracked by DVC as a whole subtree, and
  `prepare_stage` passes it as a mapping. P2.2.1's test confirms both.
- `pipeline sync` creates the new output parents' `.gitignore` entries through `dvc repro` as usual; the
  workspace's `data/staged/`, `data/interim/` and `data/reports/` need no hand-made files.

---

## 12. Kinds, codes and warnings added in P2

**Persisted kinds** (each with a golden `tests/fixtures/schemas/<kind>/v1.json`, or `v2.json`): `params` v2,
`region_profile` v2, `elec_interval_staged`, `elec_interval_index`, `elec_interval_nem`, `merge_conflicts`,
`merge_summary`, `meter_summary_compare`, `meter_gaps`, `meter_quality_monthly`, `meter_report`,
`timebase_periods`, `timebase_result`, `elec_interval_local`. Parquet kinds are metadata only (D1 §5); the JSON
ones are `Persisted` models in `core/models/meter.py`.

| Warning | Raised by |
|---|---|
| `W_MERGE_SAME_UPDATE` | `merge_interval`: days that differ with equal `UpdateDateTime` (§7.2) |
| `W_METER_GAPS` | `validate_meter`: missing meter dates (§8) |
| `W_METER_UNVALIDATED` | `validate_meter`: no covered SUMMARY row (§8) |
| `W_TIMEBASE_UNCONFIRMED` | `validate_timebase`: too little evidence, or the configured basis tied for best (§9.4) |
| `W_TIMEBASE_UNEXPLAINED` | `validate_timebase`: used periods no hypothesis fits (§9.4) |

No new error codes: failed checks are `E_CHECK_FAILED` (D1 §7), bad params `E_INVALID_INPUT`.

---

## 13. Tests

Written first and run red (the **Rules**). All values are synthetic. D1's tests that cover every stage
(`test_stages`, `test_determinism`, `test_stage_loads`, `test_stage_closure`, `test_stage_purity`, `test_verify`)
apply to each new stage as soon as it is registered.

### 13.1 Foundations and core

| Test | Covers |
|---|---|
| `tests/unit/core/test_params.py` | `Params.defaults(profile)` takes the zone and windows from the profile it is given (a test profile with a made-up zone and labels); v1 → v2 migration inserts the tolerances, `nem_aest` and `null` for `local_tz` and `windows`, and keeps nothing else; a stage reading a `null` value → `E_INVALID_INPUT` naming the field; window parsing (wrap past midnight, `24:00` rejected, a non-5-minute edge rejected); a gap or overlap in the windows → `E_INVALID_INPUT` with the field; negative tolerance rejected; property (hypothesis): any valid windows assign every minute of a day to exactly one label |
| `tests/unit/core/test_region.py` | v2 loads `sa.yaml`, with its zone a key in `tzdata` and its windows a partition; the golden `region_profile/v1.json` upgrades to v2 with `null` zone and windows and validates (D1 §13's golden test, unexercised by any exemption); `build_context()` rejects a `null` zone or windows, an unknown zone and non-partitioning windows with `E_INTERNAL` |
| `tests/architecture/test_no_region_values.py` | AST scan of `core/**` and `services/**`: no string literal that is a zone key (`Region/City` form) or a TOU window string (`HH:MM-HH:MM`) outside docstrings; plan §14.1's rule, enforced |
| `tests/unit/core/test_precedence.py` | newer `source_ts` first; equal `source_ts` → newer `file_mtime`; null `file_mtime` last; equal both → later position; naive and aware mixed |
| `tests/unit/services/test_run_stage.py`, in addition | `zone()` returns the tzdata zone; a malformed or unknown key → `E_INVALID_INPUT`; a dict-valued declared key is passed whole |
| `tests/unit/core/test_text.py` | the helpers moved from `_common.py` (BOM, LF and CRLF, `decode`, `csv_rows` with quoted fields, `is_decimal`, `parse_datetime`, `fail`) behave as before; the parsers' tests, unchanged, cover them through `inspect` |
| `tests/architecture/test_stage_purity.py`, in addition | each §6 form, with a synthetic-source case per form |
| `tests/unit/services/test_pipeline_sync.py`, in addition | v1 → v2 rewrite fills `local_tz` and `windows` from a test region profile; a hand-edited `local_tz` is drift for `--check` and is rewritten alone by `pipeline sync`; an edited `windows` and every other edited value survive; a `null` value is drift; a missing file is written from `Params.defaults(profile)` |
| `tests/unit/core/test_nem12.py` | `read_nem12` on `make_nem12` output: columns and types; `meter_start` for the first and last interval of 5-, 15- and 30-minute days; `A` days; `V` days take each `400`'s quality and reason; an uncovered and a doubly covered interval count as faults and don't raise; a non-numeric value → `bad_value`; a negative value is kept; ReasonDescription isn't kept; property: the table's `fsum(kwh)` equals the oracle's sum of the file's values. **Plan §13's NEM12 edge cases, here as well as in the parser tests** (the P1 tests reach `inspect`, not `read_nem12`): no `100` record; a `100` first, and one elsewhere (`bad_header`); repeated `200` headers with blank lines, giving the same table as one header; a blank line anywhere; CRLF and a BOM; two suffixes (`E1`, `B1`) kept apart in `suffix`; malformed rows, each with its `reason`: a `300` one field short or long, a bad date, a missing `UpdateDateTime`, an unknown record type, a missing `900`, a duplicate day, a mixed interval length, a `400` after an `A` day or with a range outside the day; and `scan_nem12` and `read_nem12` accept and reject exactly the same files (a parametrised test over every case above) |
| `tests/unit/core/test_sapn_summary_reader.py`, `test_retailer_usage_reader.py` | rows, dates, decimals, empty cells as `None`, labels; identifiers returned by `scan_*` only: the `read_*` result types have no identifier field (asserted on the dataclass fields), and both functions accept the same files |
| `tests/unit/core/test_timebase.py` | uses a test profile's zone and windows, which the SA fixtures supply; fixed examples on `sa_dst_dates(2025)` and its neighbours: a meter 10:00 in winter is local 09:30 under `nem_aest` and 10:00 under the other two; in summer it is 10:30 (`nem_aest`), 10:00 (`local_wall`), 11:00 (`local_standard`); the spring-forward and fall-back hours under `local_wall` are unplaceable; `local_standard` uses the standard offset of each meter date (the same offset all year for a zone with fixed standard time); `bucket` for the test windows at every edge (00:55, 01:00, 05:55, 06:00, 09:55, 10:00, 14:55, 15:00); property: energy is conserved by `to_local` for the fixed-offset bases |
| P1 parser tests | `test_sapn_nem12.py`, `test_sapn_summary.py`, `test_retailer_usage.py` pass **unchanged** after the move (§4.4) |

### 13.2 Fixtures

- `make_nem12.py` gains `day_values: Mapping[date, Sequence[str]]` (exact values for chosen days),
  `day_quality: Mapping[date, str]` (a non-`V` QualityMethod such as `E52`) and `negative_on: date | None`. A
  conflict is two specs sharing days with different `day_values` or `update_dt`.
- `tests/fixtures/oracle.py`, **independent of core** (no `energatron` import), so tests don't check the code
  against itself: per-day sums of a NEM12 text; the local time of a meter time under each basis from fixed
  offsets and `sa_dst_dates`; TOU sums for given windows.
- `make_sources.py` gains `make_retailer_usage_from_nem12(nem12, *, basis, windows, reads, labels)` (register
  values computed by the oracle) and `make_summary_from_nem12(nem12, *, periods)`.
- `tests/fixtures/pipeline_workspace.py` builds a fixture workspace's raw zone (blobs plus catalogs) for the P2
  stages: two overlapping SAPN datasets with one conflict day, a SUMMARY CSV, two usage exports (one with an
  empty newest row) generated under `nem_aest`, spanning both DST changes of a year. `test_determinism`,
  `test_stage_loads` and the stage tests use it.

### 13.3 Stages

| Test | Covers |
|---|---|
| `tests/unit/services/stages/test_parse_nem12.py` | one Parquet per dataset plus `index.parquet`; each check passing and failing (a tampered blob, an unknown detector version, a non-numeric value, a `V` fault, a negative value, an `extra` that disagrees); an empty catalog; precedence and `is_current` with a superseded dataset; no NMI or serial anywhere in the output |
| `tests/unit/services/stages/test_merge_interval.py` | newest `update_dt` wins; equal `update_dt` → precedence, with `W_MERGE_SAME_UPDATE`; superseded and unread datasets skipped; identical overlap is no conflict; each conflict column; mixed interval lengths fail `one_interval_length`; plan §15's interval count: merged rows = distinct days × `1440 / L` per suffix |
| `tests/unit/services/stages/test_validate_meter.py` | `match` within the tolerance and `mismatch` just outside it; `not_covered`; partial first and last rows; two summary datasets agreeing and disagreeing; a repeated period fails `summary_rows_unique`; the PDF dataset ignored; gaps and `W_METER_GAPS`; quality shares; no summary → `W_METER_UNVALIDATED`, passing |
| `tests/unit/services/stages/test_validate_timebase.py` | exports generated under each basis confirm that basis and contradict the others (with the summer months included, so `local_wall` and `local_standard` separate); the read-date alignment (§9.3); merged incomplete rows; `not_midnight`; `not_covered` and `total_mismatch`; too few periods → `unconfirmed`; **winter-only evidence, where `local_wall` and `local_standard` tie: configured either → `unconfirmed` with the warning and passing checks, configured `nem_aest` → `contradicted`**; a basis strictly ahead of the configured one → `contradicted`; a tie between two bases that both beat the configured one → `contradicted`; periods generated with other windows → unexplained, with the warning; a label mismatch fails `labels_match`; 30-minute data with an unaligned edge fails `windows_align` |
| `tests/unit/services/stages/test_localise.py` | on `dst_spec(2025)`: 23-hour and 25-hour local days under `nem_aest` and `local_standard`; `local_wall` across a change fails `local_placeable`; energy conserved; `utc_offset_minutes` tells the repeated hour apart; the gate passes only on a `confirmed` report naming the configured basis, and on `unconfirmed`, `contradicted` or a mismatched basis it raises `E_CHECK_FAILED` with the output holding no table |
| `tests/integration/test_localise_tzdata.py` | runs `localise` (on a fixture that spans both DST changes, so the verdict is `confirmed`) in a child interpreter with `PYTHONTZPATH` pointing at a directory holding a wrong `Australia/Adelaide` (a fixed-offset zone): the outputs equal those of a normal run |
| `tests/integration/test_p2_pipeline.py` | a scratch workspace: `pipeline sync` (params v2, `local_tz`), `land` of the §13.2 files, `fingerprint`, `dvc repro`: every stage passes and the merge report shows the one conflict; with a winter-only copy of the fixtures, `validate_timebase` passes `unconfirmed` and `localise` fails its gate without a table; landing a new SAPN dataset makes `parse_nem12` and its downstream stale (§11); with `meter.timebase: local_wall`, `dvc repro` fails in `validate_timebase` with `E_CHECK_FAILED` and `localise` doesn't run; `verify` passes on the clean state |

---

## 14. P2 acceptance checks

Plan §15's P2 criteria, made concrete. Run in `demq-elektric/` by the agent; results that mention data go into a
dated note in the workspace's `agent_docs/`, and `TASKS.md` gets counts and pass/fail only.

1. **Set-up:** `uv sync`; `uv run energatron pipeline sync` → `dvc.yaml` written, `params.yaml` rewritten to v2
   with `meter.local_tz` and `validation.timebase.windows` filled from the region profile (neither `null`);
   `pipeline sync --check` passes; commit both.
2. **Run:** `uv run energatron fingerprint && uv run dvc repro` → every stage passes; commit `dvc.lock` and the
   fingerprint (D1 §11.3). If `validate_timebase` fails (`contradicted`) or `localise` fails its gate
   (`unconfirmed`), stop here, don't edit `meter.timebase`, and go to check 6.
3. **Interval counts:** each staged dataset has `days × 288` rows (its catalog `extra.days`); the merged table
   has `distinct meter dates × 288` rows; `merge_summary.overlap_days` equals the overlap of the two coverages;
   `gap_days` is 0.
4. **Monthly totals:** every SUMMARY row is `match`; none `not_covered`, since the merged data spans the SUMMARY
   window.
5. **Merge conflicts reported:** `merge/summary.json` and `conflicts.parquet` exist; the counts are recorded
   (zero conflicts is the expected result, §1).
6. **Time basis chosen with evidence:** `timebase/result.json` verdict `confirmed` for the configured basis, with
   `fits` for all three recorded, and the counts of `used`, `total_mismatch`, `not_covered` and unexplained periods
   recorded too (they raise no warning of their own, §9.4). This workspace needs `confirmed`: hand-over decision #11
   allows nothing weaker to reach `localise`. If the verdict is `contradicted` or `unconfirmed`, stop and ask the
   owner before changing `meter.timebase` or anything else.
7. **DST:** `localise` passes; `details.dst_days` shows at least one short and one long day.
8. **Clean state:** `uv run energatron verify` passes; `dvc status`, `git status` and `pipeline sync --check` are
   clean; the privacy guard passes on `--tracked`.
9. **Backup:** `energatron backup --status` shows a backup pending; the owner runs P2.3.2.

---

## 15. Deferred

- Meters whose interval length changes between downloads (resampling in `merge_interval`) — when such data
  appears.
- Multi-register SUMMARY files and export registers (`B1`) — the first solar or controlled-load data, with the
  spec that adds it.
- Date-ranged TOU window sets for `validate_timebase` (§17 Q5).
- SUMMARY PDF text — not needed while the CSV is landed.
- A `local_wall` meter clock across DST changes: the ambiguous and missing hours can't be placed (§10), and a spec
  must decide how before such a household can use `localise`.
- An explicit, recorded waiver for a household with no usable retailer export: the owner declined it (§17 Q6).
  `localise` stays gated on a `confirmed` verdict, and changing that needs the owner to change decision #11 first.
- Hourly roll-ups, TOU splits per offer and every metric → P3. Supply-charge days → P4.

---

## 16. Plan, D1 and D3 changes

To be applied on `/done` of P2.1.1, as plan rev 10 and D1 rev 3 (§17 Q1), with these edits in place:

**Plan**
- §9.1: `localise` also depends on `validate_timebase`'s report (the gate, §10).
- §9.2 table: `parse_nem12` writes `index.parquet` too and checks blobs, detector versions and `V`-day coverage;
  `validate_meter` takes param `validation.meter` and warns rather than fails without SUMMARY or on gaps (the plan
  says fail); `validate_timebase` takes `meter.*` and `validation.timebase` and fails only when another basis beats
  the configured one, warning when the evidence is short or tied; `localise` adds the gate, which needs a
  `confirmed` verdict. The `params.yaml` example gains `validation.meter` and `validation.timebase` and shows
  `schema_version: 2`.
- §10.1: output columns `suffix, meter_date, meter_start, interval_minutes, kwh, quality, quality_flag,
  reason_code, update_dt, dataset_id` (no NMI, no `interval_end_nem`); merge key `(suffix, meter_start)`;
  precedence ties broken by D3 §3.3; superseded datasets skipped.
- §10.2: the retailer read date is the last meter date of its period; a period counts only when its total
  reconciles; fit and verdict as §9.4; unexplained periods are warnings; the region's zone comes from `tzdata`.
- §4 / D1 §12 dependency list: `tzdata` at runtime.
- §9.2 `params.yaml` example and §14.1 ("No region-specific values in code" row): the zone and the TOU windows
  are in the region profile and `pipeline sync` copies them; the meter-file formats P2 reads are core modules, not
  registry plugins, until a second format arrives (§3.1).
- Hand-over §4 decision #11 is **unchanged**: this spec conforms to it (§9.4, §10).

**D1 (rev 3)**
- §2: `core/` gains `sapn_summary.py`, `retailer_usage.py` and `models/meter.py` beside `nem12.py` and
  `timebase.py`.
- §4.8: `ServiceContext` gains `region: RegionProfile` (§5.3).
- §2: `core/` also gains `text.py`, the helpers moved out of `adapters/sources/_common.py` (§4).
- §4.3: drop "P2 adds `parse`". Rewrite the registry bullet from "Parsers ... are format-specific plugins chosen
  through a registry" to say the registry chooses the parser that **recognises** a file; the code that reads a
  format's values lives in core and serves both `inspect` and the stages, which name their source directories in
  their `SPEC`; a second format of one source reworks this (§3.1, §17 Q3).
- §9.2: `StageRun.zone(key)`; §9.4: its `importlib.resources` use is the second named exception; stage code
  converts zones only with its objects. §12 (time): "local conversions use `zoneinfo` with the time zone from
  `params.yaml`" becomes "… with zone objects from `StageRun.zone()`, read from the `tzdata` distribution".
- §13: the `test_stage_purity` additions (§6).
- §14: the two P2 items are settled by this spec.
- §7: the five P2 warnings.

**D3** (not edited; recorded here): §10.2 `params` v2 and §10.3 `region_profile` v2 as §5; §10.1 `pipeline sync`
and `--check` also fill `meter.local_tz` and the windows from the profile, treat a `null` value or a differing
`local_tz` as drift, and keep an edited `windows` (§5.3); the three parsers' `inspect` delegate to the core readers
with no behaviour change (§4.4).

---

## 17. Open questions / decisions for the owner

Each was decided here so P2 can be built; the recommendation is what the spec says. **Owner decision
(2026-10-30, `/done`): Q1–Q9 approved as written, except Q6, where the owner declined the waiver.**

1. **Applying the D1 changes (§16).** Recommendation: `/done` of P2.1.1 applies them as D1 rev 3 together with
   plan rev 10, rather than a separate D1 sub-task as P1.1.1 was: they are few and only this spec motivates them.
   Alternative: a P2.1.2 sub-task.
2. **No NMI in the interval tables (§3.2).** Recommendation: drop it (and the serial). The premises check already
   ties data to the premises, a constant column adds nothing, and interim tables are what P3 marts and later
   agents read. Landing's premises check, not a stage, keeps another NMI out (§3.2). Alternative: keep the plan's `nmi`
   column for multi-NMI workspaces, which D3's premises check doesn't allow today.
3. **Format readers in core; no `SourceParser.parse` (§3.1, §4).** Recommendation: as written, accepting this
   trade-off: the portal's formats are core modules, and stages name their source directories, so D1 §4.3's
   "plugins chosen through a registry" and plan §14.1's parser registry no longer describe how stages read
   values. The registry still decides what a landed file is. D1 rev 3 rewrites the bullet (§16). It is cheap while
   each source has one format and gets reworked when a second arrives. Alternative: keep readers in
   `adapters/sources/` and let stages get parsed tables from a port, which D1 §3 forbids services to build.
4. **The time zone and TOU windows live in the region profile, and `pipeline sync` copies them into
   `params.yaml` (§5).** Recommendation: as written, because stages see only params and core may hold no region
   values (plan §14.1). The zone is re-copied if it drifts; the windows are copied once and are then the owner's
   to edit. Alternative: params only, with SA values as migration defaults, which breaks the plan §14.1 rule.
5. **One TOU window set, unexplained periods as warnings (§9.4).** Recommendation: as written; window history
   would put the owner's plan-change dates into `params.yaml`. Alternative: date-ranged window sets in a private
   config file (an input, not a param), if unexplained periods ever leave too few fits.
6. **Evidence for the time basis (§9.4, §10).** Hand-over decision #11 says the basis is settled by evidence, not
   assumed, so `localise` runs only on a `confirmed` verdict; `unconfirmed` (short evidence, or the configured
   basis tied with another, as with winter-only data) passes `validate_timebase` with a warning but stops
   `localise`. This workspace's data reaches `confirmed`. Recommendation: as written. **Decided by the owner,
   2026-10-30: no waiver of hand-over decision #11.** A household with no retailer export can never reach
   `confirmed`, and `localise` stays gated on a confirmed verdict; a later spec may only add a waiver if the owner
   first changes #11. P8 is when it first matters. Separately, a missing or non-overlapping SUMMARY only warns
   (plan §9.2 says fail): acceptance (§14) still requires every row `match` here.
7. **`localise` gated on the timebase report (§10).** Recommendation: as written; otherwise `dvc repro` could build
   local-time data on a basis the evidence hasn't settled. Alternative: the plan's parallel DAG, which would need
   #11 relaxed.
8. **`tzdata` as an explicit runtime dependency (§6).** Recommendation: add it (`uv add tzdata`, project-local, not
   a global install). It is already in every stage's code entry through pandas' requirements, so the change pins
   the version and doesn't make the entry stage-specific. Alternative: a hand-written SA DST rule in core, which
   would be wrong for any other zone and for past rule changes.
9. **What §16 changes in earlier decisions.** Nothing in hand-over §4 (decision #11 is conformed to, not changed).
   It does change plan text: §9.2 (`validate_meter` warns instead of failing without SUMMARY or on gaps), §10.1
   (no NMI column; merge per day rather than per interval, which is equivalent because `UpdateDateTime` is per
   day), §10.2 (the read-date alignment and the verdict rules), §14.1's parser-registry row (Q3) and its
   region-values row (Q4), §9.2's `validate_timebase` row (it passes with a warning on `unconfirmed`, fails only
   on `contradicted`) and §9.1/§9.2 (`localise` depends on the timebase report and needs `confirmed`: the gate), and
   D1 §4.3 (the registry bullet), §4.8 (`ServiceContext.region`) and §9.4 (a second named `importlib.resources`
   exception). Recommendation: approve with this spec; all are listed in §16.
