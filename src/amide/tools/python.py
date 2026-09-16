"""Run a Python script in a subprocess inside the step directory."""

from __future__ import annotations

import json
import sys
from typing import Any

from amide.harness.errors import ToolError
from amide.harness.tool import Param, ToolContext, tool


@tool(
    name="python",
    description=(
        "Run Python code in a fresh interpreter (the one amide runs under, so installed "
        "packages are available) with the step directory as the working directory. "
        "``args`` is written to args.json and exposed as the AMIDE_ARGS environment "
        "variable. Anything the script writes to outputs.json becomes step outputs."
    ),
    inputs=[
        Param("code", "string", "The script source.", required=True),
        Param("args", "object", "Values for the script to read.", default={}),
        Param("timeout", "integer", "Seconds before the script is killed.", default=600),
    ],
    outputs=[
        Param("stdout", "string"),
        Param("stderr", "string"),
        Param("returncode", "integer"),
        Param("script", "path", "The script as run."),
    ],
    cost="moderate",
    tags=("code", "general"),
)
def python(
    ctx: ToolContext,
    code: str,
    args: dict[str, Any] | None = None,
    timeout: int = 600,
) -> dict[str, Any]:
    from amide.tools._proc import run

    script = ctx.workdir / "script.py"
    script.write_text(code)
    (ctx.workdir / "args.json").write_text(json.dumps(args or {}, indent=2, default=str))
    result = run(
        ctx,
        [sys.executable, str(script)],
        name="python",
        timeout=timeout,
        env={"AMIDE_ARGS": json.dumps(args or {}, default=str), "PYTHONUNBUFFERED": "1"},
    )
    outputs: dict[str, Any] = {
        "stdout": result["stdout"],
        "stderr": result["stderr"],
        "returncode": result["returncode"],
        "script": str(script),
    }
    written = ctx.workdir / "outputs.json"
    if written.exists():
        try:
            extra = json.loads(written.read_text())
        except json.JSONDecodeError as error:
            raise ToolError(f"python: outputs.json is not valid JSON: {error}") from None
        if not isinstance(extra, dict):
            raise ToolError("python: outputs.json must hold an object")
        outputs.update(extra)
    return outputs
