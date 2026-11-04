"""`offer_manual`: a tariff written by hand in the CDR contract shape (P4 spec §5.3), version 1.

YAML loaded with `yaml.safe_load`. A date written unquoted becomes a YAML date, which canonical JSON can't hash and DVC
refuses in `params.yaml`, so it is refused here too (`unquoted_date`). The snapshot ID is computed over the whole
document, so `land` treats a re-landed copy with the same content as the same snapshot. Holds no identifier; the
premises check warns `W_PREMISES_UNCHECKED`. Pure.
"""

import re
from datetime import date
from typing import Final

import yaml

from energatron.core.canonical import JSONValue
from energatron.core.errors import InvalidInputError, SchemaTooNewError
from energatron.core.ids import offer_snapshot_id
from energatron.core.models.catalog import Detection, SourceInfo, SourceType
from energatron.core.models.offers import OfferManual
from energatron.core.text import BOM, fail

_SOURCE: Final = SourceType.OFFER_MANUAL
_KIND_LINE: Final = re.compile(r"(?m)^kind:\s*offer_manual\s*$")


class OfferManualParser:
    """The `SourceParser` for `offer_manual` (D1 §4.3; P4 spec §5.3)."""

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
        if _holds_a_date(document):
            raise fail(_SOURCE, "A date is written unquoted: quote every date.", "unquoted_date")
        try:
            manual = OfferManual.from_document(document)
        except InvalidInputError, SchemaTooNewError:
            raise fail(_SOURCE, "The document doesn't match offer_manual v1.", "invalid_offer_manual") from None
        try:
            snapshot = offer_snapshot_id(manual.plan_id, manual.published, document)
        except ValueError, RecursionError:  # a number canonical JSON can't hold
            raise fail(_SOURCE, "The document holds a value that can't be hashed.", "unhashable") from None
        extra: dict[str, JSONValue] = {
            "plan_id": manual.plan_id,
            "offer_snapshot_id": snapshot,
            "source_ts_from": "content",
        }
        return SourceInfo(source_ts=manual.published, coverage=None, extra=extra, identifiers={}, extension=".yaml")


def _holds_a_date(value: object) -> bool:
    if isinstance(value, date):  # datetime is a date too
        return True
    if isinstance(value, dict):
        return any(_holds_a_date(key) or _holds_a_date(item) for key, item in value.items())  # pyright: ignore[reportUnknownVariableType]
    if isinstance(value, list):
        return any(_holds_a_date(item) for item in value)  # pyright: ignore[reportUnknownVariableType]
    return False


OFFER_MANUAL: Final = OfferManualParser()
"""The registered instance (`registry.PARSERS`)."""
