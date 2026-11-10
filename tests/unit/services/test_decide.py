"""`services.decide` (P6 spec §9): every refusal, the dry run, ID numbering and the projections copied.

The workspace is a scratch folder holding a synthetic recommendation (the golden `recommendation` v1 document) and run
manifests; `VersionControl` is a fake that records `track` and `commit`.
"""

import json
from collections.abc import Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import pytest

from energatron.adapters import wiring
from energatron.adapters.storage.jsonl_catalog import JsonlCatalog
from energatron.core.errors import EnergatronError, InvalidInputError, UsageError, WarningCode
from energatron.core.models.catalog import SourceType
from energatron.core.models.recommend import Decision
from energatron.core.models.vcs import BackupStatus
from energatron.core.models.workspace import WorkspacePaths
from energatron.services import decide as decide_service
from energatron.services.context import ServiceContext
from tests.fixtures.make_sources import make_pdf
from tests.fixtures.pipeline_workspace import make_record
from tests.unit.adapters._workspace import make_workspace, write
from tests.unit.services.test_context import FakeVcs, build

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "schemas"
REC_ID = "REC-20261101-0123abcd"
ELEC = "SYN-E-1~20261101T000000Z~0123abcd"
RUN_CLEAN = "run-20261102T000000Z-a1b2c3d"
RUN_DRAFT = "run-20261103T000000Z-a1b2c3d"
COMMIT = "c" * 40


class DecideVcs(FakeVcs):
    def __init__(self) -> None:
        self.tracked: list[list[str]] = []
        self.commits: list[tuple[list[str], str]] = []

    def track(self, paths: Sequence[str]) -> list[str]:
        self.tracked.append(list(paths))
        return [f"{path}.dvc" for path in paths]

    def commit(self, paths: Sequence[str], message: str) -> str:
        self.commits.append((list(paths), message))
        return COMMIT

    def backup_status(self) -> BackupStatus:
        return BackupStatus(
            head=None, last_backup_head=None, last_backup_at=None, refs_backed_up=False, commits_not_backed_up=1
        )


class Clock:
    def now(self) -> datetime:
        return datetime(2026, 11, 10, 14, 0, tzinfo=UTC)  # already the 11th in Adelaide


def manifest(run_id: str, *, draft: bool, rec: str | None = REC_ID) -> dict[str, Any]:
    doc = json.loads((FIXTURES / "run_manifest" / "v1.json").read_text(encoding="utf-8"))
    return doc | {"run_id": run_id, "draft": draft, "recommendation_id": rec}


@pytest.fixture
def paths(tmp_path: Path) -> WorkspacePaths:
    paths = make_workspace(tmp_path, private=None)
    write(
        paths.root,
        "data/reports/recommendations/recommendation.json",
        (FIXTURES / "recommendation" / "v1.json").read_text(encoding="utf-8"),
    )
    write(paths.root, "data/decisions/README.md", "Decisions.\n")
    write(paths.root, f"data/runs/{RUN_CLEAN}.json", json.dumps(manifest(RUN_CLEAN, draft=False)))
    write(paths.root, f"data/runs/{RUN_DRAFT}.json", json.dumps(manifest(RUN_DRAFT, draft=True)))
    write(
        paths.root,
        "data/runs/run-20261101T000000Z-a1b2c3d.json",
        json.dumps(manifest("run-20261101T000000Z-a1b2c3d", draft=False, rec=None)),
    )
    catalog = JsonlCatalog(paths)
    for source in SourceType:
        catalog.initialise(source)
    return paths


@pytest.fixture
def vcs() -> DecideVcs:
    return DecideVcs()


@pytest.fixture
def ctx(paths: WorkspacePaths, vcs: DecideVcs) -> ServiceContext:
    return build(
        paths=paths, catalog=JsonlCatalog(paths), vcs=vcs, clock=Clock(), region=wiring.load_region_profile("sa")
    )


