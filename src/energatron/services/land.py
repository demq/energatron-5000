"""`land`: copy raw files into the raw zone, catalog them, track them with DVC and commit (D3 §5.1, §7, §8).

The command runs in three phases. Preconditions (§7.1) and the plan (§7.2) only read, so any error they raise leaves
nothing written, and a dry run stops after the plan with the statuses and errors the real run would give. The write
phase (§7.3) leaves a pending marker first, so re-running the same `land` after any failure finishes the job
(§7.5).

Nothing personal leaves this module: inputs are shown masked (`mask_filename`), errors and warnings name an input
by its `position`, logs hold counts and IDs, and `SourceInfo.identifiers` goes only to `check_premises()`.
"""

import json
import logging
import os
import stat
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, ClassVar, Final, Literal, Self

from pydantic import AfterValidator, Field, ValidationError

from energatron.core.canonical import JSONValue, canonical_json, sha256_hex
from energatron.core.errors import (
    AmbiguousSourceError,
    CatalogCorruptError,
    Category,
    CommitRejectedError,
    DenylistHitError,
    DirtyError,
    EnergatronError,
    ForeignPremisesError,
    IdCollisionError,
    InvalidInputError,
    PartialError,
    PrivateConfigMissingError,
    ResultWarning,
    StorageError,
    SubprocessError,
    UnknownSourceError,
    UsageError,
    WarningCode,
)
from energatron.core.ids import compact_source, compact_utc, dataset_id
from energatron.core.models._common import CommitSha, DatasetId, FrozenModel, Sha256Hex
from energatron.core.models.catalog import (
    CATALOG_FILENAME,
    CatalogRecord,
    Coverage,
    Detector,
    DetectorEvidence,
    SourceInfo,
    SourceType,
    blob_key,
    raw_key,
)
from energatron.core.models.storage import RAW_PREFIX
from energatron.core.persisted import Persisted
from energatron.core.privacy import DenyEntry, check_premises, derive_denylist, mask_filename
from energatron.ports.sources import SourceParser
from energatron.services import pipeline_sync
from energatron.services.context import ServiceContext, ServiceResult
from energatron.services.files import atomic_write_bytes

_log = logging.getLogger(__name__)

MAX_BYTES: Final = 256 * 1024 * 1024
"""The largest input `land` reads (D3 §7.2 step 3.1)."""

HEAD_BYTES: Final = 64 * 1024
"""What `detect` sees of a file (D1 §4.3)."""

NOTE_MAX: Final = 500
RAW_DIR: Final = RAW_PREFIX.rstrip("/")
PIPELINE_FILES: Final = ("dvc.yaml", "params.yaml", ".pre-commit-config.yaml")
RAW_GITIGNORE: Final = f"{RAW_PREFIX}.gitignore"
OUTSIDE: Final = "<outside>"
PENDING_MARKER: Final = ".energatron/tmp/land_pending.json"
PENDING_HINT: Final = (
    f"delete {PENDING_MARKER} and re-run: the IDs it held are then not named in the next commit, which becomes a "
    "`land: resync raw pointers` one (D3 §7.4); the catalog still finds them by `landed_at`"
)
RERUN_HINT: Final = "fix the cause, then re-run the same `energatron land`: it finishes what this run started"

type LandStatus = Literal["landed", "already_landed", "duplicate_in_batch", "same_snapshot", "rejected"]

_ITEM_ERRORS: Final[dict[str, type[EnergatronError]]] = {
    error.code: error for error in (UnknownSourceError, AmbiguousSourceError, InvalidInputError, IdCollisionError)
}


# Models


class ItemError(FrozenModel):
    """A rejected item's error, as the envelope shows errors: `{"code", "category", "message", "details"}`."""

    code: str
    category: Category
    message: str
    details: dict[str, JSONValue] = Field(default_factory=dict)

    @classmethod
    def of(cls, error: EnergatronError) -> Self:
        return cls(code=error.code, category=error.category, message=error.message, details=dict(error.details))


class LandItem(FrozenModel):
    """One input file's outcome (D3 §8.2). `dataset_id` is set for every status but `rejected`."""

    position: int = Field(ge=1)
    input: str  # workspace-relative or `<outside>/<name>`, every component masked
    bytes: int | None = Field(ge=0)  # null when rejected before it was read (a symlink)
    sha256: Sha256Hex | None
    status: LandStatus
    source: SourceType | None
    dataset_id: DatasetId | None
    stored_path: str | None
    coverage: Coverage | None
    error: ItemError | None


