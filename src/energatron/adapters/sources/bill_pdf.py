"""`elec_bill` and `gas_bill`: bill PDFs, two instances of one parser (D3 §5.6), version 2 (bill PDF spec).

No filename evidence: a bare PDF also matches `sapn_summary` content-only, and the tie goes to `confirm`, which reads
the PDF's text (`pdf_text.pdf_kind`). `source_ts` is the issue date printed on the bill, else the PDF's creation date.
Version 1 read only the creation date and never confirmed.
"""

import re
from typing import Final

from energatron.adapters.sources.pdf_meta import creation_date
from energatron.adapters.sources.pdf_text import issue_date, pdf_kind, pdf_text
from energatron.core.models.catalog import Detection, SourceInfo, SourceType
from energatron.core.text import fail

PDF_MAGIC: Final = b"%PDF-"
_ENCRYPT: Final = re.compile(rb"/Encrypt(?![A-Za-z0-9])")


def is_complete_pdf(data: bytes) -> bool:
    """The file ends with `%%EOF`, trailing whitespace allowed."""
    return data.rstrip().endswith(b"%%EOF")


class BillPdfParser:
    """The `SourceParser` for one bill source type (D1 §4.3, D3 §5.6)."""

    version: int = 2

    def __init__(self, source: SourceType) -> None:
        self.source: SourceType = source

    def detect(self, filename: str, head: bytes) -> Detection | None:
        if head.startswith(PDF_MAGIC):
            return Detection(source=self.source, evidence=("content",))
        return None

    def confirm(self, filename: str, data: bytes) -> bool:
        """The PDF's text says it is this kind of bill (`pdf_kind`)."""
        return data.startswith(PDF_MAGIC) and pdf_kind(pdf_text(data)) is self.source

    def inspect(self, filename: str, data: bytes) -> SourceInfo:
        if not data.startswith(PDF_MAGIC):
            raise fail(self.source, "The file isn't a PDF.", "not_pdf")
        if not is_complete_pdf(data):
            raise fail(self.source, "The PDF doesn't end with %%EOF: it may be truncated.", "truncated")
        if _ENCRYPT.search(data):
            raise fail(self.source, "The PDF is encrypted.", "encrypted")
        source_ts, source_ts_from = issue_date(pdf_text(data)), "pdf_text"
        if source_ts is None:
            source_ts, source_ts_from = creation_date(data), "pdf_metadata"
        if source_ts is None:
            raise fail(self.source, "The PDF carries no issue date and no creation date.", "no_date")
        return SourceInfo(
            source_ts=source_ts,
            coverage=None,
            extra={"source_ts_from": source_ts_from},
            identifiers={},
            extension=".pdf",
        )


ELEC_BILL: Final = BillPdfParser(SourceType.ELEC_BILL)
GAS_BILL: Final = BillPdfParser(SourceType.GAS_BILL)
