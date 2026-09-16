from pathlib import Path

import typer

import amide

app = typer.Typer(add_completion=False, help="A molecular dynamics CLI.")
tools_app = typer.Typer(help="The tool registry.")
protocols_app = typer.Typer(help="Predefined protocols.")
runs_app = typer.Typer(help="Past and running experiments.")
config_app = typer.Typer(help="The config file.")
app.add_typer(tools_app, name="tools")
app.add_typer(protocols_app, name="protocols")
app.add_typer(runs_app, name="runs")
app.add_typer(config_app, name="config")

RESUMABLE_EXIT = 3


def _version(value: bool) -> None:
    if value:
        typer.echo(f"amide {amide.__version__}")
        raise typer.Exit()


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version,
        is_eager=True,
        help="Show the version and exit.",
    ),
) -> None:
    if ctx.invoked_subcommand is None:
        typer.echo(f"amide {amide.__version__}: hello, world")


@app.command()
def view(
    path: Path = typer.Argument(  # noqa: B008 -- typer reads the call, not its result
        Path("."), help="A PDB file to open, or a directory to browse."
    ),
) -> None:
    """Open the molecular viewer."""
    target = path.resolve()
    if not target.exists():
        typer.echo(f"amide view: {path} does not exist", err=True)
        raise typer.Exit(2)

    from amide.tui import TuiUnavailable, binary_path

    try:
        binary = binary_path()
    except TuiUnavailable as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(3) from None

    import os
    import subprocess
    import sys

    if sys.platform == "win32":
        # os.execv on Windows spawns a child and returns, handing the terminal
        # back to the shell while the TUI is still drawing.
        done = subprocess.run([str(binary), str(target)], check=False)
        raise typer.Exit(done.returncode)
    os.execv(str(binary), [str(binary), str(target)])


# --- run -------------------------------------------------------------------


@app.command()
def run(
    protocol: str = typer.Argument(
        None, help="A protocol name (see `amide protocols list`) or a YAML file."
    ),
    set_: list[str] = typer.Option(
        [], "--set", "-s", metavar="NAME=VALUE", help="Override a parameter; repeatable."
    ),
    resume: str = typer.Option(None, "--resume", metavar="RUN", help="Continue a run instead."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Approve every expensive step."),
    detach: bool = typer.Option(False, "--detach", "-d", help="Run in the background."),
    max_seconds: float = typer.Option(
        None, "--max-seconds", help="Start no step after this wall-clock budget."
    ),
    runs_dir: Path = typer.Option(None, "--runs-dir", help="Where runs are kept."),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Validate and print the resolved parameters; run nothing."
    ),
) -> None:
    """Run a protocol."""
    import sys

    from amide.harness.errors import HarnessError
    from amide.harness.protocol import find, load, parse_overrides
    from amide.harness.registry import Registry
    from amide.harness.runner import RunOptions, execute
    from amide.harness.runs import RunStore

    if (protocol is None) == (resume is None):
        typer.echo("amide run: give a protocol, or --resume RUN", err=True)
        raise typer.Exit(2)
    try:
        config = _config()
        store = RunStore(runs_dir or config.runs_dir)
        registry = Registry.load(config)
        if resume is not None:
            state = store.get(resume)
            spec = load(state.protocol_path)
            if state.status in ("passed", "failed"):
                typer.echo(f"run {state.id} already finished ({state.status})")
                raise typer.Exit()
        else:
            spec = find(protocol)
            problems = spec.problems(registry)
            if problems:
                typer.echo(f"{spec.name} is not valid:", err=True)
                for problem in problems:
                    typer.echo(f"  - {problem}", err=True)
                raise typer.Exit(2)
            params = spec.resolve_params(parse_overrides(set_))
            if dry_run:
                typer.echo(f"{spec.name}: {len(spec.steps)} steps")
                for name, value in params.items():
                    typer.echo(f"  {name} = {value!r}")
                raise typer.Exit()
            state = store.create(spec, params)
    except HarnessError as error:
        typer.echo(f"amide run: {error}", err=True)
        raise typer.Exit(2) from None

    if detach:
        _detach(state, store.root)
        typer.echo(state.id)
        raise typer.Exit()

    def approve(step, tool, args) -> bool:
        if not sys.stdin.isatty():
            return False
        return typer.confirm(f"step {step.id} runs {tool.name}, which is expensive. Continue?")

    options = RunOptions(
        yes=yes,
        approve=approve,
        max_seconds=max_seconds,
        echo=typer.echo,
    )
    state = execute(state, spec, registry, options)
    typer.echo(f"results: {state.dir / 'report.md'}")
    if state.status == "passed":
        raise typer.Exit()
    if state.status in ("paused", "budget_exceeded"):
        raise typer.Exit(RESUMABLE_EXIT)
    raise typer.Exit(1)


