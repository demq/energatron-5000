"""The usage report page, built from the `usage_profile` marts alone (P3 spec §9).

`render(marts)` is pure: the nine tables and the summary in, one self-contained HTML string out. There is no I/O, no
clock and no network asset: one inline `<style>`, inline SVG from `core/charts.py`, no script. Same marts, same bytes:
the rows of each table are sorted by their key here, so the order a caller loaded them in does not matter.

The page holds the household's usage, so it is private data and lives only in the workspace. This module knows no
identifier: the marts hold none, and every text that comes from them goes through `esc`.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

import pandas as pd

from energatron.core import charts
from energatron.core.charts import Series, esc, format_number
from energatron.core.models import usage as kinds
from energatron.core.models.usage import QUALITY_CLASSES, SuffixSummary, UsageSummary

TITLE: Final = "Household electricity usage"
FEW_DAYS: Final = 5
"""An hourly-profile panel built from fewer days than this says so."""

_SORTS: Final = {
    "daily": kinds.DAILY_SORT,
    "monthly": kinds.MONTHLY_SORT,
    "seasonal": kinds.SEASONAL_SORT,
    "yoy": kinds.YOY_SORT,
    "hourly_profile": kinds.HOURLY_PROFILE_SORT,
    "heatmap": kinds.HEATMAP_SORT,
    "tou_daily": kinds.TOU_DAILY_SORT,
    "tou_monthly": kinds.TOU_MONTHLY_SORT,
    "quality_monthly": kinds.QUALITY_MONTHLY_SORT,
}
_QUALITY_TONES: Final = {"actual": "c1", "estimated": "c2", "substituted": "c3", "other": "cx"}

_PAGE_CSS: Final = (
    "body{margin:0;background:var(--page);color:var(--ink);font-family:system-ui,-apple-system,'Segoe UI',sans-serif;"
    "line-height:1.45}"
    "main{max-width:980px;margin:0 auto;padding:16px}"
    "h1{font-size:26px;margin:8px 0 4px}"
    "h2{font-size:21px;margin:32px 0 4px;padding-top:12px;border-top:1px solid var(--grid)}"
    "h3{font-size:16px;margin:24px 0 4px}"
    "p,li{font-size:14px;color:var(--ink2)}"
    "p.lead{color:var(--ink2)}"
    "p.note{font-size:13px}"
    "p.caption{font-size:13px;font-weight:600;color:var(--ink);margin:16px 0 2px}"
    ".panels{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:8px}"
    "details{margin:4px 0 8px}"
    "summary{cursor:pointer;font-size:13px;color:var(--ink2)}"
)


@dataclass(frozen=True)
class UsageMarts:
    """What the report reads: the nine mart tables by file stem (`daily`, `monthly`, …) and the summary."""

    tables: Mapping[str, pd.DataFrame]
    summary: UsageSummary


def _text(value: object) -> str:
    """A key or label from a table as plain text."""
    if isinstance(value, pd.Timestamp):
        return value.date().isoformat()
    isoformat = getattr(value, "isoformat", None)
    return str(isoformat()) if callable(isoformat) else str(value)


def _num(value: object) -> float | None:
    """A cell as a float, or `None` for a null, `NaN` or a missing value."""
    if value is None or value is pd.NA or value is pd.NaT:
        return None
    number = float(value)  # type: ignore[arg-type]
    return None if number != number else number


def _percent(share: float | None, places: int = 1) -> str:
    return charts.format_number(None if share is None else share * 100.0, places) + ("%" if share is not None else "")


def _minute(value: object) -> str | None:
    if value is None or value is pd.NaT or value is pd.NA:
        return None
    stamp = pd.Timestamp(value)  # type: ignore[arg-type]
    return None if pd.isna(stamp) else stamp.strftime("%Y-%m-%d %H:%M")


class _Register:
    """One register's tables, sorted by key, and its summary."""

    def __init__(self, marts: UsageMarts, suffix: str, index: int) -> None:
        self.suffix = suffix
        self.index = index
        self.info: SuffixSummary = marts.summary.suffixes[suffix]
        self.summary = marts.summary
        self._tables = marts.tables

    def rows(self, name: str) -> pd.DataFrame:
        frame = self._tables[name]
        picked = frame[frame["suffix"] == self.suffix]
        return picked.sort_values(list(_SORTS[name]), kind="stable").reset_index(drop=True)

    def cid(self, name: str) -> str:
        return f"{name}-{self.index}"


