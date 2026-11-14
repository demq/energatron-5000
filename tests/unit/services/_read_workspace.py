"""A scratch workspace for the P9 read commands (P9 spec §3): catalogs, marts, a recommendation, runs, outcomes.

Everything is synthetic: the recommendation and summaries start from the golden documents in
`tests/fixtures/schemas/`, the tables are written with the stages' own schemas, and the landed files are the fixture
generators'. `make_read_workspace` builds the whole of it; tests delete or break parts.
"""

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd

from energatron.adapters import wiring
from energatron.adapters.storage.fs_blobs import FsBlobStore
from energatron.adapters.storage.jsonl_catalog import JsonlCatalog
from energatron.core.models import gas as gas_models
from energatron.core.models import usage as usage_models
from energatron.core.models.catalog import SourceType
from energatron.core.models.workspace import WorkspacePaths
from energatron.services.context import ServiceContext
from energatron.services.files import write_parquet
from energatron.services.stages.tables import GAS_MONTH_SCHEMA, USAGE_MONTHLY_SCHEMA, USAGE_TOU_MONTHLY_SCHEMA
from tests.fixtures import make_cdr as cdr
from tests.fixtures.make_sources import make_pdf
from tests.fixtures.pipeline_workspace import Dataset, land_blob
from tests.unit.adapters._workspace import make_workspace, write
from tests.unit.services.test_context import build

SCHEMAS = Path(__file__).resolve().parents[2] / "fixtures" / "schemas"
REC_ID = "REC-20261101-0123abcd"
RUN_ID = "run-20261102T000000Z-a1b2c3d"
OTHER_RUN = "run-20261101T000000Z-a1b2c3d"
BILL = make_pdf(creation_date="D:20261005120000+10'30'")


class Clock:
    def now(self) -> datetime:
        return datetime(2026, 11, 10, 14, 0, tzinfo=UTC)


def golden(kind: str) -> dict[str, Any]:
    return json.loads((SCHEMAS / kind / "v1.json").read_text(encoding="utf-8"))


def plan(ident: str, brand: str, name: str, rank: int, ongoing: str) -> dict[str, Any]:
    return {
        "offer_snapshot_id": f"{ident}~20261101T000000Z~0123abcd", "plan_id": ident, "brand": brand,
        "display_name": name, "source": "cdr", "status": "normalised", "approximations": [], "notes": [],
        "annual_ongoing": ongoing, "annual_year1": ongoing, "rank": rank,
    }  # fmt: skip


def recommendation() -> dict[str, Any]:
    """The golden recommendation: three electricity plans (the current one ranked 3rd), one gas plan, two options."""
    doc = golden("recommendation")
    first = plan("SYN-E-1", "Synthetic", "Synthetic Saver", 1, "1000.00")
    second = plan("SYN-E-2", "Other Brand", "Other Flex", 2, "1100.00")
    now = plan("SYN-E-NOW", "Synthetic", "Synthetic Now", 3, "1200.00")
    gas = plan("SYN-G-1", "Other Brand", "Other Gas", 1, "500.00") | {"notes": ["pay on time"]}
    doc["priced"] = {"electricity": [first, second, now], "gas": [gas]}
    option = doc["options"][0]
    doc["options"] = [
        option,
        option | {"rank": 2, "option_key": "SYN-E-2~20261101T000000Z~0123abcd|-", "electricity": second},
    ]
    return doc


def manifest(run_id: str, *, recommendation_id: str | None, datasets: list[str]) -> dict[str, Any]:
    return golden("run_manifest") | {"run_id": run_id, "recommendation_id": recommendation_id, "datasets": datasets}


