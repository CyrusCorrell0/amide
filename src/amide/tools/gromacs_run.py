"""Run a GROMACS subcommand."""

from __future__ import annotations

from typing import Any

from amide.harness.tool import Param, Requirements, ToolContext, tool


@tool(
    name="gromacs_run",
    description=(
        "Run one gmx subcommand (pdb2gmx, editconf, solvate, grompp, mdrun, ...) in the step "
        "directory. Interactive selections go through stdin. Chain several steps to build "
        "a full GROMACS workflow."
    ),
    inputs=[
        Param("subcommand", "string", "For example pdb2gmx or mdrun.", required=True),
        Param("args", "list", "Arguments after the subcommand.", default=[]),
        Param("stdin", "string", "Text fed to the command, for interactive prompts.", default=None),
        Param("executable", "string", default="gmx"),
    ],
    outputs=[
        Param("stdout", "string"),
        Param("stderr", "string"),
        Param("returncode", "integer"),
        Param("seconds", "number"),
        Param("files", "list", "Files present in the step directory afterwards."),
    ],
    cost="moderate",
    requires=Requirements(
        commands=("gmx",),
        hint="Install GROMACS (https://manual.gromacs.org/current/install-guide/) and put gmx on PATH.",
    ),
    tags=("simulation", "gromacs", "md"),
)
def gromacs_run(
    ctx: ToolContext,
    subcommand: str,
    args: list[str] | None = None,
    stdin: str | None = None,
    executable: str = "gmx",
) -> dict[str, Any]:
    from amide.tools._proc import require, run

    require(executable, "gromacs_run")
    argv = [executable, subcommand, *[str(arg) for arg in args or []]]
    result = run(ctx, argv, name=f"gromacs_run {subcommand}", stdin=stdin)
    result["files"] = sorted(p.name for p in ctx.workdir.iterdir() if p.is_file())
    return result
