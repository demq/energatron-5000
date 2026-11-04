"""Hand-written inline SVG for the usage report (P3 spec §9.1, §9.2, §9.4).

Pure functions from plain values to strings, with no I/O, no clock and no third-party dependency. Determinism comes from
three habits: every number goes through `format_number` or `_c` (fixed places, `.` decimal point, no locale), every text
goes through `esc` (`html.escape(…, quote=True)`), and nothing is interpolated into the `<style>`. An element id derives
from the `chart_id` the caller passes, which the caller builds from the chart's name and the data's position, never from
a label.

The page has no JavaScript, so a tooltip is a `<title>` child of a transparent hit area. A partial bar is hatched with
explicit diagonal lines instead of a `<pattern>`, so the page holds no `url(...)` reference at all (§9.1).

The look follows the `dataviz` guidance: thin marks, 4 px rounded data ends square at the baseline, a 2 px surface gap
between stacked segments, a 2 px surface ring on end dots, hairline solid grid, and text in ink tokens, never in a
series colour. At most four categorical tones come from one validated palette (`CATEGORICAL`); a fifth entry gets the
neutral tone `cx`, which a chart's legend and tooltips still name.
"""

import math
import re
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from html import escape
from typing import Final

CATEGORICAL: Final = (
    ("#2a78d6", "#3987e5"),
    ("#eb6834", "#d95926"),
    ("#1baf7a", "#199e70"),
    ("#eda100", "#c98500"),
)
"""The four categorical slots as (light, dark), in the palette's fixed order. Validated with the `dataviz` script:
adjacent pairs pass the lightness band, chroma floor and both Delta E floors in both modes. The light aqua and yellow
sit below 3:1 on the surface, so every chart that uses them also names its parts in a legend, in tooltips and in a
table."""

NEUTRAL: Final = "#898781"
"""The tone `cx`: used for a series that is context, not identity, and for entries past the fourth."""

SEQUENTIAL: Final = (
    "#cde2fb",
    "#9ec5f4",
    "#6da7ec",
    "#3987e5",
    "#256abf",
    "#184f95",
    "#0d366b",
)
"""One blue hue, light to dark (steps 100, 200, 300, 400, 500, 600, 700). On the dark surface the order is reversed so
that more energy is always the more prominent cell."""

BINS: Final = len(SEQUENTIAL)
BAR_MAX: Final = 24.0
"""A bar is at most this thick, in the chart's own units."""
RADIUS: Final = 4.0
GAP: Final = 2.0
"""The surface gap between stacked segments and between grouped bars."""
HATCH_STEP: Final = 4.0
LABEL_BAND: Final = 36.0
"""A label above a column needs a band at least this wide, or it is dropped (the value stays in the tooltip)."""

_WIDTH: Final = 720
_LEFT: Final = 44.0
_RIGHT: Final = 8.0
DASH: Final = "\N{EN DASH}"
"""What a missing value reads as."""


def _c(value: float) -> str:
    """A coordinate: one decimal, never `-0.0`."""
    text = f"{value:.1f}"
    return "0.0" if text == "-0.0" else text


def esc(text: object) -> str:
    """HTML-escape any text, quotes included, so it is safe in content and in a double-quoted attribute."""
    return escape(str(text), quote=True)


def format_number(value: float | None, places: int = 1) -> str:
    """`value` with exactly `places` decimals and a `.` point: no locale, no grouping, no `-0`; a dash for a gap."""
    if value is None or not math.isfinite(value):
        return DASH
    text = f"{value:.{places}f}"
    if text.startswith("-") and float(text) == 0.0:
        text = text[1:]
    return text


def _clean(value: float | None) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    return float(value)


def tone_for(index: int) -> str:
    """The CSS tone of the `index`-th entry of a fixed ordering: `c1`…`c4`, then the neutral `cx` (never cycled)."""
    return f"c{index + 1}" if index < len(CATEGORICAL) else "cx"


