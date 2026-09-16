"""Execute a protocol step by step inside a run directory."""

from __future__ import annotations

import os
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from amide.harness.errors import (
    ApprovalRequired,
    ExpressionError,
    HarnessError,
    MissingRequirements,
    ValidationError,
)
from amide.harness.expr import evaluate, render
from amide.harness.protocol import Protocol, Step
from amide.harness.registry import Registry
from amide.harness.remote import Runner
from amide.harness.report import write_results
from amide.harness.runs import RunState, now
from amide.harness.tool import COSTS, ToolContext, ToolSpec

Approver = Callable[[Step, ToolSpec, dict[str, Any]], bool]
Reporter = Callable[[str], None]


@dataclass
class RunOptions:
    yes: bool = False
    approve: Approver | None = None
    max_seconds: float | None = None
    env: dict[str, str] = field(default_factory=dict)
    echo: Reporter = lambda message: None
    # Where steps execute: ``runners`` by name (``local`` is always there);
    # ``runner`` is the default for steps costing at least ``remote_cost``.
    runners: dict[str, Runner] = field(default_factory=lambda: {"local": Runner()})
    runner: str | None = None
    remote_cost: str = "moderate"

    def runner_for(self, step: Step, spec: ToolSpec) -> Runner:
        name = step.runner
        if name is None:
            remote = self.runner and COSTS.index(spec.cost) >= COSTS.index(self.remote_cost)
            name = self.runner if remote else "local"
        try:
            return self.runners[name]
        except KeyError:
            raise HarnessError(
                f"step {step.id}: no runner named {name!r}; configured: "
                f"{', '.join(sorted(self.runners))}"
            ) from None


def execute(
    state: RunState,
    protocol: Protocol,
    registry: Registry,
    options: RunOptions | None = None,
) -> RunState:
    """Run every step that is not already done, then the checks and outputs.

    The state is saved after every step, so a killed run resumes where it
    stopped. Returns the final state; its ``status`` says how it went.
    """
    options = options or RunOptions()
    started_at = time.monotonic()
    state.status = "running"
    state.started = state.started or now()
    state.error = None
    state.save()
    _say(state, options, f"run {state.id}: {protocol.name}")

    context = _context(state, options)
    try:
        for step in protocol.steps:
            step_state = state.steps[step.id]
            if step_state.status == "done":
                context["steps"][step.id] = step_state.outputs
                _say(state, options, f"  {step.id}: done (resumed)")
                continue
            if step_state.status == "skipped":
                _say(state, options, f"  {step.id}: skipped (resumed)")
                continue
            if _over_budget(started_at, options):
                state.status = "budget_exceeded"
                state.error = f"wall-clock budget of {options.max_seconds}s spent before {step.id}"
                _finish(state, options)
                return state
            _run_step(state, step, registry, options, context)
        _checks(state, protocol, context)
        _outputs(state, protocol, context)
    except ApprovalRequired as error:
        state.status = "paused"
        state.error = str(error)
    except HarnessError as error:
        state.status = "error"
        state.error = str(error)
    write_results(state, protocol)
    _finish(state, options)
    return state