def _caption(text: str) -> str:
    return f'<p class="caption">{esc(text)}</p>'


def _details(table: str) -> str:
    return f"<details><summary>Table</summary>{table}</details>"


def _headline(reg: _Register) -> str:
    info = reg.info
    parts = [f'<p class="lead">Data from {esc(info.first_date.isoformat())} to {esc(info.last_date.isoformat())}.</p>']
    if info.window_start is None or info.window_end is None:
        parts.append(
            "<p>No reference window: no month has enough data to count as covered, so the figures below are empty.</p>"
        )
        return "".join(parts)
    parts.append(
        f"<p>Reference window: {esc(info.window_start)} to {esc(info.window_end)}. "
        f"{info.window_complete_days} of {info.window_days} days complete "
        f"({esc(_percent(info.window_coverage))}).</p>"
    )
    rows = [
        ["Average daily use (kWh)", format_number(info.avg_daily_kwh, 2)],
        ["Base load (kW)", format_number(info.base_load_kw, 3)],
        ["Peak demand (kW)", format_number(info.peak_demand_kw, 3)],
        ["Load factor", format_number(info.load_factor, 2)],
    ]
    parts.append(charts.data_table(["Measure", "Reference window"], rows))
    if info.tou_shares:
        shares = [[label, _percent(share)] for label, share in sorted(info.tou_shares.items())]
        parts.append(charts.data_table(["Time-of-use label", "Share of use"], shares))
    return "".join(parts)


def _monthly(reg: _Register) -> str:
    monthly = reg.rows("monthly")
    months = [_text(m) for m in monthly["month"]]
    partial = [
        i
        for i, (covered, complete, days) in enumerate(
            zip(monthly["covered"], monthly["complete_days"], monthly["days_in_month"], strict=True)
        )
        if not covered or int(complete) < int(days)
    ]
    notes = {
        i: f"Partial month: {int(monthly['complete_days'][i])} of {int(monthly['days_in_month'][i])} days complete"
        for i in partial
    }
    desc = f"Energy used in each month, in kWh, for {len(months)} months."
    if partial:
        desc += " Partial months, drawn hatched: " + ", ".join(months[i] for i in partial) + "."
    chart = charts.column_chart(
        reg.cid("monthly"),
        title="Energy used by month",
        desc=desc,
        categories=months,
        series=[Series("Energy", [_num(v) for v in monthly["kwh"]], "c1")],
        unit="kWh",
        hatched=partial,
        notes=notes,
        empty="No months.",
    )
    table = charts.data_table(
        ["Month", "Energy (kWh)", "Complete days", "Days in month"],
        [
            [m, format_number(_num(k), 1), str(int(c)), str(int(d))]
            for m, k, c, d in zip(
                months, monthly["kwh"], monthly["complete_days"], monthly["days_in_month"], strict=True
            )
        ],
    )
    return chart + _details(table)


def _season_order(daily: pd.DataFrame) -> dict[str, int]:
    """Seasons by the earliest calendar month they contain (DJF, MAM, JJA, SON for the default partition)."""
    first: dict[str, int] = {}
    for month, season in zip(daily["month"], daily["season"], strict=True):
        number = int(str(month)[5:7])
        first[str(season)] = min(first.get(str(season), 99), number)
    return first