@dataclass(frozen=True)
class Series:
    """One series of a column chart: a name, a value per category (`None` for a gap) and a CSS tone (`c1`…`cx`)."""

    name: str
    values: Sequence[float | None]
    tone: str


def nice_axis(top: float) -> tuple[float, list[float]]:
    """A round axis end at or above `top`, with five ticks from 0 (steps of 1, 2, 2.5 or 5 times a power of ten)."""
    if not math.isfinite(top) or top <= 0.0:
        return 1.0, [0.0, 0.25, 0.5, 0.75, 1.0]
    magnitude = 10.0 ** math.floor(math.log10(top / 4.0))
    step = magnitude
    for factor in (1.0, 2.0, 2.5, 5.0, 10.0):
        step = factor * magnitude
        if step * 4.0 >= top * (1.0 - 1e-12):
            break
    ticks = [round(i * step, 10) for i in range(5)]
    return ticks[-1], ticks


def _tick_places(step: float) -> int:
    for places in range(0, 6):
        if abs(step * 10**places - round(step * 10**places)) < 1e-9:
            return places
    return 6


def _svg(chart_id: str, title: str, desc: str, width: float, height: float, body: Sequence[str]) -> str:
    ident = esc(chart_id)
    head = (
        f'<svg class="chart" viewBox="0 0 {_c(width)} {_c(height)}" role="img" '
        f'aria-labelledby="t-{ident} d-{ident}">\n'
        f'<title id="t-{ident}">{esc(title)}</title>\n<desc id="d-{ident}">{esc(desc)}</desc>'
    )
    return "\n".join([head, *body, "</svg>"])


def _empty(chart_id: str, title: str, desc: str, caption: str, width: float = _WIDTH, height: float = 96.0) -> str:
    text = f'<text class="empty" x="{_c(width / 2)}" y="{_c(height / 2)}">{esc(caption)}</text>'
    return _svg(chart_id, title, desc, width, height, [text])


def hatch_path(x: float, y: float, width: float, height: float, step: float = HATCH_STEP) -> str:
    """Diagonal 45° lines, rising to the right, clipped to the box (x, y, width, height): one SVG path, or `""`."""
    if width <= 0.0 or height <= 0.0:
        return ""
    parts: list[str] = []
    offset = -height + step
    while offset < width:
        x1 = max(offset, 0.0)
        y1 = height - (x1 - offset)
        x2 = min(offset + height, width)
        y2 = height - (x2 - offset)
        if x2 - x1 > 0.05:
            parts.append(f"M{_c(x + x1)},{_c(y + y1)} L{_c(x + x2)},{_c(y + y2)}")
        offset += step
    return " ".join(parts)


def _bar_path(x: float, top: float, width: float, bottom: float, *, rounded: bool) -> str:
    """A bar from `bottom` up to `top`: 4 px corners at the data end when `rounded`, square at the baseline."""
    radius = min(RADIUS, width / 2.0, bottom - top) if rounded else 0.0
    if radius <= 0.0:
        return f"M{_c(x)},{_c(bottom)} L{_c(x)},{_c(top)} L{_c(x + width)},{_c(top)} L{_c(x + width)},{_c(bottom)} Z"
    return (
        f"M{_c(x)},{_c(bottom)} L{_c(x)},{_c(top + radius)} Q{_c(x)},{_c(top)} {_c(x + radius)},{_c(top)} "
        f"L{_c(x + width - radius)},{_c(top)} Q{_c(x + width)},{_c(top)} {_c(x + width)},{_c(top + radius)} "
        f"L{_c(x + width)},{_c(bottom)} Z"
    )


def _y_axis(
    ticks: Sequence[float],
    end: float,
    left: float,
    right: float,
    top: float,
    height: float,
    label: Callable[[float], str],
) -> list[str]:
    parts: list[str] = []
    for tick in ticks:
        y = top + height * (1.0 - tick / end)
        kind = "axis" if tick == 0.0 else "grid"
        parts.append(f'<line class="{kind}" x1="{_c(left)}" y1="{_c(y)}" x2="{_c(right)}" y2="{_c(y)}"/>')
        parts.append(f'<text class="tick y" x="{_c(left - 6)}" y="{_c(y + 3.5)}">{esc(label(tick))}</text>')
    return parts


