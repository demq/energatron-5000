"""`energatron schedule install|remove|status` (P7 spec §8): the weekly `watch` as a per-user launchd agent.

macOS only. Not a service: it configures this machine, not the workspace (P7 spec §14 Q5). `install` and `remove` are
the owner's; agents run `status` and `install --dry-run`. No workspace lock: the agent and its plist live outside the
workspace, and `install` only creates the log folder in it.
"""

import shutil
from typing import Annotated, cast

import typer

from energatron.adapters import reads, wiring
from energatron.adapters.cli.common import (
    DebugFlag,
    DryRunFlag,
    JsonFlag,
    VerboseFlag,
    WorkspaceOption,
    begin,
    open_workspace,
)
from energatron.adapters.cli.output import CommandOutput
from energatron.core.errors import StorageError, UsageError
from energatron.core.operations import (
    Schedule,
    launchd_plist,
    read_schedule,
)
from energatron.services.schedule import ScheduleResult

COMMAND = "schedule"

WeekdayOption = Annotated[int, typer.Option("--weekday", min=0, max=6, help="Day of the week, 0 Sunday to 6 Saturday.")]
HourOption = Annotated[int, typer.Option("--hour", min=0, max=23, help="Hour of the day, local time.")]
MinuteOption = Annotated[int, typer.Option("--minute", min=0, max=59, help="Minute of the hour.")]

app = typer.Typer(
    name="schedule",
    help="The weekly `energatron watch` as a launchd agent (macOS).",
    add_completion=False,
    pretty_exceptions_enable=False,
)


@app.command("install")
def install(
    ctx: typer.Context,
    weekday: WeekdayOption = 1,
    hour: HourOption = 9,
    minute: MinuteOption = 0,
    dry_run: DryRunFlag = False,
    json_output: JsonFlag = False,
    workspace: WorkspaceOption = None,
    verbose: VerboseFlag = False,
    debug: DebugFlag = False,
) -> CommandOutput:
    """Install (or replace) the weekly `watch --notify` agent for this workspace. Owner-run."""
    invocation = begin(ctx, verbose=verbose, debug=debug)
    context = open_workspace(invocation, workspace)
    agent = wiring.launchd()
    agent.check_platform()
    uv = shutil.which("uv")
    if uv is None:
        raise UsageError("uv isn't on PATH, so the agent couldn't run it.")
    label = reads.schedule_label(context)
    schedule = Schedule(weekday, hour, minute)
    plist = launchd_plist(label=label, uv=uv, workspace=str(context.paths.root), schedule=schedule)
    if not dry_run:
        try:
            context.paths.logs_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            raise StorageError("The log folder couldn't be created.", reason="logs_dir") from None
        agent.install(label, plist)
    state = agent.state(label)
    result = reads.schedule_result(
        agent, "install", label, dry_run=dry_run, installed=state.installed, loaded=state.loaded, schedule=schedule
    )
    text = _text(result)
    if dry_run:
        preview = agent.shown_text(plist.decode("utf-8"))
        text = f"Dry run: nothing written or loaded. The plist would be:\n{preview}" + text
    return CommandOutput(COMMAND, result, text)


@app.command("remove")
def remove(
    ctx: typer.Context,
    dry_run: DryRunFlag = False,
    json_output: JsonFlag = False,
    workspace: WorkspaceOption = None,
    verbose: VerboseFlag = False,
    debug: DebugFlag = False,
) -> CommandOutput:
    """Unload and delete this workspace's agent. Nothing installed is fine. Owner-run."""
    invocation = begin(ctx, verbose=verbose, debug=debug)
    context = open_workspace(invocation, workspace)
    agent = wiring.launchd()
    agent.check_platform()
    label = reads.schedule_label(context)
    before = agent.state(label)
    schedule = read_schedule(before.plist) if before.plist else None
    if not dry_run:
        agent.remove(label)
    after = before if dry_run else agent.state(label)
    result = reads.schedule_result(
        agent, "remove", label, dry_run=dry_run, installed=after.installed, loaded=after.loaded, schedule=schedule
    )
    return CommandOutput(COMMAND, result, _text(result))


@app.command("status")
def status(
    ctx: typer.Context,
    json_output: JsonFlag = False,
    workspace: WorkspaceOption = None,
    verbose: VerboseFlag = False,
    debug: DebugFlag = False,
) -> CommandOutput:
    """Whether this workspace's agent is installed and loaded, and its schedule."""
    invocation = begin(ctx, verbose=verbose, debug=debug)
    context = open_workspace(invocation, workspace)
    result = cast(ScheduleResult, reads.SCHEDULE_STATUS.run(context, reads.NoArgs()))
    return CommandOutput(COMMAND, result, _text(result))


_DAYS = ("Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday")


def _text(result: ScheduleResult) -> str:
    when = (
        f"{_DAYS[result.weekday]} {result.hour:02d}:{result.minute:02d}"
        if result.weekday is not None and result.hour is not None and result.minute is not None
        else "no schedule"
    )
    state = f"{'installed' if result.installed else 'not installed'}, {'loaded' if result.loaded else 'not loaded'}"
    return f"Agent {result.label} ({result.plist}): {state}; {when}; log {result.log}\n"