def _average(reg: _Register) -> str:
    monthly = reg.rows("monthly")
    daily = reg.rows("daily")
    months = [_text(m) for m in monthly["month"]]
    season_of: dict[str, str] = {}
    for month, season in zip(daily["month"], daily["season"], strict=True):
        season_of.setdefault(str(month), str(season))
    groups: list[tuple[str, int, int]] = []
    for i, month in enumerate(months):
        season = season_of.get(month)
        if season is None:
            continue
        if groups and groups[-1][0] == season and groups[-1][2] == i - 1:
            groups[-1] = (season, groups[-1][1], i)
        else:
            groups.append((season, i, i))
    values = [_num(v) for v in monthly["avg_daily_kwh"]]
    chart = charts.column_chart(
        reg.cid("average"),
        title="Average daily use by month",
        desc="Average energy per complete day in each month, in kWh per day, with the season named under the months.",
        categories=months,
        series=[Series("Average per day", values, "c1")],
        unit="kWh per day",
        places=2,
        groups=groups,
        empty="No months.",
    )
    monthly_table = charts.data_table(
        ["Month", "Average per day (kWh)"], [[m, format_number(v, 2)] for m, v in zip(months, values, strict=True)]
    )
    seasonal = reg.rows("seasonal")
    order = _season_order(daily)
    ordered = sorted(
        range(len(seasonal)),
        key=lambda i: (
            int(seasonal["season_year"][i]),
            order.get(str(seasonal["season"][i]), 99),
            str(seasonal["season"][i]),
        ),
    )
    seasonal_table = charts.data_table(
        ["Season year", "Season", "Complete days", "Energy (kWh)", "Average per day (kWh)"],
        [
            [
                str(int(seasonal["season_year"][i])),
                str(seasonal["season"][i]),
                str(int(seasonal["days"][i])),
                format_number(_num(seasonal["kwh"][i]), 1),
                format_number(_num(seasonal["avg_daily_kwh"][i]), 2),
            ]
            for i in ordered
        ],
        caption="Average daily use by season",
    )
    return chart + _details(monthly_table) + seasonal_table


def _yoy(reg: _Register) -> str:
    yoy = reg.rows("yoy")
    months = [_text(m) for m in yoy["month"]]
    prior_months = [_text(m) for m in yoy["prior_month"]]
    current = [_num(v) for v in yoy["avg_daily_kwh"]]
    prior = [_num(v) for v in yoy["prior_avg_daily_kwh"]]
    change = [_num(v) for v in yoy["change_pct"]]

    def sign(value: float | None) -> str:
        if value is None:
            return charts.format_number(None)
        return ("+" if value > 0 else "") + format_number(value, 1) + "%"

    def line(category: int, series: int) -> str:
        if series == 0:
            return f"{prior_months[category]}: {format_number(prior[category], 2)} kWh per day"
        return f"{months[category]}: {format_number(current[category], 2)} kWh per day ({sign(change[category])})"

    chart = charts.column_chart(
        reg.cid("yoy"),
        title="Average daily use against a year earlier",
        desc="Average energy per complete day in each month beside the same month a year earlier, with the change.",
        categories=months,
        series=[Series("A year earlier", prior, "cx"), Series("This month", current, "c1")],
        unit="kWh per day",
        places=2,
        top_labels=[sign(v) for v in change],
        series_line=line,
        empty="No month has a prior year yet.",
    )
    table = charts.data_table(
        ["Month", "Month a year earlier", "Average per day (kWh)", "A year earlier (kWh)", "Change"],
        [
            [months[i], prior_months[i], format_number(current[i], 2), format_number(prior[i], 2), sign(change[i])]
            for i in range(len(months))
        ],
    )
    legend = charts.legend([("A year earlier", "cx"), ("This month", "c1")])
    return legend + chart + _details(table)


