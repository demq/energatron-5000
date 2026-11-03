# P3 usage profile spec

- **Created:** 2026-11-03
- **Status:** Approved 2026-11-03 (owner, `/done`), with the recommendations for Q1–Q11 as written. Its §15 edits are in the plan as rev 11 and in D1 as rev 4
- **Deliverable:** P3.1.1 in `TASKS.md`
- **Builds on:** [`2026-10-23_data-pipeline-plan.md`](../2026-10/2026-10-23_data-pipeline-plan.md) rev 10 ("the plan"),
  [`2026-10-23_core-architecture-spec.md`](../2026-10/2026-10-23_core-architecture-spec.md) rev 3 ("D1", binding for all
  code) and [`2026-10-30_p2-meter-pipeline-spec.md`](../2026-10/2026-10-30_p2-meter-pipeline-spec.md) ("the P2 spec")
- **For:** the agent or person implementing P3; `/done` of P3.1.1 rewrites P3's sub-tasks from §2
- **Privacy note:** contains no identifiers or usage figures, and nothing added to it may. Facts about the workspace
  files (§1) are structure only: register names, interval lengths, what is present and what is not. Examples use
  placeholders and synthetic values.

---

## 1. What this spec fixes

P3 turns the local-time interval table that P2 built into a usage profile: tables of metrics (the marts) and an HTML
report built from those tables alone. It builds two stages, `usage_profile` and `usage_report`. This spec gives every
table, metric, rule, check and test they need, so P3 can be built without design decisions. "Must" means required.
D1 and the P2 spec stay binding; where this spec changes them, §15 lists the change.

**What P3 builds**
- core: `core/usage.py` (the metric functions, pure DataFrame in, DataFrame out), `core/charts.py` (SVG primitives),
  `core/usage_report.py` (the page), `core/models/usage.py` (kinds and column lists), `local_day_minutes` moved into
  `core/timebase.py`, `Params` v3 with an `analysis` block (§4)
- services: the stages `usage_profile` and `usage_report` in `services/stages/`; `pipeline sync` fills and checks the
  first TOU window set (§4.3)
- no new third-party dependency (§9.1), no adapter change, no new port

**Facts about the workspace files** (read on 2026-11-03 by structure only, from the P2 acceptance note and the legacy
notebook's code cells):
- **The local table** (`elec_interval_local`, P2 spec §10): one consumption register (`E1`), 5-minute intervals, no
  missing meter date, spanning more than two years and containing both kinds of daylight-saving change more than once.
  The first and last local dates are partial (local time runs ahead of meter time in summer), as the P2 acceptance note
  records; every date between them is complete. Almost every interval has quality flag `A`.
- **The retailer's TOU windows changed during the history** (early July 2025; found by probe in the P2 acceptance
  note). `params` holds one window set (P2 spec §17 Q5), so a TOU split that uses it is wrong for later dates. §6
  decides how P3 handles this.
- **The legacy notebook** (`legacy/notebooks/Meter_NEM12_Analyser.ipynb`, code cells only; its outputs were not read):
  - sums the 5-minute readings of the **latest file only** into hourly values keyed by the **meter clock**, with no
    time-zone conversion and no merge
  - gives each hour one of three TOU labels by the hour of the meter clock, using **one** window set for every date
  - its "average daily usage per label" is the label's total over the dates in a chosen range, divided by the number of
    dates, with both ends of the range inclusive
  - the window set it hard-codes is a later one, not the one in the region profile

---

## 2. Order of work

Each step needs the ones before it. `/done` of P3.1.1 turns the rows into sub-tasks P3.2.1–P3.2.5 and P3.3.1, replacing
the provisional list; the owner's backup becomes P3.3.2.

| # | Sub-task | Builds | Sections |
|---|---|---|---|
| 1 | P3.2.1 Foundations | `Params` v3 and its migration; the `analysis` models; `pipeline sync` fills and checks the first window set; `local_day_minutes` moved to `core/timebase.py` (with `localise` calling it, its tests unchanged); the P3 warnings; golden schemas | §4, §11, §13.1 |
| 2 | P3.2.2 Metric functions | `core/usage.py` and `core/models/usage.py` with unit and property tests; the fixture generator additions and the oracle additions | §5, §6, §7, §13.1, §13.2 |
| 3 | P3.2.3 `usage_profile` | the stage, its tables and checks, the golden `dvc.yaml`, the fixture workspace | §8, §10, §13.3 |
| 4 | P3.2.4 Charts and page | `core/charts.py`, `core/usage_report.py`, the golden HTML | §9, §13.1 |
| 5 | P3.2.5 `usage_report` | the stage, the end-to-end test `test_p3_pipeline` ending with `verify` | §9, §10, §13.3 |
| 6 | P3.3.1 Acceptance | in the real workspace, including the legacy reconciliation | §12 |
| 7 | P3.3.2 Owner: back up after P3 | iCloud | |

`Params` (P3.2.1) and `local_day_minutes` (P3.2.1) sit in modules that `runner.py` and `localise.py` import, so
P3.2.1 changes the code entry of every registered stage; `fingerprint --check` reports them stale until
`energatron fingerprint` is re-run (as in the P2 spec §2). That is expected, not a fault.

Each stage sub-task updates `tests/fixtures/golden/dvc.yaml` (D1 §9.5) and the fixture workspace (§13.2) for the stage
it adds, so `test_determinism` and `test_stage_loads` cover it from the start.

---

## 3. Inputs, keys and conventions

- **One input.** `usage_profile` reads `data/interim/elec_interval_local` and nothing else; `usage_report` reads
  `data/marts/usage_profile` and nothing else (§9). Neither reads a raw zone, so neither needs the catalog checks of
  P2 spec §3.1. Both load tables through `load_table` (`services/stages/tables.py`), which fails a wrong kind, version
  or schema with `E_INVALID_INPUT`.