def _run_step(
    state: RunState,
    step: Step,
    registry: Registry,
    options: RunOptions,
    context: dict[str, Any],
) -> None:
    step_state = state.steps[step.id]
    spec = registry.get(step.tool)
    if step.when is not None and not evaluate(step.when, context):
        step_state.status = "skipped"
        state.save()
        _say(state, options, f"  {step.id}: skipped ({step.when})")
        return
    try:
        args = spec.validate_inputs(render(step.with_, context))
    except (ExpressionError, ValidationError) as error:
        raise type(error)(f"step {step.id}: {error}") from None
    missing = registry.missing(spec)
    if missing:
        raise MissingRequirements(f"step {step.id} ({spec.name})", missing, spec.requires.hint)
    approved = options.yes or (options.approve is not None and options.approve(step, spec, args))
    if spec.cost == "expensive" and not approved:
        raise ApprovalRequired(
            f"step {step.id} ({spec.name}) is expensive and was not approved; "
            f"resume with `amide run --resume {state.id} --yes`"
        )

    workdir = state.step_dir(step.id)
    workdir.mkdir(parents=True, exist_ok=True)
    log_path = workdir / "log.txt"

    def log(message: str) -> None:
        with log_path.open("a") as handle:
            handle.write(f"{now()} {message}\n")

    ctx = ToolContext(
        workdir=workdir,
        run_dir=state.dir,
        log=log,
        env=options.env,
        timeout=spec.timeout,
    )
    runner = options.runner_for(step, spec)
    step_state.runner = runner.name
    step_state.status = "running"
    step_state.started = now()
    step_state.error = None
    state.save()
    where = "" if runner.name == "local" else f" on {runner.name}"
    _say(state, options, f"  {step.id}: {spec.name}{where} ...")
    clock = time.monotonic()
    try:
        outputs = runner.run_step(spec, ctx, args)
    except HarnessError as error:
        _fail_step(state, step_state, clock, str(error))
        raise
    except Exception as error:  # a tool bug is still a step failure
        log(traceback.format_exc())
        _fail_step(state, step_state, clock, f"{type(error).__name__}: {error}")
        raise HarnessError(f"step {step.id} ({spec.name}) failed: {error}") from None
    if not isinstance(outputs, dict):
        _fail_step(state, step_state, clock, "tool returned something other than a dict")
        raise HarnessError(f"step {step.id} ({spec.name}) returned {type(outputs).__name__}")
    step_state.outputs = _jsonable(outputs)
    step_state.status = "done"
    step_state.finished = now()
    step_state.seconds = round(time.monotonic() - clock, 3)
    (workdir / "outputs.json").write_text(_dumps(step_state.outputs))
    context["steps"][step.id] = step_state.outputs
    state.save()
    _say(state, options, f"  {step.id}: done in {step_state.seconds}s")


def _fail_step(state: RunState, step_state: Any, clock: float, message: str) -> None:
    step_state.status = "error"
    step_state.error = message
    step_state.finished = now()
    step_state.seconds = round(time.monotonic() - clock, 3)
    state.save()


def _checks(state: RunState, protocol: Protocol, context: dict[str, Any]) -> None:
    results = []
    for check in protocol.checks:
        entry: dict[str, Any] = {
            "id": check.id,
            "expr": check.expr,
            "description": check.description,
        }
        try:
            value = evaluate(check.expr, context)
            entry["passed"] = bool(value)
            entry["value"] = _jsonable(value)
        except ExpressionError as error:
            entry["passed"] = False
            entry["error"] = str(error)
        results.append(entry)
    state.checks = results
    state.status = "passed" if all(entry["passed"] for entry in results) else "failed"
    if state.status == "failed":
        failed = ", ".join(entry["id"] for entry in results if not entry["passed"])
        state.error = f"checks failed: {failed}"


def _outputs(state: RunState, protocol: Protocol, context: dict[str, Any]) -> None:
    outputs: dict[str, Any] = {}
    for name, template in protocol.outputs.items():
        try:
            outputs[name] = _jsonable(render(template, context))
        except ExpressionError as error:
            outputs[name] = None
            state.log(f"output {name}: {error}")
    state.outputs = outputs


def _context(state: RunState, options: RunOptions) -> dict[str, Any]:
    return {
        "params": dict(state.params),
        "steps": {},
        "run": {"id": state.id, "dir": str(state.dir)},
        "env": {**os.environ, **options.env},
    }


def _over_budget(started_at: float, options: RunOptions) -> bool:
    return options.max_seconds is not None and time.monotonic() - started_at > options.max_seconds


def _finish(state: RunState, options: RunOptions) -> None:
    state.finished = now()
    state.save()
    message = f"run {state.id}: {state.status}"
    if state.error:
        message += f": {state.error}"
    _say(state, options, message)


def _say(state: RunState, options: RunOptions, message: str) -> None:
    state.log(message)
    options.echo(message)


def _jsonable(value: Any) -> Any:
    import json

    return json.loads(json.dumps(value, default=str))


def _dumps(value: Any) -> str:
    import json

    return json.dumps(value, indent=2, default=str)