class LandResult(ServiceResult):
    """`land_result` v1 (D3 §8.2). In a dry run `landed` lists what would land; `commit` is then null."""

    KIND: ClassVar[str] = "land_result"
    SCHEMA_VERSION: ClassVar[int] = 1
    kind: Literal["land_result"] = "land_result"
    schema_version: Literal[1] = 1

    dry_run: bool
    items: list[LandItem]
    landed: list[DatasetId]  # new IDs, sorted
    commit: CommitSha | None  # the new commit's full SHA
    pointer_files: list[str]  # what was committed
    skipped_hidden: int = Field(ge=0)


def _sorted_unique(ids: list[str]) -> list[str]:
    if ids != sorted(set(ids)):
        raise ValueError("dataset IDs are sorted and unique")
    return ids


class LandPending(Persisted):
    """`land_pending` v1: `.energatron/tmp/land_pending.json`, the IDs of a `land` that hasn't committed yet (§7.3)."""

    KIND: ClassVar[str] = "land_pending"
    SCHEMA_VERSION: ClassVar[int] = 1
    kind: Literal["land_pending"] = "land_pending"
    schema_version: Literal[1] = 1

    dataset_ids: Annotated[list[DatasetId], AfterValidator(_sorted_unique)]


# Resolution (D3 §5.1)


def resolve_parser(
    parsers: Sequence[SourceParser], filename: str, head: bytes, source: SourceType | None = None
) -> tuple[SourceParser, tuple[DetectorEvidence, ...]]:
    """The parser for a file and the catalog's `detector.evidence` for it (D3 §5.1).

    With `source`: that parser (`E_USAGE` if there is none), provided its detection has `content` evidence
    (`E_INVALID_INPUT`, `content_mismatch`); the evidence is `("option",)`. Without: the single best detection with
    `content` evidence, filename + content ranking above content alone; a tie at the top → `E_AMBIGUOUS_SOURCE`
    with sorted `candidates`; only filename matches → `content_mismatch` naming the source; none →
    `E_UNKNOWN_SOURCE`.
    """
    if source is not None:
        parser = _parser_for(parsers, source)
        detection = parser.detect(filename, head)
        if detection is None or "content" not in detection.evidence:
            raise InvalidInputError(
                "The file's content doesn't match the source type given.", reason="content_mismatch"
            )
        return parser, ("option",)
    found = [(parser, detection) for parser in parsers if (detection := parser.detect(filename, head)) is not None]
    with_content = [(parser, detection) for parser, detection in found if "content" in detection.evidence]
    if with_content:
        best = max(len(detection.evidence) for _, detection in with_content)
        top = [(parser, detection) for parser, detection in with_content if len(detection.evidence) == best]
        if len(top) > 1:
            candidates = sorted(parser.source.value for parser, _ in top)
            raise AmbiguousSourceError(
                "Several parsers recognise this file: name its source with --source.", candidates=list(candidates)
            )
        parser, detection = top[0]
        return parser, detection.evidence
    if found:
        raise InvalidInputError(
            "The file is named like a known source, but its content doesn't match.",
            reason="content_mismatch",
            source=found[0][0].source.value,
        )
    raise UnknownSourceError("No parser recognises this file.")


def _parser_for(parsers: Sequence[SourceParser], source: SourceType) -> SourceParser:
    parser = next((parser for parser in parsers if parser.source == source), None)
    if parser is None:
        raise UsageError(f"no parser for {SourceType(source).value} in this version")
    return parser


# Landing (D3 §7)


@dataclass
class _Item:
    """An input file as the plan sees it."""

    position: int
    path: Path
    shown: str
    symlink: bool = False


@dataclass
class _Accepted:
    """An item that will land: everything the write phase needs."""

    position: int
    record: CatalogRecord
    data: bytes


@dataclass
class _Plan:
    items: list[LandItem] = field(default_factory=list)
    accepted: list[_Accepted] = field(default_factory=list)
    warnings: list[ResultWarning] = field(default_factory=list)
    skipped_hidden: int = 0
    pending: list[str] = field(default_factory=list)  # IDs in an interrupted run's marker


