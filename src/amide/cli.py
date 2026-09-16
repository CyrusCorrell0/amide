from pathlib import Path

import typer

import amide

app = typer.Typer(add_completion=False, help="A molecular dynamics CLI.")
tools_app = typer.Typer(help="The tool registry.")
protocols_app = typer.Typer(help="Predefined protocols.")
runs_app = typer.Typer(help="Past and running experiments.")
config_app = typer.Typer(help="The config file.")
models_app = typer.Typer(help="Model providers.")
app.add_typer(tools_app, name="tools")
app.add_typer(protocols_app, name="protocols")
app.add_typer(runs_app, name="runs")
app.add_typer(config_app, name="config")
app.add_typer(models_app, name="models")

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
    runner: str = typer.Option(
        None, "--runner", help="A [runners.*] name: execute steps there instead of here."
    ),
    remote_cost: str = typer.Option(
        "moderate", "--remote-cost", help="With --runner: send steps of at least this cost."
    ),
) -> None:
    """Run a protocol."""
    import sys

    from amide.harness.errors import HarnessError
    from amide.harness.protocol import find, load, parse_overrides
    from amide.harness.registry import Registry
    from amide.harness.remote import runners_from_config
    from amide.harness.runner import RunOptions, execute
    from amide.harness.runs import RunStore
    from amide.harness.tool import COSTS

    if remote_cost not in COSTS:
        typer.echo(f"amide run: --remote-cost must be one of {', '.join(COSTS)}", err=True)
        raise typer.Exit(2)

    if (protocol is None) == (resume is None):
        typer.echo("amide run: give a protocol, or --resume RUN", err=True)
        raise typer.Exit(2)
    try:
        config = _config()
        store = RunStore(runs_dir or config.runs_dir)
        registry = Registry.load(config)
        runners = runners_from_config(config)
        if runner is not None and runner not in runners:
            raise HarnessError(
                f"no runner named {runner!r}; add [runners.{runner}] to {config.path}"
            )
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
        _detach(state, store.root, runner, remote_cost)
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
        runners=runners,
        runner=runner,
        remote_cost=remote_cost,
    )
    state = execute(state, spec, registry, options)
    typer.echo(f"results: {state.dir / 'report.md'}")
    if state.status == "passed":
        raise typer.Exit()
    if state.status in ("paused", "budget_exceeded"):
        raise typer.Exit(RESUMABLE_EXIT)
    raise typer.Exit(1)


def _detach(state, root: Path, runner: str | None = None, remote_cost: str = "moderate") -> None:
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
        "--remote-cost",
        remote_cost,
    ]
    if runner:
        argv += ["--runner", runner]
    with state.log_path.open("a") as log:
        subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )


# --- ask -------------------------------------------------------------------