- **The local gate is upstream.** The table only exists when `localise` passed its gate (a `confirmed` verdict;
  hand-over decision #11). P3 adds no gate of its own and does not read the timebase report.
- **Registers.** Only consumption registers (suffix starting with `E`) are profiled. Any other suffix (an export
  register such as `B1`) is left out and counted in `summary.json` (`excluded_suffixes`). Every table below has
  `suffix` as the first key column, so a second consumption register (controlled load) works without a redesign. The
  report has one section per suffix. Solar and export modelling stay deferred (plan §16 Q7).
- **One interval length per suffix.** `merge_interval` already fails `one_interval_length` (P2 spec §7.2), so a table
  from P2 satisfies it. `usage_profile` re-checks it (§8, check `one_interval_length`) because it relies on it and a
  stage must not trust an upstream it can check cheaply. Resampling a changing interval length stays deferred (P2 spec
  §15); P3 writes every metric in terms of `interval_minutes` and never assumes 5.
- **Local dates and clock.** Days, months, seasons and weekdays use `local_date`; hours use `local_start`. The
  meter-clock columns are used only by the reconciliation (§12), never by a stage.
- **Local day and cell completeness** (used throughout):
  - A *local day* is **complete** for a suffix when its row count equals `local_day_minutes(local_date, zone) /
    interval_minutes`. Spring-forward days expect 23 hours of rows, fall-back days 25. A date strictly between the
    suffix's first and last local date can only fail this if intervals are missing.
  - A *covered month* is a calendar month whose first and last local dates both lie strictly inside the suffix's
    `[first, last]` local date range, so the partial edge dates are never in it.
  - An *hour cell* is one `(local_date, local_hour)` pair, `local_hour` being the wall-clock hour of `local_start`.
    It is **full** when it holds exactly `60 / interval_minutes` rows. A spring-forward day has no cell for the missing
    hour; a fall-back day's repeated hour has twice the rows, so that cell is not full. Cells that are not full carry
    no value (§5.4, §5.5), which keeps the two daylight-saving days from distorting any hourly metric (§17 Q5).
- **Reference window.** The last `analysis.reference_months` covered months of the suffix (§5.1). It is the window the
  profile, base load, peak demand and the headline numbers describe. Nothing reads the wall clock: the window ends at
  the last covered month of the data, never at "today", so a rerun on the same data gives the same window.
- **Determinism** (D1 §9.4): sort every table by its key; `groupby(sort=True)`; totals with `math.fsum`; percentiles
  with `numpy.percentile(..., method="linear")`; Parquet only through `write_parquet`, JSON only through
  `write_json`; no `datetime.now`, no randomness. Energy is float64 kWh; power is float64 kW.
- **Tolerances.** Two sums that should agree are compared with an absolute tolerance of `1e-6` kWh (a constant in
  `core/usage.py`, named `SUM_TOLERANCE_KWH`), because a sum of group sums is not bit-equal to one sum over the whole
  table. It is not a param: it is a floating-point allowance, not a household setting.

---

## 4. Configuration: `params` v3

### 4.1 The `analysis` block (`core/models/params.py`)

```yaml
kind: params
schema_version: 3
meter: {…}                 # unchanged from v2
validation: {…}            # unchanged from v2
analysis:
  reference_months: 12            # the profile window: the last N covered months (§3)
  coverage_threshold: 0.95        # least share of complete local days in that window (check reference_window)
  seasons:                        # label → calendar months; together they cover 1-12 exactly once
    DJF: [12, 1, 2]
    MAM: [3, 4, 5]
    JJA: [6, 7, 8]
    SON: [9, 10, 11]
  weekend: [saturday, sunday]     # the weekday names that make a local date a "weekend" date
  band_percentiles: [10, 50, 90]  # low, middle and high line of the hourly profile (§5.4)
  base_load:
    from: "01:00"                 # local clock; start inclusive, end exclusive, may wrap past midnight
    to: "05:00"
    percentile: 10
  demand_minutes: 30              # the block that peak demand is measured over (§5.7)
  tou:
    window_sets:                  # ordered by effective_from; the first has effective_from null (§6)
      - effective_from: null
        windows:                  # label → ["HH:MM-HH:MM", ...]; filled by pipeline sync from the region profile
          <label>: ["HH:MM-HH:MM", ...]
```

- Nested frozen models, `extra="forbid"`, as in v2.
- `reference_months` ≥ 1; `coverage_threshold` in (0, 1]; every season label non-empty; the season months partition
  1–12 (a gap, an overlap or a month outside 1–12 → `E_INVALID_INPUT`, `details.field: "analysis.seasons"`); a season may
  wrap the year (DJF does), because membership is by month number.
- `weekend` holds lower-case English weekday names, at least one and at most six; the rest are weekdays.
- `band_percentiles` is three integers 0–100, strictly increasing. `base_load.percentile` is 0–100. The base-load
  window follows the window-edge rules of P2 spec §5.1 (multiples of 5 minutes, `24:00` not allowed, wrap past
  midnight when `to` ≤ `from`) and, unlike a TOU window set, need not partition the day.
- `demand_minutes` is a positive multiple of 5 that divides 60. A stage reading it with an `interval_minutes` that does
  not divide it fails `E_INVALID_INPUT` (`details.field: "analysis.demand_minutes"`).
- `window_sets` has at least one entry. The first has `effective_from: null`; the others have strictly increasing
  `effective_from` dates (`YYYY-MM-DD`, local dates). Every set's windows partition the day by the P2 rule, and every set
  has **the same labels** (so a label is a column or a row value across the whole history). A violation →
  `E_INVALID_INPUT`, `details.field` naming the entry.
- **Region-specific values and core defaults.** The first set's windows are region-specific, so core holds no default
  for them (plan §14.1), exactly like `validation.timebase.windows`: the field allows `null`, only a migration produces
  it, `pipeline sync` replaces it from the region profile, and a stage that reads a `null` fails `E_INVALID_INPUT`
  (`details.field: "analysis.tou.window_sets[0].windows"`, "run `energatron pipeline sync`"). Every other `analysis`
  value has a default that holds in any region (the season month groups are a calendar convention, not a region value;
  the labels `DJF`… name months, not seasons of the year).
- `Params.defaults(profile)` fills the first set from `profile.tou_windows`.
- **Migration v2 → v3** is pure and has no region: it inserts the `analysis` block above with the defaults shown and
  `windows: null` in the single first set. The golden `params/v2.json` upgrades and validates (D1 §13). `schema_version`
  becomes 3; `DvcRunner` and `prepare_stage` need no change.
- DVC tracks `analysis` as a whole subtree (a dict-valued param, P2 spec §11), so any edit to it makes `usage_profile`
  stale and nothing else. `usage_report` declares **no params** (§9): changing a setting reruns it only through its
  input.

### 4.2 What stays out of v3

The plan's `analysis.sensitivity` (`usage_scale`, `alt_window`) is a P6 setting and is not added now; plan rev 11
(§15) removes it from the P3-era example. `pricing.*` and `offers.*` come with P4.

### 4.3 `pipeline sync`

As P2 spec §5.3, extended to the first window set:
- writing or upgrading `params.yaml` fills `analysis.tou.window_sets[0].windows` from `ctx.region.tou_windows` while it
  is `null`
- a non-null first set is the owner's and is never replaced (the owner may edit it; the retailer's windows can differ
  from the profile's default); every other `analysis` value is the owner's
- `--check` reports a `null` first set as `params.yaml` drift, so the commit hook and `land` catch it
- `pipeline_sync_result` is unchanged (v1)

---

## 5. Metrics (`core/usage.py`)

Every function is pure: DataFrames and plain values in, a DataFrame out, no I/O, no zone lookup (§3: the stage computes
`expected` rows per local date with `run.zone()` and passes them in). All take the `analysis` values they need as
arguments. Column lists live in `core/models/usage.py` (§10).

### 5.1 Days, months and the reference window

- **`daily`** (one row per `(suffix, local_date)` with at least one interval): `kwh` (`fsum`), `intervals`, `expected`
  (rows of a complete local day, from `local_day_minutes`), `coverage` (`intervals / expected`, at most 1),
  `complete` (`intervals == expected`), `daytype` (`weekend` or `weekday` by `analysis.weekend`), `season` (by
  `analysis.seasons`), `month` (`YYYY-MM`).
- **`monthly`** (one row per `(suffix, month)`): `days_present` (local dates with any interval), `days_in_month`,
  `complete_days`, `covered` (the month is a *covered month*, §3), `kwh` (`fsum` of every interval of the month's local
  dates, partial days included), `avg_daily_kwh` (the mean of the month's **complete** days' `kwh`; null when there is
  none), plus the §5.7 columns `base_load_kw`, `peak_demand_kw`, `peak_demand_at` and `load_factor`.
  A month's `kwh` is therefore the energy the table holds for it, and `avg_daily_kwh` is a like-for-like day average;
  the report marks months that are not `covered` or not fully complete.
- **Reference window.** The last `reference_months` months with `covered = true` for the suffix, consecutive. It is
  `window_start` / `window_end` (the first and last month, `YYYY-MM`) in `summary.json`. `coverage` of the window =
  complete local days / local days in its months. It must be ≥ `coverage_threshold`, and the window must have exactly
  `reference_months` months (check `reference_window`, §8). Months inside the window that are not fully complete count
  against the coverage but are still used.

### 5.2 Averages by season

**`seasonal`** (one row per `(suffix, season_year, season)`): `season_year` is the calendar year of the season's first
month in the data, taking December as DJF's first month (so a DJF that spans two calendar years has one `season_year`,
the year of its December); `days` (complete local days), `kwh` (of those days), `avg_daily_kwh`. Only seasons with at
least one complete day appear. All history, not only the window, so seasons can be compared year on year.

