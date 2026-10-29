"""Source types and the raw-zone catalog (D1 §4.1, §4.3; D3 §3.1, §4).

Created in P0 because the `Catalog` and `SourceParser` ports name these models. P1.2.1 completes them:
`CatalogRecord.from_json_line`, and the rule that `stored_path` equals `WorkspacePaths.blob_key(...)`.
"""

from datetime import date, datetime
from enum import StrEnum, unique
from typing import Annotated, ClassVar, Final, Literal, Self

from pydantic import AfterValidator, Field, model_validator

from energatron.core.canonical import JSONValue
from energatron.core.ids import dataset_id as make_dataset_id
from energatron.core.models._common import DatasetId, FrozenModel, Sha256Hex, UtcDatetime
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
    stored_path: str = Field(min_length=1)  # P1.2.1: equals blob_key(source, dataset_id, ext)
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
        return self
