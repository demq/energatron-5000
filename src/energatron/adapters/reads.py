"""Every CLI read path, shared by the driving adapters (P10 spec §3.2): the CLI's read commands and the HTTP adapter.

A read path takes no lock and writes nothing. Each `ReadPath` names the CLI words it stands for, holds its arguments
as a pydantic model (bounds and defaults from the same service constants the typer options use), and its body: the
command's work after opening the workspace, moved here from the command module so the CLI and `http` run the same code.
Nothing here computes a figure; it calls services and `wiring`, as the commands did.
"""

import platform
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, ClassVar, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError

import energatron
from energatron.adapters import wiring
from energatron.core.canonical import sha256_hex
from energatron.core.errors import CheckFailedError, UsageError
from energatron.core.models.catalog import SourceType
from energatron.core.models.recommend import Fuel
from energatron.core.models.workspace import WorkspacePaths
from energatron.core.operations import WATCH_LOG, Schedule, launchd_label, read_schedule
from energatron.core.privacy import DenyEntry, derive_denylist
from energatron.ports.progress import Progress
from energatron.services import backup as backup_service
from energatron.services import compare as compare_service
from energatron.services import datasets as datasets_service
from energatron.services import diagnose as diagnose_service
from energatron.services import explain as explain_service
from energatron.services import gas_bill as gas_bill_service
from energatron.services import landing as landing_service
from energatron.services import pipeline_sync, usage_summary
from energatron.services import status as status_service
from energatron.services.context import ServiceContext, ServiceResult
from energatron.services.denylist import read_manual_denylist
from energatron.services.diagnose import ToolInfo
from energatron.services.gas_bill import GasBillCheckResult
from energatron.services.offer_diff import offer_diff
from energatron.services.pipeline_sync import PipelineSyncResult
from energatron.services.schedule import ScheduleResult
from energatron.services.stages.dvc_yaml import CODE_FINGERPRINT_PATH


def open_context(
    paths: WorkspacePaths,
    on_manual: Callable[[tuple[DenyEntry, ...]], None] | None = None,
    progress: Progress | None = None,
) -> tuple[ServiceContext, tuple[DenyEntry, ...]]:
    """The workspace's context and the redaction denylist: `private/denylist.txt`'s entries, then those derived from
    the private config once it's read (D1 §8.7), so the denylist never depends on the generated file being current.

    `on_manual` lets a caller hold the manual entries before the context is built (the CLI redacts with them at once).
    `progress` is passed to `wiring.build_context`.
    """
    manual = tuple(read_manual_denylist(paths))
    if on_manual is not None:
        on_manual(manual)
    context = wiring.build_context(paths, progress=progress)
    derived = derive_denylist(context.private) if context.private is not None else ()
    return context, (*derived, *manual)


# Arguments


