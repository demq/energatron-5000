"""What a PDF's text says: its kind (electricity bill, gas bill, SAPN summary) and its issue date (bill PDF spec).

Pure: bytes in, values out. The text comes from pypdf, first `MAX_PAGES` pages only; a PDF pypdf can't read, or an
encrypted one, has no text (""). Nothing here keeps the text: callers get a source type or a date, and only
`pdf_pages` returns content, for `energatron gas-bill text` (P9 spec §4.2).
"""

import functools
import io
import logging
import re
from datetime import datetime
from typing import Final

from pypdf import PdfReader

from energatron.core.models.catalog import SourceType

MAX_PAGES: Final = 3
"""The pages read: a bill's issue date and the words that tell its kind are on its first page."""

logging.getLogger("pypdf").setLevel(logging.ERROR)  # font and encoding notes on stderr would only be noise

_BILL: Final = re.compile(r"\btax\s+invoice\b|\bbill\b", re.IGNORECASE)
_ELEC: Final = re.compile(r"\bNMI\b|national\s+metering\s+identifier|\belectricity\s+bill\b", re.IGNORECASE)
_GAS: Final = re.compile(r"\bMIRN\b|meter\s+installation\s+registration\s+number|\bgas\s+bill\b", re.IGNORECASE)
_SUMMARY: Final = re.compile(r"\bmeter\s+summary\b", re.IGNORECASE)

_LABEL: Final = r"\b(?:issue\s+date|date\s+of\s+issue|date\s+issued|issued\s+on)\b\s*:?\s*"
_NAMED: Final = re.compile(_LABEL + r"(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]{3,9})\.?,?\s+(\d{4})\b", re.IGNORECASE)
_NUMERIC: Final = re.compile(_LABEL + r"(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})\b", re.IGNORECASE)
_ISO: Final = re.compile(_LABEL + r"(\d{4})-(\d{2})-(\d{2})\b", re.IGNORECASE)
_MONTHS: Final = (
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
)


@functools.lru_cache(maxsize=8)
def pdf_text(data: bytes) -> str:
    """The text of the first `MAX_PAGES` pages, pages joined by a newline; "" when pypdf can't read the PDF."""
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            return ""
        pages = reader.pages
        return "\n".join(pages[number].extract_text() or "" for number in range(min(len(pages), MAX_PAGES)))
    except Exception:  # a malformed PDF can raise almost anything inside pypdf; it just has no usable text
        return ""


def pdf_pages(data: bytes) -> list[str]:
    """Every page's text, for transcription (P9 spec §4.2); [] when pypdf can't read the PDF or it is encrypted."""
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            return []
        return [page.extract_text() or "" for page in reader.pages]
    except Exception:  # as in `pdf_text`: a malformed PDF has no usable text
        return []


def pdf_kind(text: str) -> SourceType | None:
    """The source a PDF's text names, or `None` when it names none or several.

    A bill ("tax invoice" or "bill") naming an NMI or "electricity bill", and no MIRN or "gas bill", is `elec_bill`;
    the reverse is `gas_bill`; a "meter summary" that isn't a bill is `sapn_summary`.
    """
    bill = _BILL.search(text) is not None
    elec = _ELEC.search(text) is not None
    gas = _GAS.search(text) is not None
    if bill and elec and not gas:
        return SourceType.ELEC_BILL
    if bill and gas and not elec:
        return SourceType.GAS_BILL
    if not bill and _SUMMARY.search(text) is not None:
        return SourceType.SAPN_SUMMARY
    return None


def issue_date(text: str) -> datetime | None:
    """The first date that follows an "Issue date" label (or "Date of issue", "Date issued", "Issued on").

    The date is `17 Jan 2025` (any month name or its abbreviation, an optional `st`/`nd`/`rd`/`th`), `17/01/2025`
    (day first) or `2025-01-17`; naive, at midnight. Labels whose date doesn't parse are skipped.
    """
    found: list[tuple[int, datetime]] = []
    for match in _NAMED.finditer(text):
        month = _month(match[2])
        if month is not None:
            found.extend(_dated(match.start(), int(match[3]), month, int(match[1])))
    for match in _NUMERIC.finditer(text):
        found.extend(_dated(match.start(), int(match[3]), int(match[2]), int(match[1])))
    for match in _ISO.finditer(text):
        found.extend(_dated(match.start(), int(match[1]), int(match[2]), int(match[3])))
    return min(found, key=lambda pair: pair[0])[1] if found else None


def _month(word: str) -> int | None:
    word = word.lower()
    for number, name in enumerate(_MONTHS, start=1):
        if name.startswith(word) or (word == "sept" and number == 9):
            return number
    return None


def _dated(position: int, year: int, month: int, day: int) -> list[tuple[int, datetime]]:
    try:
        return [(position, datetime(year, month, day))]
    except ValueError:
        return []
