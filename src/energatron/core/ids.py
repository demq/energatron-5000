"""ID constructors and parsers, and the compact timestamp formats (D1 §6.2, §6.3).

IDs are opaque: code reads meaning out of an ID only through the `parse_*` functions here. They never contain
personal data, being built from source types, timestamps, hashes and public plan IDs.

A malformed argument raises `ValueError`, and a `datetime` passed where a `date` is meant raises `TypeError`.
The messages never repeat the value, which may not be an ID at all.
"""

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime

from energatron.core.canonical import JSONValue, content_hash
from energatron.core.errors import IdCollisionError

# D1 §6.3's patterns, matched with `fullmatch`, with `[0-9]` for `\d`, which also matches non-ASCII digits.
_DATASET_ID = re.compile(r"(?P<source>[a-z][a-z0-9_]*)-(?P<at>[0-9]{8}T[0-9]{6})-(?P<hash>[0-9a-f]{8})")
_OFFER_SNAPSHOT_ID = re.compile(r"(?P<plan>[^~\s]+)~(?P<at>[0-9]{8}T[0-9]{6})Z~(?P<hash>[0-9a-f]{8})")
_FETCH_BATCH_ID = re.compile(r"fetch-(?P<at>[0-9]{8}T[0-9]{6})Z")
_RUN_ID = re.compile(r"run-(?P<at>[0-9]{8}T[0-9]{6})Z-(?P<head>[0-9a-f]{7})")
_REC_ID = re.compile(r"REC-(?P<day>[0-9]{8})-(?P<hash>[0-9a-f]{8})")
_DECISION_ID = re.compile(r"DEC-(?P<day>[0-9]{8})-(?P<number>[0-9]{2})")

_SOURCE = re.compile(r"[a-z][a-z0-9_]*")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_COMMIT_SHA = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")  # SHA-1 or SHA-256 object format


@dataclass(frozen=True, slots=True)
class DatasetIdParts:
    source: str
    source_ts: datetime  # naive: as the source gave it, or converted to UTC if the source gave an offset
    sha256_prefix: str


@dataclass(frozen=True, slots=True)
class OfferSnapshotIdParts:
    plan_id: str
    last_updated: datetime  # UTC
    content_hash_prefix: str


@dataclass(frozen=True, slots=True)
class FetchBatchIdParts:
    fetched_at: datetime  # UTC


@dataclass(frozen=True, slots=True)
class RunIdParts:
    started_at: datetime  # UTC
    head_prefix: str


@dataclass(frozen=True, slots=True)
class RecIdParts:
    as_of: date
    payload_hash_prefix: str


@dataclass(frozen=True, slots=True)
class DecisionIdParts:
    day: date
    number: int  # 1 for the day's first decision


# Timestamps (D1 §6.2). Seconds are the finest unit: microseconds are dropped.


def compact_utc(dt: datetime) -> str:
    """An aware datetime, converted to UTC, as `YYYYMMDDTHHMMSSZ`; a naive one raises `ValueError`."""
    if not _is_aware(dt):
        raise ValueError("compact_utc needs a timezone-aware datetime")
    return _compact(dt.astimezone(UTC)) + "Z"


def compact_source(dt: datetime) -> str:
    """A source timestamp as `YYYYMMDDTHHMMSS`: an aware one converted to UTC, a naive one as given.

    Used only in dataset IDs; the catalog keeps the full `source_ts`, including any offset.
    """
    return _compact(dt.astimezone(UTC) if _is_aware(dt) else dt)


# Constructors (D1 §6.3)


def dataset_id(source: str, source_ts: datetime, sha256: str) -> str:
    """`<source>-<compact_source(source_ts)>-<sha256[:8]>`, e.g. `sapn_interval-20261023T100345-3fa9c2e1`."""
    if _SOURCE.fullmatch(source) is None:
        raise ValueError("source must be a snake_case source type")
    if _SHA256.fullmatch(sha256) is None:
        raise ValueError("sha256 must be 64 lowercase hex characters")
    return f"{source}-{compact_source(source_ts)}-{sha256[:8]}"


def offer_snapshot_id(plan_id: str, last_updated: datetime, data: Mapping[str, JSONValue]) -> str:
    """`<plan_id>~<compact_utc(last_updated)>~<content_hash(data)[:8]>`.

    `data` is the parsed `data` object of the plan-detail response, without `links` and `meta`. Git forbids `~` in
    ref names, so an offer snapshot ID never appears in a branch or tag name.
    """
    if not plan_id or "~" in plan_id or any(character.isspace() for character in plan_id):
        raise ValueError("plan_id must be non-empty and hold no '~' or whitespace")
    return f"{plan_id}~{compact_utc(last_updated)}~{content_hash(data)[:8]}"


def fetch_batch_id(now: datetime) -> str:
    """`fetch-<compact_utc(now)>`."""
    return f"fetch-{compact_utc(now)}"