def _default_tick_label(ticks: Sequence[float]) -> Callable[[float], str]:
    places = _tick_places(ticks[1]) if len(ticks) > 1 else 0
    return lambda value: format_number(value, places)


def _x_labels(labels: Sequence[str], band: float, left: float, baseline: float) -> list[str]:
    widest = max((len(label) for label in labels), default=1)
    every = max(1, math.ceil((widest * 6.2 + 8.0) / band))
    return [
        f'<text class="tick x" x="{_c(left + (i + 0.5) * band)}" y="{_c(baseline + 14)}">{esc(label)}</text>'
        for i, label in enumerate(labels)
        if i % every == 0
    ]


def column_chart(
    chart_id: str,
    *,
    title: str,
    desc: str,
    categories: Sequence[str],
    series: Sequence[Series],
    unit: str,
    places: int = 1,
    stacked: bool = False,
    hatched: Collection[int] = (),
    notes: Mapping[int, str] | None = None,
    top_labels: Sequence[str] | None = None,
    groups: Sequence[tuple[str, int, int]] = (),
    axis_max: float | None = None,
    tick_label: Callable[[float], str] | None = None,
    series_line: Callable[[int, int], str] | None = None,
    empty: str = "No data.",
    height: float = 240.0,
) -> str:
    """Columns per category: side by side per series, or stacked from the baseline.

    `hatched` lists category indexes drawn as partial (a pale fill with diagonal lines). `notes` adds a line to a
    category's tooltip. `top_labels` prints a short text above each category's tallest column when its band is wide
    enough. `groups` are (label, first, last) category runs named under the axis. `axis_max` fixes the top of the axis
    (1.0 for shares). `series_line(category, series)` replaces a tooltip's line for one value.
    """
    rows = [[_clean(v) for v in s.values] for s in series]
    count = len(categories)
    if count == 0 or not any(v is not None for row in rows for v in row):
        return _empty(chart_id, title, desc, empty)

    totals = [
        sum(row[i] or 0.0 for row in rows) if stacked else max((row[i] or 0.0) for row in rows) for i in range(count)
    ]
    end, ticks = nice_axis(axis_max if axis_max is not None else max(totals))
    label_for = tick_label or _default_tick_label(ticks)

    top = 22.0
    bottom_pad = 28.0 + (18.0 if groups else 0.0)
    plot_h = height - top - bottom_pad
    left, right = _LEFT, _WIDTH - _RIGHT
    band = (right - left) / count
    baseline = top + plot_h

    def y_of(value: float) -> float:
        return top + plot_h * (1.0 - value / end)

    body = _y_axis(ticks, end, left, right, top, plot_h, label_for)
    body += _x_labels(categories, band, left, baseline)
    for text, first, last in groups:
        x1, x2 = left + first * band + GAP, left + (last + 1) * band - GAP
        y = baseline + 32.0
        body.append(f'<line class="grid" x1="{_c(x1)}" y1="{_c(y - 12)}" x2="{_c(x2)}" y2="{_c(y - 12)}"/>')
        body.append(f'<text class="tick group" x="{_c((x1 + x2) / 2)}" y="{_c(y)}">{esc(text)}</text>')

    partial = set(hatched)
    for i in range(count):
        centre = left + (i + 0.5) * band
        shape: list[tuple[float, float, float, float, str]] = []  # (x, top, width, bottom, tone)
        if stacked:
            width = max(1.0, min(BAR_MAX, band * 0.7))
            running, lowest = 0.0, baseline
            last_nonzero = max((s for s, row in enumerate(rows) if (row[i] or 0.0) > 0.0), default=-1)
            for s, row in enumerate(rows):
                value = row[i] or 0.0
                if value <= 0.0:
                    continue
                running += value
                seg_top = y_of(running)
                seg_bottom = lowest - (GAP if lowest != baseline else 0.0)
                lowest = seg_top
                if seg_bottom - seg_top < 0.5:
                    continue
                shape.append((centre - width / 2, seg_top, width, seg_bottom, tone_for_series(series, s)))
                if s == last_nonzero:
                    shape[-1] = (*shape[-1][:4], shape[-1][4] + " round")
        else:
            k = len(series)
            width = max(1.0, min(BAR_MAX, (min(band * 0.8, BAR_MAX * k + GAP * (k - 1)) - GAP * (k - 1)) / k))
            group = width * k + GAP * (k - 1)
            for s, row in enumerate(rows):
                value = row[i]
                if value is None or value <= 0.0:
                    continue
                x = centre - group / 2 + s * (width + GAP)
                shape.append((x, y_of(value), width, baseline, tone_for_series(series, s) + " round"))
        for x, seg_top, width, seg_bottom, tone in shape:
            name, _, rounded = tone.partition(" ")
            extra = " partial" if i in partial else ""
            path = _bar_path(x, seg_top, width, seg_bottom, rounded=bool(rounded))
            body.append(f'<path class="bar {name}{extra}" d="{path}"/>')
            if i in partial:
                lines = hatch_path(x, seg_top, width, seg_bottom - seg_top)
                if lines:
                    body.append(f'<path class="hatch {name}" d="{lines}"/>')
        if top_labels is not None and band >= LABEL_BAND and top_labels[i]:
            body.append(
                f'<text class="toplabel" x="{_c(centre)}" y="{_c(y_of(totals[i]) - 5)}">{esc(top_labels[i])}</text>'
            )

    note_for = notes or {}
    for i, category in enumerate(categories):
        lines = [category]
        for s, item in enumerate(series):
            value = rows[s][i]
            if series_line is not None:
                lines.append(series_line(i, s))
            else:
                lines.append(f"{item.name}: {format_number(value, places)} {unit}".rstrip())
        if i in note_for:
            lines.append(note_for[i])
        body.append(
            f'<rect class="hit" x="{_c(left + i * band)}" y="{_c(top)}" width="{_c(band)}" height="{_c(plot_h)}">'
            f"<title>{esc(chr(10).join(lines))}</title></rect>"
        )
    return _svg(chart_id, title, desc, _WIDTH, height, body)