def land(
    ctx: ServiceContext,
    paths: Sequence[Path],
    *,
    source: SourceType | None = None,
    supersedes: str | None = None,
    note: str = "",
    dry_run: bool = False,
) -> LandResult:
    """Land `paths` (files, or directories of files) into the raw zone and commit the pointer files (D3 §7).

    The caller holds the workspace lock, for a dry run too. Item-level problems are in the result's items, never
    raised; `outcome()` gives the error the command ends with. Whole-command errors are raised.
    """
    landed_at = ctx.clock.now().astimezone(UTC).replace(microsecond=0)
    denylist = _preconditions(ctx, paths, source=source, supersedes=supersedes, note=note)
    plan = _plan(ctx, paths, denylist, source=source, supersedes=supersedes, note=note, landed_at=landed_at)
    new_ids = sorted(accepted.record.dataset_id for accepted in plan.accepted)
    if dry_run:
        _log.info("land dry run: %d item(s), %d would land", len(plan.items), len(new_ids))
        return _result(plan, new_ids, dry_run=True, commit=None, pointers=[])
    commit, pointers = _write(ctx, plan, new_ids)
    if commit is not None:
        status = ctx.vcs.backup_status()
        plan.warnings.append(
            ResultWarning(
                code=WarningCode.W_BACKUP_PENDING,
                message="The new commit isn't backed up yet: run `energatron backup` when convenient.",
                details={"commits_not_backed_up": status.commits_not_backed_up},
            )
        )
    _log.info("land: %d item(s), %d landed, commit made: %s", len(plan.items), len(new_ids), commit is not None)
    return _result(plan, new_ids, dry_run=False, commit=commit, pointers=pointers)


def outcome(result: LandResult) -> EnergatronError | None:
    """The error a `land` ends with (D3 §8.3): None when every item landed or was a no-op; a single input's own
    error when it was the only one and was rejected; otherwise `E_PARTIAL`, whose `data` is the result."""
    rejected = [item for item in result.items if item.status == "rejected"]
    if not rejected:
        return None
    if len(result.items) == 1:
        error = rejected[0].error
        assert error is not None
        cls = _ITEM_ERRORS.get(error.code, InvalidInputError)
        return cls(error.message, **error.details)
    return PartialError(
        f"{len(rejected)} of {len(result.items)} input files were rejected; the others were handled.",
        rejected=len(rejected),
        items=len(result.items),
    )


def write_order_key(
    source: SourceType, source_ts: datetime, file_mtime: datetime | None, position: int
) -> tuple[str, str, int, str, int]:
    """The order items are written in (D3 §7.3 step 2): source, data age, file age (null last), position."""
    mtime = ("", 1) if file_mtime is None else (compact_utc(file_mtime), 0)
    return SourceType(source).value, compact_source(source_ts), mtime[1], mtime[0], position


# Preconditions (§7.1)


def _preconditions(
    ctx: ServiceContext, paths: Sequence[Path], *, source: SourceType | None, supersedes: str | None, note: str
) -> tuple[DenyEntry, ...]:
    if ctx.private is None:
        raise PrivateConfigMissingError("land needs private/config.toml for the premises check.")
    denylist = derive_denylist(ctx.private)
    pipeline_sync.check(ctx)
    raw_dirty = [
        path for path in ctx.vcs.dirty_paths([RAW_DIR]) if not _is_pointer_file(path) and path != RAW_GITIGNORE
    ]
    if raw_dirty:
        raise DirtyError(
            "The raw zone has uncommitted changes other than DVC pointer files.",
            paths=[_mask_path(path, denylist) for path in raw_dirty],
        )
    pipeline_dirty = ctx.vcs.dirty_paths(PIPELINE_FILES)
    if pipeline_dirty:
        raise DirtyError(
            "The pipeline files have uncommitted changes.", paths=list(pipeline_dirty), hint="commit these first"
        )
    raw_root = _location(ctx.paths.raw_root)
    for position, path in enumerate(paths, start=1):
        if not os.path.lexists(path):
            raise UsageError("An input path doesn't exist.", position=position)
        if _location(path).is_relative_to(raw_root):
            raise UsageError("An input path lies inside the raw zone (data/raw/).", position=position)
    if supersedes is not None and ctx.catalog.get(supersedes) is None:
        raise UsageError("--supersedes names no dataset in the catalog.")
    if len(note) > NOTE_MAX or "\n" in note or "\r" in note:
        raise UsageError(f"--note is one line of at most {NOTE_MAX} characters.")
    if source is not None:
        _parser_for(ctx.parsers, source)
    return denylist