def _detach(state, root: Path) -> None:
    import subprocess
    import sys

    argv = [
        sys.executable,
        "-m",
        "amide",
        "run",
        "--resume",
        state.id,
        "--yes",
        "--runs-dir",
        str(root),
    ]
    with state.log_path.open("a") as log:
        subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )


# --- tools -----------------------------------------------------------------


@tools_app.command("list")
def tools_list(
    tag: str = typer.Option(None, "--tag", help="Only tools carrying this tag."),
) -> None:
    """List the tools in the registry."""
    from amide.harness.registry import Registry

    registry = _registry()
    for spec in registry:
        if tag and tag not in spec.tags:
            continue
        missing = Registry.missing(spec)
        status = "ready" if not missing else "missing " + ", ".join(missing)
        typer.echo(f"{spec.name:<20} {spec.cost:<10} {status}")


@tools_app.command("show")
def tools_show(name: str = typer.Argument(..., help="A tool name.")) -> None:
    """Show a tool's manifest."""
    import json

    from amide.harness.errors import HarnessError

    try:
        spec = _registry().get(name)
    except HarnessError as error:
        typer.echo(f"amide tools show: {error}", err=True)
        raise typer.Exit(2) from None
    typer.echo(json.dumps(spec.to_dict(), indent=2))


@tools_app.command("check")
def tools_check(
    name: str = typer.Argument(None, help="One tool, or every tool when omitted."),
) -> None:
    """Report which tools can run on this machine."""
    from amide.harness.errors import HarnessError
    from amide.harness.registry import Registry

    registry = _registry()
    try:
        specs = [registry.get(name)] if name else list(registry)
    except HarnessError as error:
        typer.echo(f"amide tools check: {error}", err=True)
        raise typer.Exit(2) from None
    absent = 0
    for spec in specs:
        missing = Registry.missing(spec)
        if not missing:
            typer.echo(f"{spec.name}: ready")
            continue
        absent += 1
        typer.echo(f"{spec.name}: missing {', '.join(missing)}")
        if spec.requires.hint:
            typer.echo(f"    {spec.requires.hint}")
    if absent:
        raise typer.Exit(1)


# --- protocols -------------------------------------------------------------


@protocols_app.command("list")
def protocols_list() -> None:
    """List bundled and local protocols."""
    from amide.harness.errors import HarnessError
    from amide.harness.protocol import available

    try:
        protocols = available()
    except HarnessError as error:
        typer.echo(f"amide protocols list: {error}", err=True)
        raise typer.Exit(2) from None
    for protocol in protocols:
        first = protocol.description.strip().splitlines()[0] if protocol.description else ""
        typer.echo(f"{protocol.name:<20} {len(protocol.steps)} steps  {first}")


@protocols_app.command("show")
def protocols_show(protocol: str = typer.Argument(..., help="A name or a YAML file.")) -> None:
    """Print a protocol's parameters and steps."""
    from amide.harness.errors import HarnessError
    from amide.harness.protocol import find

    try:
        spec = find(protocol)
    except HarnessError as error:
        typer.echo(f"amide protocols show: {error}", err=True)
        raise typer.Exit(2) from None
    typer.echo(f"{spec.name} (version {spec.version})")
    if spec.source:
        typer.echo(f"  from {spec.source}")
    if spec.description:
        typer.echo(f"  {spec.description.strip()}")
    typer.echo("parameters:")
    for name, param in spec.params.items():
        need = " (required)" if param.required else f" = {param.default!r}"
        typer.echo(f"  {name}: {param.type}{need}  {param.description}".rstrip())
    typer.echo("steps:")
    for step in spec.steps:
        when = f"  when {step.when}" if step.when else ""
        typer.echo(f"  {step.id}: {step.tool}{when}")
    if spec.checks:
        typer.echo("checks:")
        for check in spec.checks:
            typer.echo(f"  {check.id}: {check.expr}")


@protocols_app.command("validate")
def protocols_validate(protocol: str = typer.Argument(..., help="A name or a YAML file.")) -> None:
    """Check a protocol against the tool registry without running it."""
    from amide.harness.errors import HarnessError
    from amide.harness.protocol import find

    try:
        spec = find(protocol)
    except HarnessError as error:
        typer.echo(f"amide protocols validate: {error}", err=True)
        raise typer.Exit(2) from None
    problems = spec.problems(_registry())
    for problem in problems:
        typer.echo(f"  - {problem}")
    if problems:
        typer.echo(f"{spec.name}: {len(problems)} problem(s)")
        raise typer.Exit(1)
    typer.echo(f"{spec.name}: ok ({len(spec.steps)} steps, {len(spec.checks)} checks)")


