"""The tools `energatron mcp` lists (P9 spec §6): one table, each tool a CLI command line built from its arguments.

A tool's input model (pydantic, extra fields refused) is both its JSON Schema in `tools/list` and the validation of a
call's arguments. `argv(arguments)` gives the command's words: options first, then `--`, then positional values, so a
value can never be read as an option; the runner adds `--json` and `--workspace` before the `--`. Write tools commit
(their commands do); none backs up, clears the inbox, schedules, upgrades or migrates (§6.2).
"""

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Annotated, Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field

READ_TIMEOUT: Final = 120.0

_DATASET_ID: Final = r"^[a-z_]+-[0-9]{8}T[0-9]{6}-[0-9a-f]{8}$"
_REC_ID: Final = r"^REC-[0-9]{8}-[0-9a-f]{8}$"
_SNAPSHOT: Final = r"^[^\s~]+~[0-9]{8}T[0-9]{6}Z~[0-9a-f]{8}$"
_EXTRACTED_BY: Final = r"^(manual|agent:[A-Za-z0-9][A-Za-z0-9._:/-]{0,79})$"

DatasetId = Annotated[str, Field(pattern=_DATASET_ID, description="A landed dataset ID.")]


class Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


class NoArguments(Arguments):
    pass


class ListDatasets(Arguments):
    source: Annotated[str | None, Field(pattern=r"^[a-z_]+$", description="Only this source type.")] = None
    limit: Annotated[int | None, Field(ge=1, le=1000, description="How many, newest first (default 50).")] = None


class UsageSummary(Arguments):
    months: Annotated[int | None, Field(ge=1, le=60, description="Newest months per list (default 13).")] = None


class CompareOffers(Arguments):
    fuel: Literal["electricity", "gas"] | None = None
    limit: Annotated[int | None, Field(ge=1, le=200, description="Options and plans per list (default 10).")] = None
    brand: Annotated[str | None, Field(min_length=1, max_length=80, description="Brands containing this text.")] = None


class ExplainRecommendation(Arguments):
    recommendation_id: Annotated[str | None, Field(pattern=_REC_ID, description="Must be the current one.")] = None
    options: Annotated[int | None, Field(ge=1, le=20, description="Options to show (default 3).")] = None


class OffersDiff(Arguments):
    plan_id: Annotated[str | None, Field(min_length=1, max_length=200, pattern=r"^[^\s]+$")] = None


class GasBillRecordArgument(Arguments):
    record: Annotated[
        dict[str, Any], Field(description="A gas_bill_record document (kind, schema_version 2, every value a string).")
    ]


class WriteGasBillRecord(GasBillRecordArgument):
    force: bool = False
    dry_run: bool = False


class LandFile(Arguments):
    path: Annotated[
        str,
        Field(min_length=1, max_length=500, description="A file or folder inside data/landing/, workspace-relative."),
    ]
    source: Annotated[
        str | None, Field(pattern=r"^[a-z_]+$", description="The source type, when detection can't tell.")
    ] = None
    supersedes: DatasetId | None = None
    dry_run: bool = False


class FetchOffers(Arguments):
    dry_run: bool = False


class RunPipeline(Arguments):
    draft: Annotated[bool, Field(description="Only with the owner's agreement: a draft can't be decided on.")] = False


class RecordDecision(Arguments):
    recommendation_id: Annotated[str, Field(pattern=_REC_ID)]
    switch_date: date
    elec: Annotated[str | None, Field(pattern=_SNAPSHOT, description="The chosen electricity offer snapshot ID.")] = (
        None
    )
    gas: Annotated[str | None, Field(pattern=_SNAPSHOT, description="The chosen gas offer snapshot ID.")] = None
    evidence: Annotated[list[DatasetId], Field(max_length=20)] = []
    note: Annotated[str | None, Field(min_length=1, max_length=200, pattern=r"^[^\n\r]+$")] = None
    dry_run: Annotated[bool, Field(description="Dry-run first and show the owner.")] = True


