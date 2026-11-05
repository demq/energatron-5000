"""Reading a raw directory whole (P5's gas stages): each catalogued dataset with its blob, checked against its record.

A stage may not import another stage (D1 §11.1), so the gas stages share this module. A dataset whose blob is missing
or doesn't match its `sha256` is `mismatched`; one landed by a detector version the stage doesn't know is
`unknown_detector`. Neither is returned.
"""

from collections.abc import Collection
from dataclasses import dataclass, field
from pathlib import Path

from energatron.core.canonical import sha256_hex
from energatron.core.errors import InvalidInputError
from energatron.core.models.catalog import CATALOG_FILENAME, CatalogRecord, SourceType, parse_catalog, raw_key


@dataclass
class RawZone:
    """The datasets read from one raw directory, in catalog order, and those that couldn't be used."""

    datasets: list[tuple[CatalogRecord, bytes]] = field(default_factory=lambda: list[tuple[CatalogRecord, bytes]]())
    mismatched: list[str] = field(default_factory=lambda: list[str]())
    unknown_detector: list[str] = field(default_factory=lambda: list[str]())


def catalog_records(raw: Path, source: SourceType) -> list[CatalogRecord]:
    """The records of `raw/_catalog.jsonl`; an unreadable catalog → `E_INVALID_INPUT` naming the source."""
    try:
        catalog = (raw / CATALOG_FILENAME).read_bytes()
    except OSError:
        raise InvalidInputError("A raw directory has no readable catalog.", input=source.value) from None
    return parse_catalog(catalog, source)


def read_raw_zone(raw: Path, source: SourceType, known_versions: Collection[int]) -> RawZone:
    """Every dataset of `raw` whose blob matches its record and whose detector is `source` at a known version."""
    zone = RawZone()
    for record in catalog_records(raw, source):
        relative = record.stored_path.removeprefix(raw_key(record.source))
        try:
            data: bytes | None = raw.joinpath(*relative.split("/")).read_bytes()
        except OSError:
            data = None
        if data is None or sha256_hex(data) != record.sha256:
            zone.mismatched.append(record.dataset_id)
        elif record.detector.name != source or record.detector.version not in known_versions:
            zone.unknown_detector.append(record.dataset_id)
        else:
            zone.datasets.append((record, data))
    return zone
