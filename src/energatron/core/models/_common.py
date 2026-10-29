"""What the core models share: the frozen base for port models and the constrained field types.

Validator messages never repeat the value: some fields hold identifiers or filenames.
"""

import re
from datetime import datetime, timedelta
from typing import Annotated

from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict

from energatron.core.ids import parse_dataset_id


class FrozenModel(BaseModel):
    """Base of the models that ports take and return (D1 §4) and of the sections of persisted documents.

    Frozen, extra fields refused, input never shown in validation errors, and no NaN or infinities, which
    `canonical_json` can't write (D1 §6.1).
    """

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True, allow_inf_nan=False)


_SHA256 = re.compile(r"[0-9a-f]{64}")
_COMMIT_SHA = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")  # SHA-1 or SHA-256 object names (D1 §4.4)
_STAGE_NAME = re.compile(r"[a-z][a-z0-9_]*")
_REGION = re.compile(r"[a-z][a-z0-9_]*")
_BUNDLE_NAME = re.compile(r"[0-9]{8}T[0-9]{6}Z-[0-9a-f]{7}\.bundle")  # D2 §8.2


def _sha256(value: str) -> str:
    if _SHA256.fullmatch(value) is None:
        raise ValueError("expected 64 lowercase hex characters")
    return value


def _commit_sha(value: str) -> str:
    if _COMMIT_SHA.fullmatch(value) is None:
        raise ValueError("expected a full commit SHA: 40 or 64 lowercase hex characters")
    return value


def _bundle_name(value: str) -> str:
    if _BUNDLE_NAME.fullmatch(value) is None:
        raise ValueError("expected a bundle file name such as 20261023T093000Z-1a2b3c4.bundle")
    return value


def _stage_name(value: str) -> str:
    if _STAGE_NAME.fullmatch(value) is None:
        raise ValueError("a stage name is snake_case, such as catalog_index")
    return value


def _region(value: str) -> str:
    if _REGION.fullmatch(value) is None:
        raise ValueError("a region is a profile name in snake_case, such as sa")
    return value


def _relative_posix(value: str) -> str:
    if not value or value.startswith("/") or "\\" in value or any(part in ("", ".", "..") for part in value.split("/")):
        raise ValueError("expected a relative POSIX path with no empty, . or .. component")
    return value


def _utc(value: datetime) -> datetime:
    if value.utcoffset() != timedelta(0):
        raise ValueError("expected a UTC time")
    return value


def _dataset_id(value: str) -> str:
    parse_dataset_id(value)  # its ValueError never repeats the value
    return value


Sha256Hex = Annotated[str, AfterValidator(_sha256)]
"""A SHA-256 digest: 64 lowercase hex characters."""

CommitSha = Annotated[str, AfterValidator(_commit_sha)]
"""A full git object name: 40 hex characters in a SHA-1 repo, 64 in a SHA-256 repo. Never assume the length."""

BundleName = Annotated[str, AfterValidator(_bundle_name)]
"""A git bundle's file name, `<compact_utc(now)>-<head[:7]>.bundle`, with no directory (D2 §8.2)."""

UtcDatetime = Annotated[AwareDatetime, AfterValidator(_utc)]
"""An instant energatron generated: timezone-aware, with a zero UTC offset (D1 §12). Dumped with `Z`."""

DatasetId = Annotated[str, AfterValidator(_dataset_id)]
"""A dataset ID (D1 §6.3)."""

StageName = Annotated[str, AfterValidator(_stage_name)]
"""A pipeline stage's name, snake_case (D1 §9.2)."""

RegionName = Annotated[str, AfterValidator(_region)]
"""A region profile's name, snake_case, such as `sa` (D2 §6.1, D3 §10.3)."""

RelativePosixPath = Annotated[str, AfterValidator(_relative_posix)]
"""A relative POSIX path: no leading `/`, no backslash, no empty, `.` or `..` component (D1 §12)."""
