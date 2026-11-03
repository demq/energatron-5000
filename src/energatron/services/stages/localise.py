"""The `localise` stage (P2 spec §10): the merged meter intervals placed on the real clock.

The merged table is keyed by the naive meter clock. Under the configured `meter.timebase` and the zone from
`StageRun.zone()`, each interval gets `utc_start`, `local_start`, `local_date` and the zone's `utc_offset_minutes`,
which tells the repeated hour of a daylight-saving fall-back apart.

A gate comes first. `validate_timebase` must have judged the same basis as the param and given the verdict
`confirmed`; `unconfirmed` and `contradicted` both fail it, so no local-time data is ever built on a basis the
evidence hasn't settled (hand-over decision #11). A failed gate writes no table and runs no other check.

The checks hold counts only, never a value.
"""

import math
from collections.abc import Mapping
from datetime import UTC, date, tzinfo
from pathlib import Path
from typing import Final, cast

import pandas as pd
import pyarrow as pa

from energatron.core.errors import InternalError, InvalidInputError
from energatron.core.models.meter import (
    INTERVALS_KIND,
    INTERVALS_VERSION,
    LOCAL_COLUMNS,
    LOCAL_KIND,
    LOCAL_SORT,
    LOCAL_VERSION,
    TimebaseResult,
)
from energatron.core.timebase import LocalTimes, Timebase, local_day_minutes, to_local
from energatron.services.files import read_json, write_parquet
from energatron.services.stages.runner import StageRun, StageSpec
from energatron.services.stages.tables import STAGED_SCHEMA, load_table

INTERVALS_FILENAME: Final = "intervals.parquet"
RESULT_FILENAME: Final = "result.json"
_MINUTES_PER_DAY: Final = 1440

LOCAL_SCHEMA: Final = pa.schema(
    [
        ("suffix", pa.string()),
        ("utc_start", pa.timestamp("us", tz=UTC)),
        ("local_start", pa.timestamp("us")),
        ("local_date", pa.date32()),
        ("utc_offset_minutes", pa.int16()),
        ("meter_start", pa.timestamp("us")),
        ("meter_date", pa.date32()),
        ("interval_minutes", pa.int16()),
        ("kwh", pa.float64()),
        ("quality", pa.string()),
        ("quality_flag", pa.string()),
        ("reason_code", pa.int16()),
        ("dataset_id", pa.string()),
    ]
)


def _run(run: StageRun) -> None:
    configured = Timebase(_text(run.params, "meter.timebase"))
    zone = run.zone(_text(run.params, "meter.local_tz"))
    report = _report(run.input("timebase") / RESULT_FILENAME)
    gate = report.verdict == "confirmed" and report.configured == configured.value
    run.check(
        "timebase_gate", gate, verdict=report.verdict, report_basis=report.configured, param_basis=configured.value
    )
    if not gate:
        return

    merged = load_table(
        run.input("interval") / INTERVALS_FILENAME, STAGED_SCHEMA, INTERVALS_KIND, INTERVALS_VERSION, input="interval"
    ).to_pandas()
    placed = to_local(merged["meter_start"], configured, zone)
    out = _local_table(merged, placed)
    write_parquet(
        out,
        run.output("interval") / INTERVALS_FILENAME,
        kind=LOCAL_KIND,
        schema_version=LOCAL_VERSION,
        sort_by=LOCAL_SORT,
        schema=LOCAL_SCHEMA,
    )

    run.check(
        "energy_conserved",
        len(out) == len(merged) and math.fsum(out["kwh"]) == math.fsum(merged["kwh"]),
        rows=len(merged),
    )
    located = out[~placed.unplaceable]
    duplicates = int(located.duplicated(["suffix", "utc_start"]).sum())
    run.check("key_unique", duplicates == 0, duplicates=duplicates)
    unplaceable = int(placed.unplaceable.sum())
    run.check("local_placeable", unplaceable == 0, unplaceable=unplaceable)
    days = _day_lengths(located, zone)
    run.check(
        "local_day_lengths",
        days.wrong == 0,
        days=days.checked,
        wrong=days.wrong,
        dst_days={"short": days.short, "long": days.long},
    )


def _text(params: Mapping[str, object], key: str) -> str:
    value = params.get(key)
    if not isinstance(value, str):
        raise InternalError(f"localise got no {key}.")
    return value


def _report(path: Path) -> TimebaseResult:
    """The time-basis report `validate_timebase` wrote; one that is missing or isn't a `timebase_result` →
    `E_INVALID_INPUT`."""
    try:
        doc = read_json(path)
        if not isinstance(doc, dict):
            raise ValueError("not an object")
        return TimebaseResult.from_document(doc)
    except OSError, ValueError, InvalidInputError:
        raise InvalidInputError(
            "The time-basis report is missing or isn't a timebase_result.", input="timebase", file=path.name
        ) from None


def _local_table(merged: pd.DataFrame, placed: LocalTimes) -> pd.DataFrame:
    """The merged rows with the placed times, in `LOCAL_COLUMNS` order, row for row."""
    local = placed.local_start
    local_date = local.dt.normalize().dt.date.astype(object).where(local.notna(), None)
    return merged.assign(
        utc_start=placed.utc_start,
        local_start=local,
        local_date=local_date,
        utc_offset_minutes=placed.utc_offset_minutes,
        reason_code=merged["reason_code"].astype("Int16"),
    ).reindex(columns=list(LOCAL_COLUMNS))


class _Days:
    def __init__(self) -> None:
        self.checked = 0
        self.wrong = 0
        self.short = 0
        self.long = 0


def _day_lengths(located: pd.DataFrame, zone: tzinfo) -> _Days:
    """Check each register's complete local days: the rows of a local date equal its length in the zone divided by the
    interval length. Only dates strictly between the register's first and last local date count, because the first and
    last are cut by the data's edges."""
    found = _Days()
    if located.empty:
        return found
    counts = (
        located.groupby(["suffix", "local_date"], sort=True)
        .agg(rows=("kwh", "size"), minutes=("interval_minutes", "first"))
        .reset_index()
    )
    for _, group in counts.groupby("suffix", sort=True):
        first, last = group["local_date"].min(), group["local_date"].max()
        for local_date, rows, minutes in zip(group["local_date"], group["rows"], group["minutes"], strict=True):
            if not first < local_date < last:
                continue
            length = local_day_minutes(cast("date", local_date), zone)
            found.checked += 1
            if length % int(minutes) or length // int(minutes) != int(rows):
                found.wrong += 1
            if length < _MINUTES_PER_DAY:
                found.short += 1
            elif length > _MINUTES_PER_DAY:
                found.long += 1
    return found


SPEC: Final = StageSpec(
    name="localise",
    inputs={"interval": "data/interim/elec_interval_nem", "timebase": "data/reports/validation/timebase"},
    params=("meter.local_tz", "meter.timebase"),
    outputs={"interval": "data/interim/elec_interval_local"},
    impl=_run,
)