def _hourly(reg: _Register) -> str:
    profile = reg.rows("hourly_profile")
    low_p, mid_p, high_p = reg.summary.band_percentiles
    intro = (
        f'<p class="note">Line: mean energy in each clock hour over the reference window. '
        f"Band: p{low_p} to p{high_p} of the days (median p{mid_p}).</p>"
    )
    if profile.empty:
        return intro + "<p>No hourly profile: there is no reference window.</p>"
    order = _season_order(reg.rows("daily"))
    combos = sorted(
        {(str(s), str(d)) for s, d in zip(profile["season"], profile["daytype"], strict=True)},
        key=lambda c: (order.get(c[0], 99), c[0], c[1]),
    )
    top = max(
        [_num(v) or 0.0 for v in profile["p_high_kwh"]] + [_num(v) or 0.0 for v in profile["mean_kwh"]], default=0.0
    )
    panels = []
    for p, (season, daytype) in enumerate(combos):
        part = profile[(profile["season"] == season) & (profile["daytype"] == daytype)]
        hours = [int(h) for h in part["hour"]]
        cells = [int(c) for c in part["cells"]]
        mean = [_num(v) for v in part["mean_kwh"]]
        low = [_num(v) for v in part["p_low_kwh"]]
        high = [_num(v) for v in part["p_high_kwh"]]
        fewest = min(cells)
        tips = [
            f"{h:02d}:00 · mean {format_number(m, 2)} kWh · p{low_p} {format_number(lo, 2)} · "
            f"p{high_p} {format_number(hi, 2)} · {n} days"
            for h, m, lo, hi, n in zip(hours, mean, low, high, cells, strict=True)
        ]
        panels.append(
            charts.band_panel(
                f"hourly-{reg.index}-{p}",
                title=f"{season} · {daytype}",
                desc=(
                    f"Mean energy per clock hour on {season} {daytype} days, in kWh, "
                    f"with the p{low_p} to p{high_p} band."
                    + (f" Built from as few as {fewest} days." if fewest < FEW_DAYS else "")
                ),
                hours=hours,
                mean=mean,
                low=low,
                high=high,
                y_top=top,
                unit="kWh",
                caption=f"Only {fewest} days" if fewest < FEW_DAYS else "",
                tips=tips,
            )
        )
    return intro + '<div class="panels">' + "".join(panels) + "</div>"


def _heatmap(reg: _Register) -> str:
    frame = reg.rows("heatmap")
    days = sorted({_text(d) for d in frame["local_date"]})
    position = {d: i for i, d in enumerate(days)}
    cells = {
        (position[_text(d)], int(h)): v
        for d, h, k in zip(frame["local_date"], frame["hour"], frame["kwh"], strict=True)
        if (v := _num(k)) is not None
    }
    dst = reg.summary.dst_days
    desc = "Energy in each clock hour of each day, in kWh; a darker cell is more energy."
    if days:
        desc = (
            f"Energy in each clock hour of each day from {days[0]} to {days[-1]}, in kWh; a darker cell is more energy."
        )
    if dst.short + dst.long:
        desc += (
            " Blank cells are hours with no reading or that do not count: the hour that did not happen "
            "or happened twice on a daylight-saving day."
        )
    return charts.heatmap_chart(
        reg.cid("heatmap"),
        title="Energy by day and hour",
        desc=desc,
        days=days,
        cells=cells,
        unit="kWh",
        empty="No days.",
    )