@app.command()
def ask(
    prompt: str = typer.Argument(..., help="What to ask."),
    model: str = typer.Option(
        None, "--model", "-m", metavar="PROVIDER/MODEL", help="Default: the config's model."
    ),
    system: str = typer.Option(None, "--system", help="A system prompt."),
    tool: list[str] = typer.Option(
        [], "--tool", "-t", help="Expose only this tool; repeatable. Default: every runnable tool."
    ),
    no_tools: bool = typer.Option(False, "--no-tools", help="Plain chat, no tools."),
    max_turns: int = typer.Option(10, "--max-turns", help="Model calls before giving up."),
    max_tokens: int = typer.Option(None, "--max-tokens", help="Output cap per model call."),
    effort: str = typer.Option(None, "--effort", help="Reasoning effort, if the model has it."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Approve every expensive tool call."),
    workdir: Path = typer.Option(
        None, "--workdir", help="Where tools write. Default: .amide/scratch/ask-<time>."
    ),
    no_stream: bool = typer.Option(False, "--no-stream", help="Print the reply once, whole."),
) -> None:
    """Ask a model once, with the tool registry at its disposal."""
    import json
    import sys

    from amide.harness.errors import HarnessError
    from amide.harness.registry import Registry
    from amide.harness.tool import ToolContext
    from amide.models import Message, Request, resolve
    from amide.models.loop import converse

    config = _config()
    try:
        provider, model_id = resolve(model, config)
        adapter = provider.adapter()
        registry = Registry.load(config)
        tools = []
        if not no_tools:
            if tool:
                tools = [registry.get(name).to_schema() for name in tool]
            else:
                tools = [spec.to_schema() for spec in registry if not Registry.missing(spec)]
    except HarnessError as error:
        typer.echo(f"amide ask: {error}", err=True)
        raise typer.Exit(2) from None

    if workdir is None:
        import time

        workdir = Path(".amide/scratch") / f"ask-{time.strftime('%Y%m%d-%H%M%S')}"
    workdir = workdir.resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    log_path = workdir / "log.txt"

    def log(message: str) -> None:
        from amide.harness.runs import now

        with log_path.open("a") as handle:
            handle.write(f"{now()} {message}\n")

    ctx = ToolContext(workdir=workdir, run_dir=workdir, log=log)
    request = Request(
        model=model_id,
        messages=[Message.user(prompt)],
        system=system,
        tools=tools,
        max_tokens=max_tokens or int(config.defaults.get("max_tokens") or 16000),
        effort=effort,
    )

    def on_text(piece: str) -> None:
        sys.stdout.write(piece)
        sys.stdout.flush()

    def on_call(call, result: str, ok: bool) -> None:
        args = ", ".join(f"{k}={v!r}" for k, v in call.arguments.items())
        verdict = "ok" if ok else "error"
        typer.echo(f"-> {call.name}({args}): {verdict} {result[:200]}", err=True)

    def approve(spec, args) -> bool:
        if not sys.stdin.isatty():
            return False
        return typer.confirm(f"{spec.name} is expensive. Run it?", err=True)

    try:
        outcome = converse(
            adapter,
            request,
            registry,
            ctx,
            yes=yes,
            approve=approve,
            on_text=on_text,
            on_call=on_call,
            max_turns=max_turns,
            stream=not no_stream,
        )
    except HarnessError as error:
        typer.echo(f"\namide ask: {error}", err=True)
        raise typer.Exit(1) from None
    if outcome.reply.text and not outcome.reply.text.endswith("\n"):
        sys.stdout.write("\n")
    (workdir / "transcript.json").write_text(
        json.dumps([_message_dict(m) for m in request.messages], indent=2, default=str)
    )
    usage = outcome.usage
    summary = (
        f"[{provider.name}/{outcome.reply.model or model_id}: {usage.input_tokens} in, "
        f"{usage.output_tokens} out, {len(outcome.calls)} tool call(s), "
        f"{outcome.turns} turn(s)]"
    )
    if outcome.calls:
        summary += f" files: {workdir}"
    typer.echo(summary, err=True)
    if outcome.stop == "end":
        raise typer.Exit()
    reasons = {
        "refusal": f"the model declined: {outcome.reply.detail}",
        "length": "the reply hit the max_tokens cap",
        "max_turns": f"stopped after {max_turns} turns; raise --max-turns",
    }
    typer.echo(f"amide ask: {reasons.get(outcome.stop, outcome.stop)}", err=True)
    raise typer.Exit(1)


def _message_dict(message) -> dict:
    entry = {"role": message.role, "content": message.content}
    if message.tool_calls:
        entry["tool_calls"] = [
            {"id": c.id, "name": c.name, "arguments": c.arguments} for c in message.tool_calls
        ]
    if message.tool_call_id:
        entry["tool_call_id"] = message.tool_call_id
        entry["name"] = message.name
        entry["is_error"] = message.is_error
    return entry


# --- experiment ------------------------------------------------------------


@app.command()
def experiment(
    question: str = typer.Argument(
        None, help="The question to investigate; with --resume, a follow-up message."
    ),
    model: str = typer.Option(
        None, "--model", "-m", metavar="PROVIDER/MODEL", help="Default: the config's model."
    ),
    resume: str = typer.Option(None, "--resume", metavar="ID", help="Continue an experiment."),
    interactive: bool = typer.Option(
        False, "--interactive", "-i", help="Chat with the orchestrator between its turns."
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Approve every expensive step."),
    max_tokens: int = typer.Option(None, "--max-tokens", help="Total token budget."),
    max_seconds: float = typer.Option(None, "--max-seconds", help="Wall-clock budget."),
    max_dollars: float = typer.Option(
        None, "--max-dollars", help="Dollar budget; needs [pricing] in the config."
    ),
    max_turns: int = typer.Option(40, "--max-turns", help="Model calls per agent."),
    runs_dir: Path = typer.Option(None, "--runs-dir", help="Where experiments are kept."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Hide sub-agent activity."),
    runner: str = typer.Option(
        None, "--runner", help="A [runners.*] name: protocol steps execute there."
    ),
    remote_cost: str = typer.Option(
        "moderate", "--remote-cost", help="With --runner: send steps of at least this cost."
    ),
) -> None:
    """Run an open experiment: an orchestrator and its sub-agents answer a question."""
    import sys

    from amide.agents.session import Budget, Experiment, Session
    from amide.harness.errors import HarnessError
    from amide.harness.registry import Registry
    from amide.harness.runs import RunStore
    from amide.models import resolve

    if resume is None and not question:
        typer.echo("amide experiment: give a question, or --resume ID", err=True)
        raise typer.Exit(2)
    config = _config()
    agents_config = config.agents
    try:
        store = RunStore(runs_dir or config.runs_dir)
        registry = Registry.load(config)
        if runner is not None and runner not in config.runners:
            raise HarnessError(
                f"no runner named {runner!r}; add [runners.{runner}] to {config.path}"
            )
        if resume is not None:
            state = store.get_any(resume)
            if not isinstance(state, Experiment):
                typer.echo(
                    f"amide experiment: {resume} is a protocol run, not an experiment", err=True
                )
                raise typer.Exit(2)
            if model:
                state.model = model
        else:
            resolve(model or agents_config.get("model"), config)  # fail early on a bad model
            budget = Budget(
                max_tokens=max_tokens or agents_config.get("max_tokens"),
                max_seconds=max_seconds or agents_config.get("max_seconds"),
                max_dollars=max_dollars or agents_config.get("max_dollars"),
            )
            state = Experiment.create(store.root, question, model, budget)
    except HarnessError as error:
        typer.echo(f"amide experiment: {error}", err=True)
        raise typer.Exit(2) from None
    typer.echo(f"experiment {state.id}: {state.dir}", err=True)

    def on_text(piece: str) -> None:
        sys.stdout.write(piece)
        sys.stdout.flush()

    def on_event(kind: str, record, payload) -> None:
        tag = f"[{record.role} #{record.n}]"
        if kind == "agent_start" and record.n > 1:
            typer.echo(f"{tag} {record.task[:120]}", err=True)
        elif kind == "agent_done" and record.n > 1:
            typer.echo(
                f"{tag} {record.status}: {(record.result or record.error or '')[:200]}", err=True
            )
        elif kind == "call":
            call, result, ok = payload
            if record.n > 1 and quiet:
                return
            args = ", ".join(f"{k}={str(v)[:60]!r}" for k, v in call.arguments.items())
            typer.echo(
                f"{tag} -> {call.name}({args}): {'ok' if ok else 'error'} {result[:160]}", err=True
            )
        elif kind == "run" and not quiet:
            typer.echo(f"{tag}    {payload}", err=True)

    def approve(spec, args) -> bool:
        if not sys.stdin.isatty():
            return False
        return typer.confirm(f"{spec.name} is expensive. Run it?", err=True)

    def ask_user() -> str | None:
        sys.stdout.write("\n")
        try:
            return typer.prompt("you", prompt_suffix="> ", default="", show_default=False)
        except (EOFError, typer.Abort):
            return None

    session = Session(
        state,
        registry,
        config,
        model=model,
        yes=yes,
        approve=approve,
        on_text=on_text,
        on_event=on_event,
        ask_user=ask_user if interactive else None,
        max_turns=max_turns,
        runner=runner,
        remote_cost=remote_cost,
    )
    state = session.run(question if resume is not None else None)
    sys.stdout.write("\n")
    typer.echo(
        f"experiment {state.id}: {state.status}; {len(state.agents)} agent(s), "
        f"{state.input_tokens} in, {state.output_tokens} out, {state.seconds:.0f}s"
        + (f", ${state.dollars:.2f}" if state.dollars else ""),
        err=True,
    )
    if state.error:
        typer.echo(f"  {state.error}", err=True)
    for name in ("abstract", "methodology", "results"):
        if name in state.documents:
            typer.echo(f"  {name}: {state.documents[name]}", err=True)
    if state.status == "done":
        raise typer.Exit()
    if state.status in ("paused", "budget_exceeded"):
        raise typer.Exit(RESUMABLE_EXIT)
    raise typer.Exit(1)


# --- models ----------------------------------------------------------------


@models_app.command("list")
def models_list(
    remote: bool = typer.Option(
        False, "--remote", help="Also ask each provider that has a key which models it serves."
    ),
    provider: str = typer.Option(None, "--provider", "-p", help="Only this provider."),
) -> None:
    """List providers, whether their key is set, and (with --remote) their models."""
    from amide.harness.errors import HarnessError
    from amide.models import providers

    config = _config()
    try:
        known = providers(config)
    except HarnessError as error:
        typer.echo(f"amide models list: {error}", err=True)
        raise typer.Exit(2) from None
    if provider and provider not in known:
        typer.echo(f"amide models list: unknown provider {provider!r}", err=True)
        raise typer.Exit(2)
    default = config.defaults.get("model")
    if default:
        typer.echo(f"default: {default}")
    failed = 0
    for name in sorted(known):
        if provider and name != provider:
            continue
        entry = known[name]
        typer.echo(f"{name:<12} {entry.kind:<10} {entry.base_url:<48} {entry.key_status}")
        if not remote or not entry.usable:
            continue
        try:
            for model_id in entry.adapter().list_models():
                typer.echo(f"    {name}/{model_id}")
        except HarnessError as error:
            failed += 1
            typer.echo(f"    {error}", err=True)
    if failed:
        raise typer.Exit(1)


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


@tools_app.command("run")
def tools_run(
    name: str = typer.Argument(..., help="A tool name."),
    inputs: Path = typer.Option(None, "--inputs", help="A JSON file of inputs."),
    set_: list[str] = typer.Option(
        [], "--set", "-s", metavar="NAME=VALUE", help="An input; repeatable."
    ),
    workdir: Path = typer.Option(
        None, "--workdir", help="Where the tool writes. Default: the current directory."
    ),
    run_dir: Path = typer.Option(None, "--run-dir", help="The run directory; default: workdir."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Print only the outputs JSON."),
) -> None:
    """Run one tool by itself; outputs go to stdout and to outputs.json in the workdir."""
    import json

    from amide.harness.errors import HarnessError
    from amide.harness.protocol import parse_overrides
    from amide.harness.registry import Registry
    from amide.harness.tool import ToolContext

    registry = _registry()
    try:
        spec = registry.get(name)
        given = {}
        if inputs is not None:
            try:
                given = json.loads(inputs.read_text())
            except (OSError, json.JSONDecodeError) as error:
                raise HarnessError(f"cannot read inputs {inputs}: {error}") from None
            if not isinstance(given, dict):
                raise HarnessError(f"{inputs} must hold a JSON object")
        given.update(parse_overrides(set_))
        args = spec.validate_inputs(given)
        missing = Registry.missing(spec)
        if missing:
            raise HarnessError(f"{name} needs {', '.join(missing)}. {spec.requires.hint}".strip())
    except HarnessError as error:
        typer.echo(f"amide tools run: {error}", err=True)
        raise typer.Exit(2) from None
    workdir = (workdir or Path.cwd()).resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    log_path = workdir / "log.txt"

    def log(message: str) -> None:
        with log_path.open("a") as handle:
            handle.write(f"{message}\n")
        if not quiet:
            typer.echo(message, err=True)

    ctx = ToolContext(
        workdir=workdir, run_dir=(run_dir or workdir).resolve(), log=log, timeout=spec.timeout
    )
    try:
        outputs = spec.run(ctx, args)
    except HarnessError as error:
        typer.echo(f"amide tools run: {error}", err=True)
        raise typer.Exit(1) from None
    except Exception as error:  # a tool bug is still a failed run, reported not dumped
        import traceback

        log(traceback.format_exc())
        typer.echo(f"amide tools run: {name} crashed: {type(error).__name__}: {error}", err=True)
        raise typer.Exit(1) from None
    text = json.dumps(outputs, indent=2, default=str)
    (workdir / "outputs.json").write_text(text)
    typer.echo(text)


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
    """List runs and experiments, newest first."""
    from amide.agents.session import Experiment
    from amide.harness.runs import RunStore

    store = RunStore(runs_dir or _config().runs_dir)
    rows = [state.summary() for state in store.list()]
    rows += [state.summary() for state in Experiment.list(store.root)]
    for row in sorted(rows, key=lambda r: (r["created"], r["id"]), reverse=True):
        typer.echo(f"{row['id']}  {row['status']:<16} {row['steps']:<9} {row['protocol']}")


@runs_app.command("show")
def runs_show(
    run: str = typer.Argument(..., help="A run id, or a unique prefix of one."),
    runs_dir: Path = typer.Option(None, "--runs-dir"),
    log_lines: int = typer.Option(10, "--log", help="How many lines of run.log to show."),
) -> None:
    """Show a run's status, steps, checks, and log tail (or an experiment's agents)."""
    from amide.agents.session import Experiment
    from amide.harness.errors import HarnessError
    from amide.harness.runs import RunStore

    store = RunStore(runs_dir or _config().runs_dir)
    try:
        state = store.get_any(run)
    except HarnessError as error:
        typer.echo(f"amide runs show: {error}", err=True)
        raise typer.Exit(2) from None
    if isinstance(state, Experiment):
        _show_experiment(state, log_lines)
        return
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


def _show_experiment(state, log_lines: int) -> None:
    typer.echo(f"{state.id}: experiment, {state.status}")
    typer.echo(f"  question: {state.question}")
    if state.error:
        typer.echo(f"  {state.error}")
    typer.echo(f"  dir: {state.dir}")
    typer.echo(
        f"  usage: {state.input_tokens} in, {state.output_tokens} out, {state.seconds:.0f}s"
        + (f", ${state.dollars:.2f}" if state.dollars else "")
    )
    for record in state.agents:
        parent = f" (from #{record.parent})" if record.parent else ""
        error = f"  {record.error}" if record.error else ""
        typer.echo(
            f"  #{record.n} {record.role:<16} {record.status:<16} {record.turns} turns, "
            f"{record.calls} calls{parent}{error}"
        )
    for name, path in state.documents.items():
        typer.echo(f"  {name}: {path}")
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
