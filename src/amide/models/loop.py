"""The tool-calling loop: call the model, run what it asks for, repeat.

The model decides; the registry runs. Every tool result goes back to the
model as text, errors included, so it can recover or explain.
"""

from __future__ import annotations

import json
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from amide.harness.errors import HarnessError, ValidationError
from amide.harness.registry import Registry
from amide.harness.tool import ToolContext, ToolSpec
from amide.models.base import Adapter, Message, Reply, Request, ToolCall, Usage

Approver = Callable[[ToolSpec, dict[str, Any]], bool]
CallSink = Callable[[ToolCall, str, bool], None]


@dataclass
class CallRecord:
    call: ToolCall
    ok: bool
    seconds: float
    result: str


@dataclass
class Outcome:
    reply: Reply
    usage: Usage = field(default_factory=Usage)
    calls: list[CallRecord] = field(default_factory=list)
    turns: int = 0
    stop: str = "end"  # a Reply stop, "max_turns", or "stopped"
    detail: str = ""


def converse(
    adapter: Adapter,
    request: Request,
    registry: Registry,
    ctx: ToolContext,
    *,
    yes: bool = False,
    approve: Approver | None = None,
    on_text: Callable[[str], None] | None = None,
    on_call: CallSink | None = None,
    max_turns: int = 10,
    stream: bool = True,
    stop_if: Callable[[], str | None] | None = None,
    after_turn: Callable[[Reply], None] | None = None,
    after_results: Callable[[], None] | None = None,
    annotate: Callable[[], str | None] | None = None,
) -> Outcome:
    """Drive ``request`` to a final reply, appending every turn to its messages.

    ``stop_if`` runs before each model call and returns a reason to stop
    instead (a spent budget); ``after_turn`` sees every reply as soon as it is
    appended (to account for it); ``annotate`` runs after the tool calls of a
    turn and its text, if any, is appended to the last tool result so the
    model reads it next; ``after_results`` runs once those results are
    appended (to checkpoint).
    """
    outcome = Outcome(reply=Reply())
    for _ in range(max_turns):
        if stop_if is not None:
            reason = stop_if()
            if reason:
                outcome.stop, outcome.detail = "stopped", reason
                return outcome
        outcome.turns += 1
        if stream:
            reply = adapter.stream(request, on_text or (lambda piece: None))
        else:
            reply = adapter.complete(request)
            if on_text and reply.text:
                on_text(reply.text)
        outcome.reply = reply
        outcome.usage = outcome.usage + reply.usage
        request.messages.append(reply.message())
        outcome.stop = reply.stop
        if after_turn is not None:
            after_turn(reply)
        if reply.stop != "tool_calls":
            return outcome
        results = []
        for call in reply.tool_calls:
            record = run_call(registry, call, ctx, yes=yes, approve=approve)
            outcome.calls.append(record)
            if on_call:
                on_call(call, record.result, record.ok)
            results.append(Message.tool_result(call, record.result, is_error=not record.ok))
        if annotate is not None and results:
            note = annotate()
            if note:
                results[-1].content += f"\n\n[harness note: {note}]"
        request.messages.extend(results)
        if after_results is not None:
            after_results()
    outcome.stop = "max_turns"
    return outcome


def run_call(
    registry: Registry,
    call: ToolCall,
    ctx: ToolContext,
    *,
    yes: bool = False,
    approve: Approver | None = None,
) -> CallRecord:
    """Run one tool call. Never raises: every failure becomes an error result."""
    clock = time.monotonic()

    def done(result: str, ok: bool) -> CallRecord:
        ctx.log(f"{'ok' if ok else 'error'} {call.name}: {result[:500]}")
        return CallRecord(call, ok, round(time.monotonic() - clock, 3), result)

    if call.error:
        return done(call.error, False)
    if call.name not in registry:
        return done(
            f"there is no tool named {call.name!r}; available: {', '.join(registry.names())}", False
        )
    spec = registry.get(call.name)
    try:
        args = spec.validate_inputs(call.arguments)
    except ValidationError as error:
        return done(str(error), False)
    missing = registry.missing(spec)
    if missing:
        hint = f" {spec.requires.hint}" if spec.requires.hint else ""
        return done(f"{spec.name} cannot run here: missing {', '.join(missing)}.{hint}", False)
    if spec.cost == "expensive" and not (yes or (approve is not None and approve(spec, args))):
        return done(
            f"{spec.name} is expensive and was not approved; the user must allow it "
            "(for instance by rerunning with --yes)",
            False,
        )
    ctx.log(f"call {call.name} {json.dumps(args, default=str)}")
    try:
        outputs = spec.run(ctx, args)
    except HarnessError as error:
        return done(str(error), False)
    except Exception as error:  # a tool bug is the tool's failure, not the session's
        ctx.log(traceback.format_exc())
        return done(f"{spec.name} crashed: {type(error).__name__}: {error}", False)
    if not isinstance(outputs, dict):
        return done(f"{spec.name} returned {type(outputs).__name__}, not a dict", False)
    return done(json.dumps(outputs, default=str), True)
