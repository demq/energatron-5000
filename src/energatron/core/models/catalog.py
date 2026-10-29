"""Source types and the raw-zone catalog (D1 §4.1, §4.3; D3 §3.1, §4).

Created in P0 because the `Catalog` and `SourceParser` ports name these models. P1.2.1 completes them:
`CatalogRecord.from_json_line`, `blob_key()` and the rule that `stored_path` equals it.
"""

import json
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum, unique
from typing import Annotated, ClassVar, Final, Literal, Self

from pydantic import AfterValidator, Field, model_validator

from energatron.core.canonical import JSONValue
from energatron.core.errors import CatalogCorruptError, InvalidInputError
from energatron.core.ids import dataset_id as make_dataset_id
from energatron.core.ids import parse_dataset_id
from energatron.core.models._common import DatasetId, FrozenModel, Sha256Hex, UtcDatetime
from energatron.core.models.storage import RAW_PREFIX
from energatron.core.persisted import Persisted


@unique
class SourceType(StrEnum):
    """Every source type in plan §8.2. All exist from P1, even where no parser does yet (D3 §3.1)."""

    ELEC_BILL = "elec_bill"
    GAS_BILL = "gas_bill"
    GAS_BILL_RECORD = "gas_bill_record"
    OFFER_CDR = "offer_cdr"
    OFFER_CDR_INDEX = "offer_cdr_index"
    OFFER_MANUAL = "offer_manual"
    RETAILER_TXN = "retailer_txn"
    RETAILER_USAGE = "retailer_usage"
    SAPN_INTERVAL = "sapn_interval"
    SAPN_SUMMARY = "sapn_summary"


PUBLIC_SOURCES: Final[frozenset[SourceType]] = frozenset({SourceType.OFFER_CDR, SourceType.OFFER_CDR_INDEX})
"""Public data, exempt from the premises check (D3 §6)."""

type Evidence = Literal["filename", "content"]
type DetectorEvidence = Literal["filename", "content", "option"]
type IdentifierKind = Literal["nmi", "meter_serial", "account"]

_EVIDENCE_ORDER: Final[tuple[Evidence, ...]] = ("filename", "content")
_EXTENSIONS: Final = frozenset({".csv", ".pdf", ".json", ".yaml"})

CATALOG_FILENAME: Final = "_catalog.jsonl"
"""Each source's catalog file, in its raw directory (D3 §3.2)."""


def raw_key(source: SourceType) -> str:
    """The key prefix of a source's raw directory, `data/raw/<source>/` (D3 §3.2)."""
    return f"{RAW_PREFIX}{SourceType(source).value}/"


def blob_key(source: SourceType, dataset_id: str, ext: str) -> str:
    """Where a dataset's file is stored: `data/raw/<source>/<YYYY>/<dataset_id><ext>` (D3 §3.2).

    `<YYYY>` is the first four characters of the ID's timestamp, so an aware `source_ts` files under its UTC year.
    Raises `ValueError` for an extension other than `.csv .pdf .json .yaml`, an invalid ID, or an ID of another
    source.
    """
    _extension(ext)
    parts = parse_dataset_id(dataset_id)
    if parts.source != SourceType(source).value:
        raise ValueError("the dataset ID belongs to another source")
    return f"{raw_key(source)}{parts.source_ts.year:04d}/{dataset_id}{ext}"


def _detection_evidence(evidence: tuple[str, ...]) -> tuple[str, ...]:
    if not evidence or evidence != tuple(item for item in _EVIDENCE_ORDER if item in evidence):
        raise ValueError("evidence is filename, content or both, in that order")
    return evidence


def _detector_evidence(evidence: tuple[str, ...]) -> tuple[str, ...]:
    if evidence == ("option",):
        return evidence
    return _detection_evidence(evidence)


def _extension(value: str) -> str:
    if value not in _EXTENSIONS:
        raise ValueError("the extension is one of .csv .pdf .json .yaml, lower-case")
    return value


def _base_name(value: str) -> str:
    if "/" in value:
        raise ValueError("expected a file name without a directory")
    return value


