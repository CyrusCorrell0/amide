"""Run a LAMMPS input script."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from amide.harness.errors import ToolError
from amide.harness.tool import Param, Requirements, ToolContext, tool


@tool(
    name="lammps_run",
    description=(
        "Run a LAMMPS input script with the lmp executable. The script and any data file "
        "are copied into the step directory, so relative paths inside the script resolve "
        "there. Variables are passed with -var."
    ),
    inputs=[
        Param("input", "path", "The LAMMPS input script.", required=True),
        Param("data", "path", "A data file the script reads, copied alongside it.", default=None),
        Param("variables", "object", "Name to value, passed as -var name value.", default={}),
        Param("extra_args", "list", "Additional command-line arguments.", default=[]),
        Param(
            "executable",
            "string",
            "The LAMMPS binary; symlink yours to lmp if it has another name.",
            default="lmp",
        ),
    ],
    outputs=[
        Param("log", "path", "log.lammps from the run."),
        Param("stdout", "string"),
        Param("returncode", "integer"),
        Param("seconds", "number"),
        Param("files", "list", "Files the run left in the step directory."),
    ],
    cost="expensive",
    requires=Requirements(
        commands=("lmp",),
        hint="Install LAMMPS (https://docs.lammps.org/Install.html) and put lmp on PATH.",
    ),
    tags=("simulation", "lammps", "md"),
)
def lammps_run(
    ctx: ToolContext,
    input: str,
    data: str | None = None,
    variables: dict[str, Any] | None = None,
    extra_args: list[str] | None = None,
    executable: str = "lmp",
) -> dict[str, Any]:
    from amide.tools._proc import require, run

    script = Path(input)
    if not script.is_file():
        raise ToolError(f"lammps_run: {input} does not exist")
    require(executable, "lammps_run")
    local_script = ctx.workdir / script.name
    shutil.copyfile(script, local_script)
    if data:
        data_file = Path(data)
        if not data_file.is_file():
            raise ToolError(f"lammps_run: {data} does not exist")
        shutil.copyfile(data_file, ctx.workdir / data_file.name)
    argv = [executable, "-in", script.name, "-log", "log.lammps"]
    for name, value in (variables or {}).items():
        argv += ["-var", str(name), str(value)]
    argv += [str(arg) for arg in extra_args or []]
    result = run(ctx, argv, name="lammps_run")
    log = ctx.workdir / "log.lammps"
    return {
        "log": str(log) if log.exists() else None,
        "stdout": result["stdout"],
        "returncode": result["returncode"],
        "seconds": result["seconds"],
        "files": sorted(p.name for p in ctx.workdir.iterdir() if p.is_file()),
    }
