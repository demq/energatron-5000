"""The error model: `EnergatronError`, its codes and categories, and the warning model (D1 §7).

Each error code is a subclass of `EnergatronError`, defined here and named after its code (`E_NOT_A_WORKSPACE` is
`NotAWorkspaceError`). Its category decides the exit code. Warnings are returned in results as `ResultWarning`
values, never raised.

Messages and details never hold identifiers, personal values or original filenames (SAPN filenames start with
the NMI). Refer to an input by its position or by a redacted name instead.
"""

from collections.abc import Mapping
from enum import StrEnum, unique
from types import MappingProxyType
from typing import ClassVar, Final

from pydantic import BaseModel, ConfigDict, Field

from energatron.core.canonical import JSONValue


class Category(StrEnum):
    """What kind of failure an error is. The category decides the exit code."""

    INTERNAL = "internal"
    USAGE = "usage"
    INPUT = "input"
    STATE = "state"
    PRIVACY = "privacy"
    EXTERNAL = "external"
    CHECK = "check"
    PARTIAL = "partial"


# Success, including a no-op such as "already landed", exits 0.
EXIT_CODES: Final[Mapping[Category, int]] = MappingProxyType(
    {
        Category.INTERNAL: 1,  # a bug
        Category.USAGE: 2,  # bad arguments, not in a workspace, confirmation needed
        Category.INPUT: 3,  # an input file was rejected
        Category.STATE: 4,  # the workspace state blocks the action
        Category.PRIVACY: 5,  # a privacy rule blocked the action
        Category.EXTERNAL: 6,  # a subprocess, network or backup-storage failure
        Category.CHECK: 7,  # a stage's quality check failed
        Category.PARTIAL: 8,  # a multi-item command where some items failed
    }
)


class EnergatronError(Exception):
    """Base of every error energatron raises on purpose. Raise a subclass: each one is a code.

    `message` is one human-readable sentence; `details` are JSON values for the CLI's error envelope.
    """

    code: ClassVar[str]
    category: ClassVar[Category]

    def __init__(self, message: str, **details: JSONValue) -> None:
        if not hasattr(type(self), "code"):
            raise TypeError(f"{type(self).__name__} has no code: raise one of its subclasses")
        super().__init__(message)
        self.message = message
        self.details: dict[str, JSONValue] = details

    @property
    def exit_code(self) -> int:
        return EXIT_CODES[self.category]


# internal (exit 1)


class InternalError(EnergatronError):
    """A bug. The CLI reports any exception that isn't an `EnergatronError` the same way."""

    code = "E_INTERNAL"
    category = Category.INTERNAL


# usage (exit 2)


class UsageError(EnergatronError):
    """Bad arguments or options."""

    code = "E_USAGE"
    category = Category.USAGE


class NotAWorkspaceError(EnergatronError):
    """No `.energatron/workspace.toml` in the given directory, or in the current one or above (D1 §8.3)."""

    code = "E_NOT_A_WORKSPACE"
    category = Category.USAGE


class ConfirmationRequiredError(EnergatronError):
    """The command needs confirmation but can't prompt; `--yes` gives it (D1 §8.6)."""

    code = "E_CONFIRMATION_REQUIRED"
    category = Category.USAGE


# input (exit 3)


class UnknownSourceError(EnergatronError):
    """No parser recognises an input file."""

    code = "E_UNKNOWN_SOURCE"
    category = Category.INPUT


class AmbiguousSourceError(EnergatronError):
    """More than one parser claims an input file."""

    code = "E_AMBIGUOUS_SOURCE"
    category = Category.INPUT


class InvalidInputError(EnergatronError):
    """An input file or document is malformed or fails validation."""

    code = "E_INVALID_INPUT"
    category = Category.INPUT


# state (exit 4)


class LockedError(EnergatronError):
    """Another command holds the workspace lock (D1 §8.8)."""

    code = "E_LOCKED"
    category = Category.STATE


class DirtyError(EnergatronError):
    """Uncommitted changes block the action."""

    code = "E_DIRTY"
    category = Category.STATE


class IdCollisionError(EnergatronError):
    """A computed ID already exists for different content. IDs are never lengthened (D1 §6.3)."""

    code = "E_ID_COLLISION"
    category = Category.STATE


class BlobExistsError(EnergatronError):
    """A raw blob already exists under this key with different bytes (D1 §4.2)."""

    code = "E_BLOB_EXISTS"
    category = Category.STATE


class CatalogCorruptError(EnergatronError):
    """A catalog file is malformed. It is never repaired automatically (D1 §4.1)."""

    code = "E_CATALOG_CORRUPT"
    category = Category.STATE


class SchemaTooNewError(EnergatronError):
    """A document was written by a newer energatron; upgrade the tool (D1 §5)."""

    code = "E_SCHEMA_TOO_NEW"
    category = Category.STATE


class FingerprintStaleError(EnergatronError):
    """A stage's recorded code entry differs from its live one; run `energatron fingerprint` (D1 §9.3)."""

    code = "E_FINGERPRINT_STALE"
    category = Category.STATE