def _one_line(value: str) -> str:
    if "\n" in value or "\r" in value:
        raise ValueError("expected one line")
    return value


class Coverage(FrozenModel):
    """The first and last dates a source covers, both inclusive."""

    start: date
    end: date

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.start > self.end:
            raise ValueError("coverage starts after it ends")
        return self


class Detection(FrozenModel):
    """A parser's claim on a file: what matched, `filename` before `content`, never empty (D3 §4.2)."""

    source: SourceType
    evidence: Annotated[tuple[Evidence, ...], AfterValidator(_detection_evidence)]


class SourceInfo(FrozenModel):
    """What `SourceParser.inspect()` found in a file (D1 §4.3, D3 §4.2).

    `identifiers` exists only for the landing privacy check: it is never persisted, logged or returned, and never
    shown in a repr. `extra` holds non-personal facts only.
    """

    source_ts: datetime  # aware or naive; D3 §3.3 sets the rule per source type
    coverage: Coverage | None
    extra: dict[str, JSONValue]
    identifiers: dict[IdentifierKind, list[str]] = Field(repr=False, exclude=True)
    extension: Annotated[str, AfterValidator(_extension)]


class Detector(FrozenModel):
    """The `detector` of a catalog record: the parser that landed it and why it was chosen (D3 §4.1)."""

    name: SourceType  # the parser's `source`
    version: int = Field(ge=1)
    evidence: Annotated[tuple[DetectorEvidence, ...], AfterValidator(_detector_evidence)]  # or ("option",)


class CatalogRecord(Persisted):
    """One line of `data/raw/<source>/_catalog.jsonl` (D3 §4.1). Immutable once appended.

    `original_filename` and `notes` are private (the catalog is DVC-tracked) and never shown in a repr.
    """

    KIND: ClassVar[str] = "catalog_record"
    SCHEMA_VERSION: ClassVar[int] = 1
    kind: Literal["catalog_record"] = "catalog_record"
    schema_version: Literal[1] = 1

    dataset_id: DatasetId
    source: SourceType
    sha256: Sha256Hex
    bytes: int = Field(gt=0)
    stored_path: str = Field(min_length=1)  # equals blob_key(source, dataset_id, ext)
    original_filename: Annotated[str, AfterValidator(_base_name)] = Field(min_length=1, max_length=255, repr=False)
    landed_at: UtcDatetime
    file_mtime: UtcDatetime | None
    source_ts: datetime  # naive or aware, persisted as given
    coverage: Coverage | None
    detector: Detector
    extra: dict[str, JSONValue]
    supersedes: DatasetId | None
    notes: Annotated[str, AfterValidator(_one_line)] = Field(max_length=500, repr=False)

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.dataset_id != make_dataset_id(self.source, self.source_ts, self.sha256):
            raise ValueError("dataset_id doesn't match source, source_ts and sha256")
        if self.detector.name != self.source:
            raise ValueError("detector.name doesn't match source")
        if self.stored_path not in {blob_key(self.source, self.dataset_id, ext) for ext in _EXTENSIONS}:
            raise ValueError("stored_path isn't the blob key for source, dataset_id and a known extension")
        return self

    @classmethod
    def from_json_line(cls, line: str | bytes, *, number: int | None = None) -> Self:
        """Parse, upgrade and validate one catalog line (D3 §4.1). Surrounding whitespace, such as the trailing
        newline, is allowed.

        Invalid JSON, anything but one JSON object, NaN, a repeated key or a failed validation raises
        `E_CATALOG_CORRUPT` with `details.line` (`number`, the 1-based line number, when the caller knows it) and,
        for a failed validation, the fields and rules; never the line's text. A line from a newer energatron
        raises `E_SCHEMA_TOO_NEW`.
        """
        try:
            text = line.decode("utf-8") if isinstance(line, bytes) else line
            doc = json.loads(text, object_pairs_hook=_unique_keys, parse_constant=_no_constant)
        except ValueError:  # UnicodeDecodeError and JSONDecodeError are ValueErrors
            raise CatalogCorruptError(
                "A catalog line isn't one valid JSON object.", line=number, reason="invalid_json"
            ) from None
        if not isinstance(doc, dict):
            raise CatalogCorruptError("A catalog line isn't a JSON object.", line=number, reason="not_an_object")
        try:
            return cls.from_document(doc)
        except InvalidInputError as error:
            raise CatalogCorruptError(
                f"A catalog line is invalid: {error.message}",
                line=number,
                reason="invalid_record",
                errors=error.details.get("errors"),
            ) from None