def _tou(reg: _Register) -> str:
    tou = reg.rows("tou_monthly")
    months = sorted({_text(m) for m in tou["month"]})
    labels = sorted({str(label) for label in tou["label"]})
    tones = {label: charts.tone_for(i) for i, label in enumerate(labels)}
    cell: dict[tuple[str, str], tuple[float | None, float | None]] = {}
    mixed: set[str] = set()
    for month, label, kwh, share, flag in zip(
        tou["month"], tou["label"], tou["kwh"], tou["share"], tou["mixed_windows"], strict=True
    ):
        cell[(_text(month), str(label))] = (_num(share), _num(kwh))
        if bool(flag):
            mixed.add(_text(month))
    marked = [m + "*" if m in mixed else m for m in months]

    def line(category: int, series: int) -> str:
        share, kwh = cell.get((months[category], labels[series]), (None, None))
        return f"{labels[series]}: {_percent(share)} · {format_number(kwh, 1)} kWh"

    chart = charts.column_chart(
        reg.cid("tou"),
        title="Share of energy by time-of-use label",
        desc="Each month's energy split by time-of-use label, as a share of the month."
        + (" Months marked * span two window sets." if mixed else ""),
        categories=marked,
        series=[
            Series(label, [cell.get((m, label), (None, None))[0] or 0.0 for m in months], tones[label])
            for label in labels
        ],
        unit="share",
        stacked=True,
        axis_max=1.0,
        tick_label=lambda v: f"{v * 100:.0f}%",
        series_line=line,
        notes={i: "The month spans two window sets" for i, m in enumerate(months) if m in mixed},
        empty="No months.",
    )
    parts = [charts.legend([(label, tones[label]) for label in labels]), chart]
    if mixed:
        parts.append('<p class="note">* The month spans two window sets; its split mixes both.</p>')
    table = charts.data_table(
        ["Month", "Label", "Energy (kWh)", "Share", "Spans two window sets"],
        [
            [
                _text(m),
                str(label),
                format_number(_num(k), 1),
                _percent(_num(s)),
                "yes" if bool(f) else "no",
            ]
            for m, label, k, s, f in zip(
                tou["month"], tou["label"], tou["kwh"], tou["share"], tou["mixed_windows"], strict=True
            )
        ],
    )
    parts.append(_details(table))
    sets = reg.summary.window_sets_used
    if len(sets) <= 1:
        parts.append(
            "<p>Caution: the retailer's time-of-use windows may have changed during this history. "
            "The split above applies one set of windows to every date.</p>"
        )
    else:
        items = []
        for number, window_set in enumerate(sets, start=1):
            start = (
                "the start of the data" if window_set.effective_from is None else window_set.effective_from.isoformat()
            )
            windows = "; ".join(f"{label} {', '.join(times)}" for label, times in sorted(window_set.windows.items()))
            items.append(f"<li>Window set {number}, effective from {esc(start)}: {esc(windows)}</li>")
        parts.append("<p>The split uses these window sets, each from its date:</p><ul>" + "".join(items) + "</ul>")
    return "".join(parts)


def _monthly_lines(reg: _Register) -> str:
    monthly = reg.rows("monthly")
    months = [_text(m) for m in monthly["month"]]
    base = [_num(v) for v in monthly["base_load_kw"]]
    peak = [_num(v) for v in monthly["peak_demand_kw"]]
    factor = [_num(v) for v in monthly["load_factor"]]
    at = [_minute(v) for v in monthly["peak_demand_at"]]
    base_chart = charts.line_chart(
        reg.cid("base"),
        title="Base load by month",
        desc="The month's low-use level in kW: a percentile of the energy in the overnight window.",
        categories=months,
        values=base,
        unit="kW",
        places=3,
        empty="No months.",
    )
    peak_chart = charts.line_chart(
        reg.cid("peak"),
        title="Peak demand by month",
        desc="The month's highest block of use in kW. A tooltip names the time of the peak.",
        categories=months,
        values=peak,
        unit="kW",
        places=3,
        tips={i: f"at {t}" for i, t in enumerate(at) if t is not None},
        empty="No months.",
    )
    factor_chart = charts.line_chart(
        reg.cid("load"),
        title="Load factor by month",
        desc="Average demand divided by peak demand, from 0 to 1.",
        categories=months,
        values=factor,
        unit="",
        places=2,
        axis_max=1.0,
        empty="No months.",
    )
    table = charts.data_table(
        ["Month", "Base load (kW)", "Peak demand (kW)", "Peak at", "Load factor"],
        [
            [
                months[i],
                format_number(base[i], 3),
                format_number(peak[i], 3),
                at[i] or charts.DASH,
                format_number(factor[i], 2),
            ]
            for i in range(len(months))
        ],
    )
    return (
        _caption("Base load by month (kW)")
        + base_chart
        + _caption("Peak demand by month (kW)")
        + peak_chart
        + _caption("Load factor by month")
        + factor_chart
        + _details(table)
    )


