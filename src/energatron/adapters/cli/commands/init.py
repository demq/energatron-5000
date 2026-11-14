"""`energatron init [DIR]`: a new workspace from the template (P8 spec §4). Works outside a workspace; takes no lock.

The command resolves what the service needs from this machine: the target path, the home folder, the shipped regions,
the template texts and the tool source (`--tool-source`, else the running install). On a terminal it asks for the
private values `--nmi` and `--meter-serial` leave out; with `--json` or without a terminal it never prompts (D1 §8.6).
"""

import sys
from pathlib import Path
from typing import Annotated, Literal

import typer

from energatron.adapters import wiring
from energatron.adapters.cli.common import DebugFlag, DryRunFlag, JsonFlag, VerboseFlag, WorkspaceOption, begin
from energatron.adapters.cli.output import CommandOutput, Invocation
from energatron.core.canonical import JSONValue
from energatron.core.errors import UsageError
from energatron.core.models.private import PrivateConfig
from energatron.core.models.workspace import WorkspacePaths
from energatron.core.privacy import DenyEntry, derive_denylist
from energatron.core.workspace_template import tool_source_line
from energatron.services import init as init_service
from energatron.services.context import ServiceContext
from energatron.services.demo import demo_private_values
from energatron.services.init import InitOptions, InitResult, PrivateValues, private_document

COMMAND = "init"

DirArgument = Annotated[str | None, typer.Argument(metavar="DIR", help="The new workspace folder (default: here).")]
RegionOption = Annotated[str, typer.Option("--region", help="The region profile: sa.")]
RemoteOption = Annotated[
    str | None,
    typer.Option("--remote", metavar="PATH", help="A local folder for the DVC backup remote.", show_default=False),
]
ToolSourceOption = Annotated[
    str | None,
    typer.Option(
        "--tool-source",
        metavar="SOURCE",
        help="Where the workspace gets energatron: a folder (editable) or git+https://…[@ref]. "
        "Default: where this energatron came from.",
        show_default=False,
    ),
]
NmiOption = Annotated[str | None, typer.Option("--nmi", help="Your NMI (asked for on a terminal).", show_default=False)]
SerialOption = Annotated[
    list[str] | None, typer.Option("--meter-serial", help="A meter serial; repeat for more.", show_default=False)
]
PostcodeOption = Annotated[str | None, typer.Option("--postcode", help="Your postcode.", show_default=False)]
DemoFlag = Annotated[bool, typer.Option("--demo", help="Fill the workspace with synthetic data and run it.")]
NoSyncFlag = Annotated[bool, typer.Option("--no-sync", help="Skip `uv sync` and `pre-commit install`.")]


def init(
    ctx: typer.Context,
    directory: DirArgument = None,
    region: RegionOption = "sa",
    remote: RemoteOption = None,
    tool_source: ToolSourceOption = None,
    nmi: NmiOption = None,
    meter_serial: SerialOption = None,
    postcode: PostcodeOption = None,
    demo: DemoFlag = False,
    no_sync: NoSyncFlag = False,
    dry_run: DryRunFlag = False,
    json_output: JsonFlag = False,
    workspace: WorkspaceOption = None,
    verbose: VerboseFlag = False,
    debug: DebugFlag = False,
) -> CommandOutput:
    """Create a new energatron workspace: git, uv, DVC, the private config, the pipeline files and the hooks."""
    invocation = begin(ctx, verbose=verbose, debug=debug)
    cwd = Path.cwd()
    root = (cwd / Path(directory).expanduser()).resolve() if directory else cwd.resolve()
    serials = tuple(meter_serial or ())
    if demo and (nmi or serials or postcode):
        raise UsageError("--demo uses synthetic private values; leave out --nmi, --meter-serial and --postcode.")
    private = demo_private_values() if demo else _private(invocation, nmi, serials, postcode, ask=not dry_run)
    kind, line = _tool_source(tool_source, cwd)
    options = InitOptions(
        root=root,
        region=region,
        regions=wiring.region_names(),
        tool_source=line,
        tool_source_kind=kind,
        home=Path.home().resolve(),
        sources=wiring.template_sources(),
        remote=(cwd / Path(remote).expanduser()).resolve() if remote else None,
        private=private,
        demo=demo,
        sync=not no_sync,
        dry_run=dry_run,
    )
    if private is not None:
        invocation.denylist = _denylist(private)
    setup = wiring.workspace_setup()
    result = init_service.init(
        options, setup=setup, open_context=_open_context, run_demo=_demo_in_process if no_sync else setup.run_demo
    )
    return CommandOutput(COMMAND, result, text(result))