def tone_for_series(series: Sequence[Series], index: int) -> str:
    """The tone a series draws in (its own `tone`)."""
    return series[index].tone


def line_chart(
    chart_id: str,
    *,
    title: str,
    desc: str,
    categories: Sequence[str],
    values: Sequence[float | None],
    unit: str,
    places: int = 1,
    tips: Mapping[int, str] | None = None,
    axis_max: float | None = None,
    empty: str = "No data.",
    height: float = 200.0,
) -> str:
    """One 2 px line over categories, broken at a gap, with an end dot and a hit target on every point."""
    points = [_clean(v) for v in values]
    count = len(categories)
    if count == 0 or all(v is None for v in points):
        return _empty(chart_id, title, desc, empty)
    end, ticks = nice_axis(axis_max if axis_max is not None else max(v for v in points if v is not None))
    top, bottom_pad = 12.0, 28.0
    plot_h = height - top - bottom_pad
    left, right = _LEFT, _WIDTH - _RIGHT
    band = (right - left) / count
    baseline = top + plot_h
    body = _y_axis(ticks, end, left, right, top, plot_h, _default_tick_label(ticks))
    body += _x_labels(categories, band, left, baseline)

    def at(i: int, value: float) -> tuple[float, float]:
        return left + (i + 0.5) * band, top + plot_h * (1.0 - value / end)

    segments: list[str] = []
    drawing = False
    for i, value in enumerate(points):
        if value is None:
            drawing = False
            continue
        x, y = at(i, value)
        segments.append(f"{'L' if drawing else 'M'}{_c(x)},{_c(y)}")
        drawing = True
    body.append(f'<path class="line" d="{" ".join(segments)}"/>')
    last = max(i for i, v in enumerate(points) if v is not None)
    x, y = at(last, points[last] or 0.0)
    body.append(f'<circle class="dot" cx="{_c(x)}" cy="{_c(y)}" r="4.0"/>')
    extra = tips or {}
    for i, value in enumerate(points):
        if value is None:
            continue
        x, y = at(i, value)
        text = f"{categories[i]} · {format_number(value, places)} {unit}".rstrip()
        if i in extra:
            text += f" · {extra[i]}"
        body.append(f'<circle class="hit" cx="{_c(x)}" cy="{_c(y)}" r="10.0"><title>{esc(text)}</title></circle>')
    return _svg(chart_id, title, desc, _WIDTH, height, body)


