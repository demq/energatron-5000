"""What the `BlobStore` port returns, and the rules for its keys (D1 §4.2). Created in P0; P1.2.1 adds the key rules."""

from typing import Annotated, Final

from pydantic import AfterValidator, Field

from energatron.core.errors import InternalError
from energatron.core.models._common import FrozenModel, Sha256Hex

RAW_PREFIX: Final = "data/raw/"
"""Every blob key lies under this workspace-relative directory."""


def is_blob_key(key: str, *, prefix: bool = False) -> bool:
    """Whether `key` follows D1 §4.2: a workspace-relative POSIX path under `data/raw/`.

    No leading `/`, no backslash, no empty, `.` or `..` component. A file key names something below `data/raw/` and
    doesn't end with `/`; a prefix (`prefix=True`) is `data/raw/` or a directory below it, and ends with `/`.
    """
    if not key.startswith(RAW_PREFIX) or "\\" in key:
        return False
    if prefix:
        if not key.endswith("/"):
            return False
        parts = key[len(RAW_PREFIX) : -1].split("/") if key != RAW_PREFIX else []
    else:
        parts = key[len(RAW_PREFIX) :].split("/")
    return all(part not in ("", ".", "..") for part in parts)


def check_blob_key(key: str, *, prefix: bool = False) -> str:
    """`key` when `is_blob_key` accepts it. Otherwise `E_INTERNAL`: callers build keys, so a bad one is a bug."""
    if not is_blob_key(key, prefix=prefix):
        what = "key prefix" if prefix else "key"
        raise InternalError(f"Invalid blob {what}: expected a workspace-relative path under {RAW_PREFIX}.")
    return key


def _blob_key(value: str) -> str:
    if not is_blob_key(value):
        raise ValueError(f"expected a workspace-relative path under {RAW_PREFIX}")
    return value


BlobKey = Annotated[str, AfterValidator(_blob_key)]
"""A blob key (D1 §4.2)."""


class StoredBlob(FrozenModel):
    """A stored raw file: its workspace-relative key, the sha256 of its exact bytes, and its size."""

    key: BlobKey
    sha256: Sha256Hex
    bytes: int = Field(ge=0)
