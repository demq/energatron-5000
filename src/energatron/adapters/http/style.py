"""The web UI's one stylesheet, served at `/style.css` (P10 spec §6.2): light and dark, no external resource."""

from typing import Final

STYLE: Final = """
:root { color-scheme: light dark; --fg: #1d2330; --bg: #ffffff; --muted: #687083; --line: #d9dde5;
  --accent: #2f5bd3; --panel: #f5f7fa; --warn: #8a5a00; --warn-bg: #fff6e0; --err: #a4262c; --err-bg: #fdecec; }
@media (prefers-color-scheme: dark) {
  :root { --fg: #e6e9ef; --bg: #14171d; --muted: #9aa3b2; --line: #2c323d; --accent: #8fb0ff; --panel: #1b1f27;
    --warn: #f3c56b; --warn-bg: #2d2615; --err: #ff9b9b; --err-bg: #331a1c; }
}
* { box-sizing: border-box; }
body { margin: 0; font: 15px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; color: var(--fg);
  background: var(--bg); }
header { display: flex; flex-wrap: wrap; gap: .5rem 1rem; align-items: baseline; padding: .75rem 1.5rem;
  border-bottom: 1px solid var(--line); background: var(--panel); }
nav { display: flex; flex-wrap: wrap; gap: .25rem .9rem; }
nav a { color: var(--accent); text-decoration: none; }
nav a[aria-current="page"] { color: var(--fg); font-weight: 600; text-decoration: underline; }
main { max-width: 72rem; padding: 1rem 1.5rem 3rem; }
h1 { font-size: 1.5rem; margin: .5rem 0 1rem; }
h2 { font-size: 1.15rem; margin: 2rem 0 .5rem; border-bottom: 1px solid var(--line); padding-bottom: .25rem; }
h3 { font-size: 1rem; margin: 1rem 0 .4rem; }
a.json { font-size: .75rem; font-weight: normal; margin-left: .5rem; color: var(--muted); }
table { border-collapse: collapse; margin: .25rem 0; }
th, td { text-align: left; vertical-align: top; padding: .3rem .6rem; border-bottom: 1px solid var(--line); }
table.kv th { color: var(--muted); font-weight: 500; white-space: nowrap; }
table.grid thead th { position: sticky; top: 0; background: var(--panel); white-space: nowrap; }
td table { margin: 0; font-size: .9em; }
.scroll { overflow-x: auto; }
ul { margin: 0; padding-left: 1.2rem; }
.muted { color: var(--muted); }
.warnings { background: var(--warn-bg); color: var(--warn); padding: .5rem .8rem; border-radius: 6px; margin: .5rem 0; }
.error { background: var(--err-bg); color: var(--err); padding: .5rem .8rem; border-radius: 6px; margin: .5rem 0; }
.warnings h3 { margin: 0 0 .25rem; font-size: .9rem; }
form.filters { display: flex; flex-wrap: wrap; gap: .5rem 1rem; align-items: end; margin: .5rem 0 1rem; }
form.filters label { display: flex; flex-direction: column; font-size: .85rem; color: var(--muted); }
input, select, textarea, button { font: inherit; color: inherit; background: var(--bg); border: 1px solid var(--line);
  border-radius: 6px; padding: .3rem .5rem; }
button { background: var(--accent); color: var(--bg); border-color: var(--accent); cursor: pointer; }
form.record { display: flex; flex-direction: column; gap: .5rem; }
textarea { width: 100%; font-family: ui-monospace, Menlo, monospace; font-size: .85rem; }
pre { white-space: pre-wrap; background: var(--panel); padding: 1rem; border-radius: 6px; }
code { font-family: ui-monospace, Menlo, monospace; font-size: .9em; }
"""
