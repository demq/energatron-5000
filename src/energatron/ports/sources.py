"""The `SourceParser` port: format-specific plugins that recognise and inspect input files (D1 §4.3)."""

from typing import Protocol

from energatron.core.models.catalog import Detection, SourceInfo, SourceType


class SourceParser(Protocol):
    """Pure: bytes in, models out. P2 adds `parse(dataset_id, data) -> ParsedSource`."""

    source: SourceType  # e.g. "sapn_interval"
    version: int  # recorded in the catalog as detector.version; bump on any behaviour change

    def detect(self, filename: str, head: bytes) -> Detection | None:
        """`head` is the file's first 64 KiB; `detect` must not need more."""
        ...

    def inspect(self, filename: str, data: bytes) -> SourceInfo:
        """`SourceInfo.identifiers` is for the landing privacy check only, then discarded."""
        ...
