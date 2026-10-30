"""The `merge_interval` stage (P2 spec §7.2): the staged datasets as one table of meter intervals, plus a report.

Candidates are the current datasets that `parse_nem12` could read. For each `(suffix, meter_date)` that one or more
candidates hold, the whole day is kept from the candidate with the newest `UpdateDateTime`, ties broken by dataset
precedence; the day carries its own `UpdateDateTime`, so a per-day choice is the plan's per-interval rule. A candidate's
day that differs in value or quality from the kept one is a finding, not a failure: it becomes a row of
`conflicts.parquet` and counts in `summary.json`. Superseded and unread datasets are named in the summary and not used.

Everything is keyed by the meter clock (`meter_start`, naive), because which real clock that is stays unknown until
`validate_timebase`. The inputs hold no identifier, so the outputs can't.
"""

import math
from pathlib import Path
from typing import Final

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from energatron.core.errors import InvalidInputError, WarningCode
from energatron.core.models.meter import (
    CONFLICTS_COLUMNS,
    CONFLICTS_KIND,
    CONFLICTS_SORT,
    CONFLICTS_VERSION,
    INDEX_KIND,
    INDEX_VERSION,
    INTERVALS_COLUMNS,
    INTERVALS_KIND,
    INTERVALS_SORT,
    INTERVALS_VERSION,
    STAGED_KIND,
    STAGED_VERSION,
    MergeSummary,
    SkippedDatasets,
)
from energatron.services.files import write_json, write_parquet
from energatron.services.stages.runner import StageRun, StageSpec
from energatron.services.stages.tables import INDEX_SCHEMA, STAGED_SCHEMA

INDEX_FILENAME: Final = "index.parquet"
INTERVALS_FILENAME: Final = "intervals.parquet"
CONFLICTS_FILENAME: Final = "conflicts.parquet"
SUMMARY_FILENAME: Final = "summary.json"
_DAY: Final = ["suffix", "meter_date"]
_INTERVAL: Final = ["suffix", "meter_start"]

CONFLICTS_SCHEMA: Final = pa.schema(
    [
        ("suffix", pa.string()),
        ("meter_date", pa.date32()),
        ("kept_dataset_id", pa.string()),
        ("dropped_dataset_id", pa.string()),
        ("kept_update_dt", pa.timestamp("us")),
        ("dropped_update_dt", pa.timestamp("us")),
        ("intervals_differing", pa.int16()),
        ("quality_differing", pa.int16()),
        ("kept_kwh", pa.float64()),
        ("dropped_kwh", pa.float64()),
        ("kept_flags", pa.string()),
        ("dropped_flags", pa.string()),
    ]
)


def _run(run: StageRun) -> None:
    staged = run.input("staged")
    index = _load(staged / INDEX_FILENAME, INDEX_SCHEMA, INDEX_KIND, INDEX_VERSION).to_pandas()
    index = index.sort_values("precedence", kind="stable")
    used: list[str] = []
    superseded: list[str] = []
    unread: list[str] = []
    for row in index.itertuples():
        ident = str(row.dataset_id)
        if not row.is_current:
            superseded.append(ident)
        elif not row.read_ok:
            unread.append(ident)
        else:
            used.append(ident)
    rank = {
        str(ident): int(precedence) for ident, precedence in zip(index["dataset_id"], index["precedence"], strict=True)
    }

    frames = [
        _load(staged / f"{ident}.parquet", STAGED_SCHEMA, STAGED_KIND, STAGED_VERSION, dataset=ident).to_pandas()
        for ident in used
    ]
    table = pd.concat(frames, ignore_index=True) if frames else STAGED_SCHEMA.empty_table().to_pandas()
    table = table.assign(precedence=table["dataset_id"].map(rank))

    kept, conflicts, overlap_days = _merge(table)

    same_update = _same_update_days(conflicts)
    summary = MergeSummary(
        datasets_used=used,
        datasets_skipped=SkippedDatasets(superseded=superseded, unread=unread),
        days={str(suffix): int(count) for suffix, count in kept.groupby("suffix")["meter_date"].nunique().items()},
        overlap_days=overlap_days,
        conflict_days=_day_count(conflicts),
        conflict_intervals=int(conflicts["any_differing"].sum()) if len(conflicts) else 0,
        same_update_conflicts=same_update,
    )

    write_parquet(
        kept.reindex(columns=list(INTERVALS_COLUMNS)).astype({"reason_code": "Int16"}),
        run.output("interval") / INTERVALS_FILENAME,
        kind=INTERVALS_KIND,
        schema_version=INTERVALS_VERSION,
        sort_by=INTERVALS_SORT,
        schema=STAGED_SCHEMA,
    )
    report = run.output("report")
    write_parquet(
        conflicts.reindex(columns=list(CONFLICTS_COLUMNS)),
        report / CONFLICTS_FILENAME,
        kind=CONFLICTS_KIND,
        schema_version=CONFLICTS_VERSION,
        sort_by=CONFLICTS_SORT,
        schema=CONFLICTS_SCHEMA,
    )
    write_json(report / SUMMARY_FILENAME, summary.model_dump(mode="json"))

    duplicates = int(kept.duplicated(_INTERVAL).sum())
    run.check("key_unique", duplicates == 0, duplicates=duplicates)
    lengths = {
        str(suffix): sorted(int(length) for length in found)
        for suffix, found in table.groupby("suffix")["interval_minutes"].unique().items()
        if len(found) > 1
    }
    run.check("one_interval_length", not lengths, lengths=lengths)
    if same_update:
        run.warn(
            WarningCode.W_MERGE_SAME_UPDATE,
            "Some days differ between datasets with the same UpdateDateTime; precedence chose between them.",
            same_update_conflicts=same_update,
        )