def _quality(reg: _Register) -> str:
    quality = reg.rows("quality_monthly")
    months = sorted({_text(m) for m in quality["month"]})
    cell: dict[tuple[str, str], tuple[float | None, float | None]] = {}
    for month, cls, share, kwh in zip(
        quality["month"], quality["quality_class"], quality["share_intervals"], quality["kwh"], strict=True
    ):
        cell[(_text(month), str(cls))] = (_num(share), _num(kwh))
    classes = list(QUALITY_CLASSES)

    def line(category: int, series: int) -> str:
        share, kwh = cell.get((months[category], classes[series]), (None, None))
        return f"{classes[series]}: {_percent(share)} of intervals · {format_number(kwh, 1)} kWh"

    chart = charts.column_chart(
        reg.cid("quality"),
        title="Reading quality by month",
        desc="Each month's readings by quality class, as a share of the month's intervals.",
        categories=months,
        series=[
            Series(cls, [cell.get((m, cls), (None, None))[0] or 0.0 for m in months], _QUALITY_TONES[cls])
            for cls in classes
        ],
        unit="share",
        stacked=True,
        axis_max=1.0,
        tick_label=lambda v: f"{v * 100:.0f}%",
        series_line=line,
        empty="No months.",
    )
    monthly = reg.rows("monthly")
    m_months = [_text(m) for m in monthly["month"]]
    complete = [
        int(c) / int(d) if int(d) else None
        for c, d in zip(monthly["complete_days"], monthly["days_in_month"], strict=True)
    ]
    coverage = charts.line_chart(
        reg.cid("coverage"),
        title="Complete days by month",
        desc="The share of each month's days that have every interval, from 0 to 1.",
        categories=m_months,
        values=complete,
        unit="",
        places=2,
        axis_max=1.0,
        empty="No months.",
    )
    table = charts.data_table(
        ["Month", "Class", "Intervals", "Share of intervals", "Energy (kWh)"],
        [
            [_text(m), str(c), str(int(n)), _percent(_num(s)), format_number(_num(k), 1)]
            for m, c, n, s, k in zip(
                quality["month"],
                quality["quality_class"],
                quality["intervals"],
                quality["share_intervals"],
                quality["kwh"],
                strict=True,
            )
        ],
    )
    dst = reg.summary.dst_days
    note = (
        f'<p class="note">The data holds {dst.short} short and {dst.long} long local days '
        "(daylight-saving changes); hourly figures leave out an hour that did not happen or happened twice.</p>"
    )
    return (
        charts.legend([(cls, _QUALITY_TONES[cls]) for cls in classes])
        + chart
        + _details(table)
        + _caption("Complete days by month")
        + coverage
        + note
    )


_SECTIONS: Final = (
    ("Headline", _headline),
    ("Monthly usage", _monthly),
    ("Average daily use by month and season", _average),
    ("Year on year", _yoy),
    ("Hourly profile", _hourly),
    ("Day by hour heatmap", _heatmap),
    ("Time-of-use split", _tou),
    ("Base load, peak demand and load factor", _monthly_lines),
    ("Data quality", _quality),
)


def _register(marts: UsageMarts, suffix: str, index: int) -> str:
    reg = _Register(marts, suffix, index)
    parts = [f'<section class="register"><h2>Register {esc(suffix)}</h2>']
    for heading, build in _SECTIONS:
        parts.append(f"<h3>{heading}</h3>{build(reg)}")
    parts.append("</section>")
    return "".join(parts)


def render(marts: UsageMarts) -> str:
    """The whole page. Same marts, same bytes."""
    suffixes: Sequence[str] = sorted(marts.summary.suffixes)
    if suffixes:
        body = "\n".join(_register(marts, suffix, i) for i, suffix in enumerate(suffixes))
    else:
        body = "<p>No consumption register is in the data, so there is nothing to show.</p>"
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{TITLE}</title>\n<style>{charts.stylesheet()}{_PAGE_CSS}</style>\n</head>\n"
        f"<body>\n<main>\n<h1>{TITLE}</h1>\n{body}\n</main>\n</body>\n</html>\n"
    )
