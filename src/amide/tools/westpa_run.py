"""Run a WESTPA weighted-ensemble simulation."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from amide.harness.errors import ToolError
from amide.harness.tool import Param, Requirements, ToolContext, tool


@tool(
    name="westpa_run",
    description=(
        "Run a WESTPA weighted-ensemble simulation from a prepared project directory "
        "(west.cfg, system definition, basis states, propagator scripts). The directory is "
        "copied into the step, then w_init and w_run execute there."
    ),
    inputs=[
        Param("directory", "path", "The WESTPA project directory.", required=True),
        Param("config", "string", "Config file name inside the directory.", default="west.cfg"),
        Param("init", "boolean", "Run w_init before w_run.", default=True),
        Param("max_iterations", "integer", "Stop after this many iterations.", default=None),
        Param("work_manager", "string", "w_run work manager.", default="processes"),
        Param("n_workers", "integer", "Workers for the work manager.", default=None),
        Param("init_args", "list", "Extra arguments for w_init.", default=[]),
    ],
    outputs=[
        Param("h5", "path", "west.h5 with the ensemble data."),
        Param("project", "path", "The copied project directory."),
        Param("stdout", "string"),
        Param("returncode", "integer"),
        Param("seconds", "number"),
    ],
    cost="expensive",
    requires=Requirements(
        python=("westpa",),
        commands=("w_run",),
        hint="Install with: pip install 'amide[westpa]'",
    ),
    tags=("simulation", "westpa", "enhanced-sampling"),
)
def westpa_run(
    ctx: ToolContext,
    directory: str,
    config: str = "west.cfg",
    init: bool = True,
    max_iterations: int | None = None,
    work_manager: str = "processes",
    n_workers: int | None = None,
    init_args: list[str] | None = None,
) -> dict[str, Any]:
    from amide.tools._proc import require

    source = Path(directory)
    if not source.is_dir():
        raise ToolError(f"westpa_run: {directory} is not a directory")
    if not (source / config).is_file():
        raise ToolError(f"westpa_run: {directory} has no {config}")
    project = ctx.workdir / "project"
    if project.exists():
        shutil.rmtree(project)
    shutil.copytree(source, project)
    env = {"WEST_SIM_ROOT": str(project)}
    seconds = 0.0
    if init:
        require("w_init", "westpa_run")
        init_argv = ["w_init", "--rcfile", config, *[str(a) for a in init_args or []]]
        ctx.log("w_init")
        seconds += _in(project, ctx, init_argv, env, "westpa_run w_init")["seconds"]
    require("w_run", "westpa_run")
    argv = ["w_run", "--rcfile", config, "--work-manager", work_manager]
    if n_workers:
        argv += ["--n-workers", str(n_workers)]
    if max_iterations:
        argv += ["--max-iterations", str(max_iterations)]
    result = _in(project, ctx, argv, env, "westpa_run w_run")
    seconds += result["seconds"]
    h5 = project / "west.h5"
    return {
        "h5": str(h5) if h5.exists() else None,
        "project": str(project),
        "stdout": result["stdout"],
        "returncode": result["returncode"],
        "seconds": round(seconds, 3),
    }


def _in(
    project: Path, ctx: ToolContext, argv: list[str], env: dict[str, str], name: str
) -> dict[str, Any]:
    from dataclasses import replace

    from amide.tools._proc import run

    return run(replace(ctx, workdir=project), argv, name=name, env=env)
