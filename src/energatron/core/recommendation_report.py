"""The recommendation page (P6 spec §7.4), built from the `recommendation` document alone.

`render(document)` is pure: one self-contained HTML string, with one inline `<style>`, no script and no external asset.
Same document, same bytes. The page holds the household's costs, so it is private data and lives only in the
workspace. Every text that comes from the document goes through `esc`.
"""

from collections.abc import Sequence
from decimal import Decimal
from typing import Final

from energatron.core import charts
from energatron.core.charts import esc
from energatron.core.models.recommend import (
    CurrentPlan,
    PricedTariff,
    Recommendation,
    RecommendedOption,
)

TITLE: Final = "Energy plan recommendation"
_ACTIONS: Final = {
    "switch": "Switch",
    "stay": "Stay on the current plans",
    "no_current_plan": "No current plan to compare with",
}
_VARIATIONS: Final = {
    "conditions_not_met": "Conditional discounts missed",
    "usage_up": "Usage up",
    "usage_down": "Usage down",
    "alt_window": "The earlier usage window",
}
_PAGE_CSS: Final = (
    "body{margin:0;background:var(--page);color:var(--ink);font-family:system-ui,-apple-system,'Segoe UI',sans-serif;"
    "line-height:1.45}"
    "main{max-width:980px;margin:0 auto;padding:16px}"
    "h1{font-size:26px;margin:8px 0 4px}"
    "h2{font-size:21px;margin:32px 0 4px;padding-top:12px;border-top:1px solid var(--grid)}"
    "p,li{font-size:14px;color:var(--ink2)}"
    "p.lead{font-size:17px;color:var(--ink)}"
    "p.warn{font-size:14px;color:var(--ink)}"
    "table.rec{border-collapse:collapse;font-size:13px;width:100%}"
    "table.rec th,table.rec td{text-align:left;padding:4px 8px;border-bottom:1px solid var(--grid);vertical-align:top}"
    "table.rec td.num,table.rec th.num{text-align:right;font-variant-numeric:tabular-nums}"
    "details{margin:4px 0 8px}"
    "summary{cursor:pointer;font-size:13px;color:var(--ink2)}"
    "code{font-size:12px}"
)


def _money(value: Decimal | None) -> str:
    if value is None:
        return "-"
    sign = "-" if value < 0 else ""
    return f"{sign}${abs(value):,.2f}"


def _plan(found: PricedTariff | None) -> str:
    if found is None:
        return "-"
    flags = f" <em>(approximate: {esc(', '.join(found.approximations))})</em>" if found.approximations else ""
    return f"{esc(found.brand)} · {esc(found.display_name)}{flags}<br><code>{esc(found.offer_snapshot_id)}</code>"


def _current_plan(found: CurrentPlan | None) -> str:
    if found is None:
        return "-"
    source = "private config" if found.source == "private_config" else "the open backtest range"
    return f"<code>{esc(found.offer_snapshot_id)}</code> (from {source}): {_money(found.annual_ongoing)} a year"


def _table(head: Sequence[str], rows: Sequence[Sequence[str]], numeric: frozenset[int] = frozenset()) -> str:
    def cell(tag: str, index: int, text: str) -> str:
        style = ' class="num"' if index in numeric else ""
        return f"<{tag}{style}>{text}</{tag}>"

    header = "".join(cell("th", i, esc(text)) for i, text in enumerate(head))
    body = "".join("<tr>" + "".join(cell("td", i, text) for i, text in enumerate(row)) + "</tr>" for row in rows)
    return f'<table class="rec"><thead><tr>{header}</tr></thead><tbody>{body}</tbody></table>'


def _headline(document: Recommendation) -> str:
    top = document.options[0] if document.options else None
    parts = [f'<p class="lead"><strong>{esc(_ACTIONS[document.action])}</strong>']
    if document.action == "switch" and top is not None:
        parts.append(f": option 1 saves {_money(top.saving_ongoing)} a year ({_money(top.saving_year1)} in year 1)")
    parts.append(". The ranking " + ("holds" if document.robust else "does <strong>not</strong> hold"))
    parts.append(" in every sensitivity check.</p>")
    dates = (
        f"<p>Prices as of {esc(document.as_of)}; recheck by {esc(document.recheck_by)}. "
        f"ID <code>{esc(document.recommendation_id)}</code>.</p>"
    )
    return "".join(parts) + dates