class NewGasBillRecord(Arguments):
    pdf_dataset_id: DatasetId
    extracted_by: Annotated[
        str | None, Field(pattern=_EXTRACTED_BY, description="manual, or agent:<your model id>.")
    ] = None
    force: bool = False


class ReadGasBillText(Arguments):
    pdf_dataset_id: DatasetId


@dataclass(frozen=True)
class Tool:
    name: str
    title: str
    description: str
    model: type[Arguments]
    argv: Callable[[Any], tuple[list[str], list[str]]]  # (command and options, positional values)
    read_only: bool = True
    idempotent: bool = True
    open_world: bool = False
    timeout: float = READ_TIMEOUT
    stdin: Callable[[Any], bytes] | None = None
    documents: bool = False  # listed only with --allow-documents

    def listing(self) -> dict[str, Any]:
        """The tool as `tools/list` gives it."""
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "inputSchema": input_schema(self.model),
            "annotations": {
                "title": self.title,
                "readOnlyHint": self.read_only,
                "destructiveHint": False,
                "idempotentHint": self.idempotent,
                "openWorldHint": self.open_world,
            },
        }


def input_schema(model: type[Arguments]) -> dict[str, Any]:
    schema = model.model_json_schema()
    schema.setdefault("properties", {})
    schema["additionalProperties"] = False
    return schema


def _opt(flag: str, value: object) -> list[str]:
    return [] if value is None else [flag, str(value)]


def _flag(flag: str, on: bool) -> list[str]:
    return [flag] if on else []


def _record_json(arguments: GasBillRecordArgument) -> bytes:
    return json.dumps(arguments.record, ensure_ascii=False).encode("utf-8")


FIGURES: Final = " Figures (kWh, $) are the workspace's own aggregates; they go to the model provider like any result."

