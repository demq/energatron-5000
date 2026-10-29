"""`energatron stage <name>` and `energatron stage --emit-dvc-yaml` (D1 §9.5).

`stage <name>` runs one registered stage: inputs and outputs default to the registry's paths under the workspace
root, and `--input name=path` / `--output name=path` override them for tests; a relative override is taken from the
current directory. The adapter only collects these values, calls `prepare_stage`, computes the stage's live code
entry and reads the recorded one, then calls `run_stage`. It never takes the workspace lock: `dvc repro` runs it under
a parent that may hold it (D1 §8.8). `main()` writes its repro-log records (D1 §4.5).

`--emit-dvc-yaml` prints the `dvc.yaml` generated from the registry; with `--json`, as a `dvc_yaml_result`.
"""

from collections.abc import Sequence
from pathlib import Path
from typing import Annotated

import typer

from energatron.adapters import wiring
from energatron.adapters.cli.common import (
    DebugFlag,
    JsonFlag,
    VerboseFlag,
    WorkspaceOption,
    begin,
    open_workspace,
)
from energatron.adapters.cli.output import CommandOutput
from energatron.core.errors import UsageError
from energatron.services.stages.dvc_yaml import CODE_FINGERPRINT_PATH, DvcYamlResult, emit_dvc_yaml
from energatron.services.stages.registry import STAGES, get_stage
from energatron.services.stages.runner import StageResult, prepare_stage, run_stage

NameArgument = Annotated[str | None, typer.Argument(metavar="NAME", help="The stage to run.", show_default=False)]
InputOption = Annotated[
    list[str] | None,
    typer.Option("--input", metavar="NAME=PATH", help="Read input NAME from PATH instead of its default (tests)."),
]
OutputOption = Annotated[
    list[str] | None,
    typer.Option("--output", metavar="NAME=PATH", help="Write output NAME to PATH instead of its default (tests)."),
]
EmitFlag = Annotated[
    bool, typer.Option("--emit-dvc-yaml", help="Print the dvc.yaml generated from the registered stages.")
]


def stage(
    ctx: typer.Context,
    name: NameArgument = None,
    inputs: InputOption = None,
    outputs: OutputOption = None,
    emit: EmitFlag = False,
    json_output: JsonFlag = False,
    workspace: WorkspaceOption = None,
    verbose: VerboseFlag = False,
    debug: DebugFlag = False,
) -> CommandOutput:
    """Run one pipeline stage, as dvc.yaml does; or print dvc.yaml with --emit-dvc-yaml."""
    invocation = begin(ctx, verbose=verbose, debug=debug)
    input_overrides = _overrides("--input", inputs or [])
    output_overrides = _overrides("--output", outputs or [])
    if emit:
        if name is not None or input_overrides or output_overrides:
            raise UsageError("--emit-dvc-yaml takes no stage name, --input or --output.")
        open_workspace(invocation, workspace)
        text = emit_dvc_yaml(STAGES)
        return CommandOutput("stage", DvcYamlResult(text=text), text)
    if name is None:
        raise UsageError("Name the stage to run, or use --emit-dvc-yaml.")
    context = open_workspace(invocation, workspace)
    root = context.paths.root
    spec = get_stage(name)
    prepared = prepare_stage(
        spec,
        root=root,
        params_file=root / "params.yaml",
        input_overrides=input_overrides,
        output_overrides=output_overrides,
    )
    code = wiring.stage_code(name)
    recorded = wiring.read_fingerprint(root / CODE_FINGERPRINT_PATH)
    entry = recorded.stages.get(name) if recorded is not None else None
    result = run_stage(
        spec,
        prepared.inputs,
        prepared.params,
        prepared.outputs,
        code=code,
        recorded_code=entry.stage_sha256 if entry is not None else None,
    )
    return CommandOutput("stage", result, _summary(result))


def _overrides(option: str, values: Sequence[str]) -> dict[str, Path]:
    """`NAME=PATH` values as absolute paths; a relative PATH is taken from the current directory."""
    found: dict[str, Path] = {}
    for value in values:
        name, separator, path = value.partition("=")
        if not separator or not name or not path:
            raise UsageError(f"{option} takes NAME=PATH.", parameter=option)
        if name in found:
            raise UsageError(f"{option} names {name} twice.", parameter=option)
        found[name] = Path.cwd() / path
    return found


def _plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def _summary(result: StageResult) -> str:
    files = sum(len(items) for items in result.outputs.values())
    outputs, checks = len(result.outputs), len(result.checks)
    return (
        f"Stage {result.stage} done: {_plural(outputs, 'output')}, {_plural(files, 'file')}, "
        f"{_plural(checks, 'check')} passed.\n"
    )