def _usage(document: Recommendation) -> str:
    items: list[str] = []
    elec, gas = document.usage.electricity, document.usage.gas
    if elec is not None:
        items.append(f"<li>Electricity: metered usage {esc(elec.window_start)} to {esc(elec.window_end)}.</li>")
    if gas is not None:
        items.append(
            f"<li>Gas: bills {esc(gas.reference_start)} to {esc(gas.reference_end)} ({gas.reference_days} days).</li>"
        )
    warning = ""
    if gas is not None and gas.partial_year:
        warning = (
            '<p class="warn">The gas reference covers less than a year: its annual figures are scaled up by days '
            "and miss the seasons not covered.</p>"
        )
    return f"<h2>Usage priced</h2><ul>{''.join(items) or '<li>None.</li>'}</ul>{warning}"


def _current(document: Recommendation) -> str:
    current = document.current
    rules = f" (bundle rules: {esc(', '.join(current.rules))})" if current.rules else ""
    return (
        "<h2>Current plans</h2><ul>"
        f"<li>Electricity: {_current_plan(current.electricity)}</li>"
        f"<li>Gas: {_current_plan(current.gas)}</li>"
        f"<li>Together: {_money(current.annual_ongoing)} a year{rules}</li></ul>"
    )


def _options(options: Sequence[RecommendedOption]) -> str:
    rows = [
        [
            str(option.rank),
            _plan(option.electricity),
            _plan(option.gas),
            esc("same brand" if option.kind == "same_brand" else "independent")
            + (f"<br>{esc(', '.join(option.rules))}" if option.rules else ""),
            _money(option.annual_ongoing),
            _money(option.annual_year1),
            _money(option.saving_ongoing),
        ]
        for option in options
    ]
    head = ["#", "Electricity", "Gas", "Kind", "A year", "Year 1", "Saving a year"]
    return "<h2>Top options</h2>" + (_table(head, rows, frozenset({0, 4, 5, 6})) if rows else "<p>None.</p>")


def _sensitivity(document: Recommendation) -> str:
    rows = [
        [
            esc(_VARIATIONS.get(found.variation, found.variation)),
            "yes" if found.holds else "no",
            "-" if found.base_top_rank is None else str(found.base_top_rank),
            f"<code>{esc(found.top_option_key)}</code>",
        ]
        for found in document.sensitivity
    ]
    head = ["Check", "Option 1 still first", "Its rank there", "First there"]
    return "<h2>Sensitivity</h2>" + (_table(head, rows) if rows else "<p>No variation could be priced.</p>")


def _best(document: Recommendation, fuel: str, label: str, top: int) -> str:
    plans = document.priced["electricity" if fuel == "electricity" else "gas"][:top]
    rows = [
        [
            str(found.rank),
            _plan(found),
            esc("; ".join(found.notes)) or "-",
            _money(found.annual_ongoing),
            _money(found.annual_year1),
        ]
        for found in plans
    ]
    head = ["#", "Plan", "Notes", "A year", "Year 1"]
    return f"<h2>Best {label} plans</h2>" + (_table(head, rows, frozenset({0, 3, 4})) if rows else "<p>None.</p>")


def _citations(document: Recommendation) -> str:
    inputs = document.inputs

    def listing(ids: Sequence[str]) -> str:
        return "<br>".join(f"<code>{esc(found)}</code>" for found in ids) or "none"

    return (
        "<h2>Traceability</h2>"
        f"<details><summary>{len(inputs.dataset_ids)} datasets</summary><p>{listing(inputs.dataset_ids)}</p></details>"
        f"<details><summary>{len(inputs.offer_snapshot_ids)} offer snapshots</summary>"
        f"<p>{listing(inputs.offer_snapshot_ids)}</p></details>"
        f"<p>Params <code>{esc(inputs.params_sha256)}</code>; stage code <code>{esc(inputs.stage_sha256)}</code>.</p>"
    )


def render(document: Recommendation) -> str:
    """The whole page. Same document, same bytes."""
    top = len(document.options) or 5
    body = "\n".join(
        [
            _headline(document),
            _usage(document),
            _current(document),
            _options(document.options),
            _sensitivity(document),
            _best(document, "electricity", "electricity", top),
            _best(document, "gas", "gas", top),
            _citations(document),
        ]
    )
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{TITLE}</title>\n<style>{charts.stylesheet()}{_PAGE_CSS}</style>\n</head>\n"
        f"<body>\n<main>\n<h1>{TITLE}</h1>\n{body}\n</main>\n</body>\n</html>\n"
    )