TOOLS: Final[tuple[Tool, ...]] = (
    Tool(
        "status", "Workspace status",
        "Where the workspace stands: pipeline, last run, recommendation, offers, inbox, decisions, backup and drill, "
        "with reminders. Start every check-in here; backups, the drill and clearing the inbox are the owner's.",
        NoArguments, lambda a: (["status"], []),
    ),
    Tool(
        "list_datasets", "Landed datasets",
        "The landed datasets (IDs, source types, dates, coverage, who extracted them), newest first, with counts per "
        "source type. No file names or notes.",
        ListDatasets, lambda a: (["datasets", *_opt("--source", a.source), *_opt("--limit", a.limit)], []),
    ),
    Tool(
        "list_inbox", "Inbox files",
        "The files waiting in data/landing/ and whether each is already landed.",
        NoArguments, lambda a: (["landing", "list"], []),
    ),
    Tool(
        "usage_summary", "Usage summary",
        "Electricity and gas usage aggregates and monthly totals (no interval rows)." + FIGURES,
        UsageSummary, lambda a: (["usage", *_opt("--months", a.months)], []),
    ),
    Tool(
        "compare_offers", "Compare offers",
        "The current recommendation's ranked options and plans per fuel, with the current plan's rank." + FIGURES,
        CompareOffers,
        lambda a: (["compare", *_opt("--fuel", a.fuel), *_opt("--limit", a.limit), *_opt("--brand", a.brand)], []),
    ),
    Tool(
        "explain_recommendation", "Explain the recommendation",
        "What the current recommendation says, how robust it is, what to check before switching, the run it came "
        "from and how far the workspace has moved since." + FIGURES,
        ExplainRecommendation,
        lambda a: (["explain", *_opt("--options", a.options)], [a.recommendation_id] if a.recommendation_id else []),
    ),
    Tool(
        "offers_diff", "Offer changes",
        "What changed between each plan's two newest landed offer snapshots.",
        OffersDiff, lambda a: (["offers", "diff", *_opt("--plan", a.plan_id)], []),
    ),
    Tool(
        "check_gas_bill_record", "Check a gas bill record",
        "Recompute a gas_bill_record from its own reads and lines, as the pipeline will; writes nothing. A failing "
        "check means a transcription error: fix it against the bill.",
        GasBillRecordArgument, lambda a: (["gas-bill", "check"], ["-"]), stdin=_record_json,
    ),
    Tool(
        "land_file", "Land a file",
        "Land a file or folder from data/landing/ into the raw zone (one commit). Dry-run first. A gas bill PDF needs "
        "source gas_bill.",
        LandFile,
        lambda a: (
            ["land", *_opt("--source", a.source), *_opt("--supersedes", a.supersedes), *_flag("--dry-run", a.dry_run)],
            [a.path],
        ),
        read_only=False, timeout=600.0,
    ),
    Tool(
        "fetch_offers", "Fetch offers",
        "Fetch current retail offers from the public CDR API and land the changed plans (one commit).",
        FetchOffers, lambda a: (["fetch-offers", *_flag("--dry-run", a.dry_run)], []),
        read_only=False, open_world=True, timeout=1800.0,
    ),
    Tool(
        "run_pipeline", "Run the pipeline",
        "Run the pipeline to a recommendation and commit the run. Refuses uncommitted config or tool changes; a draft "
        "only with the owner's agreement. Can take minutes.",
        RunPipeline, lambda a: (["run", *_flag("--draft", a.draft)], []),
        read_only=False, idempotent=False, timeout=3600.0,
    ),
    Tool(
        "record_decision", "Record a decision",
        "Record the owner's decision to switch plans against the current recommendation. Only when the owner says "
        "they have switched or will, naming the plans; dry-run first (the default) and show the owner.",
        RecordDecision,
        lambda a: (
            [
                "decide", "--switch-date", a.switch_date.isoformat(), *_opt("--elec", a.elec), *_opt("--gas", a.gas),
                *[word for item in a.evidence for word in ("--evidence", item)], *_opt("--note", a.note),
                *_flag("--dry-run", a.dry_run),
            ],
            [a.recommendation_id],
        ),
        read_only=False, idempotent=False,
    ),
    Tool(
        "new_gas_bill_record", "Scaffold a gas bill record",
        "Write the YAML skeleton for transcribing a landed gas bill PDF into data/landing/.",
        NewGasBillRecord,
        lambda a: (
            ["gas-bill", "new", *_opt("--extracted-by", a.extracted_by), *_flag("--force", a.force)],
            [a.pdf_dataset_id],
        ),
        read_only=False,
    ),
    Tool(
        "write_gas_bill_record", "Write a gas bill record",
        "Check a gas_bill_record and, only if every check passes, write it to data/landing/ ready for land_file. Set "
        "extracted_by to agent:<your model id>.",
        WriteGasBillRecord,
        lambda a: (["gas-bill", "write", *_flag("--force", a.force), *_flag("--dry-run", a.dry_run)], ["-"]),
        read_only=False, stdin=_record_json,
    ),
    Tool(
        "read_gas_bill_text", "Read a gas bill's text",
        "The text of a landed gas bill PDF, for transcription. It holds personal details (address, account): they go "
        "to the model provider. Never copy them into a record.",
        ReadGasBillText, lambda a: (["gas-bill", "text"], [a.pdf_dataset_id]), documents=True,
    ),
)  # fmt: skip


def tools_for(*, allow_documents: bool) -> tuple[Tool, ...]:
    """The tools a server lists: the opt-in ones only with `--allow-documents` (§6.3)."""
    return tuple(tool for tool in TOOLS if allow_documents or not tool.documents)


CLI_COMMAND: Final[dict[str, str]] = {
    "status": "status", "list_datasets": "datasets", "list_inbox": "landing", "usage_summary": "usage",
    "compare_offers": "compare", "explain_recommendation": "explain", "offers_diff": "offers",
    "check_gas_bill_record": "gas-bill", "land_file": "land", "fetch_offers": "fetch-offers", "run_pipeline": "run",
    "record_decision": "decide", "new_gas_bill_record": "gas-bill", "write_gas_bill_record": "gas-bill",
    "read_gas_bill_text": "gas-bill",
}  # fmt: skip
"""Each tool's CLI command (the envelope's `command`), for the envelopes the server builds itself."""