class PipelineStaleError(EnergatronError):
    """The pipeline isn't up to date with its inputs, params or code (D1 §11.4)."""

    code = "E_PIPELINE_STALE"
    category = Category.STATE


class PrivateConfigMissingError(EnergatronError):
    """The workspace has no `private/config.toml`."""

    code = "E_PRIVATE_CONFIG_MISSING"
    category = Category.STATE


class CommitRejectedError(EnergatronError):
    """A commit hook rejected the commit, other than a privacy-guard hit (D1 §4.4, D2 §8.6)."""

    code = "E_COMMIT_REJECTED"
    category = Category.STATE


class BackupNotConfiguredError(EnergatronError):
    """The backup bundle directory is missing, or isn't an absolute path outside the workspace (D2 §6.2)."""

    code = "E_BACKUP_NOT_CONFIGURED"
    category = Category.STATE


class DenylistStaleError(EnergatronError):
    """The generated denylist is missing or doesn't match `private/config.toml` (D2 §7.2)."""

    code = "E_DENYLIST_STALE"
    category = Category.STATE


class PipelineNotSyncedError(EnergatronError):
    """`dvc.yaml`, `params.yaml` or the catalogs don't match the tool; run `energatron pipeline sync` (D3 §10.1)."""

    code = "E_PIPELINE_NOT_SYNCED"
    category = Category.STATE


# privacy (exit 5)


class ForeignPremisesError(EnergatronError):
    """An input file belongs to premises other than the workspace's."""

    code = "E_FOREIGN_PREMISES"
    category = Category.PRIVACY


class DenylistHitError(EnergatronError):
    """The privacy guard found a denylisted value in what was about to be committed (D1 §4.4)."""

    code = "E_DENYLIST_HIT"
    category = Category.PRIVACY


# external (exit 6)


class SubprocessError(EnergatronError):
    """A subprocess such as git or DVC failed."""

    code = "E_SUBPROCESS"
    category = Category.EXTERNAL


class NetworkError(EnergatronError):
    """A network request failed."""

    code = "E_NETWORK"
    category = Category.EXTERNAL


class StorageError(EnergatronError):
    """The backup storage failed or can't be reached."""

    code = "E_STORAGE"
    category = Category.EXTERNAL


class StageFailedError(EnergatronError):
    """A pipeline stage failed, other than by a failed check."""

    code = "E_STAGE_FAILED"
    category = Category.EXTERNAL


# check (exit 7)


class CheckFailedError(EnergatronError):
    """A stage's quality check failed. Its outputs are on disk to read (D1 §9.3)."""

    code = "E_CHECK_FAILED"
    category = Category.CHECK


# partial (exit 8)


class PartialError(EnergatronError):
    """Some items of a multi-item command failed; the per-item results are in the output."""

    code = "E_PARTIAL"
    category = Category.PARTIAL


# Warnings


@unique
class WarningCode(StrEnum):
    """Every warning code. Warnings are returned in results, never raised."""

    W_PRIVATE_FIELDS_MISSING = "W_PRIVATE_FIELDS_MISSING"  # optional private fields are absent (D2 §7.2)
    W_BACKUP_DATALESS = "W_BACKUP_DATALESS"  # backed-up files are iCloud placeholders (D2 §8.1)
    W_FINGERPRINT_METADATA = "W_FINGERPRINT_METADATA"  # only the fingerprint's recording fields differ (D1 §11.3)
    W_BACKUP_PENDING = "W_BACKUP_PENDING"  # a commit was made that no backup holds yet (D1 §4.4, D3 §11)
    W_PREMISES_UNCHECKED = "W_PREMISES_UNCHECKED"  # nothing in the file could confirm the premises (D3 §6)
    W_UNKNOWN_IDENTIFIER = "W_UNKNOWN_IDENTIFIER"  # a meter serial or account not in the private config (D3 §6)
    W_MERGE_SAME_UPDATE = "W_MERGE_SAME_UPDATE"  # days that differ with equal UpdateDateTime (P2 spec §7.2)
    W_METER_GAPS = "W_METER_GAPS"  # missing meter dates (P2 spec §8)
    W_METER_UNVALIDATED = "W_METER_UNVALIDATED"  # no SUMMARY row covers the merged data (P2 spec §8)
    W_TIMEBASE_UNCONFIRMED = "W_TIMEBASE_UNCONFIRMED"  # too little evidence, or the configured basis tied (P2 §9.4)
    W_TIMEBASE_UNEXPLAINED = "W_TIMEBASE_UNEXPLAINED"  # periods no time-basis hypothesis fits (P2 spec §9.4)


class ResultWarning(BaseModel):
    """A warning returned in a result: `{"code", "message", "details"}` (D1 §7).

    Not named `Warning`, which would shadow the Python builtin (D3 §6). NaN and infinities are refused, as
    `canonical_json` can't write them (D1 §6.1).
    """

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    code: WarningCode
    message: str
    details: dict[str, JSONValue] = Field(default_factory=dict)