# --- runs ------------------------------------------------------------------


@runs_app.command("list")
def runs_list(runs_dir: Path = typer.Option(None, "--runs-dir")) -> None:
    """List runs, newest first."""
    from amide.harness.runs import RunStore

    store = RunStore(runs_dir or _config().runs_dir)
    for state in store.list():
        row = state.summary()
        typer.echo(f"{row['id']}  {row['status']:<16} {row['steps']:<6} {row['protocol']}")


@runs_app.command("show")
def runs_show(
    run: str = typer.Argument(..., help="A run id, or a unique prefix of one."),
    runs_dir: Path = typer.Option(None, "--runs-dir"),
    log_lines: int = typer.Option(10, "--log", help="How many lines of run.log to show."),
) -> None:
    """Show a run's status, steps, checks, and log tail."""
    from amide.harness.errors import HarnessError
    from amide.harness.runs import RunStore

    store = RunStore(runs_dir or _config().runs_dir)
    try:
        state = store.get(run)
    except HarnessError as error:
        typer.echo(f"amide runs show: {error}", err=True)
        raise typer.Exit(2) from None
    typer.echo(f"{state.id}: {state.protocol_name}, {state.status}")
    if state.error:
        typer.echo(f"  {state.error}")
    typer.echo(f"  dir: {state.dir}")
    for step in state.steps.values():
        seconds = "" if step.seconds is None else f"  {step.seconds:.1f}s"
        error = f"  {step.error}" if step.error else ""
        typer.echo(f"  {step.id:<16} {step.tool:<20} {step.status}{seconds}{error}")
    for check in state.checks:
        verdict = "pass" if check.get("passed") else "FAIL"
        typer.echo(f"  check {check['id']}: {verdict}  ({check['expr']})")
    if log_lines and state.log_path.exists():
        lines = state.log_path.read_text().splitlines()[-log_lines:]
        typer.echo("log:")
        for line in lines:
            typer.echo(f"  {line}")


@runs_app.command("export")
def runs_export(
    run: str = typer.Argument(..., help="A run id, or a unique prefix of one."),
    out: Path = typer.Option(None, "--out", "-o", help="Archive path; default <id>.tar.gz."),
    runs_dir: Path = typer.Option(None, "--runs-dir"),
) -> None:
    """Write manifest.json and pack the run into a tarball to share."""
    from amide.harness.errors import HarnessError
    from amide.harness.runs import RunStore

    store = RunStore(runs_dir or _config().runs_dir)
    try:
        archive = store.export(run, out)
    except HarnessError as error:
        typer.echo(f"amide runs export: {error}", err=True)
        raise typer.Exit(2) from None
    typer.echo(str(archive))


# --- config ----------------------------------------------------------------


@config_app.command("path")
def config_path() -> None:
    """Print where the config file is read from."""
    from amide.config import config_path as path

    typer.echo(str(path()))


@config_app.command("init")
def config_init(
    force: bool = typer.Option(False, "--force", help="Overwrite an existing file."),
) -> None:
    """Write a template config file."""
    from amide.config import init

    try:
        typer.echo(str(init(force=force)))
    except FileExistsError as error:
        typer.echo(f"amide config init: {error}", err=True)
        raise typer.Exit(1) from None


@config_app.command("show")
def config_show() -> None:
    """Print the effective config with secrets redacted."""
    import json

    config = _config()
    typer.echo(f"# {config.path}{'' if config.exists else ' (not found; defaults)'}")
    effective = {
        "runs": {"dir": str(config.runs_dir)},
        "tools": {"paths": [str(p) for p in config.tool_paths]},
        "defaults": config.defaults,
        "providers": config.redacted().get("providers", {}),
    }
    typer.echo(json.dumps(effective, indent=2))


# --- helpers ---------------------------------------------------------------


def _config():
    from amide.config import load

    try:
        return load()
    except ValueError as error:
        typer.echo(f"amide: {error}", err=True)
        raise typer.Exit(2) from None


def _registry():
    from amide.harness.errors import HarnessError
    from amide.harness.registry import Registry

    try:
        return Registry.load(_config())
    except HarnessError as error:
        typer.echo(f"amide: {error}", err=True)
        raise typer.Exit(2) from None
