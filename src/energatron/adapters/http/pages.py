"""HTML for the web UI (P10 spec §6.2): pages built from redacted envelope documents, and nothing else.

These functions format; they don't compute. They take the JSON documents the read paths answered (already redacted),
show values as the documents hold them, in the documents' order, and escape every one. Links come only from the fixed
routes the caller passes in, with quoted query values. An import contract keeps this module away from services, core,
`wiring` and `reads` (D1 §3).
"""

import html
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final
from urllib.parse import urlencode

NAV: Final = (
    ("/", "Overview"),
    ("/recommendation", "Recommendation"),
    ("/usage", "Usage"),
    ("/datasets", "Datasets"),
    ("/offers", "Offer changes"),
    ("/inbox", "Inbox"),
    ("/health", "Health"),
    ("/gas-bill", "Gas bill check"),
    ("/reports/recommendation", "Recommendation report"),
    ("/reports/usage", "Usage report"),
    ("/api/v1", "API"),
)

_HIDDEN: Final = frozenset({"kind", "schema_version"})


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def href(path: str, query: Mapping[str, object] | None = None) -> str:
    """A link to one of the app's fixed routes, with its query values quoted."""
    pairs = [(key, str(value)) for key, value in (query or {}).items() if value is not None and value != ""]
    return esc(path + (f"?{urlencode(pairs)}" if pairs else ""))


def humanise(key: str) -> str:
    """`annual_ongoing` → `Annual ongoing`."""
    text = key.replace("_", " ").strip()
    return text[:1].upper() + text[1:]


def scalar(value: object) -> str:
    if value is None:
        return '<span class="muted">n/a</span>'
    if isinstance(value, bool):
        return "yes" if value else "no"
    return esc(value)


def _records(value: Sequence[Any]) -> bool:
    return bool(value) and all(isinstance(item, Mapping) for item in value)


def value_html(value: object) -> str:
    """Any JSON value: a mapping as a key/value table, a list of records as a table, a list of scalars as a list."""
    if isinstance(value, Mapping):
        return mapping_html(value)
    if isinstance(value, list | tuple):
        if not value:
            return '<span class="muted">none</span>'
        if _records(value):
            return records_html(value)
        return "<ul>" + "".join(f"<li>{value_html(item)}</li>" for item in value) + "</ul>"
    return scalar(value)


def mapping_html(value: Mapping[str, Any]) -> str:
    rows = [
        f'<tr><th scope="row">{esc(humanise(str(key)))}</th><td>{value_html(item)}</td></tr>'
        for key, item in value.items()
        if key not in _HIDDEN
    ]
    if not rows:
        return '<span class="muted">none</span>'
    return '<table class="kv">' + "".join(rows) + "</table>"


def records_html(rows: Sequence[Mapping[str, Any]]) -> str:
    """A table with a column per key, in first-seen order; nested values are shown inside their cell."""
    columns: list[str] = []
    for row in rows:
        columns.extend(key for key in row if key not in columns and key not in _HIDDEN)
    head = "".join(f'<th scope="col">{esc(humanise(column))}</th>' for column in columns)
    body = "".join(
        "<tr>" + "".join(f"<td>{value_html(row.get(column))}</td>" for column in columns) + "</tr>" for row in rows
    )
    return f'<div class="scroll"><table class="grid"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


@dataclass(frozen=True)
class Part:
    """One titled block of a section: some of the result's fields, or all of them (`keys` None)."""

    title: str
    keys: tuple[str, ...] | None = None
    single: str | None = None  # show this one field's value alone (a table, a list)


@dataclass(frozen=True)
class Section:
    """A read path's answer on a page: its parts, its warnings, its error, and a link to the JSON that produced it."""

    title: str
    document: Mapping[str, Any]
    api: str
    parts: tuple[Part, ...] = (Part(""),)
    note: str = ""


def _warnings(document: Mapping[str, Any]) -> str:
    warnings = document.get("warnings") or []
    if not warnings:
        return ""
    items = "".join(
        f"<li><code>{esc(item.get('code'))}</code> {esc(item.get('message'))}</li>"
        for item in warnings
        if isinstance(item, Mapping)
    )
    return f'<div class="warnings"><h3>Warnings</h3><ul>{items}</ul></div>'


