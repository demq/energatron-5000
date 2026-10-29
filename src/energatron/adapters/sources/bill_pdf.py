"""`elec_bill` and `gas_bill`: bill PDFs, two instances of one parser (D3 §5.6), version 1.

No filename evidence: a bare PDF also matches `sapn_summary` content-only, so a bill always needs `--source`. P1
reads only the PDF's creation date; the P5 spec adds text extraction as version 2.
"""

import re
from typing import Final

from energatron.adapters.sources._common import fail
from energatron.adapters.sources.pdf_meta import creation_date
from energatron.core.models.catalog import Detection, SourceInfo, SourceType

PDF_MAGIC: Final = b"%PDF-"
_ENCRYPT: Final = re.compile(rb"/Encrypt(?![A-Za-z0-9])")


def is_complete_pdf(data: bytes) -> bool:
    """The file ends with `%%EOF`, trailing whitespace allowed."""
    return data.rstrip().endswith(b"%%EOF")


class BillPdfParser:
    """The `SourceParser` for one bill source type (D1 §4.3, D3 §5.6)."""

    version: int = 1

    def __init__(self, source: SourceType) -> None:
        self.source: SourceType = source

    def detect(self, filename: str, head: bytes) -> Detection | None:
        if head.startswith(PDF_MAGIC):
            return Detection(source=self.source, evidence=("content",))
        return None

    def inspect(self, filename: str, data: bytes) -> SourceInfo:
        if not data.startswith(PDF_MAGIC):
            raise fail(self.source, "The file isn't a PDF.", "not_pdf")
        if not is_complete_pdf(data):
            raise fail(self.source, "The PDF doesn't end with %%EOF: it may be truncated.", "truncated")
        if _ENCRYPT.search(data):
            raise fail(self.source, "The PDF is encrypted.", "encrypted")
        created = creation_date(data)
        if created is None:
            raise fail(self.source, "The PDF carries no creation date.", "no_creation_date")
        return SourceInfo(
            source_ts=created,
            coverage=None,
            extra={"source_ts_from": "pdf_metadata"},
            identifiers={},
            extension=".pdf",
        )


ELEC_BILL: Final = BillPdfParser(SourceType.ELEC_BILL)
GAS_BILL: Final = BillPdfParser(SourceType.GAS_BILL)