### 5.3 Year on year by month

**`yoy`** (one row per `(suffix, month)`, only where the same calendar month exists a year earlier): `prior_month`,
`avg_daily_kwh`, `prior_avg_daily_kwh`, `change_pct` (`(this − prior) / prior × 100`, null when `prior` is zero). A row
exists only when **both** months are covered and have at least one complete day. With a history of two years there is
at most one comparison per calendar month; the table grows with each download (plan §10.3).

### 5.4 Hourly profile

**`hourly_profile`** (one row per `(suffix, season, daytype, hour)`), over the reference window's **full hour cells** of
**complete local days** only:
- the cell value is the sum of the cell's rows' `kwh` (kWh in that clock hour)
- `cells` (how many cells fed the row), `mean_kwh`, `p_low_kwh`, `p_mid_kwh`, `p_high_kwh` (the `band_percentiles` of the
  cell values)
- a row appears only for combinations that have at least one cell. Spring-forward and fall-back days never contribute to
  their affected hour (§3), so `cells` for that hour is lower than for its neighbours by the number of such days
- the percentile values used are recorded in `summary.json` (`band_percentiles`) so the report labels its band without
  reading params

### 5.5 Heatmap

**`heatmap`** (one row per `(suffix, local_date, hour)` over **all** history): `kwh` for a full cell, null for a cell that
is not full (so the missing and the doubled hour of a daylight-saving day, and cells of the partial edge dates, show as
blank, not as a wrong colour). Hours with no rows at all do not appear.

### 5.6 TOU split

The split uses the window set in force on each interval's `local_date` (§6), and `bucket()` from `core/timebase.py`
on `local_start`, so the window edges behave exactly as in P2 (only the minute of the day counts; edges on 5-minute
marks).
- **`tou_daily`** (one row per `(suffix, local_date, label)`): `kwh` (`fsum`), `intervals`, `window_set` (the 0-based
  index of the set in force on that date). A label with no intervals that day gets no row.
