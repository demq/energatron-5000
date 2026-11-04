"""What the core models share: the frozen base for port models and the constrained field types.

Validator messages never repeat the value: some fields hold identifiers or filenames.
"""

import re
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta
from typing import Annotated

from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict

from energatron.core.errors import InvalidInputError
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
ZONE_KEY = re.compile(r"^[A-Za-z0-9_+-]+(/[A-Za-z0-9_+-]+)*$")
"""The form of a time zone key: tzdata path-like text (P2 spec §6). `ZoneKey` and `StageRun.zone()` both use it."""
_WINDOW = re.compile(r"([0-9]{2}):([0-9]{2})-([0-9]{2}):([0-9]{2})")
_MINUTES_PER_DAY = 1440
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


def _zone_key(value: str) -> str:
    if ZONE_KEY.fullmatch(value) is None:
        raise ValueError("a time zone key is tzdata path-like text, such as Region/City")
    return value


def _minute_of_day(hours: str, minutes: str) -> int:
    hour, minute = int(hours), int(minutes)
    if hour > 23 or minute > 59 or minute % 5:
        raise ValueError("a window edge is HH:MM from 00:00 to 23:55, a multiple of 5 minutes")
    return hour * 60 + minute


def window_minutes(text: str) -> list[int]:
    """The minutes of the day (0-1439, in time order from the start) that a window `HH:MM-HH:MM` covers.

    Start inclusive, end exclusive; an end at or before the start wraps past midnight (equal edges are the whole
    day). `24:00` isn't a time, and every edge is a multiple of 5 minutes. A malformed window → `ValueError`, whose
    text never repeats the window.
    """
    found = _WINDOW.fullmatch(text)
    if found is None:
        raise ValueError("a window is HH:MM-HH:MM")
    start = _minute_of_day(found[1], found[2])
    end = _minute_of_day(found[3], found[4])
    if end > start:
        return list(range(start, end))
    return [*range(start, _MINUTES_PER_DAY), *range(0, end)]


def windows_partition_the_day(windows: Mapping[str, Sequence[str]], *, field: str) -> None:
    """Every window parses, each label has one, and together they cover every minute of the day exactly once.

    Otherwise `E_INVALID_INPUT` with `details.field` set to `field`; the message never repeats a window.
    """
    if not windows:
        raise InvalidInputError("The TOU windows list no label.", field=field)
    counts = [0] * _MINUTES_PER_DAY
    for label_windows in windows.values():
        if not label_windows:
            raise InvalidInputError("A TOU label has no window.", field=field)
        for text in label_windows:
            try:
                minutes = window_minutes(text)
            except ValueError as error:
                raise InvalidInputError(f"A TOU window is malformed: {error}.", field=field) from None
            for minute in minutes:
                counts[minute] += 1
    if any(count != 1 for count in counts):
        raise InvalidInputError("The TOU windows must cover every minute of the day exactly once.", field=field)


def partitioned_windows[T: Mapping[str, Sequence[str]]](field: str) -> Callable[[T], T]:
    """A pydantic validator for a window mapping that names `field` in its error (see `windows_partition_the_day`)."""

    def check(windows: T) -> T:
        windows_partition_the_day(windows, field=field)
        return windows

    return check


_RETAILER_BASE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


def retailer_bases(field: str) -> Callable[[tuple[str, ...]], tuple[str, ...]]:
    """A validator for a non-empty tuple of unique CDR base URIs (lower-case words joined by `-`), naming `field`."""

    def check(bases: tuple[str, ...]) -> tuple[str, ...]:
        if not bases or len(set(bases)) != len(bases) or any(_RETAILER_BASE.fullmatch(base) is None for base in bases):
            raise ValueError(f"{field}: unique CDR base URIs, lower-case words joined by '-'")
        return bases

    return check


def trimmed_text(field: str) -> Callable[[str], str]:
    """A validator for a non-empty string with no surrounding whitespace, naming `field`."""

    def check(value: str) -> str:
        if not value or value != value.strip():
            raise ValueError(f"{field}: non-empty, with no surrounding whitespace")
        return value

    return check


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

ZoneKey = Annotated[str, AfterValidator(_zone_key)]
"""A time zone key in the form tzdata uses, such as `Region/City`. Only the form is checked: whether the zone exists
is checked where tzdata is read (`build_context()`, `StageRun.zone()`)."""

RelativePosixPath = Annotated[str, AfterValidator(_relative_posix)]
"""A relative POSIX path: no leading `/`, no backslash, no empty, `.` or `..` component (D1 §12)."""
