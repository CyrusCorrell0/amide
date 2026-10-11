"""Check everything `amide view` needs. Reads the disk and the terminal, never the network."""

from __future__ import annotations

import os
import platform
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path

import amide
from amide import tui


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str
    fix: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def checks(interactive: bool) -> list[Check]:
    """The four preconditions of `amide view`, in the order a person would fix them."""
    return [_amide(), _platform(), _viewer(), _terminal(interactive)]


def _amide() -> Check:
    detail = f"amide {amide.__version__}, python {platform.python_version()}"
    return Check("amide", True, detail)


def _platform() -> Check:
    try:
        os_, arch = tui.target()
    except tui.TuiUnavailable as exc:
        return Check(
            "platform",
            False,
            str(exc).removeprefix("amide view: "),
            "use a supported platform or set `AMIDE_TUI` to a viewer you built from tui/",
        )
    return Check("platform", True, f"platform {os_}/{arch} is supported")


def _viewer() -> Check:
    override = os.environ.get("AMIDE_TUI")
    if override:
        if Path(override).exists():
            return Check("viewer", True, f"viewer override at {override}")
        return Check(
            "viewer",
            False,
            f"viewer override at {override} does not exist",
            "unset `AMIDE_TUI` or point it at an existing file",
        )

    version = amide.__version__
    try:
        cached = tui.cached_binary()
    except tui.TuiUnavailable:
        return Check(
            "viewer",
            False,
            f"viewer {version} has no build for this platform",
            "set `AMIDE_TUI` to a viewer you built from tui/",
        )
    if cached.exists():
        return Check("viewer", True, f"viewer {version} cached at {cached}")
    return Check(
        "viewer",
        False,
        f"viewer {version} not downloaded yet",
        "run `amide view` once while online",
    )


def _terminal(interactive: bool) -> Check:
    if not interactive:
        return Check(
            "terminal",
            False,
            "no interactive terminal",
            "use `amide info` here, or run `amide view` in a terminal",
        )
    cols, rows = shutil.get_terminal_size()
    return Check("terminal", True, f"terminal is interactive ({cols}x{rows})")


def render(results: list[Check]) -> str:
    """Two-space indent, a status column, the detail; a fix line under each failure."""
    lines = []
    for check in results:
        lines.append(f"  {'ok' if check.ok else 'fail':<4}  {check.detail}")
        if check.fix:
            lines.append(f"        fix: {check.fix}")
    return "\n".join(lines)
