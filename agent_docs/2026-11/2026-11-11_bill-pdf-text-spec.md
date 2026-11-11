# Bill PDF text spec: the issue date and the kind, from the PDF's text

- **Created:** 2026-11-11
- **Status:** Approved 2026-11-11 (owner, `/done` of P7.4.1). The decision is the owner's (2026-11-11): "get it from
  the PDF's text: they contain fields like 'Issue date' with the actual date", and land all new bills in a single
  command. Its §9 edits are in the plan as rev 16, and as notes in D3 §5.1, §5.6 and the P5 spec Q2
- **Builds on:** the P1 spec ([`2026-10-23_p1-landing-catalog-spec.md`](../2026-10/2026-10-23_p1-landing-catalog-spec.md),
  "D3") §5.1 and §5.6, the P5 spec ([`2026-11-05_p5-gas-spec.md`](2026-11-05_p5-gas-spec.md)) §3.1 and Q2, the plan
  ([`2026-10-23_data-pipeline-plan.md`](../2026-10/2026-10-23_data-pipeline-plan.md)) §4 and §10.6
- **Privacy note:** contains no identifiers or usage figures, and nothing added to it may. The formats below were read
  from the owner's bills by structure only; examples are synthetic.

## 1. Why

- A bill PDF is landed as `elec_bill` or `gas_bill` (D3 §5.6, version 1), dated by the PDF's creation date. The owner's
  electricity retailer writes PDFs with no creation date, so none of its invoices could land (`no_creation_date`).
- A bare PDF matches three parsers (`elec_bill`, `gas_bill`, `sapn_summary`), so every bill needed `--source`
  (`E_AMBIGUOUS_SOURCE`), and an inbox of bills of two kinds needed one command per kind.
- The bills print their issue date and say what they are ("electricity bill", an NMI; "gas bill", a MIRN). Reading the
  text fixes both. This reverses P5's Q2 ("no PDF text extraction") for the date and the kind only: P5's YAML
  transcription stays the way gas figures enter the pipeline (§7).

## 2. The text and the issue date (`adapters/sources/pdf_text.py`)

- `pdf_text(data) -> str`: the text of the first three pages (`MAX_PAGES`), from **pypdf** (pure Python, no native
  dependencies, BSD-3), pages joined by a newline. An encrypted PDF, or one pypdf can't read, has no text (`""`); pypdf's
  own log messages are kept below `ERROR`. The text is never stored, logged or returned beyond the call.
- `issue_date(text) -> datetime | None`: the first date that follows a label `Issue date`, `Date of issue`,
  `Date issued` or `Issued on` (any case; an optional `:`; any whitespace, newlines included). The date is
  `23 Jan 2025` (a month name or an abbreviation of at least three letters, `Sept` too, an optional ordinal suffix and
  comma), `23/01/2025` (day first, `/`, `.` or `-`) or `2025-01-23`. It is naive, at midnight. A label whose date
  doesn't exist (31 Feb) is skipped; the earliest label in the text wins.
- pypdf replaces the plan's "pdfplumber (later, for bill PDFs)": pdfplumber pulls in pdfminer, Pillow and pypdfium2
  for layout features the date and the kind don't need.

## 3. The kind, and settling the PDF tie

- `pdf_kind(text) -> SourceType | None`:
  - a bill (`tax invoice` or the word `bill`) that names an electricity marker (`NMI`, `National Metering Identifier`,
    `electricity bill`) and no gas marker (`MIRN`, `Meter Installation Registration Number`, `gas bill`) →
    `elec_bill`
  - the reverse → `gas_bill`
  - not a bill, and `meter summary` in the text → `sapn_summary`
  - otherwise (no text, a dual-fuel bill naming both, anything else) → `None`
- A new port protocol, `ports/sources.py: ConfirmingParser`, with `confirm(filename, data) -> bool`. It sees the whole
  file; `detect` still sees only the first 64 KiB.
- D3 §5.1 step 3 becomes: several detections at the top rank → the tied parsers that are `ConfirmingParser`s and
  decline the file drop out; exactly one parser left → use it (evidence as detected, `["content"]`); otherwise
  `E_AMBIGUOUS_SOURCE` with the candidates still in the tie (all of them if every one declined). `confirm` never
  overrides a higher rank, and `--source` still skips detection ranking and `confirm` (evidence `["option"]`), so an
  unusual bill can always be landed by naming its source.
- `elec_bill` and `gas_bill` confirm when `pdf_kind` names their source. `sapn_summary` confirms every CSV and a PDF
  when `pdf_kind` names `sapn_summary`; a SAPN-named PDF already wins on filename + content and never reaches the tie.

## 4. `elec_bill`, `gas_bill` version 2

- `inspect`: the version 1 checks (a PDF, `%%EOF`, not encrypted), then `source_ts` = `issue_date(pdf_text(data))`
  with `extra.source_ts_from: "pdf_text"`; else the creation date as before (`"pdf_metadata"`); neither →
  `E_INVALID_INPUT`, `details.reason: "no_date"` (replaces `no_creation_date` for these two parsers).
- `identifiers` stay empty: the premises check for bills is unchanged (a possible follow-up, not in this change).
- `sapn_summary` stays version 1: `confirm` changes which parser is chosen, never what it records.
- Already-landed bills keep their records: the same bytes land as `already_landed`, and no stage reads a bill PDF's
  `detector.version` (`gas_bills` only checks the PDF's dataset is catalogued).

## 5. Dependency

- `pypdf>=7.0` in `[project] dependencies` and both lock files: the tool's `uv.lock`, and the workspace's (the
  workspace depends on the tool as an editable package; the owner commits its `uv.lock` change).
- `adapters/sources` may import it; core, ports and services don't (no import-linter change is needed: the existing
  contracts already confine third-party I/O libraries).

## 6. Landing many bills

- With §3, `uv run energatron land data/landing/` lands every new bill in the inbox, of either kind, in one command and
  one commit; nothing about `land` itself changes (it already takes several paths and directories).
- The README's bill note changes from "a bill PDF needs `--source`" to "bills are recognised from their text; name the
  source only if `land` says it can't tell (`E_AMBIGUOUS_SOURCE`)".

## 7. What stays

- `gas_bill_record` (P5) remains the way gas figures enter the pipeline: the text gives the date and the kind only,
  never figures or identifiers.
- Text extraction never runs on non-PDF files and never on PDFs that a filename already settles.

## 8. Tests

- `test_pdf_text.py`: text from generated PDFs (`make_pdf(text=…)`, Helvetica lines), unreadable PDFs, every kind
  rule, every date form and the skip rules.
- `test_bill_pdf.py`: version 2, the issue date over the creation date, the fallback, `no_date`, `confirm` per kind.
- `test_sapn_summary.py`: `confirm`.
- `test_resolve_parser.py`: the real three-way tie settled by text, unsettled without it, stand-in confirmers (drop-out,
  all decline, rank and `--source` untouched, `confirm` gets the whole file and `detect` the head).
- `test_land.py`: a folder of two electricity bills and a gas bill lands in one call, without `--source`, dated by issue
  date, in one commit.

## 9. Edits to earlier docs

- D3 §5.1 step 3 and §5.6: as §3 and §4 (a note at the head of each points here).
- P5 spec Q2: a note that the date and the kind now come from the text (§1).
- The plan: rev 16 (§4's pdfplumber line → pypdf; §10.6 and the source table row for bills).