def _load(path: Path, schema: pa.Schema, kind: str, version: int, *, dataset: str | None = None) -> pa.Table:
    """A staged table, checked against its kind, version and columns; anything else → `E_INVALID_INPUT`.

    A dataset's table (`dataset` given) must hold only rows of that dataset, the one its file name and the index name,
    so every row has a precedence."""
    try:
        table = pq.read_table(path)
    except OSError, pa.ArrowException:
        raise InvalidInputError("A staged table is missing or isn't Parquet.", input="staged", file=path.name) from None
    metadata = table.schema.metadata or {}
    found = (metadata.get(b"energatron.kind"), metadata.get(b"energatron.schema_version"))
    if found != (kind.encode(), str(version).encode()) or not table.schema.remove_metadata().equals(schema):
        raise InvalidInputError(f"A staged table isn't a {kind} v{version} table.", input="staged", file=path.name)
    if dataset is not None and set(table.column("dataset_id").to_pylist()) - {dataset}:
        raise InvalidInputError("A staged table holds rows of another dataset.", input="staged", file=path.name)
    return table


def _merge(table: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, int]:
    """The kept rows, the conflict rows (with a helper column `any_differing`) and the number of overlap days."""
    if table.empty:
        return table.drop(columns="precedence"), pd.DataFrame(columns=[*CONFLICTS_COLUMNS, "any_differing"]), 0
    days = (
        table.groupby([*_DAY, "dataset_id"], sort=True)
        .agg(update_dt=("update_dt", "max"), precedence=("precedence", "first"))
        .reset_index()
    )
    ranked = days.sort_values([*_DAY, "update_dt", "precedence"], ascending=[True, True, False, True], kind="stable")
    winners = ranked.drop_duplicates(_DAY, keep="first")[[*_DAY, "dataset_id"]].assign(_kept=True)
    overlap_days = int((days.groupby(_DAY).size() > 1).sum())

    marked = table.merge(winners, on=[*_DAY, "dataset_id"], how="left")
    is_kept = marked["_kept"].notna()
    kept = marked[is_kept].drop(columns=["_kept", "precedence"])
    dropped = marked[~is_kept].drop(columns="_kept")
    if dropped.empty:
        return kept, pd.DataFrame(columns=[*CONFLICTS_COLUMNS, "any_differing"]), overlap_days
    return kept, _conflicts(kept, dropped), overlap_days


def _flags(flags: pd.Series[str]) -> str:
    return "".join(sorted(set(flags)))


def _conflicts(kept: pd.DataFrame, dropped: pd.DataFrame) -> pd.DataFrame:
    """One row per dropped candidate day whose values or qualities differ from the kept day."""
    kept_side = (
        kept[[*_INTERVAL, "kwh", "quality"]]
        .drop_duplicates(_INTERVAL)
        .rename(columns={"kwh": "kept_value", "quality": "kept_quality"})
    )
    pairs = dropped.merge(kept_side, on=_INTERVAL, how="left")
    kwh_differs = pairs["kept_value"].isna() | (pairs["kwh"] != pairs["kept_value"])
    quality_differs = pairs["kept_quality"].isna() | (pairs["quality"] != pairs["kept_quality"])
    pairs = pairs.assign(
        kwh_differs=kwh_differs, quality_differs=quality_differs, any_differing=kwh_differs | quality_differs
    )
    found = (
        pairs.groupby([*_DAY, "dataset_id"], sort=True)
        .agg(
            dropped_update_dt=("update_dt", "max"),
            intervals_differing=("kwh_differs", "sum"),
            quality_differing=("quality_differs", "sum"),
            any_differing=("any_differing", "sum"),
            dropped_kwh=("kwh", math.fsum),
            dropped_flags=("quality_flag", _flags),
        )
        .reset_index()
        .rename(columns={"dataset_id": "dropped_dataset_id"})
    )
    found = found[found["any_differing"] > 0]
    if found.empty:
        return pd.DataFrame(columns=[*CONFLICTS_COLUMNS, "any_differing"])
    kept_days = (
        kept.groupby(_DAY, sort=True)
        .agg(
            kept_dataset_id=("dataset_id", "first"),
            kept_update_dt=("update_dt", "max"),
            kept_kwh=("kwh", math.fsum),
            kept_flags=("quality_flag", _flags),
        )
        .reset_index()
    )
    return found.merge(kept_days, on=_DAY, how="left")


def _day_count(conflicts: pd.DataFrame) -> int:
    return len(conflicts.drop_duplicates(_DAY)) if len(conflicts) else 0


def _same_update_days(conflicts: pd.DataFrame) -> int:
    """Conflict days where a dropped day has the kept day's `UpdateDateTime`: precedence alone decided them."""
    if not len(conflicts):
        return 0
    tied = conflicts[conflicts["kept_update_dt"] == conflicts["dropped_update_dt"]]
    return len(tied.drop_duplicates(_DAY))


SPEC: Final = StageSpec(
    name="merge_interval",
    inputs={"staged": "data/staged/elec_interval"},
    params=(),
    outputs={"interval": "data/interim/elec_interval_nem", "report": "data/reports/validation/merge"},
    impl=_run,
)
