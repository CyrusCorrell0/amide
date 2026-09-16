"""Shared fixtures: a stub tool registry and a protocol that uses it."""

from __future__ import annotations

from pathlib import Path

import pytest

from amide.harness.protocol import Protocol, from_dict
from amide.harness.registry import Registry
from amide.harness.tool import Param, Requirements, ToolContext, tool

DATA = Path(__file__).parent / "data"


@tool(
    name="add",
    description="Add two integers.",
    inputs=[Param("a", "integer", required=True), Param("b", "integer", default=1)],
    outputs=[Param("sum", "integer")],
)
def add(ctx: ToolContext, a: int, b: int = 1) -> dict:
    ctx.log(f"adding {a} and {b}")
    return {"sum": a + b}


@tool(
    name="write",
    description="Write text to a file in the workdir.",
    inputs=[Param("text", "string", required=True), Param("name", "string", default="out.txt")],
    outputs=[Param("path", "path")],
)
def write(ctx: ToolContext, text: str, name: str = "out.txt") -> dict:
    path = ctx.workdir / name
    path.write_text(text)
    return {"path": str(path)}


@tool(
    name="boom",
    description="Always fails.",
    inputs=[Param("message", "string", default="boom")],
)
def boom(ctx: ToolContext, message: str = "boom") -> dict:
    raise RuntimeError(message)


@tool(
    name="pricey",
    description="An expensive no-op.",
    outputs=[Param("ok", "boolean")],
    cost="expensive",
)
def pricey(ctx: ToolContext) -> dict:
    return {"ok": True}


@tool(
    name="needs_unicorn",
    description="Requires something that is never installed.",
    requires=Requirements(python=("unicorn_module_xyz",), hint="There is no such thing."),
)
def needs_unicorn(ctx: ToolContext) -> dict:
    return {}


STUB_TOOLS_SOURCE = """
from amide.harness.tool import Param, Requirements, ToolContext, tool


@tool(
    name="add",
    description="Add two integers.",
    inputs=[Param("a", "integer", required=True), Param("b", "integer", default=1)],
    outputs=[Param("sum", "integer")],
)
def add(ctx: ToolContext, a: int, b: int = 1) -> dict:
    return {"sum": a + b}


@tool(
    name="write",
    description="Write text to a file in the workdir.",
    inputs=[Param("text", "string", required=True), Param("name", "string", default="out.txt")],
    outputs=[Param("path", "path")],
)
def write(ctx: ToolContext, text: str, name: str = "out.txt") -> dict:
    path = ctx.workdir / name
    path.write_text(text)
    return {"path": str(path)}


@tool(name="boom", description="Always fails.", inputs=[Param("message", "string", default="boom")])
def boom(ctx: ToolContext, message: str = "boom") -> dict:
    raise RuntimeError(message)


@tool(name="pricey", description="An expensive no-op.", outputs=[Param("ok", "boolean")], cost="expensive")
def pricey(ctx: ToolContext) -> dict:
    return {"ok": True}
"""

STUB_PROTOCOL = {
    "name": "arith",
    "description": "Adds numbers and writes the answer.",
    "params": {
        "a": {"type": "integer", "default": 2},
        "b": {"type": "integer", "default": 3},
        "limit": {"type": "integer", "default": 100},
        "note": {"type": "string", "default": None},
    },
    "steps": [
        {"id": "first", "tool": "add", "with": {"a": "{{ params.a }}", "b": "{{ params.b }}"}},
        {"id": "second", "tool": "add", "with": {"a": "{{ steps.first.sum }}", "b": 10}},
        {
            "id": "annotate",
            "tool": "write",
            "when": "params.note is not None",
            "with": {"text": "{{ params.note }}", "name": "note.txt"},
        },
        {
            "id": "save",
            "tool": "write",
            "with": {"text": "total={{ steps.second.sum }}"},
        },
    ],
    "checks": [
        {"id": "small", "expr": "steps.second.sum < params.limit", "description": "Stays small."},
    ],
    "outputs": {"total": "{{ steps.second.sum }}", "file": "{{ steps.save.path }}"},
}


@pytest.fixture
def stub_registry() -> Registry:
    registry = Registry()
    for func in (add, write, boom, pricey, needs_unicorn):
        registry.add(func.spec)
    return registry


@pytest.fixture
def stub_protocol() -> Protocol:
    return from_dict(STUB_PROTOCOL)


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A working directory with stub tools in .amide/tools and a local protocol."""
    import yaml

    (tmp_path / ".amide" / "tools").mkdir(parents=True)
    (tmp_path / ".amide" / "tools" / "stubs.py").write_text(STUB_TOOLS_SOURCE)
    (tmp_path / ".amide" / "protocols").mkdir()
    (tmp_path / ".amide" / "protocols" / "arith.yaml").write_text(yaml.safe_dump(STUB_PROTOCOL))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AMIDE_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.delenv("AMIDE_RUNS_DIR", raising=False)
    return tmp_path
