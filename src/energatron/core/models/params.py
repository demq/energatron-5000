"""`params.yaml`, kind `params` (D3 §10.2).

Version 1 holds no settings: the only P1 stage declares no params. Each phase that adds keys bumps the version and
adds a migration that inserts its defaults. `pipeline sync` rewrites an older file, because DVC reads declared keys
from the file itself; energatron's readers still upgrade it in memory (D1 §5).
"""

from typing import ClassVar, Literal

from energatron.core.persisted import Persisted


class Params(Persisted):
    """The workspace's non-personal pipeline settings. `prepare_stage` validates the whole file through this model."""

    KIND: ClassVar[str] = "params"
    SCHEMA_VERSION: ClassVar[int] = 1
    kind: Literal["params"] = "params"
    schema_version: Literal[1] = 1