def _is_pointer_file(path: str) -> bool:
    """`data/raw/<name>.dvc`, directly under the raw zone."""
    return path.startswith(RAW_PREFIX) and path.endswith(".dvc") and "/" not in path[len(RAW_PREFIX) :]


def _location(path: Path) -> Path:
    """Where a path's own entry lives, its parent resolved; a symlink isn't followed."""
    absolute = Path(os.path.abspath(path))
    return absolute.parent.resolve() / absolute.name


# Plan (§7.2)


def _plan(
    ctx: ServiceContext,
    paths: Sequence[Path],
    denylist: tuple[DenyEntry, ...],
    *,
    source: SourceType | None,
    supersedes: str | None,
    note: str,
    landed_at: datetime,
) -> _Plan:
    plan = _Plan()
    items = _expand(ctx, paths, denylist, plan)
    if not items:
        raise UsageError("No input files.")
    if supersedes is not None and len(items) > 1:
        raise UsageError("--supersedes takes exactly one input file.")
    plan.pending = _read_pending(ctx)
    unclaimed = _unclaimed_keys(ctx)
    for item in items:
        plan.items.append(_plan_item(ctx, item, plan, source=source, supersedes=supersedes, note=note, at=landed_at))
    accepted_keys = {accepted.record.stored_path for accepted in plan.accepted}
    for raw_source in sorted(unclaimed):
        stray = unclaimed[raw_source] - accepted_keys
        if stray:
            raise CatalogCorruptError(
                "The raw zone holds a file the catalog doesn't list; nothing was landed.",
                reason="uncatalogued_file",
                source=raw_source.value,
                count=len(stray),
            )
    return plan


def _expand(ctx: ServiceContext, paths: Sequence[Path], denylist: tuple[DenyEntry, ...], plan: _Plan) -> list[_Item]:
    """Files stay; a directory gives its regular files, not recursively, sorted by name, hidden ones skipped."""
    items: list[_Item] = []

    def add(path: Path, *, symlink: bool = False) -> None:
        items.append(_Item(len(items) + 1, path, _shown(ctx, path, denylist), symlink))

    for argument, path in enumerate(paths, start=1):
        if path.is_symlink():
            add(path, symlink=True)
        elif path.is_dir():
            try:
                entries = sorted(path.iterdir(), key=lambda entry: entry.name)
            except OSError:
                raise UsageError("An input folder can't be listed.", position=argument) from None
            for entry in entries:
                if entry.name.startswith("."):
                    plan.skipped_hidden += 1
                elif entry.is_symlink():
                    add(entry, symlink=True)
                elif entry.is_file():
                    add(entry)
        else:
            add(path)
    return items


def _shown(ctx: ServiceContext, path: Path, denylist: tuple[DenyEntry, ...]) -> str:
    """The input as output shows it: workspace-relative or `<outside>/<name>`, each component masked (§8.2)."""
    location = _location(path)
    root = ctx.paths.root.resolve()
    if location.is_relative_to(root):
        return _mask_path(location.relative_to(root).as_posix(), denylist)
    return f"{OUTSIDE}/{mask_filename(location.name, denylist)}"


def _mask_path(path: str, denylist: Iterable[DenyEntry]) -> str:
    entries = tuple(denylist)
    return "/".join(mask_filename(part, entries) for part in path.split("/"))


def _read_pending(ctx: ServiceContext) -> list[str]:
    path = _pending_path(ctx)
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return []
    except OSError:
        raise StorageError("The pending-land marker can't be read.", location="workspace") from None
    try:
        document = json.loads(data)
        if not isinstance(document, dict):
            raise InvalidInputError("The pending-land marker isn't a JSON object.")
        return LandPending.from_document(document).dataset_ids
    except ValueError, InvalidInputError:  # UnicodeDecodeError and JSONDecodeError are ValueErrors
        raise InvalidInputError(
            "The pending-land marker is corrupt.", kind=LandPending.KIND, hint=PENDING_HINT
        ) from None


def _pending_path(ctx: ServiceContext) -> Path:
    return ctx.paths.root / PENDING_MARKER