- **`tou_monthly`** (one row per `(suffix, month, label)`): `kwh`, `share` (of the month's kWh, partial days included),
  `avg_daily_kwh` (the label's `kwh` over complete days divided by the number of complete days; null with none),
  `mixed_windows` (more than one window set applied inside the month).
- Every interval gets exactly one label (the windows partition the day), and the labels' sum equals the day's `kwh`
  within `SUM_TOLERANCE_KWH` (check `tou_conserved`).

### 5.7 Base load, peak demand, load factor

Computed per `(suffix, month)` and stored in `monthly`; the reference-window values go into `summary.json`.
- **Base load** (`base_load_kw`): the `analysis.base_load.percentile` of the intervals' power (`kwh × 60 /
  interval_minutes`, kW) over every interval of **complete local days** whose local clock falls in
  `[from, to)`. Null when the month has no such interval. Reference-window value: the same percentile over the
  window's intervals (not an average of monthly values).
- **Peak demand** (`peak_demand_kw`, `peak_demand_at`): split each local day into blocks of `demand_minutes` aligned to
  the local clock (00:00, 00:30, … for 30). A block is **full** when it holds `demand_minutes / interval_minutes` rows.
  Its power is `block kWh × 60 / demand_minutes`. The month's peak is the largest full-block power, and
  `peak_demand_at` is that block's `local_start` (naive). Aligned blocks, not a rolling window: that is what a demand
  tariff measures, and it makes the number independent of where the data starts.
- **Load factor** (`load_factor`): the mean power over the month's full blocks divided by `peak_demand_kw`; null when
  the peak is zero or missing. It lies in (0, 1].

### 5.8 Data quality

**`quality_monthly`** (one row per `(suffix, month, quality_class)`): `intervals`, `kwh`, `share_intervals`
(of the month's intervals), `share_kwh`. `quality_class` maps the NEM12 quality flag (the first character of
`quality`, the table's `quality_flag`) with a fixed core table: `A` → `actual`, `E` → `estimated`, `F` and `S` →
`substituted`, anything else → `other`. The class table is a core constant (`QUALITY_CLASSES`), covered by a test
that every flag the P2 spec §4.1 can produce maps somewhere. The month's coverage (present vs expected intervals) is in
`monthly` (`coverage` = present intervals / expected intervals over its local dates that lie inside the data's range),
so "missing" is separate from "substituted".

This is by local month; `validate_meter`'s `quality_monthly` is by meter month. They are different tables for
different readers and are not merged.

---

## 6. TOU windows before P4 and the retailer's window change

Plan §10.3 wants the TOU split for the network windows and, later, for each candidate offer's windows. P3 does the
first and builds the seam for the second.

- **The windows** are `analysis.tou.window_sets` (§4.1), not `validation.timebase.windows`. The two have different jobs:
  the validation windows are what the retailer's export used, which is evidence for the time basis (P2); the analysis
  windows are what the usage report shows. Both start from the region profile. They may differ.
- **Date-ranged sets.** The window set in force on a local date is the one with the latest `effective_from` ≤ that date;
  the first has no start. Only `local_date` decides it, so no interval is split across two sets, and a month that spans
  a change has `mixed_windows = true` and raises `W_USAGE_MIXED_WINDOWS` (once, with the months).
- **The retailer's change in early July 2025.** The P2 acceptance note holds the evidence (workspace). P3.3.1 does not
  decide the change date or the new windows: it **asks the owner** to confirm a second set (the labels unchanged, the
  new windows and `effective_from`) before the workspace's `params.yaml` is edited, and it records the answer in the
  workspace note. Until the owner adds it, the split uses the single configured set for all dates and the report shows
  the one-set caution (§9.3). The real second set lives in the workspace's `params.yaml`; the tool repo holds the
  mechanism and made-up fixture values. Retailer plan details (windows, rates, dates) are not private (owner,
  2026-11-03); the privacy rules cover the household's usage and identifiers only.
- **Why params and not private config.** The windows and the date are public facts of a retailer's plan, not
  identifiers or usage figures, and `params.yaml` is where the owner already edits the validation windows. The P2 spec
  §17 Q5 noted that a date-ranged set would put the plan-change date in `params.yaml`; the owner is asked to accept that
  (§17 Q2).
- **`validate_timebase` is untouched.** It keeps one window set and reports the later periods as unexplained
  (`W_TIMEBASE_UNEXPLAINED`). Making it read `analysis.tou.window_sets` would clear that warning but changes a P2 stage
  and its evidence rule; that is a separate decision (§17 Q10).
- **Seam for P4.** `core/usage.py: tou_split(local, window_sets)` takes the sets as an argument. P4's engine calls it
  with an offer's windows; P3 calls it with the configured sets.

---

## 7. Edge cases the metrics must handle

- **Daylight-saving days.** Daily totals use all rows (a 23-hour day holds less energy, a 25-hour day more); hour cells
  that are not full are blank (§3); base load and the profile use complete days and full cells; the demand blocks are
  aligned to the local clock, so a block in the repeated hour exists once per occurrence and a block in the missing hour
  not at all. No metric needs a zone except `expected` (via `local_day_minutes`).
- **Partial edge dates** are never `complete` and never in a covered month, so they feed `monthly.kwh` of an edge month
  that is not covered, but nothing that is a day average, a profile, a window or a year-on-year row.
- **A season with no complete day** has no `seasonal` row; a window shorter than a year may miss whole seasons in the
  hourly profile, which then has no rows for them.
- **Negative values.** P2 keeps a negative `kwh` (P2 spec §7.1). P3 sums them like any value and warns nowhere; the
  power metrics take them as they are. (A meter with export on the consumption register is out of scope, §3.)
- **Zero energy.** A zero peak gives a null load factor, not a division error.
- **Several consumption registers** are profiled separately, never summed.

---

## 8. `usage_profile` (`services/stages/usage_profile.py`)

```python
SPEC = StageSpec(
    name="usage_profile",
    inputs={"interval": "data/interim/elec_interval_local"},
    params=("meter.local_tz", "analysis"),
    outputs={"profile": "data/marts/usage_profile"},
    impl=_run,
)
```

- **Body.** Load the table; keep consumption suffixes (§3); compute `expected` per local date with
  `local_day_minutes(local_date, run.zone(meter.local_tz))`; call the §5 functions; write each table with
  `write_parquet` and `summary.json` with `write_json`. The stage does no arithmetic of its own beyond wiring.
- **Outputs** (all in `profile/`):

| File | Kind (v1) | Key / sort | Section |
|---|---|---|---|
| `daily.parquet` | `usage_daily` | `suffix`, `local_date` | §5.1 |
| `monthly.parquet` | `usage_monthly` | `suffix`, `month` | §5.1, §5.7 |
| `seasonal.parquet` | `usage_seasonal` | `suffix`, `season_year`, `season` | §5.2 |
| `yoy.parquet` | `usage_yoy` | `suffix`, `month` | §5.3 |
| `hourly_profile.parquet` | `usage_hourly_profile` | `suffix`, `season`, `daytype`, `hour` | §5.4 |
| `heatmap.parquet` | `usage_heatmap` | `suffix`, `local_date`, `hour` | §5.5 |
| `tou_daily.parquet` | `usage_tou_daily` | `suffix`, `local_date`, `label` | §5.6 |
| `tou_monthly.parquet` | `usage_tou_monthly` | `suffix`, `month`, `label` | §5.6 |
| `quality_monthly.parquet` | `usage_quality_monthly` | `suffix`, `month`, `quality_class` | §5.8 |
| `summary.json` | `usage_summary` | | below |

- **`summary.json`** (kind `usage_summary`, v1, a `Persisted` model in `core/models/usage.py`): per suffix, the
  `window_start`, `window_end`, `window_days`, `window_complete_days`, `window_coverage`; the reference-window headline
  values (`avg_daily_kwh`, `base_load_kw`, `peak_demand_kw`, `load_factor`, `tou_shares` by label); `first_date`,
  `last_date`, `covered_months`; `excluded_suffixes`; `window_sets_used` (the sets in force, as copied from params:
  public tariff windows only); `band_percentiles`; `dst_days` (counts of short and long local days seen); the per-check
  results mirrored. Values only, no free text; the privacy of a workspace summary is the workspace's matter (it holds
  figures), but the model has no identifier field.
- **Checks** (fail the stage; counts only in `details`):
  - `one_interval_length`: each suffix has a single `interval_minutes`
  - `daily_conserved`: `|fsum(daily kwh) − fsum(interval kwh)| ≤ SUM_TOLERANCE_KWH`
  - `keys_unique`: each table's key columns have no duplicates (`details.tables` lists failing tables)
  - `tou_conserved`: every interval got exactly one label (`unlabelled == 0`) and the labels' sum equals `daily` within
    the tolerance, per suffix
  - `windows_resolved`: every local date resolves to a window set (true by construction while params validation holds;
    asserted so a doctored or future params shape cannot leave a date unlabelled)
  - `reference_window`: each suffix has exactly `reference_months` covered months and `window_coverage ≥
    coverage_threshold` (`details.months_found`, `details.coverage`). **Failing it fails the stage** (plan §9.2): P4
    and P6 need a full year, and a profile of less would mislead. §17 Q4.
  - `profile_cells`: for every `(season, daytype)` that has a complete day in the window, all 24 hours have at least one
    cell (a DST day never removes an hour for every day of a season)
- **Warnings:** `W_USAGE_MIXED_WINDOWS` (months with more than one window set, `details.months`) and
  `W_USAGE_PARTIAL_DAYS` (local dates inside a covered month that are not complete, `details.days`). Neither fails the
  stage.
- **Refusals** (`E_INVALID_INPUT`, not checks): a `null` `meter.local_tz` or first window set; a bad table schema; a
  `demand_minutes` that the interval length does not divide.

---

## 9. The report: `usage_report` (`services/stages/usage_report.py`)

```python
SPEC = StageSpec(
    name="usage_report",
    inputs={"profile": "data/marts/usage_profile"},
    params=(),
    outputs={"report": "data/reports/usage"},
    impl=_run,
)
```

The report is a second stage, not part of `usage_profile`, so it is built **from the marts alone**: its only input is
the mart directory, which `verify` and DVC both see (plan §15, P3 acceptance; §17 Q1). It re-renders without recomputing
a metric, and a chart cannot read anything the marts do not hold.

### 9.1 What it writes and what it depends on

- `report/index.html`: one self-contained file. No network asset of any kind: no `<script>`, no `<link>`, no `<img>`
  with a URL, no web font, no CSS `@import` or `url(...)`, no `http:` or `https:` text anywhere in the file (a test
  scans for each). Styles are one inline `<style>`; charts are inline SVG; there is no JavaScript, so tooltips are SVG
  `<title>` children and the page works in any browser and in a text-mode viewer's fallback.
- No new third-party dependency: `html.escape` and string building from the standard library, with pandas for reading
  the marts. matplotlib, plotly and a template engine are not added (§17 Q6): they bring byte-unstable output across
  versions, web assets or a large dependency, and the stage's code entry would grow to cover them.
- The page is rendered by `core/usage_report.py: render(marts: UsageMarts) -> str`, where `UsageMarts` is a frozen
  dataclass of the DataFrames and the `UsageSummary` the stage loaded. Core stays pure; the stage reads and writes.

### 9.2 Deterministic output

- Same marts → the same bytes: no timestamp, no random or counter-based ids (element ids derive from the chart and the
  data keys), tables and series iterated in sorted order, every number written through one formatter
  (`format_number(value, places)`; fixed places, `.` decimal point, no locale), SVG coordinates rounded to one decimal.
- Text is escaped with `html.escape(…, quote=True)`; no value is interpolated into an attribute or the `<style>`
  unescaped. A test feeds hostile strings (`<`, `"`, `&`, `</svg>`) through every label that comes from the marts (the
  window labels).
- The page's title is fixed text ("Household electricity usage"). It contains no premises name, NMI, serial, account,
  address or postcode, and none exists in the marts (no table has an identifier column; `summary.json` has none).

### 9.3 Content

In this order, per suffix (one section each):
1. **Headline** (from `summary.json`): the reference window's months, average daily kWh, base load, peak demand and its
   block length, load factor, and the TOU shares; a coverage line ("`n` of `m` days complete"). Numbers in a small table,
   not cards.
2. **Monthly usage**: bars of `monthly.kwh` over the history; months that are not covered, or whose `complete_days` is
   below `days_in_month`, are drawn hatched and named in the chart's description. A line of `avg_daily_kwh` on a second
   scale is a second chart, not a second axis on the first (§9.4).
3. **Average daily use by month and season**: `avg_daily_kwh` per month, with season groups labelled; a table of
   `seasonal` below.
4. **Year on year**: for each calendar month, the two years side by side and `change_pct` in the label; months without a
   prior year are absent.
5. **Hourly profile**: small multiples, one panel per `(season, daytype)`; the mean line, and the `p_low`–`p_high` band;
   the band's percentiles are named from `summary.json`. A panel with `cells` below a handful of days says so.
6. **Day × hour heatmap**: one `<rect>` per non-null `heatmap` row (blank for null cells), days along one axis, hours
   along the other, one sequential single-hue scale with its legend; DST days are visibly blank in their affected hour.
   Size budget: the page stays under 4 MB for three years of 5-minute data (a test measures the fixture's size per
   cell and projects it).
7. **TOU split**: stacked bars per month, one colour per label, `share` in each segment's `<title>`; months with
   `mixed_windows` carry a marker and a note. When the configuration has **one** window set the report adds a caution
   that the retailer's windows may have changed during the history (§6) and that the split holds one set for all
   dates; with more than one set, it lists each set's `effective_from` and windows.
8. **Base load, peak demand and load factor** by month: two small line charts (kW) and one (ratio), each with
   `peak_demand_at` in the tooltip.
9. **Data quality**: per month, stacked share of `actual`, `substituted`, `estimated`, `other`, and a coverage line
   beneath.

### 9.4 Visual rules

Colours, light and dark, accessibility and the form of each chart follow the `dataviz` guidance in this environment (the
implementer reads it before writing chart code). What this spec fixes, so tests can check it:
- every chart is an `<svg role="img">` with a `<title>` and a `<desc>` that states what it shows and the notable fact
  (the coverage caveat, the DST blanks), so the page is readable without sight of the chart
- colour is never the only carrier: the TOU labels also differ by a text label in the legend, partial months by a
  hatch pattern, quality classes by a legend with names
- one `prefers-color-scheme` media block switches the palette; no JS
- at most four categorical colours per chart (the TOU labels, the quality classes), from one validated palette kept in
  one module constant; the heatmap and bars use that palette's sequential ramp
- no chart has two y axes (§9.3 item 2)

### 9.5 Privacy of `reports/usage/`

The report holds the household's usage figures, so it is private data: it lives only in the workspace
(`data/reports/usage/`, DVC-tracked, backed up with the rest), and the tool repo never gets one. Tests in the tool repo
build a report from **synthetic** marts only and check the privacy properties above (§13.1). The privacy guard's data-file
rule already stops `*.html` outside `tests/fixtures/` (D2 §7.4); the golden HTML lives there (§13.2).

---

## 10. The pipeline after P3

DAG, extending P2 spec §11:

```
… ─► localise ─► usage_profile ─► usage_report
```

`STAGES` order: `catalog_index`, `parse_nem12`, `merge_interval`, `validate_meter`, `validate_timebase`, `localise`,
`usage_profile`, `usage_report`. The golden `dvc.yaml` gains, after `localise` (the emitter fixes the exact bytes):

```yaml
  usage_profile:
    cmd: energatron stage usage_profile
    deps:
    - data/interim/elec_interval_local
    params:
    - meter.local_tz
    - analysis
    - .energatron/code_fingerprint.json:
      - stages.usage_profile.stage_sha256
    outs:
    - data/marts/usage_profile
  usage_report:
    cmd: energatron stage usage_report
    deps:
    - data/marts/usage_profile
    params:
    - .energatron/code_fingerprint.json:
      - stages.usage_report.stage_sha256
    outs:
    - data/reports/usage
```

- `usage_report` has no declared `params`, which is the first stage with none besides its code entry. P3.2.5's test
  confirms `run_stage`, `prepare_stage` and the emitter accept an empty `params` tuple (D1 §9.1 says "exactly the
  stage's declared dotted keys", which an empty tuple satisfies); if one of them does not, stop and ask.
- `data/marts/.gitignore` and `data/reports/.gitignore` already exist in the workspace (P1.4.3, P2.3.1). `dvc repro`
  creates the `data/marts/usage_profile` and `data/reports/usage` entries.
- The plan's `usage_profile` row (§9.2) gave both outputs to one stage; plan rev 11 splits it (§15).

---

## 11. Kinds, codes and warnings added in P3

**Persisted kinds** (each with a golden `tests/fixtures/schemas/<kind>/v1.json`, or `v2.json`/`v3.json` for `params`):
`params` v3 and the nine Parquet kinds in §8's table (metadata only, D1 §5), plus `usage_summary` (JSON, a `Persisted`
model).

| Warning | Raised by |
|---|---|
| `W_USAGE_MIXED_WINDOWS` | `usage_profile`: months in which more than one TOU window set applied (§5.6, §6) |
| `W_USAGE_PARTIAL_DAYS` | `usage_profile`: local dates inside a covered month that are not complete (§8) |

No new error codes: failed checks are `E_CHECK_FAILED` (D1 §7), bad params and tables `E_INVALID_INPUT`.

---

## 12. P3 acceptance checks

Plan §15's P3 criteria, made concrete. Run in `demq-elektric/` by the agent; results that mention data go into a dated
note in the workspace's `agent_docs/`, and `TASKS.md` gets counts and pass/fail only.

1. **Set-up.** `uv sync`; `uv run energatron pipeline sync` → `params.yaml` upgraded to v3, with
   `analysis.tou.window_sets[0].windows` filled from the region profile (not `null`); `pipeline sync --check` passes.
2. **Window history (ask the owner first).** From the P2 acceptance note's probe the agent proposes a second window set
   (same labels, the new windows, the `effective_from`) in a workspace note and **waits for the owner's yes** before
   editing `params.yaml`. With the answer applied (or a recorded "keep one set"), `pipeline sync --check` passes. Which
   of the two happened goes into `TASKS.md` as a word, not as dates or windows.
3. **Run.** `fingerprint`, then `dvc repro`: `usage_profile` (all its checks) and `usage_report` pass. Report the check
   counts, warnings, and whether `W_USAGE_MIXED_WINDOWS` appeared (it should, if a second set was added; the months are
   in the note).
4. **Reference window.** `window_months == reference_months`, `window_coverage ≥ coverage_threshold`; the window's
   first and last month and the number of complete days go in the note.
5. **Report builds from marts.** `data/reports/usage/index.html` exists; it contains no `http:`/`https:`, `<script>` or
   external reference (the same scan the unit test runs, applied to this file); two runs of `usage_report` give identical
   bytes (`verify` covers this); the page size is within the budget of §9.3. **The owner opens it in a browser** and says
   whether it reads correctly: that visual check is theirs, and the sub-task's report asks for it. The agent does not
   attach or paste its contents.
6. **Legacy reconciliation** (plan §15: TOU daily averages reconcile with the legacy notebook for the same window, with
   the differences explained). A throwaway script outside both repos (`scratchpad/`) does this; nothing from the legacy
   notebook or its figures is copied into the tool repo. Let *W* be the legacy notebook's own date range (the dates of
   the file it reads) and *S* the legacy window set (its hard-coded one). Four comparisons, all over the same meter dates:
   - **R1, data parity.** Daily kWh per meter date from the legacy method (hourly sums of the 5-minute readings from the
     legacy file) against the same per-date sums from `elec_interval_local` (grouped by `meter_date`). Expect equality
     within `SUM_TOLERANCE_KWH` on every date both cover. Dates that differ are listed by count and are the merge or
     quality effect (the legacy notebook used one file and ignored quality flags); with R1 clean the merge fixes
     explain nothing and say so.
   - **R2, method parity.** The legacy per-label daily average (meter-clock hours, set *S*, dates inclusive) against an
     independent recomputation from `elec_interval_local` bucketed on `meter_start` with *S*. Expect equality within
     the tolerance. This proves the oracle reproduces the legacy method.
   - **R3, tool against oracle.** The mart's `tou_daily` averaged over the local dates that fall in *W*, restricted to the
     dates whose window set equals *S* (all of *W* if the configured sets already include it; otherwise run the stage's
     core function in the script on the local table with *S*), against an independent recomputation bucketed on
     `local_start` with *S*. Expect equality within the tolerance: this proves the mart is correct on its own terms.
   - **R4, the explained difference.** The per-label difference between R3's and R2's figures, in kWh per day, by
     label. It must be accounted for entirely by the time-basis fix: the same energy, bucketed on the local clock rather
     than the meter clock, plus the day-boundary shift of up to one half hour (local dates against meter dates). The
     script shows this by computing the oracle on the **meter clock** and on the **local clock** from the same rows and
     confirming that each label's totals change only by energy that crossed a window edge. The per-label sums over all
     labels are equal in both (conservation).
   - Pass = R1 (or its explained exceptions), R2 and R3 within tolerance, R4 explained. `TASKS.md` gets "R1–R4 pass" and
     the counts (dates compared, labels), never a figure.
