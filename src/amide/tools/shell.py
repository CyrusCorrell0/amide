"""Run a shell command inside the step directory."""

from __future__ import annotations

import os
from typing import Any

from amide.harness.tool import Param, ToolContext, tool


@tool(
    name="shell",
    description=(
        "Run a shell command with the step directory as the working directory and return "
        "its output. A non-zero exit fails the step."
    ),
    inputs=[
        Param(
            "command", "string", "The command line, run through the system shell.", required=True
        ),
        Param("timeout", "integer", "Seconds before the command is killed.", default=600),
    ],
    outputs=[
        Param("stdout", "string"),
        Param("stderr", "string"),
        Param("returncode", "integer"),
    ],
    cost="moderate",
    tags=("general",),
)
def shell(ctx: ToolContext, command: str, timeout: int = 600) -> dict[str, Any]:
    from amide.tools._proc import run

    argv = ["cmd", "/c", command] if os.name == "nt" else ["/bin/sh", "-c", command]
    result = run(ctx, argv, name="shell", timeout=timeout)
    return {key: result[key] for key in ("stdout", "stderr", "returncode")}