def write_usage_marts(root: Path) -> None:
    out = root / "data/marts/usage_profile"
    out.mkdir(parents=True, exist_ok=True)
    write(root, "data/marts/usage_profile/summary.json", json.dumps(golden("usage_summary")))
    months = [f"2025-{month:02d}" for month in range(1, 13)] + ["2026-01"]
    monthly = pd.DataFrame(
        [
            {
                "suffix": "E1", "month": month, "days_present": 30, "days_in_month": 30, "complete_days": 30,
                "covered": True, "coverage": 1.0, "kwh": 10.0 + index, "avg_daily_kwh": float("nan"),
                "base_load_kw": 0.25, "peak_demand_kw": 2.0, "peak_demand_at": pd.Timestamp("2025-01-01 18:00"),
                "load_factor": 0.125,
            }
            for index, month in enumerate(months)
        ]
    )  # fmt: skip
    write_parquet(
        monthly, out / "monthly.parquet", kind=usage_models.MONTHLY_KIND, schema_version=1,
        sort_by=usage_models.MONTHLY_SORT, schema=USAGE_MONTHLY_SCHEMA,
    )  # fmt: skip
    tou = pd.DataFrame(
        [
            {"suffix": "E1", "month": month, "label": label, "kwh": 1.0, "share": 0.5, "avg_daily_kwh": 0.1,
             "mixed_windows": False}
            for month in months
            for label in ("Peak", "Off Peak")
        ]
    )  # fmt: skip
    write_parquet(
        tou, out / "tou_monthly.parquet", kind=usage_models.TOU_MONTHLY_KIND, schema_version=1,
        sort_by=usage_models.TOU_MONTHLY_SORT, schema=USAGE_TOU_MONTHLY_SCHEMA,
    )  # fmt: skip


def write_gas_marts(root: Path) -> None:
    out = root / "data/marts/gas_profile"
    out.mkdir(parents=True, exist_ok=True)
    write(root, "data/marts/gas_profile/summary.json", json.dumps(golden("gas_profile_summary")))
    rows = pd.DataFrame(
        [
            {"month": "2026-08", "days_covered": 31, "mj": Decimal("1000.000000"), "mj_per_day": Decimal("32.258065")},
            {"month": "2026-09", "days_covered": 30, "mj": Decimal("1000.000000"), "mj_per_day": Decimal("33.333333")},
        ]
    )
    write_parquet(
        rows, out / gas_models.GAS_MONTHLY_FILENAME, kind=gas_models.GAS_MONTH_KIND, schema_version=1,
        sort_by=gas_models.GAS_MONTH_SORT, schema=GAS_MONTH_SCHEMA,
    )  # fmt: skip


def make_read_workspace(tmp_path: Path) -> tuple[WorkspacePaths, list[Dataset]]:
    paths = make_workspace(tmp_path, private=None)
    catalog = JsonlCatalog(paths)
    for source in SourceType:
        catalog.initialise(source)
    log = cdr.fetch_log(batch="fetch-20261101T000000Z", fetched_at="2026-11-01T00:00:00Z", retailers=[], selected=[])
    landed = [
        land_blob(
            paths.root, SourceType.OFFER_CDR_INDEX, "fetch-log.json", cdr.fetch_log_body(log),
            landed_at=datetime(2026, 11, 1, 1, 0, tzinfo=UTC),
        ),
        land_blob(paths.root, SourceType.GAS_BILL, "Private Name bill.pdf", BILL,
                  landed_at=datetime(2026, 11, 2, 1, 0, tzinfo=UTC)),
    ]  # fmt: skip
    write(paths.root, "data/reports/recommendations/recommendation.json", json.dumps(recommendation()))
    write(
        paths.root, f"data/runs/{OTHER_RUN}.json",
        json.dumps(manifest(OTHER_RUN, recommendation_id="REC-20261031-0123abcd", datasets=[])),
    )  # fmt: skip
    write(
        paths.root, f"data/runs/{RUN_ID}.json",
        json.dumps(manifest(RUN_ID, recommendation_id=REC_ID, datasets=[landed[0].dataset_id])),
    )  # fmt: skip
    write(paths.root, "data/reports/outcomes/summary.json", json.dumps(golden("outcomes_summary")))
    write_usage_marts(paths.root)
    write_gas_marts(paths.root)
    return paths, landed


def context(paths: WorkspacePaths, **changes: Any) -> ServiceContext:
    arguments: dict[str, Any] = {
        "paths": paths,
        "catalog": JsonlCatalog(paths),
        "blobs": FsBlobStore(paths),
        "clock": Clock(),
        "region": wiring.load_region_profile("sa"),
    } | changes
    return build(**arguments)