def decide(ctx: ServiceContext, **changes: Any) -> decide_service.DecideResult:
    arguments: dict[str, Any] = {"switch_date": date(2026, 12, 1), "electricity": ELEC} | changes
    rec = arguments.pop("rec_id", REC_ID)
    return decide_service.decide(ctx, rec, **arguments)


def test_a_decision_is_written_tracked_and_committed_with_its_projection(
    ctx: ServiceContext, vcs: DecideVcs, paths: WorkspacePaths
) -> None:
    result = decide(ctx, note="moving house")
    assert (result.decision_id, result.run_id, result.commit, result.dry_run) == (
        "DEC-20261111-01",
        RUN_CLEAN,
        COMMIT,
        False,
    )
    assert result.path == "data/decisions/DEC-20261111-01.json"
    assert vcs.tracked == [["data/decisions"]]
    assert vcs.commits == [(["data/decisions.dvc"], f"decide: DEC-20261111-01 {REC_ID}")]
    found = Decision.from_document(json.loads((paths.root / result.path).read_bytes()))
    choice = found.choices["electricity"]
    assert choice is not None
    assert (choice.offer_snapshot_id, choice.annual_ongoing, choice.annual_year1) == (
        ELEC,
        Decimal("1000.00"),
        Decimal("950.00"),
    )
    assert (found.choices["gas"], found.switch_date, found.note) == (None, date(2026, 12, 1), "moving house")
    assert [warning.code for warning in result.warnings] == [WarningCode.W_BACKUP_PENDING]


def test_a_second_decision_that_day_gets_the_next_number(ctx: ServiceContext) -> None:
    decide(ctx)
    assert decide(ctx).decision_id == "DEC-20261111-02"


def test_a_dry_run_writes_and_commits_nothing(ctx: ServiceContext, vcs: DecideVcs, paths: WorkspacePaths) -> None:
    result = decide(ctx, dry_run=True)
    assert (result.commit, result.dry_run) == (None, True)
    assert (vcs.tracked, vcs.commits) == ([], [])
    assert not (paths.root / result.path).exists()


@pytest.mark.parametrize(
    ("changes", "error", "detail"),
    [
        ({"rec_id": "REC-1"}, UsageError, ("field", "rec_id")),
        ({"electricity": None}, UsageError, ("field", "choices")),
        ({"rec_id": "REC-20261101-ffffffff"}, InvalidInputError, ("reason", "not_current")),
        ({"electricity": "SYN-E-9~20261101T000000Z~0123abcd"}, InvalidInputError, ("field", "electricity")),
        ({"gas": ELEC}, InvalidInputError, ("field", "gas")),
        ({"evidence": ("gas_bill-20261001T000000-0123abcd",)}, InvalidInputError, ("field", "evidence")),
    ],
)
def test_refusals(
    ctx: ServiceContext, changes: dict[str, Any], error: type[Exception], detail: tuple[str, str]
) -> None:
    with pytest.raises(error) as caught:
        decide(ctx, **changes)
    assert cast(EnergatronError, caught.value).details[detail[0]] == detail[1]


def test_a_recommendation_only_a_draft_run_made_is_refused(ctx: ServiceContext, paths: WorkspacePaths) -> None:
    (paths.root / f"data/runs/{RUN_CLEAN}.json").unlink()
    with pytest.raises(InvalidInputError) as caught:
        decide(ctx)
    assert caught.value.details["reason"] == "not_in_clean_run"


def test_landed_evidence_is_kept(ctx: ServiceContext, paths: WorkspacePaths) -> None:
    record = make_record(SourceType.GAS_BILL, "bill.pdf", make_pdf(creation_date="D:20261005120000+10'30'"))
    JsonlCatalog(paths).append(record)
    result = decide(ctx, evidence=(record.dataset_id,))
    found = Decision.from_document(json.loads((paths.root / result.path).read_bytes()))
    assert found.evidence == [record.dataset_id]
