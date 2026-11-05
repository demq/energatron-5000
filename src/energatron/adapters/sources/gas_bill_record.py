"""`gas_bill_record`: one gas bill transcribed as YAML from its landed PDF (P5 spec §3.2), version 1.

Loaded with `yaml.safe_load`; an unquoted date is refused (`unquoted_date`), as for `offer_manual`. `source_ts` is the
issue date at midnight UTC and `coverage` the bill period. The record holds no identifier, so the premises check warns
`W_PREMISES_UNCHECKED`. A corrected transcription lands as a new dataset; `gas_bills` keeps the newest per PDF. Pure.
"""

import re
from datetime import UTC, datetime, time
from typing import Final

import yaml

from energatron.adapters.sources.offer_manual import holds_a_date
from energatron.core.canonical import JSONValue
from energatron.core.errors import InvalidInputError, SchemaTooNewError
from energatron.core.models.catalog import Coverage, Detection, SourceInfo, SourceType
from energatron.core.models.gas import GasBillRecord
from energatron.core.text import BOM, fail

_SOURCE: Final = SourceType.GAS_BILL_RECORD
_KIND_LINE: Final = re.compile(r"(?m)^kind:\s*gas_bill_record\s*$")


class GasBillRecordParser:
    """The `SourceParser` for `gas_bill_record` (D1 §4.3; P5 spec §3.2)."""

    source: SourceType = _SOURCE
    version: int = 1

    def detect(self, filename: str, head: bytes) -> Detection | None:
        text = head.decode("utf-8", errors="replace").removeprefix(BOM)
        return Detection(source=self.source, evidence=("content",)) if _KIND_LINE.search(text) else None

    def inspect(self, filename: str, data: bytes) -> SourceInfo:
        try:
            document = yaml.safe_load(data.decode("utf-8").removeprefix(BOM))
        except UnicodeDecodeError:
            raise fail(_SOURCE, "The file isn't UTF-8.", "not_utf8") from None
        except yaml.YAMLError:
            raise fail(_SOURCE, "The file isn't valid YAML.", "bad_yaml") from None
        if not isinstance(document, dict):
            raise fail(_SOURCE, "The file isn't a YAML mapping.", "not_a_mapping")
        if holds_a_date(document):
            raise fail(_SOURCE, "A date is written unquoted: quote every date.", "unquoted_date")
        record = read_record(document)
        extra: dict[str, JSONValue] = {
            "pdf_dataset_id": record.pdf_dataset_id,
            "period_start": record.period_start.isoformat(),
            "period_end": record.period_end.isoformat(),
            "source_ts_from": "content",
        }
        return SourceInfo(
            source_ts=datetime.combine(record.issue_date, time(), tzinfo=UTC),
            coverage=Coverage(start=record.period_start, end=record.period_end),
            extra=extra,
            identifiers={},
            extension=".yaml",
        )


def read_record(document: dict[str, JSONValue]) -> GasBillRecord:
    """The record in `document`; one that doesn't validate → `invalid_gas_bill_record` (no value is repeated)."""
    try:
        return GasBillRecord.from_document(document)
    except InvalidInputError, SchemaTooNewError:
        raise fail(_SOURCE, "The document doesn't match gas_bill_record v1.", "invalid_gas_bill_record") from None


GAS_BILL_RECORD: Final = GasBillRecordParser()
"""The registered instance (`registry.PARSERS`)."""