def _error(document: Mapping[str, Any]) -> str:
    error = document.get("error")
    if not isinstance(error, Mapping):
        return ""
    details = error.get("details") if isinstance(error.get("details"), Mapping) else {}
    hint = details.get("hint") if isinstance(details, Mapping) else None
    hint_html = f"<p>{esc(hint)}</p>" if isinstance(hint, str) else ""
    return (
        f'<div class="error"><p><code>{esc(error.get("code"))}</code> {esc(error.get("message"))}</p>{hint_html}'
        + (f"<details><summary>Details</summary>{value_html(details)}</details>" if details else "")
        + "</div>"
    )


def _part(part: Part, data: Mapping[str, Any]) -> str:
    title = f"<h3>{esc(part.title)}</h3>" if part.title else ""
    if part.single is not None:
        return title + value_html(data.get(part.single))
    keys = part.keys if part.keys is not None else tuple(key for key in data if key not in _HIDDEN)
    return title + mapping_html({key: data.get(key) for key in keys})


def section_html(section: Section) -> str:
    document = section.document
    data = document.get("data")
    body = _error(document)
    if isinstance(data, Mapping):
        body += "".join(_part(part, data) for part in section.parts)
    note = f"<p>{esc(section.note)}</p>" if section.note else ""
    return (
        f'<section><h2>{esc(section.title)} <a class="json" href="{section.api}">JSON</a></h2>'
        f"{note}{_warnings(document)}{body}</section>"
    )


@dataclass(frozen=True)
class Field:
    """One input of a filter form: its query name, label and current value."""

    name: str
    label: str
    value: object = None
    choices: tuple[str, ...] = ()


def form_html(action: str, fields: Iterable[Field]) -> str:
    """A `GET` form back to the same page, with the current values filled in."""
    inputs: list[str] = []
    for item in fields:
        current = "" if item.value is None else str(item.value)
        if item.choices:
            options = "".join(
                f'<option value="{esc(choice)}"{" selected" if choice == current else ""}>'
                f"{esc(choice or 'any')}</option>"
                for choice in ("", *item.choices)
            )
            control = f'<select name="{esc(item.name)}">{options}</select>'
        else:
            control = f'<input name="{esc(item.name)}" value="{esc(current)}">'
        inputs.append(f"<label>{esc(item.label)} {control}</label>")
    return f'<form class="filters" method="get" action="{esc(action)}">{"".join(inputs)}<button>Show</button></form>'


@dataclass(frozen=True)
class Page:
    """A whole page: title, current path, sections, and any extra HTML (a form) before them."""

    title: str
    path: str
    sections: tuple[Section, ...] = ()
    before: str = ""
    version: str | None = None
    extra: list[str] = field(default_factory=list)


def page_html(page: Page) -> str:
    nav = "".join(
        f'<a href="{esc(path)}"{' aria-current="page"' if path == page.path else ""}>{esc(label)}</a>'
        for path, label in NAV
    )
    version = f'<span class="muted">energatron {esc(page.version)}</span>' if page.version else ""
    sections = "".join(section_html(section) for section in page.sections)
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<title>{esc(page.title)} · energatron</title><link rel="stylesheet" href="/style.css"></head>'
        f"<body><header><strong>energatron</strong> {version}<nav>{nav}</nav></header>"
        f"<main><h1>{esc(page.title)}</h1>{page.before}{sections}{''.join(page.extra)}</main></body></html>"
    )


def message_html(title: str, text: str, *, path: str = "", version: str | None = None) -> str:
    """A page holding one message, such as a missing report."""
    return page_html(Page(title=title, path=path, before=f"<p>{esc(text)}</p>", version=version))


def gas_bill_form_html(record: str = "") -> str:
    return (
        '<form class="record" method="post" action="/gas-bill">'
        '<label for="record">Gas bill record (YAML or JSON)</label>'
        f'<textarea id="record" name="record" rows="18" spellcheck="false">{esc(record)}</textarea>'
        "<button>Check</button></form>"
        "<p>Checking writes nothing. Use <code>energatron gas-bill write</code> and <code>land</code> to keep it.</p>"
    )


def preformatted_html(text: object) -> str:
    return f"<pre>{esc(text)}</pre>"