class Args(BaseModel):
    """A read path's arguments: the CLI's options and arguments, by their field names."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


class NoArgs(Args):
    pass


class DatasetsArgs(Args):
    source: SourceType | None = None
    limit: int = Field(datasets_service.DEFAULT_LIMIT, ge=1, le=datasets_service.MAX_LIMIT)


class UsageArgs(Args):
    months: int = Field(usage_summary.DEFAULT_MONTHS, ge=1, le=usage_summary.MAX_MONTHS)


class CompareArgs(Args):
    fuel: Literal["electricity", "gas"] | None = None
    limit: int = Field(compare_service.DEFAULT_LIMIT, ge=1, le=compare_service.MAX_LIMIT)
    brand: str | None = None


class ExplainArgs(Args):
    rec_id: str | None = None
    options: int = Field(explain_service.DEFAULT_OPTIONS, ge=1, le=explain_service.MAX_OPTIONS)


class OffersDiffArgs(Args):
    plan: str | None = None


class GasBillCheckArgs(Args):
    record: bytes


class GasBillTextArgs(Args):
    pdf_dataset_id: str


# Bodies


class VersionResult(ServiceResult):
    """`version_result` v1 (D2 §6.4)."""

    KIND: ClassVar[str] = "version_result"
    SCHEMA_VERSION: ClassVar[int] = 1
    kind: Literal["version_result"] = "version_result"
    schema_version: Literal[1] = 1

    energatron_version: str
    python: str


def _version(_: ServiceContext | None, __: NoArgs) -> ServiceResult:
    return VersionResult(energatron_version=energatron.__version__, python=platform.python_version())


def _status(context: ServiceContext, _: NoArgs) -> ServiceResult:
    """The live code state comes from `wiring` (services can't compute it, D1 §11.4)."""
    stale, tool_dirty = wiring.code_state(context.paths)
    unpublished = wiring.tool_unpublished(context.paths)
    return status_service.status(context, code_stale=stale, tool_dirty=tool_dirty, tool_unpublished=unpublished)


def _datasets(context: ServiceContext, args: DatasetsArgs) -> ServiceResult:
    return datasets_service.datasets(context, source=args.source, limit=args.limit)


def _usage(context: ServiceContext, args: UsageArgs) -> ServiceResult:
    return usage_summary.usage(context, months=args.months)


def _compare(context: ServiceContext, args: CompareArgs) -> ServiceResult:
    return compare_service.compare(context, fuel=cast(Fuel | None, args.fuel), limit=args.limit, brand=args.brand)


def _explain(context: ServiceContext, args: ExplainArgs) -> ServiceResult:
    stale, _ = wiring.code_state(context.paths)
    return explain_service.explain(context, args.rec_id, options=args.options, code_stale=stale)


def _offers_diff(context: ServiceContext, args: OffersDiffArgs) -> ServiceResult:
    return offer_diff(context, plan=args.plan)


def _landing(context: ServiceContext, _: NoArgs) -> ServiceResult:
    return landing_service.landing_list(context)


def _backup_status(context: ServiceContext, _: NoArgs) -> ServiceResult:
    return backup_service.backup_status(context)


def _diagnose(context: ServiceContext, _: NoArgs) -> ServiceResult:
    """What services can't gather: the live code fingerprint, and the versions of git, uv and DVC."""
    live = wiring.code_fingerprint()
    stale, tool_dirty = wiring.code_state(context.paths)
    versions = wiring.program_versions()
    tool = ToolInfo(
        energatron_version=live.energatron_version,
        install=live.install,
        tool_dirty=tool_dirty,
        python=platform.python_version(),
        platform=sys.platform,
        machine=platform.machine(),
        git=versions["git"],
        uv=versions["uv"],
        dvc=versions["dvc"],
    )
    return diagnose_service.diagnose(
        context, tool=tool, code_stale=stale, tool_unpublished=wiring.tool_unpublished(context.paths)
    )


def _fingerprint_check(context: ServiceContext, _: NoArgs) -> ServiceResult:
    return wiring.refresh_fingerprint(context.paths.root / CODE_FINGERPRINT_PATH, wiring.code_fingerprint(), check=True)


def _pipeline_check(context: ServiceContext, _: NoArgs) -> ServiceResult:
    pipeline_sync.check(context)
    return PipelineSyncResult(
        dvc_yaml_written=False,
        params_written=False,
        params_rewritten=False,
        catalogs_created=[],
        pointer_files=[],
        dry_run=False,
    )


def schedule_label(context: ServiceContext) -> str:
    """This workspace's launchd label (P7 spec §8)."""
    return launchd_label(sha256_hex(str(context.paths.root).encode("utf-8")))


def schedule_result(
    agent: wiring.Launchd,
    action: str,
    label: str,
    *,
    dry_run: bool,
    installed: bool,
    loaded: bool,
    schedule: Schedule | None,
) -> ScheduleResult:
    return ScheduleResult.model_validate(
        {
            "action": action,
            "dry_run": dry_run,
            "label": label,
            "plist": agent.shown(agent.plist_path(label)),
            "installed": installed,
            "loaded": loaded,
            "weekday": schedule.weekday if schedule else None,
            "hour": schedule.hour if schedule else None,
            "minute": schedule.minute if schedule else None,
            "log": WATCH_LOG,
        }
    )


def _schedule_status(context: ServiceContext, _: NoArgs) -> ServiceResult:
    agent = wiring.launchd()
    agent.check_platform()
    label = schedule_label(context)
    state = agent.state(label)
    schedule = read_schedule(state.plist) if state.plist else None
    return schedule_result(
        agent, "status", label, dry_run=False, installed=state.installed, loaded=state.loaded, schedule=schedule
    )


def raise_if_failed(result: GasBillCheckResult) -> None:
    """`E_CHECK_FAILED` (exit 7) naming the failing checks, with both totals (P9 spec §4.2)."""
    failed = result.failed()
    if failed:
        raise CheckFailedError(
            "The record doesn't recompute: fix the failing checks against the bill.",
            failed=failed,
            pdf_dataset_id=result.pdf_dataset_id,
            recomputed_total=str(result.recomputed_total),
            printed_total=str(result.printed_total),
        )


def _gas_bill_check(context: ServiceContext, args: GasBillCheckArgs) -> ServiceResult:
    result = gas_bill_service.check_gas_bill_record(context, args.record)
    raise_if_failed(result)
    return result


def _gas_bill_text(context: ServiceContext, args: GasBillTextArgs) -> ServiceResult:
    return gas_bill_service.gas_bill_text(context, args.pdf_dataset_id, wiring.pdf_pages)


# The table


@dataclass(frozen=True)
class ReadPath:
    """One read path: its API name, the envelope's `command`, the CLI words, its arguments and its body."""

    name: str
    command: str
    argv: tuple[str, ...]
    args: type[Args]
    body: Callable[[Any, Any], ServiceResult]
    needs_workspace: bool = True
    document: bool = False  # serves a document's text: only on request (P9 spec §6.3, P10 spec §5.3)

    def parse(self, values: Mapping[str, object]) -> Args:
        """The arguments from loose values (an HTTP query): `E_USAGE` naming the field, never echoing its value."""
        try:
            return self.args.model_validate(dict(values))
        except ValidationError as exc:
            errors = exc.errors(include_input=False, include_url=False)
            location = errors[0]["loc"] if errors else ()
            name = str(location[0]) if location else "arguments"
            raise UsageError(f"Invalid value for {name}.", parameter=name) from None

    def run(self, context: ServiceContext | None, args: Args) -> ServiceResult:
        if not isinstance(args, self.args):
            raise TypeError(f"{self.name} takes {self.args.__name__}")
        return self.body(context, args)


STATUS = ReadPath("status", "status", ("status",), NoArgs, _status)
DATASETS = ReadPath("datasets", "datasets", ("datasets",), DatasetsArgs, _datasets)
USAGE = ReadPath("usage", "usage", ("usage",), UsageArgs, _usage)
COMPARE = ReadPath("compare", "compare", ("compare",), CompareArgs, _compare)
EXPLAIN = ReadPath("explain", "explain", ("explain",), ExplainArgs, _explain)
OFFERS_DIFF = ReadPath("offers/diff", "offers", ("offers", "diff"), OffersDiffArgs, _offers_diff)
LANDING = ReadPath("landing", "landing", ("landing", "list"), NoArgs, _landing)
BACKUP_STATUS = ReadPath("backup/status", "backup", ("backup", "--status"), NoArgs, _backup_status)
DIAGNOSE = ReadPath("diagnose", "diagnose", ("diagnose",), NoArgs, _diagnose)
FINGERPRINT_CHECK = ReadPath("fingerprint/check", "fingerprint", ("fingerprint", "--check"), NoArgs, _fingerprint_check)
PIPELINE_CHECK = ReadPath("pipeline/check", "pipeline", ("pipeline", "sync", "--check"), NoArgs, _pipeline_check)
SCHEDULE_STATUS = ReadPath("schedule/status", "schedule", ("schedule", "status"), NoArgs, _schedule_status)
VERSION = ReadPath("version", "version", ("version",), NoArgs, _version, needs_workspace=False)
GAS_BILL_CHECK = ReadPath("gas-bill/check", "gas-bill", ("gas-bill", "check"), GasBillCheckArgs, _gas_bill_check)
GAS_BILL_TEXT = ReadPath(
    "gas-bill/text", "gas-bill", ("gas-bill", "text"), GasBillTextArgs, _gas_bill_text, document=True
)

READS: tuple[ReadPath, ...] = (
    STATUS,
    DATASETS,
    USAGE,
    COMPARE,
    EXPLAIN,
    OFFERS_DIFF,
    LANDING,
    BACKUP_STATUS,
    DIAGNOSE,
    FINGERPRINT_CHECK,
    PIPELINE_CHECK,
    SCHEDULE_STATUS,
    VERSION,
    GAS_BILL_CHECK,
    GAS_BILL_TEXT,
)
"""Every read path, in the P10 spec §3.2's order."""