def _unclaimed_keys(ctx: ServiceContext) -> dict[SourceType, set[str]]:
    """Each source's stored files that neither are its catalog nor a catalogued `stored_path` (§7.2 step 2)."""
    unclaimed: dict[SourceType, set[str]] = {}
    for raw_source in SourceType:
        prefix = raw_key(raw_source)
        claimed = {f"{prefix}{CATALOG_FILENAME}"} | {record.stored_path for record in ctx.catalog.records(raw_source)}
        unclaimed[raw_source] = set(ctx.blobs.keys(prefix)) - claimed
    return unclaimed


def _plan_item(
    ctx: ServiceContext,
    item: _Item,
    plan: _Plan,
    *,
    source: SourceType | None,
    supersedes: str | None,
    note: str,
    at: datetime,
) -> LandItem:
    """Steps 3.1-3.6 for one item, so every earlier item's outcome is known."""
    base: dict[str, object] = {"position": item.position, "input": item.shown}

    def rejected(error: EnergatronError, *, size: int | None = None, sha: str | None = None) -> LandItem:
        error.details["position"] = item.position
        return LandItem.model_validate(
            {
                **base,
                "bytes": size,
                "sha256": sha,
                "status": "rejected",
                "source": None,
                "dataset_id": None,
                "stored_path": None,
                "coverage": None,
                "error": ItemError.of(error),
            }
        )

    def known(status: LandStatus, record: CatalogRecord, size: int, sha: str) -> LandItem:
        return LandItem.model_validate(
            {
                **base,
                "bytes": size,
                "sha256": sha,
                "status": status,
                "source": record.source,
                "dataset_id": record.dataset_id,
                "stored_path": record.stored_path,
                "coverage": record.coverage,
                "error": None,
            }
        )

    # 3.1 Read
    if item.symlink:
        return rejected(InvalidInputError("Symbolic links aren't landed.", reason="symlink"))
    try:
        st = item.path.stat()
        if not stat.S_ISREG(st.st_mode):
            return rejected(InvalidInputError("The input isn't a regular file.", reason="not_a_file"))
        if st.st_size > MAX_BYTES:
            return rejected(InvalidInputError("The file is over 256 MiB.", reason="too_large"), size=st.st_size)
        data = item.path.read_bytes()
    except OSError:
        return rejected(InvalidInputError("The file can't be read.", reason="unreadable"))
    size, sha = len(data), sha256_hex(data)
    if not data:
        return rejected(InvalidInputError("The file is empty.", reason="empty"), size=size, sha=sha)
    file_mtime = datetime.fromtimestamp(st.st_mtime, UTC).replace(microsecond=0)

    # 3.2 Already landed
    existing = ctx.catalog.find_sha256(sha)
    if existing is not None:
        return known("already_landed", existing, size, sha)
    earlier = next((accepted for accepted in plan.accepted if accepted.record.sha256 == sha), None)
    if earlier is not None:
        return known("duplicate_in_batch", earlier.record, size, sha)

    # 3.3 Detect and inspect
    name = item.path.name
    try:
        parser, evidence = resolve_parser(ctx.parsers, name, data[:HEAD_BYTES], source)
        info = parser.inspect(name, data)
    except (UnknownSourceError, AmbiguousSourceError, InvalidInputError) as error:
        return rejected(error, size=size, sha=sha)

    # 3.4 Premises
    assert ctx.private is not None
    try:
        warnings = check_premises(parser.source, dict(info.identifiers.items()), ctx.private)
    except ForeignPremisesError as error:
        error.details["position"] = item.position
        raise
    except InvalidInputError as error:
        return rejected(error, size=size, sha=sha)

    # 3.5 ID
    new_id = dataset_id(parser.source.value, info.source_ts, sha)
    if ctx.catalog.get(new_id) is not None or any(accepted.record.dataset_id == new_id for accepted in plan.accepted):
        error = IdCollisionError("The file's dataset ID already names other content.", dataset_id=new_id)
        return rejected(error, size=size, sha=sha)
    snapshot = _same_snapshot(ctx, parser.source, info, plan)
    if snapshot is not None:
        return known("same_snapshot", snapshot, size, sha)

    # 3.6 Supersedes
    if supersedes is not None:
        target = ctx.catalog.get(supersedes)
        if target is None or target.source != parser.source:
            raise UsageError("--supersedes names a dataset of another source.")
        if any(record.supersedes == supersedes for record in ctx.catalog.records(parser.source)):
            raise UsageError("--supersedes names a dataset that another one already supersedes.")

    try:
        record = CatalogRecord(
            dataset_id=new_id,
            source=parser.source,
            sha256=sha,
            bytes=size,
            stored_path=blob_key(parser.source, new_id, info.extension),
            original_filename=name,
            landed_at=at,
            file_mtime=file_mtime,
            source_ts=info.source_ts,
            coverage=info.coverage,
            detector=Detector(name=parser.source, version=parser.version, evidence=evidence),
            extra=info.extra,
            supersedes=supersedes,
            notes=note,
        )
    except ValidationError:
        return rejected(InvalidInputError("The file can't be catalogued.", reason="invalid_record"), size=size, sha=sha)
    plan.accepted.append(_Accepted(item.position, record, data))
    plan.warnings.extend(
        warning.model_copy(update={"details": {**warning.details, "position": item.position}}) for warning in warnings
    )
    return known("landed", record, size, sha)