def band_panel(
    chart_id: str,
    *,
    title: str,
    desc: str,
    hours: Sequence[int],
    mean: Sequence[float | None],
    low: Sequence[float | None],
    high: Sequence[float | None],
    y_top: float,
    unit: str,
    places: int = 2,
    caption: str = "",
    tips: Sequence[str] | None = None,
    empty: str = "No data.",
) -> str:
    """One small-multiple panel: the mean line over a pale low-to-high band, per clock hour, on a shared `y_top`."""
    width, height = 260.0, 156.0
    if not hours:
        return _empty(chart_id, title, desc, empty, width, 96.0)
    end, ticks = nice_axis(y_top)
    left, right, top, bottom_pad = 34.0, width - 6.0, 24.0, 22.0
    plot_h = height - top - bottom_pad
    band = (right - left) / 24.0
    baseline = top + plot_h

    def at(hour: int, value: float) -> tuple[float, float]:
        return left + (hour + 0.5) * band, top + plot_h * (1.0 - min(value, end) / end)

    body = [f'<text class="panel-title" x="{_c(left)}" y="12.0">{esc(title)}</text>']
    if caption:
        body.append(f'<text class="panel-note" x="{_c(right)}" y="12.0">{esc(caption)}</text>')
    body += _y_axis(ticks, end, left, right, top, plot_h, _default_tick_label(ticks))
    for hour in (0, 6, 12, 18, 23):
        body.append(f'<text class="tick x" x="{_c(left + (hour + 0.5) * band)}" y="{_c(baseline + 14)}">{hour}</text>')
    rows = [(h, _clean(m), _clean(lo), _clean(hi)) for h, m, lo, hi in zip(hours, mean, low, high, strict=True)]
    solid = [(h, lo, hi) for h, _, lo, hi in rows if lo is not None and hi is not None]
    if solid:
        upper = [f"{'M' if i == 0 else 'L'}{_c(at(h, hi)[0])},{_c(at(h, hi)[1])}" for i, (h, _, hi) in enumerate(solid)]
        lower = [f"L{_c(at(h, lo)[0])},{_c(at(h, lo)[1])}" for h, lo, _ in reversed(solid)]
        body.append(f'<path class="band" d="{" ".join([*upper, *lower])} Z"/>')
    path: list[str] = []
    drawing = False
    for h, m, _, _ in rows:
        if m is None:
            drawing = False
            continue
        x, y = at(h, m)
        path.append(f"{'L' if drawing else 'M'}{_c(x)},{_c(y)}")
        drawing = True
    body.append(f'<path class="line" d="{" ".join(path)}"/>')
    for i, (h, m, _, _) in enumerate(rows):
        text = tips[i] if tips is not None else f"{h:02d}:00 · {format_number(m, places)} {unit}"
        body.append(
            f'<rect class="hit" x="{_c(left + h * band)}" y="{_c(top)}" width="{_c(band)}" height="{_c(plot_h)}">'
            f"<title>{esc(text)}</title></rect>"
        )
    return _svg(chart_id, title, desc, width, height, body)