7. **Clean state.** `verify` passes; `dvc status` up to date; `pipeline sync --check` and `fingerprint --check`
   current; privacy guard `--tracked` 0 violations; `git status` lists only the files `/done` commits.
8. **Backup.** `backup --status` reports it pending; the owner runs P3.3.2.

If a check cannot pass, stop and ask the owner; do not change a param to make a check pass.

---

## 13. Tests

Written first and run red (the **Rules**). All values are synthetic. D1's tests that cover every stage
(`test_stages`, `test_determinism`, `test_stage_loads`, `test_stage_closure`, `test_stage_purity`, `test_verify`) apply
to each new stage as soon as it is registered.

### 13.1 Foundations and core

| Test | Covers |
|---|---|
| `tests/unit/core/test_params.py`, in addition | v2 → v3 inserts the `analysis` defaults with a `null` first-set `windows`; `Params.defaults(profile)` fills it from a test profile; seasons that overlap, leave a month out or name month 13 → `E_INVALID_INPUT` naming `analysis.seasons`; a season that wraps the year is accepted; `band_percentiles` not strictly increasing rejected; `demand_minutes` not a divisor of 60 rejected; window sets: first has a start date → rejected, `effective_from` not increasing → rejected, differing labels → rejected, a set that does not partition the day → rejected; a stage reading the `null` first set → `E_INVALID_INPUT` naming the field |
| `tests/unit/core/test_timebase.py`, in addition | `local_day_minutes` on `sa_dst_dates(2025)` and the days either side: 1440, 1380 (spring-forward), 1500 (fall-back); `localise`'s tests pass unchanged |
| `tests/unit/core/test_usage.py` | each §5 function on a small hand-built local table, expected values written out by hand: `daily` (complete, partial, a 23-hour and a 25-hour day, `coverage`); `monthly` (`covered` false for an edge month, `avg_daily_kwh` from complete days only, `kwh` from all); the reference window (the last N covered months; fewer than N; consecutive); `seasonal` (a DJF spanning two calendar years has one `season_year`; a season with no complete day is absent); `yoy` (both months covered and with a complete day; the zero-prior null); `hourly_profile` (a repeated hour's cell is not full and does not contribute; band percentiles on a known set; combinations without cells are absent); `heatmap` (null for the missing and the doubled hour); `tou_split` (each edge minute of a test window set; two sets switching on `effective_from`; a month spanning the switch is `mixed_windows`); base load (the percentile over `[from, to)`, a wrapping window); peak demand (aligned blocks; a non-full block ignored; `peak_demand_at`); load factor (null on a zero peak); `quality_class` (every P2 flag maps; an unknown flag → `other`) |
| `tests/unit/core/test_usage_properties.py` (hypothesis) | `fsum` of `daily` equals the interval sum within tolerance for any generated table; every interval gets exactly one TOU label under any valid window set (plan §13); labels sum to the day's `kwh`; `tou_split` under a single set equals bucketing the whole table; `share` per month sums to 1; seasons partition every month; load factor in (0, 1] when the peak is positive |
| `tests/architecture/test_no_region_values.py`, in addition | the `analysis` defaults contain no zone key or window string in `core/**` or `services/**` (the labels `DJF`… are not zone keys) |
| `tests/unit/core/test_charts.py` | each SVG primitive on a tiny series: byte-stable output, coordinates rounded to one decimal, `<title>` and `<desc>` present, hatch pattern for a flagged bar, an empty series draws an empty chart with its caption, the number formatter is locale-free (`.` decimal point under a locale-like environment) |
| `tests/unit/core/test_usage_report.py` | on synthetic marts: the page has a section per suffix and every §9.3 item; no `http:`, `https:`, `<script`, `<link`, `src=`, `@import` or `url(` anywhere; hostile window labels are escaped everywhere they appear; the one-set caution appears with one window set and is replaced by the list with two; mixed-window months carry the marker; the title is fixed text; the page is byte-identical on a second render; the page matches `tests/fixtures/golden/usage_report.html` (a deliberate update when the page changes); size per heatmap cell × a three-year projection is under the §9.3 budget |
| `tests/unit/services/test_pipeline_sync.py`, in addition | a v2 → v3 rewrite fills the first set's windows from a test profile; an edited first set and every other edited `analysis` value survive; a `null` first set is drift for `--check`; a missing file is written from `Params.defaults(profile)` |

### 13.2 Fixtures

- `tests/fixtures/oracle.py` (independent of core, no `energatron` import) gains: per-day sums and per-label TOU sums of
  a local-table spec under a window set on a chosen clock; hour-cell sums; aligned-block peak; the percentile of a list
  by the same linear rule, written out; and the same quantities on the **meter** clock, which §12 uses.
- `tests/fixtures/pipeline_workspace.py` grows a P3 layer: the P2 fixture workspace already spans both daylight-saving
  changes of a year under `nem_aest` and confirms the basis, so `usage_profile` runs on `localise`'s real output. It
  gains a second window-set variant (a made-up change date inside the span) and a 14-month variant so the reference
  window (12 months) fits, and a short variant for `reference_window` failing. `tests/fixtures/params_text.py` gains
  `params_v3_text(...)` (a made-up `analysis` block, optionally with a second window set).
- `tests/fixtures/usage_marts.py`: a builder of small, valid mart DataFrames and a `UsageSummary` for the page tests,
  so the renderer tests do not need a pipeline.

### 13.3 Stages

| Test | Covers |
|---|---|
| `tests/unit/services/stages/test_usage_profile.py` | one Parquet per table plus `summary.json`; each table equals the oracle's value on the fixture; each check passing and failing (a table with two interval lengths; a window set order that leaves a date unresolved is rejected earlier by params, so the check is tested with a doctored mart; a short fixture fails `reference_window`; a low-coverage window fails it; a tampered `tou` label leaves `tou_conserved` failing); the warnings; a `null` zone or first window set → `E_INVALID_INPUT`; an export suffix is excluded and counted; no identifier column anywhere |
| `tests/unit/services/stages/test_usage_report.py` | reads only the profile directory; writes `index.html` and `_provenance.json`; the same marts twice give identical bytes; a mart directory with a wrong kind or version → `E_INVALID_INPUT`; accepts the empty `params` |
| `tests/integration/test_p3_pipeline.py` | a scratch workspace: `pipeline sync` (params v3), `land` of the fixtures, `fingerprint`, `dvc repro`: all eight stages pass; changing `analysis.reference_months` makes `usage_profile` and `usage_report` stale and nothing upstream; changing only `usage_report`'s code entry (a test double) leaves `usage_profile` up to date; editing a window set's `effective_from` changes the TOU tables and the report; the P2 conditions still hold (winter-only fixtures stop at `localise`'s gate, and `usage_profile` then has no input and does not run); `verify` passes on the clean state |

---

## 14. Deferred

- Resampling when the interval length changes between downloads (P2 spec §15): `usage_profile` fails `one_interval_length`.
- Export (solar) registers and their profile: when such data appears and its spec is written.
- Public holidays as weekend-like days: a region-profile feature; weekday/weekend is Saturday/Sunday by `analysis.weekend`
  until then (§17 Q9).
- The TOU split for each candidate offer's windows and the per-offer cost: P4 (the seam is `tou_split`, §6).
- Date-ranged windows for `validate_timebase`: §17 Q10.
- Supply-charge days: P4.
- An interactive report (filters, zoom, JS): P10's web UI, not this file.
- A per-hour demand profile for demand tariffs beyond the monthly peak: P4, if an offer needs it.

---

## 15. Plan, D1 and P2 spec changes

To be applied on `/done` of P3.1.1, as plan rev 11 and D1 rev 4 (§17 Q3), with these edits in place:

**Plan**
- §9.1 DAG: `usage_profile` is followed by `usage_report`; `recommend` still reads `marts/usage_profile` (unchanged).
- §9.2 table: the `usage_profile` row becomes two rows: `usage_profile` (deps `interim/elec_interval_local`, params
  `meter.local_tz`, `analysis`; out `marts/usage_profile/`; checks: one interval length, conservation, TOU
  partition and conservation, `reference_window`: the last `reference_months` covered months at the coverage
  threshold) and `usage_report` (dep `marts/usage_profile`, no params; out `reports/usage/`; check: a deterministic
  self-contained HTML built from the marts alone).
- §9.2 `params.yaml` example: `schema_version: 3`; the `analysis` block as §4.1 (the placeholder `reference_months` and
  `sensitivity` lines are replaced; `sensitivity` returns with P6).
- §10.3: table list and definitions as §5 (reference window, covered months, complete days and full cells, aligned
  demand blocks, quality classes), TOU split by date-ranged window sets (§6) with the offer-window split left to P4, the
  HTML as §9.
- §14.1 ("No region-specific values in code"): the first TOU window set is copied from the region profile by
  `pipeline sync`, like the validation windows.
- §15 P3 acceptance: made concrete as §12 of this spec.

**D1 (rev 4)**
- §2: `core/` gains `usage.py`, `charts.py`, `usage_report.py` and `models/usage.py`; `core/timebase.py` gains
  `local_day_minutes`.
- §7: the two P3 warnings.
- §9.1: a stage may declare no params (`params=()`); the P3 integration test confirms the runner, `prepare_stage`
  and the emitter accept it (§10).
- Nothing else: the layers, ports, stage contract and determinism rules are used as they are. P3 adds no process-state
  change, no dynamic import and no new zone construction (`usage_profile` takes its zone from `run.zone()` and passes
  it, as an object, to core).

**P2 spec** (not edited; recorded here): `local_day_minutes` moves from `localise.py` into `core/timebase.py` with no
change in behaviour (P2 spec §10 describes the check, which does not change); `localise`'s tests stand.

---

## 16. Risks

- **A wrong second window set misleads the TOU split.** The window history is the owner's to confirm (§12 step 2), and
  the report states which sets it used. A wrong set shows up in the next retailer export, where `validate_timebase`
  would report the period as unexplained (it does not read the analysis sets, so it is an independent check).
- **A large report.** The heatmap's one `<rect>` per cell dominates the size (§9.3 budget). If a longer history makes it
  heavy, a later sub-task can draw runs of equal colour as one shape; the byte-determinism rule is unaffected.
- **`analysis` as one DVC param subtree** reruns `usage_profile` on any edit, including a cosmetic one. That is cheap
  (seconds) and avoids a key list to keep in step with the stage.
- **The `W_USAGE_PARTIAL_DAYS` list for a household with meter outages** can be long; it holds counts and the dates in
  `details`, and the dates are workspace data, not tool-repo text.

---

## 17. Open questions / decisions for the owner

Each is decided here so P3 can be built; the recommendation is what the spec says. The owner's answers are recorded on
`/done` of P3.1.1.

1. **Two stages, not one (§9, §10, §15).** **Decided by the owner, 2026-11-03: recommendation accepted.**
   `usage_profile` and `usage_report`, so the report is built from the marts alone (its only input is the mart
   directory) and re-renders without recomputing a metric. The marts are also the stable interface for later phases'
   usage summaries (`recommend` reads them); P4's cost engine still reads `elec_interval_local` directly.
   Alternative (declined): the plan's single stage with two outputs, where nothing structural stops the report reading
   the stage's input.
2. **Date-ranged TOU window sets in `params.yaml` (§4.1, §6).** **Decided by the owner, 2026-11-03: recommendation
   accepted, with the clarification that retailer plan details (TOU windows, dates, rates) are not private; only the
   household's usage and identifiers are.** The windows and their effective date are public facts about a retailer plan,
   `params.yaml` already holds the validation windows, and the seam is what P4 needs. The plan-change date therefore
   appears in `params.yaml`, which the P2 spec §17 Q5 had flagged. Alternatives (declined): one set and a caution; or the
   sets in the private config as an input.
3. **Applying the plan and D1 changes (§15).** **Decided by the owner, 2026-11-03: recommendation accepted.** Recommendation: `/done` of P3.1.1 applies plan rev 11 and D1 rev 4
   together, as for P2; they are few. Alternative: a separate sub-task.
4. **`reference_window` fails the stage (§8).** **Decided by the owner, 2026-11-03: recommendation accepted.** Recommendation: fail when fewer than `reference_months` covered months
   exist or coverage is below `coverage_threshold`, because P4's annual costing and P6's recommendation need a full year
   and plan §9.2 makes the coverage threshold a failing check. Alternative: warn and describe the shorter window, which
   would let a household with eight months of data run P3 but leaves later phases to refuse.
5. **Daylight-saving days in hourly metrics (§3, §5.4, §5.5).** **Decided by the owner, 2026-11-03: recommendation accepted.** Recommendation: the two affected days contribute to daily
   and monthly totals but not to the hourly profile or heatmap cells that are not full, and the heatmap shows them as
   blank. Alternatives: map the 25th hour onto the repeated wall hour by summing (it would double that cell), or drop the
   two days entirely (it would lose their energy from daily totals).
6. **Hand-written SVG and no new dependency (§9.1).** **Decided by the owner, 2026-11-03: recommendation accepted.** Recommendation: as written, for byte-stable, offline, auditable
   output. Alternatives: matplotlib (heavy, SVG output varies by version and font), plotly (JavaScript and assets), a
   template engine (a dependency for little gain).
7. **How "reconciles with the legacy notebook" is decided (§12 step 6).** **Decided by the owner, 2026-11-03: recommendation accepted.** Recommendation: four comparisons, R1–R4, run in
   the workspace by a throwaway script, with the legacy method re-run on its own file and the differences accounted for as
   the time-basis and merge effects. The legacy notebook's own saved outputs are not read. Alternative: compare against
   the notebook's saved numbers, which means reading its outputs (they hold usage figures) and risks nothing being
   reproducible.
8. **Consumption registers only (§3).** **Decided by the owner, 2026-11-03: recommendation accepted.** Recommendation: profile `E*` suffixes; leave export registers out and count
   them. Alternative: sum all registers, which would corrupt a household with solar.
9. **No public holidays (§14).** **Decided by the owner, 2026-11-03: recommendation accepted.** Recommendation: weekday/weekend by Saturday/Sunday through `analysis.weekend`; holidays
   wait for a region-profile feature. Alternative: add a holiday list to params now, which the owner would maintain by
   hand.
10. **`validate_timebase` and the window history (§6).** **Decided by the owner, 2026-11-03: recommendation accepted.** Recommendation: leave it alone. P3 adds its own sets;
    `W_TIMEBASE_UNEXPLAINED` stays as an independent signal. Alternative: have it read `analysis.tou.window_sets` so the
    three unexplained periods become explained, which changes a finished P2 stage and its evidence rule; a maintenance
    sub-task if the owner wants it.
11. **What §15 changes in earlier decisions.** Nothing in hand-over §4. It does change plan text (§9.1, §9.2, §10.3, §14.1)
    and D1 (§2, §7, §9.1, all additive), and P2 code placement of one helper. **Decided by the owner, 2026-11-03: recommendation accepted.** Recommendation: approve with this spec; all
    are listed in §15.
