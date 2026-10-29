"""Stage provenance and the code fingerprint (D1 §10, §11).

`StageCode` is one stage's code entry; `CodeFingerprint` holds one per stage (`.energatron/code_fingerprint.json`);
`StageProvenance` is the `_provenance.json` in every stage output directory. `stale_stages()` compares a live
fingerprint with the recorded one (D1 §11.4). Nothing here is run-specific: no timestamps, run IDs or absolute
paths, so a rerun writes the same bytes.
"""

from typing import Annotated, ClassVar, Final, Literal, Self

from pydantic import AfterValidator, Field, model_validator

from energatron.core.canonical import JSONValue, content_hash
from energatron.core.models._common import (
    CommitSha,
    DatasetId,
    FrozenModel,
    RelativePosixPath,
    Sha256Hex,
    StageName,
)
from energatron.core.persisted import Persisted

PROVENANCE_FILENAME: Final = "_provenance.json"
"""The provenance file in every stage output directory (D1 §10)."""

type Install = Literal["editable", "local_dir", "vcs", "archive", "index"]


def _sorted_unique[T: str](values: list[T]) -> list[T]:
    if values != sorted(set(values)):
        raise ValueError("expected sorted, unique values")
    return values


class StageCode(FrozenModel):
    """A stage's code entry (D1 §11.1). `stage_sha256` is `content_hash` of `source_sha256`, `packages` and `python`."""

    stage_sha256: Sha256Hex
    source_sha256: Sha256Hex  # tree_hash over `files`
    python: str = Field(min_length=1)
    packages: dict[str, str]  # distribution → installed version
    files: Annotated[list[RelativePosixPath], AfterValidator(_sorted_unique)]  # package-relative

    @model_validator(mode="after")
    def _stage_hash(self) -> Self:
        expected = content_hash(
            {"source_sha256": self.source_sha256, "packages": dict(self.packages), "python": self.python}
        )
        if self.stage_sha256 != expected:
            raise ValueError("stage_sha256 isn't the content hash of source_sha256, packages and python")
        return self


class CodeFingerprint(Persisted):
    """`.energatron/code_fingerprint.json` (D1 §11). Only `stages.<name>.stage_sha256` triggers anything.

    The recording fields follow D1 §11.2's table: a commit only for an editable or VCS install, and only an
    editable install can be dirty.
    """

    KIND: ClassVar[str] = "code_fingerprint"
    SCHEMA_VERSION: ClassVar[int] = 1
    kind: Literal["code_fingerprint"] = "code_fingerprint"
    schema_version: Literal[1] = 1

    energatron_version: str = Field(min_length=1)
    install: Install
    tool_commit: CommitSha | None
    tool_dirty: bool
    stages: dict[StageName, StageCode]

    @model_validator(mode="after")
    def _install_fields(self) -> Self:
        if self.tool_commit is not None and self.install not in ("editable", "vcs"):
            raise ValueError("only an editable or vcs install records a tool commit")
        if self.install == "vcs" and self.tool_commit is None:
            raise ValueError("a vcs install records its commit")
        if self.tool_dirty and self.install != "editable":
            raise ValueError("only an editable install can be dirty")
        return self


def stale_stages(live: CodeFingerprint, recorded: CodeFingerprint | None) -> list[str]:
    """The stages whose `stage_sha256` differs between `live` and `recorded`, or that only one holds, sorted.

    With no recorded fingerprint, every live stage is stale. The recording fields never count (D1 §11.3, §11.4).
    """
    if recorded is None:
        return sorted(live.stages)
    names = set(live.stages) | set(recorded.stages)
    return sorted(
        name
        for name in names
        if name not in live.stages
        or name not in recorded.stages
        or live.stages[name].stage_sha256 != recorded.stages[name].stage_sha256
    )


class ProvenanceInput(FrozenModel):
    """One input of a stage run (D1 §10). `path` is always the declared default, never an override."""

    name: str = Field(min_length=1)
    type: Literal["file", "dir"]
    path: RelativePosixPath  # workspace-relative
    sha256: Sha256Hex  # the file's hash, or the tree hash of a directory
    dataset_ids: Annotated[list[DatasetId], AfterValidator(_sorted_unique)]


class ProvenanceParams(FrozenModel):
    """The stage's declared `params.yaml` keys in full, and `content_hash` of them."""

    values: dict[str, JSONValue]
    sha256: Sha256Hex

    @model_validator(mode="after")
    def _hash(self) -> Self:
        if self.sha256 != content_hash(dict(self.values)):
            raise ValueError("sha256 isn't the content hash of values")
        return self


class ProvenanceFile(FrozenModel):
    """A file in the output directory. `rows` comes from Parquet metadata, or is null for other files."""

    path: RelativePosixPath  # relative to the output directory
    sha256: Sha256Hex
    bytes: int = Field(ge=0)
    rows: int | None = Field(ge=0)


class CheckResult(FrozenModel):
    """A stage check: it passes or fails. Warnings go to the `StageResult`, not here (D1 §9.2)."""

    name: str = Field(min_length=1)
    status: Literal["pass", "fail"]
    details: dict[str, JSONValue]


class StageProvenance(Persisted):
    """`_provenance.json` in a stage output directory (D1 §10)."""

    KIND: ClassVar[str] = "stage_provenance"
    SCHEMA_VERSION: ClassVar[int] = 1
    kind: Literal["stage_provenance"] = "stage_provenance"
    schema_version: Literal[1] = 1

    stage: StageName
    output: str = Field(min_length=1)
    code: StageCode
    inputs: list[ProvenanceInput]
    params: ProvenanceParams
    files: list[ProvenanceFile]  # sorted by path; never `_provenance.json` itself
    checks: list[CheckResult]

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        names = [item.name for item in self.inputs]
        if len(set(names)) != len(names):
            raise ValueError("an input is listed twice")
        paths = [item.path for item in self.files]
        if paths != sorted(set(paths)):
            raise ValueError("files must be sorted by path, each once")
        if PROVENANCE_FILENAME in paths:
            raise ValueError(f"files never list {PROVENANCE_FILENAME}")
        return self