def _unique_keys(pairs: list[tuple[str, JSONValue]]) -> dict[str, JSONValue]:
    doc = dict(pairs)
    if len(doc) != len(pairs):
        raise ValueError("repeated key")
    return doc


def _no_constant(_name: str) -> JSONValue:
    raise ValueError("NaN and infinities aren't JSON")


@dataclass(frozen=True, slots=True)
class CatalogLine:
    """One line of a `_catalog.jsonl`: its 1-based number and either its record or why it was refused."""

    number: int
    record: CatalogRecord | None
    error: CatalogCorruptError | None


def catalog_lines(data: bytes) -> Iterator[CatalogLine]:
    """Every line of a `_catalog.jsonl`, given its bytes, in file order, each parsed on its own (D3 §9).

    The per-line rules `parse_catalog` applies, without stopping at the first bad line: a CRLF line (`crlf`), a last
    line without its `\\n` (`unterminated`) or any `from_json_line` failure becomes that line's `error`, never its
    text. A record's `source` isn't checked against the file here. A line from a newer energatron still raises
    `E_SCHEMA_TOO_NEW` when it is reached: the tool is older than the data. An empty file has no lines. Lazy, so a
    reader that stops at the first bad line never parses the lines after it.
    """
    if not data:
        return
    raw = data.split(b"\n")
    terminated, last = raw[:-1], raw[-1]
    for number, line in enumerate(terminated, start=1):
        if line.endswith(b"\r"):
            yield CatalogLine(
                number, None, CatalogCorruptError("A catalog line ends in CRLF.", line=number, reason="crlf")
            )
            continue
        try:
            record = CatalogRecord.from_json_line(line, number=number)
        except CatalogCorruptError as error:
            yield CatalogLine(number, None, error)
            continue
        yield CatalogLine(number, record, None)
    if last:
        error = CatalogCorruptError("The catalog's last line is unterminated.", line=len(raw), reason="unterminated")
        yield CatalogLine(len(raw), None, error)


def parse_catalog(data: bytes, source: SourceType) -> list[CatalogRecord]:
    """Every record of one source's `_catalog.jsonl`, given its bytes, in file order (D1 §4.1; D3 §3.2).

    The rules every reader shares, `JsonlCatalog` and stage code alike: each line is one record followed by `\\n`
    (LF only, D1 §12), parsed and upgraded by `CatalogRecord.from_json_line`, and of this `source`. An empty file is
    an empty catalog. A last line without its `\\n` (`unterminated`), a CRLF line (`crlf`), a record of another
    source (`wrong_source`) or any `from_json_line` failure raises `E_CATALOG_CORRUPT` with `details.source` and
    `details.line`, never the line's text. A line from a newer energatron raises `E_SCHEMA_TOO_NEW`.
    `catalog_lines` applies the same per-line rules without stopping at a bad line; this reader stops at the first
    one, so a later line from a newer energatron doesn't mask it.
    """
    source = SourceType(source)
    if data and not data.endswith(b"\n"):
        raise CatalogCorruptError(
            "The catalog's last line is unterminated.",
            source=source.value,
            line=data.count(b"\n") + 1,
            reason="unterminated",
        )
    records: list[CatalogRecord] = []
    for line in catalog_lines(data):
        if line.error is not None:
            raise CatalogCorruptError(line.error.message, source=source.value, **line.error.details) from None
        assert line.record is not None
        if line.record.source != source:
            raise CatalogCorruptError(
                "A catalog line holds a record of another source.",
                source=source.value,
                line=line.number,
                reason="wrong_source",
            )
        records.append(line.record)
    return records