def run_id(now: datetime, head: str) -> str:
    """`run-<compact_utc(now)>-<head[:7]>`, where `head` is the full workspace HEAD SHA at run start.

    The SHA is sliced here, never shortened with `git rev-parse --short`, whose length varies.
    """
    if _COMMIT_SHA.fullmatch(head) is None:
        raise ValueError("head must be a full commit SHA in lowercase hex")
    return f"run-{compact_utc(now)}-{head[:7]}"


def rec_id(as_of: date, payload: JSONValue) -> str:
    """`REC-<as_of>-<content_hash(payload)[:8]>`. The payload is content only, so a rerun gives the same ID."""
    _require_date(as_of, "as_of")
    return f"REC-{_compact_date(as_of)}-{content_hash(payload)[:8]}"


def decision_id(day: date, existing: Iterable[str]) -> str:
    """`DEC-<day>-<n>`, where `n` is 1 + the number of decisions already made on that workspace-local day.

    `existing` holds the IDs of the decisions made so far, on any day. An ID that already exists, which a gap
    in the numbering would give, raises `IdCollisionError`; more than 99 decisions a day raises `ValueError`.
    """
    _require_date(day, "day")
    taken = set(existing)
    number = 1 + sum(1 for value in taken if parse_decision_id(value).day == day)
    if number > 99:
        raise ValueError("a day has room for 99 decisions")
    new_id = f"DEC-{_compact_date(day)}-{number:02d}"
    if new_id in taken:
        raise IdCollisionError("The next decision ID for that day already exists.", decision_id=new_id)
    return new_id


def check_dataset_id(computed_id: str, sha256: str, existing_sha256: str | None) -> bool:
    """Apply the collision rule to a computed dataset ID and the sha256 already recorded under it, if any.

    Returns False when the ID is free, and True when it holds these same bytes: already landed, a no-op. Raises
    `IdCollisionError` when it holds different bytes. IDs are never lengthened to make room.
    """
    if existing_sha256 is None:
        return False
    if existing_sha256 != sha256:
        raise IdCollisionError("The dataset ID already exists for different content.", dataset_id=computed_id)
    return True


# Parsers


def parse_dataset_id(value: str) -> DatasetIdParts:
    match = _match(_DATASET_ID, value, "dataset ID")
    return DatasetIdParts(match["source"], _datetime(match["at"], "dataset ID", utc=False), match["hash"])


def parse_offer_snapshot_id(value: str) -> OfferSnapshotIdParts:
    match = _match(_OFFER_SNAPSHOT_ID, value, "offer snapshot ID")
    return OfferSnapshotIdParts(match["plan"], _datetime(match["at"], "offer snapshot ID", utc=True), match["hash"])


def parse_fetch_batch_id(value: str) -> FetchBatchIdParts:
    match = _match(_FETCH_BATCH_ID, value, "fetch batch ID")
    return FetchBatchIdParts(_datetime(match["at"], "fetch batch ID", utc=True))


def parse_run_id(value: str) -> RunIdParts:
    match = _match(_RUN_ID, value, "run ID")
    return RunIdParts(_datetime(match["at"], "run ID", utc=True), match["head"])


def parse_rec_id(value: str) -> RecIdParts:
    match = _match(_REC_ID, value, "recommendation ID")
    return RecIdParts(_date(match["day"], "recommendation ID"), match["hash"])


def parse_decision_id(value: str) -> DecisionIdParts:
    match = _match(_DECISION_ID, value, "decision ID")
    number = int(match["number"])
    if number == 0:
        raise ValueError("not a valid decision ID: numbering starts at 01")
    return DecisionIdParts(_date(match["day"], "decision ID"), number)


# Helpers


def _is_aware(dt: datetime) -> bool:
    return dt.tzinfo is not None and dt.utcoffset() is not None


def _compact(dt: datetime) -> str:
    # Formatted by hand: `strftime("%Y")` doesn't zero-pad years before 1000 on every platform.
    return f"{_compact_date(dt)}T{dt.hour:02d}{dt.minute:02d}{dt.second:02d}"


def _compact_date(day: date) -> str:
    return f"{day.year:04d}{day.month:02d}{day.day:02d}"


def _require_date(value: date, name: str) -> None:
    if isinstance(value, datetime):
        raise TypeError(f"{name} must be a date, not a datetime")


def _match(pattern: re.Pattern[str], value: str, what: str) -> re.Match[str]:
    match = pattern.fullmatch(value)
    if match is None:
        raise ValueError(f"not a valid {what}")
    return match


def _datetime(digits: str, what: str, *, utc: bool) -> datetime:
    """`YYYYMMDDTHHMMSS` as a datetime, in UTC or naive."""
    try:
        return datetime(
            int(digits[0:4]),
            int(digits[4:6]),
            int(digits[6:8]),
            int(digits[9:11]),
            int(digits[11:13]),
            int(digits[13:15]),
            tzinfo=UTC if utc else None,
        )
    except ValueError:
        raise ValueError(f"not a valid {what}: no such date or time") from None


def _date(digits: str, what: str) -> date:
    """`YYYYMMDD` as a date."""
    try:
        return date(int(digits[0:4]), int(digits[4:6]), int(digits[6:8]))
    except ValueError:
        raise ValueError(f"not a valid {what}: no such date") from None
