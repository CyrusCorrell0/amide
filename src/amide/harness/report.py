"""results.json and report.md for a run."""

from __future__ import annotations

import json
from typing import Any

from amide.harness.protocol import Protocol
from amide.harness.runs import RunState


def write_results(state: RunState, protocol: Protocol) -> None:
    results = {
        "run": state.id,
        "protocol": {"name": protocol.name, "version": protocol.version},
        "status": state.status,
        "error": state.error,
        "params": state.params,
        "steps": {
            step_id: {
                "tool": step.tool,
                "status": step.status,
                "seconds": step.seconds,
                "outputs": step.outputs,
                "error": step.error,
            }
            for step_id, step in state.steps.items()
        },
        "checks": state.checks,
        "outputs": state.outputs,
    }
    (state.dir / "results.json").write_text(json.dumps(results, indent=2, default=str))
    (state.dir / "report.md").write_text(render_report(state, protocol))


def render_report(state: RunState, protocol: Protocol) -> str:
    lines = [f"# {protocol.name}: run {state.id}", ""]
    if protocol.description:
        lines += [protocol.description, ""]
    lines += [f"Status: **{state.status}**"]
    if state.error:
        lines += ["", state.error]
    lines += ["", "## Parameters", ""]
    if state.params:
        lines += ["| name | value |", "|---|---|"]
        lines += [f"| {name} | `{_cell(value)}` |" for name, value in state.params.items()]
    else:
        lines += ["none"]
    lines += ["", "## Steps", "", "| step | tool | status | seconds |", "|---|---|---|---|"]
    for step in state.steps.values():
        seconds = "" if step.seconds is None else f"{step.seconds:.1f}"
        lines.append(f"| {step.id} | {step.tool} | {step.status} | {seconds} |")
    for step in state.steps.values():
        if step.error:
            lines += ["", f"`{step.id}` failed: {step.error}"]
    if protocol.checks:
        lines += [
            "",
            "## Checks",
            "",
            "| check | result | expression | value |",
            "|---|---|---|---|",
        ]
        by_id = {entry["id"]: entry for entry in state.checks}
        for check in protocol.checks:
            entry = by_id.get(check.id)
            if entry is None:
                lines.append(f"| {check.id} | not run | `{check.expr}` | |")
                continue
            result = "pass" if entry.get("passed") else "FAIL"
            value = entry.get("error") or _cell(entry.get("value"))
            lines.append(f"| {check.id} | {result} | `{check.expr}` | {value} |")
    if state.outputs:
        lines += ["", "## Outputs", "", "| name | value |", "|---|---|"]
        lines += [f"| {name} | {_cell(value)} |" for name, value in state.outputs.items()]
    lines += ["", "## Step outputs", ""]
    for step in state.steps.values():
        if not step.outputs:
            continue
        lines += [f"### {step.id}", ""]
        for name, value in step.outputs.items():
            if name in ("stdout", "stderr"):
                continue
            lines.append(f"- {name}: {_cell(value)}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _cell(value: Any) -> str:
    if isinstance(value, float):
        text = f"{value:.4g}"
    elif isinstance(value, (dict, list)):
        text = json.dumps(value, default=str)
    else:
        text = str(value)
    text = text.replace("\n", " ").replace("|", "\\|")
    return text if len(text) <= 120 else text[:117] + "..."
