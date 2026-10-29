"""What the `BlobStore` port returns (D1 §4.2). Created in P0 because the port names it."""

from pydantic import Field

from energatron.core.models._common import FrozenModel, Sha256Hex


class StoredBlob(FrozenModel):
    """A stored raw file: its workspace-relative key, the sha256 of its exact bytes, and its size."""

    key: str
    sha256: Sha256Hex
    bytes: int = Field(ge=0)