def _same_snapshot(ctx: ServiceContext, source: SourceType, info: SourceInfo, plan: _Plan) -> CatalogRecord | None:
    """For `offer_cdr`: a catalogued or earlier accepted dataset with the same `extra.offer_snapshot_id` (§5.7)."""
    if source != SourceType.OFFER_CDR:
        return None
    snapshot = info.extra.get("offer_snapshot_id")
    if snapshot is None:
        return None
    candidates = [*ctx.catalog.records(source), *(accepted.record for accepted in plan.accepted)]
    return next((record for record in candidates if record.extra.get("offer_snapshot_id") == snapshot), None)


# Write (§7.3)


def _write(ctx: ServiceContext, plan: _Plan, new_ids: list[str]) -> tuple[str | None, list[str]]:
    """Marker, blobs and catalog lines, track, commit, marker cleanup. Returns the commit (or None) and its paths."""
    marker = _pending_path(ctx)
    if new_ids:
        pending = LandPending(dataset_ids=sorted(set(new_ids) | set(plan.pending)))
        _write_marker(marker, pending)
    ordered = sorted(
        plan.accepted,
        key=lambda accepted: write_order_key(
            accepted.record.source, accepted.record.source_ts, accepted.record.file_mtime, accepted.position
        ),
    )
    for accepted in ordered:
        ctx.blobs.put(accepted.record.stored_path, accepted.data)
        ctx.catalog.append(accepted.record)
        _log.info("land: stored %s", accepted.record.dataset_id)
    try:
        ctx.vcs.track([f"{RAW_PREFIX}{raw_source.value}" for raw_source in sorted(SourceType)])
    except SubprocessError as error:
        error.details.setdefault("hint", RERUN_HINT)
        raise
    dirty = ctx.vcs.dirty_paths([RAW_DIR])
    commit: str | None = None
    if dirty:
        ids = sorted(set(new_ids) | {pending_id for pending_id in plan.pending if ctx.catalog.get(pending_id)})
        try:
            commit = ctx.vcs.commit(dirty, commit_message(ids))
        except (CommitRejectedError, DenylistHitError, SubprocessError) as error:
            error.details["hint"] = RERUN_HINT
            raise
    _remove_marker(marker)
    return commit, (list(dirty) if commit is not None else [])


def commit_message(ids: Sequence[str]) -> str:
    """D3 §7.4: one ID inline, several as a sorted body, none as a resync. Nothing else goes in."""
    if not ids:
        return "land: resync raw pointers"
    if len(ids) == 1:
        return f"land: {ids[0]}"
    return f"land: {len(ids)} datasets\n\n" + "\n".join(sorted(ids))


def _write_marker(path: Path, pending: LandPending) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_bytes(path, canonical_json(pending.model_dump(mode="json")) + b"\n")
    except OSError:
        raise StorageError(
            "The pending-land marker couldn't be written.",
            location="workspace",
            hint="check that the workspace's `.energatron/` folder is writable and the disk isn't full",
        ) from None


def _remove_marker(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        raise StorageError("The pending-land marker couldn't be removed.", location="workspace") from None


def _result(plan: _Plan, new_ids: list[str], *, dry_run: bool, commit: str | None, pointers: list[str]) -> LandResult:
    return LandResult(
        dry_run=dry_run,
        items=plan.items,
        landed=new_ids,
        commit=commit,
        pointer_files=pointers,
        skipped_hidden=plan.skipped_hidden,
        warnings=tuple(plan.warnings),
    )
