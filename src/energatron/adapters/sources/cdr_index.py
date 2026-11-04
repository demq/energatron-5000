"""`offer_cdr_index`: one `fetch-offers` batch's fetch log (P4 spec §3.3), version 1.

Public data: list pages and what was selected; no postcode, no identifier. Detected by its `kind` in the head, which
the writer's sorted keys place before the page bodies. Pure.
"""

import json
import re
from typing import Final

from energatron.core.canonical import JSONValue
from energatron.core.errors import InvalidInputError, SchemaTooNewError
from energatron.core.models.catalog import Detection, SourceInfo, SourceType
from energatron.core.models.offers import OfferFetchLog
from energatron.core.text import BOM, fail

_SOURCE: Final = SourceType.OFFER_CDR_INDEX
_KIND: Final = re.compile(r'"kind"\s*:\s*"offer_fetch_log"')


class CdrIndexParser:
    """The `SourceParser` for `offer_cdr_index` (D1 §4.3; P4 spec §3.3)."""

    source: SourceType = _SOURCE
    version: int = 1

    def detect(self, filename: str, head: bytes) -> Detection | None:
        text = head.decode("utf-8", errors="replace").removeprefix(BOM)
        if text.lstrip().startswith("{") and _KIND.search(text):
            return Detection(source=self.source, evidence=("content",))
        return None

    def inspect(self, filename: str, data: bytes) -> SourceInfo:
        try:
            document = json.loads(data.decode("utf-8").removeprefix(BOM))
        except UnicodeDecodeError, ValueError, RecursionError:
            raise fail(_SOURCE, "The file isn't valid JSON.", "bad_json") from None
        if not isinstance(document, dict):
            raise fail(_SOURCE, "The file isn't a fetch log.", "not_a_fetch_log")
        try:
            log = OfferFetchLog.from_document(document)
        except InvalidInputError, SchemaTooNewError:
            raise fail(_SOURCE, "The fetch log doesn't match offer_fetch_log v1.", "invalid_fetch_log") from None
        extra: dict[str, JSONValue] = {
            "fetch_batch_id": log.fetch_batch_id,
            "retailers": [retailer.base for retailer in log.retailers],
            "selected": len(log.selected),
            "source_ts_from": "content",
        }
        return SourceInfo(source_ts=log.fetched_at, coverage=None, extra=extra, identifiers={}, extension=".json")


OFFER_CDR_INDEX: Final = CdrIndexParser()
"""The registered instance (`registry.PARSERS`)."""
