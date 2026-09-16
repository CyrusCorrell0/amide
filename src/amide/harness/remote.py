"""Run a step on another machine through a CLI the user already has.

amide manages no cloud. A ``[runners.<name>]`` table in the config gives
three command templates and the runner does, per step:

1. ``exec``: make the remote root and clear any stale copy of this run;
2. ``copy_to``: copy the whole run directory to ``<root>/<run-id>``;
3. ``exec``: ``<python> -m amide tools run <tool> --inputs ... --workdir ...``
   in the remote copy, which writes the step's ``outputs.json`` there;
4. ``copy_from``: copy the remote step directory back over the local one.

Paths inside the inputs that point into the local run directory are
rewritten to the remote copy on the way out, and back on the way in, so
tools see ordinary local paths on both sides.

Templates get ``{src}``, ``{dst}``, ``{host}``, and ``{command}`` (already
shell-quoted); they run through the local shell.
"""

from __future__ import annotations

import json
import shlex
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any

from amide.harness.errors import HarnessError, ToolError
from amide.harness.tool import ToolContext, ToolSpec

REQUIRED = ("copy_to", "copy_from", "exec")


class Runner:
    """Where a step executes. The local runner just calls the tool."""

    def __init__(self, name: str = "local") -> None:
        self.name = name

    def run_step(self, spec: ToolSpec, ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
        return spec.run(ctx, args)


@dataclass
class CommandRunner(Runner):
    name: str
    copy_to: str
    copy_from: str
    exec: str
    host: str = ""
    python: str = "python3"
    root: str = "/tmp/amide"
    timeout: float | None = None
    env: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_config(cls, name: str, data: dict[str, Any]) -> CommandRunner:
        missing = [key for key in REQUIRED if not data.get(key)]
        if missing:
            raise HarnessError(f"runner {name}: [runners.{name}] needs {', '.join(missing)}")
        return cls(
            name=name,
            copy_to=str(data["copy_to"]),
            copy_from=str(data["copy_from"]),
            exec=str(data["exec"]),
            host=str(data.get("host") or ""),
            python=str(data.get("python") or "python3"),
            root=str(data.get("root") or "/tmp/amide"),
            timeout=float(data["timeout"]) if data.get("timeout") else None,
            env={str(k): str(v) for k, v in (data.get("env") or {}).items()},
        )

    def run_step(self, spec: ToolSpec, ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
        run_dir = ctx.run_dir.resolve()
        step_dir = ctx.workdir.resolve()
        try:
            relative = step_dir.relative_to(run_dir)
        except ValueError:
            raise HarnessError(
                f"runner {self.name}: the step directory {step_dir} is not inside the run "
                f"directory {run_dir}"
            ) from None
        remote_run = PurePosixPath(self.root) / run_dir.name
        remote_step = remote_run / relative.as_posix()

        step_dir.mkdir(parents=True, exist_ok=True)
        (step_dir / "inputs.json").write_text(
            json.dumps(_translate(args, str(run_dir), str(remote_run)), indent=2, default=str)
        )
        ctx.log(f"runner {self.name}: copying {run_dir} to {self.host or 'remote'}:{remote_run}")
        self._exec(
            ctx,
            f"mkdir -p {shlex.quote(str(remote_run.parent))} && rm -rf {shlex.quote(str(remote_run))}",
        )
        self._shell(ctx, self.copy_to, src=str(run_dir), dst=str(remote_run))

        command = (
            f"cd {shlex.quote(str(remote_run))} && {self.python} -m amide tools run "
            f"{shlex.quote(spec.name)} --inputs {shlex.quote(str(remote_step / 'inputs.json'))} "
            f"--workdir {shlex.quote(str(remote_step))} --run-dir {shlex.quote(str(remote_run))} "
            f"--quiet"
        )
        ctx.log(f"runner {self.name}: {command}")
        self._exec(ctx, command)

        staging = step_dir.with_name(step_dir.name + ".remote")
        shutil.rmtree(staging, ignore_errors=True)
        self._shell(ctx, self.copy_from, src=str(remote_step), dst=str(staging))
        if not staging.is_dir():
            raise ToolError(f"runner {self.name}: copy_from did not produce {staging}")
        outputs_path = staging / "outputs.json"
        if not outputs_path.is_file():
            raise ToolError(
                f"runner {self.name}: the remote step left no outputs.json in {remote_step}"
            )
        for entry in staging.iterdir():
            target = step_dir / entry.name
            if entry.name == "log.txt" and target.is_file():
                # Keep this side's log and append what the remote step wrote.
                with target.open("a") as handle:
                    handle.write(entry.read_text())
                continue
            if target.is_dir():
                shutil.rmtree(target)
            elif target.exists():
                target.unlink()
            shutil.move(str(entry), str(target))
        shutil.rmtree(staging, ignore_errors=True)
        try:
            outputs = json.loads((step_dir / "outputs.json").read_text())
        except json.JSONDecodeError as error:
            raise ToolError(f"runner {self.name}: outputs.json is not JSON: {error}") from None
        return _translate(outputs, str(remote_run), str(run_dir))

    # ---

    def _shell(self, ctx: ToolContext, template: str, **fields: str) -> None:
        try:
            command = template.format(host=self.host, command="", **fields)
        except (KeyError, IndexError) as error:
            raise HarnessError(f"runner {self.name}: bad template {template!r}: {error}") from None
        self._run(ctx, command)

    def _exec(self, ctx: ToolContext, remote_command: str) -> None:
        try:
            command = self.exec.format(
                host=self.host, command=shlex.quote(remote_command), src="", dst=""
            )
        except (KeyError, IndexError) as error:
            raise HarnessError(f"runner {self.name}: bad exec template: {error}") from None
        self._run(ctx, command)

    def _run(self, ctx: ToolContext, command: str) -> None:
        import os

        try:
            done = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                env={**os.environ, **ctx.env, **self.env},
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise ToolError(
                f"runner {self.name}: timed out after {self.timeout}s: {command}"
            ) from None
        if done.stdout.strip():
            ctx.log(done.stdout.rstrip())
        if done.stderr.strip():
            ctx.log(done.stderr.rstrip())
        if done.returncode != 0:
            tail = (done.stderr or done.stdout).strip().splitlines()[-5:]
            raise ToolError(
                f"runner {self.name}: `{command}` exited {done.returncode}"
                + (": " + " | ".join(tail) if tail else "")
            )


def runners_from_config(config: Any) -> dict[str, Runner]:
    runners: dict[str, Runner] = {"local": Runner()}
    if config is None:
        return runners
    for name, data in config.runners.items():
        if any(data.get(key) for key in REQUIRED):
            runners[name] = CommandRunner.from_config(name, data)
    return runners


def _translate(value: Any, old: str, new: str) -> Any:
    """Rewrite path strings that start with ``old`` so they start with ``new``."""
    if isinstance(value, str):
        if value == old or value.startswith(old + "/"):
            return new + value[len(old) :]
        return value
    if isinstance(value, list):
        return [_translate(item, old, new) for item in value]
    if isinstance(value, dict):
        return {key: _translate(item, old, new) for key, item in value.items()}
    return value
