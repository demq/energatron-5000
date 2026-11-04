"""The `usage_report` stage (P3 spec §9): the nine usage marts turned into one self-contained HTML page.

It reads the `usage_profile` directory and nothing else: no params, no other table. Each Parquet file is checked
against its kind, version and columns, and `summary.json` against `usage_summary` v1; anything else is
`E_INVALID_INPUT`, naming the input and the file, never a value. The page itself is `core.usage_report.render`, a pure
function, so the same marts always give the same bytes.
"""

from pathlib import Path
from typing import Final

import pandas as pd

from energatron.core.errors import InvalidInputError
from energatron.core.models.usage import UsageSummary
from energatron.core.usage_report import UsageMarts, render
from energatron.services.files import atomic_write_text, read_json
from energatron.services.stages.runner import StageRun, StageSpec
from energatron.services.stages.tables import SUMMARY_FILENAME, USAGE_TABLES, load_table

REPORT_FILENAME: Final = "index.html"


def _run(run: StageRun) -> None:
    marts = _load(run.input("profile"))
    atomic_write_text(run.output("report") / REPORT_FILENAME, render(marts))


def _load(directory: Path) -> UsageMarts:
    tables: dict[str, pd.DataFrame] = {
        table.stem: load_table(
            directory / f"{table.stem}.parquet", table.schema, table.kind, table.version, input="profile"
        ).to_pandas()
        for table in USAGE_TABLES
    }
    return UsageMarts(tables, _load_summary(directory / SUMMARY_FILENAME))


def _load_summary(path: Path) -> UsageSummary:
    """The summary; one that is missing, isn't a JSON object, or isn't a `usage_summary` v1 → `E_INVALID_INPUT`."""
    try:
        doc = read_json(path)
    except OSError, ValueError:
        raise InvalidInputError("The summary is missing or isn't JSON.", input="profile", file=path.name) from None
    if not isinstance(doc, dict) or doc.get("schema_version") != UsageSummary.SCHEMA_VERSION:
        raise InvalidInputError(
            f"The summary isn't a {UsageSummary.KIND} v{UsageSummary.SCHEMA_VERSION} document.",
            input="profile",
            file=path.name,
        )
    try:
        return UsageSummary.from_document(doc)
    except InvalidInputError as error:
        raise InvalidInputError(str(error), input="profile", file=path.name) from None


SPEC: Final = StageSpec(
    name="usage_report",
    inputs={"profile": "data/marts/usage_profile"},
    params=(),
    outputs={"report": "data/reports/usage"},
    impl=_run,
)
