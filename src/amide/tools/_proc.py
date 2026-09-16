"""Subprocesses for tools that wrap an executable."""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from typing import Any

from amide.harness.errors import ToolError
from amide.harness.tool import ToolContext


def run(
    ctx: ToolContext,
    argv: list[str],
    *,
    name: str,
    timeout: float | None = None,
    stdin: str | None = None,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> dict[str, Any]:
    """Run ``argv`` in the step's workdir and return stdout, stderr, returncode, seconds."""
    ctx.log("$ " + " ".join(argv))
    started = time.monotonic()
    try:
        done = subprocess.run(
            argv,
            cwd=ctx.workdir,
            input=stdin,
            capture_output=True,
            text=True,
            timeout=timeout if timeout is not None else ctx.timeout,
            check=False,
            env={**os.environ, **ctx.env, **(env or {})},
        )
    except subprocess.TimeoutExpired:
        raise ToolError(f"{name} timed out after {timeout or ctx.timeout}s") from None
    except OSError as error:
        raise ToolError(f"{name}: could not start {argv[0]}: {error.strerror}") from None
    seconds = round(time.monotonic() - started, 3)
    (ctx.workdir / "stdout.txt").write_text(done.stdout)
    (ctx.workdir / "stderr.txt").write_text(done.stderr)
    if check and done.returncode != 0:
        tail = "\n".join((done.stderr or done.stdout).strip().splitlines()[-8:])
        raise ToolError(f"{name} exited {done.returncode}" + (f":\n{tail}" if tail else ""))
    return {
        "stdout": done.stdout,
        "stderr": done.stderr,
        "returncode": done.returncode,
        "seconds": seconds,
    }


def require(executable: str, name: str, hint: str = "") -> str:
    path = shutil.which(executable)
    if path is None:
        raise ToolError(f"{name}: {executable} is not on PATH" + (f". {hint}" if hint else ""))
    return path