def heatmap_chart(
    chart_id: str,
    *,
    title: str,
    desc: str,
    days: Sequence[str],
    cells: Mapping[tuple[int, int], float],
    unit: str,
    places: int = 2,
    empty: str = "No data.",
) -> str:
    """Days along the x axis (ISO dates, oldest first), clock hours down the y axis, one cell per entry of `cells`.

    `cells` maps (day index, hour) to a value; a missing cell stays blank. The seven-step blue scale is linear from 0 to
    the largest value, with its key under the plot.
    """
    if not days or not cells:
        return _empty(chart_id, title, desc, empty)
    left, top, cell_h = 30.0, 8.0, 8.0
    cell_w = max(1.0, min(6.0, 640.0 / len(days)))
    plot_w = len(days) * cell_w
    width = max(left + plot_w + 10.0, float(_WIDTH))
    plot_h = 24 * cell_h
    biggest = max(cells.values())
    body: list[str] = []
    for hour in (0, 6, 12, 18):
        body.append(f'<text class="tick y" x="{_c(left - 4)}" y="{_c(top + hour * cell_h + 7)}">{hour:02d}</text>')
    last_label_x = -1000.0
    previous = ""
    for i, day in enumerate(days):
        month = day[:7]
        if month != previous:
            previous = month
            x = left + i * cell_w
            if x - last_label_x >= 52.0:
                body.append(f'<text class="tick x" x="{_c(x)}" y="{_c(top + plot_h + 12)}">{esc(month)}</text>')
                last_label_x = x
    for (d, hour), value in sorted(cells.items()):
        step = 0 if biggest <= 0.0 else min(BINS - 1, int(value / biggest * BINS))
        body.append(
            f'<rect x="{_c(left + d * cell_w)}" y="{_c(top + hour * cell_h)}" '
            f'width="{_c(cell_w)}" height="{_c(cell_h)}" class="q{step}">'
            f"<title>{esc(days[d])} {hour:02d}:00 · {format_number(value, places)} {esc(unit)}</title></rect>"
        )
    key_y = top + plot_h + 22.0
    body.append(f'<text class="tick key-label" x="{_c(left)}" y="{_c(key_y + 9)}">0</text>')
    for step in range(BINS):
        body.append(
            f'<rect class="key q{step}" x="{_c(left + 16 + step * 18)}" y="{_c(key_y)}" width="16.0" height="10.0"/>'
        )
    body.append(
        f'<text class="tick key-label" x="{_c(left + 16 + BINS * 18 + 4)}" y="{_c(key_y + 9)}">'
        f"{esc(format_number(biggest, places))} {esc(unit)}</text>"
    )
    return _svg(chart_id, title, desc, width, key_y + 18.0, body)


def legend(entries: Sequence[tuple[str, str]]) -> str:
    """A legend: a swatch in each (name, tone), with the name in ink. Identity never rests on colour alone."""
    items = "".join(
        f'<li><span class="swatch {esc(tone)}"></span><span>{esc(name)}</span></li>' for name, tone in entries
    )
    return f'<ul class="legend">{items}</ul>'


def _numeric(cell: str) -> bool:
    text = cell.strip()
    if text == DASH:
        return True
    text = text.removeprefix("+").removeprefix("-").removesuffix("%")
    return re.fullmatch(r"\d+(\.\d+)?", text) is not None


def data_table(headers: Sequence[str], rows: Sequence[Sequence[str]], *, caption: str | None = None) -> str:
    """An HTML table; numeric cells align right. Every chart has a table twin built with this."""
    parts = ["<table>"]
    if caption:
        parts.append(f"<caption>{esc(caption)}</caption>")
    parts.append("<thead><tr>" + "".join(f"<th>{esc(h)}</th>" for h in headers) + "</tr></thead>")
    body = []
    for row in rows:
        cells = "".join(f'<td class="num">{esc(c)}</td>' if _numeric(c) else f"<td>{esc(c)}</td>" for c in row)
        body.append(f"<tr>{cells}</tr>")
    parts.append("<tbody>" + "".join(body) + "</tbody></table>")
    return "".join(parts)