def _open_context(paths: WorkspacePaths, dvc: Path | None) -> ServiceContext:
    return wiring.build_context(paths, dvc=dvc)


def _demo_in_process(root: Path) -> JSONValue:
    """With `--no-sync` the running venv already has DVC and the tool: run the demo here (P8 spec §4.3 step 11)."""
    from energatron.adapters.cli.commands.demo import run_demo

    return run_demo(WorkspacePaths(root=root))


def _private(
    invocation: Invocation, nmi: str | None, serials: tuple[str, ...], postcode: str | None, *, ask: bool
) -> PrivateValues | None:
    interactive = ask and not invocation.json_mode and sys.stdin.isatty() and sys.stderr.isatty()
    if nmi is None and interactive:
        nmi = typer.prompt("NMI (from your meter-data download; Enter to skip)", default="", err=True).strip() or None
        if nmi is not None and not serials:
            found = typer.prompt("Meter serial(s), comma-separated (Enter to skip)", default="", err=True)
            serials = tuple(item.strip() for item in found.split(",") if item.strip())
        if nmi is not None and postcode is None:
            postcode = typer.prompt("Postcode (Enter to skip)", default="", err=True).strip() or None
    if nmi is None:
        if serials or postcode:
            raise UsageError("--meter-serial and --postcode need --nmi.")
        return None
    return PrivateValues(nmi=nmi, meter_serials=serials, postcode=postcode)


def _denylist(values: PrivateValues) -> tuple[DenyEntry, ...]:
    """Redaction for this command's own output, before any private config exists (D1 §8.7). An invalid value fails
    here, before anything is written (`E_INVALID_INPUT`, naming the field)."""
    return derive_denylist(PrivateConfig.from_document(private_document(values, "0" * 32)))


def _tool_source(option: str | None, cwd: Path) -> tuple[Literal["path", "git", "index"], str]:
    """The `[tool.uv.sources]` line (P8 spec §4.2)."""
    if option is not None:
        if option.startswith("git+"):
            url, ref = _split_ref(option.removeprefix("git+"))
            return "git", tool_source_line("git", url, ref)
        folder = (cwd / Path(option).expanduser()).resolve()
        if not (folder / "pyproject.toml").is_file():
            raise UsageError("--tool-source must be an energatron-5000 folder or a git+https:// URL.")
        return "path", tool_source_line("path", folder.as_posix())
    kind, value, ref = wiring.running_tool_source()
    if kind == "index" or value is None:
        return "index", tool_source_line("index")
    return kind, tool_source_line(kind, value, ref)


def _split_ref(url: str) -> tuple[str, str | None]:
    """`https://host/path@ref` → (`https://host/path`, `ref`); an `@` before the path is a user, not a ref."""
    scheme, _, rest = url.partition("://")
    host, slash, path = rest.partition("/")
    if "@" in path:
        path, _, ref = path.rpartition("@")
        return f"{scheme}://{host}{slash}{path}", ref or None
    return url, None


def text(result: InitResult) -> str:
    if result.dry_run:
        planned = ", ".join(step.name for step in result.steps if step.status == "planned")
        lines = [f"Dry run: would create {result.root_name} ({len(result.files)} files) and run: {planned}."]
    else:
        lines = [f"Created the workspace {result.root_name} (region {result.region}) and committed it."]
        if result.git_identity_set:
            lines.append("git had no identity here: set a repo-local one (energatron workspace).")
        if result.demo is not None:
            rec = result.demo.get("recommendation_id")
            lines.append(f"Demo: landed and ran the pipeline; recommendation {rec}.")
    lines += [f"Next: {step}" for step in result.next_steps]
    return "\n".join(lines) + "\n"