def stylesheet() -> str:
    """The CSS of every chart, legend and table: light and dark custom properties in one colour-scheme block."""
    light = [
        "--surface:#fcfcfb",
        "--page:#f9f9f7",
        "--ink:#0b0b0b",
        "--ink2:#52514e",
        f"--muted:{NEUTRAL}",
        "--grid:#e1e0d9",
        "--axis:#c3c2b7",
        f"--cx:{NEUTRAL}",
        *(f"--c{i + 1}:{pair[0]}" for i, pair in enumerate(CATEGORICAL)),
        *(f"--q{i}:{hex_}" for i, hex_ in enumerate(SEQUENTIAL)),
    ]
    dark = [
        "--surface:#1a1a19",
        "--page:#0d0d0d",
        "--ink:#f0efec",
        "--ink2:#c3c2b7",
        "--grid:#2c2c2a",
        "--axis:#383835",
        *(f"--c{i + 1}:{pair[1]}" for i, pair in enumerate(CATEGORICAL)),
        *(f"--q{i}:{hex_}" for i, hex_ in enumerate(reversed(SEQUENTIAL))),
    ]
    tones = "".join(f".c{i}{{fill:var(--c{i})}}.swatch.c{i}{{background:var(--c{i})}}" for i in range(1, 5))
    cells = "".join(f".q{i}{{fill:var(--q{i})}}.swatch.q{i}{{background:var(--q{i})}}" for i in range(BINS))
    strokes = "".join(f"path.hatch.c{i}{{stroke:var(--c{i})}}" for i in range(1, 5))
    return (
        f":root{{color-scheme:light dark;{';'.join(light)}}}"
        f"@media (prefers-color-scheme:dark){{:root{{{';'.join(dark)}}}}}"
        f"{tones}.cx{{fill:var(--cx)}}.swatch.cx{{background:var(--cx)}}{cells}"
        "svg.chart{display:block;width:100%;height:auto;background:var(--surface)}"
        ".chart text{fill:var(--ink2);font-size:11px;font-family:inherit}"
        ".chart .tick{fill:var(--muted);text-anchor:middle}"
        ".chart .tick.y{text-anchor:end}"
        ".chart .tick.group,.chart .key-label{fill:var(--ink2)}"
        ".chart .key-label{text-anchor:start}"
        ".chart .toplabel{font-size:10px;text-anchor:middle}"
        ".chart .panel-title{font-size:12px;font-weight:600;fill:var(--ink);text-anchor:start}"
        ".chart .panel-note{font-size:10px;text-anchor:end}"
        ".chart .empty{font-size:13px;text-anchor:middle}"
        ".chart .grid{stroke:var(--grid);stroke-width:1}"
        ".chart .axis{stroke:var(--axis);stroke-width:1}"
        ".chart .partial{fill-opacity:.35}"
        f"{strokes}path.hatch{{fill:none;stroke-width:1}}"
        ".chart path.line{fill:none;stroke:var(--c1);stroke-width:2;stroke-linejoin:round;stroke-linecap:round}"
        ".chart path.band{fill:var(--c1);fill-opacity:.12}"
        ".chart circle.dot{fill:var(--c1);stroke:var(--surface);stroke-width:2}"
        ".chart .hit{fill:transparent}"
        ".legend{display:flex;flex-wrap:wrap;gap:4px 16px;list-style:none;margin:8px 0;padding:0;font-size:13px}"
        ".swatch{display:inline-block;width:12px;height:12px;border-radius:3px;margin-right:6px;vertical-align:-1px}"
        "table{border-collapse:collapse;font-size:13px;margin:8px 0}"
        "caption{text-align:left;color:var(--ink2);padding:4px 0}"
        "th,td{border-bottom:1px solid var(--grid);padding:3px 12px 3px 0;text-align:left}"
        "td.num{text-align:right;font-variant-numeric:tabular-nums}"
    )
