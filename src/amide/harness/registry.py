"""Where tools come from: builtin modules, configured directories, ``./.amide/tools``."""

from __future__ import annotations

import importlib
import importlib.util
import pkgutil
import shutil
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from amide.harness.errors import HarnessError, ProtocolError, ToolError
from amide.harness.expr import render
from amide.harness.tool import Param, Requirements, ToolContext, ToolSpec

LOCAL_DIR = Path(".amide") / "tools"
_CONTAINER_WORK = "/work"
_CONTAINER_RUN = "/run"


class Registry:
    """A name-to-ToolSpec map with requirement checking."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    @classmethod
    def load(
        cls,
        config: Any | None = None,
        cwd: Path | None = None,
        builtin: bool = True,
    ) -> Registry:
        """Builtin tools first, then configured directories, then the local directory."""
        registry = cls()
        if builtin:
            for spec in builtin_tools():
                registry.add(spec)
        directories: list[Path] = []
        if config is not None:
            directories.extend(config.tool_paths)
        directories.append((cwd or Path.cwd()) / LOCAL_DIR)
        for directory in directories:
            if directory.is_dir():
                for spec in load_directory(directory):
                    registry.add(spec)
        return registry

    def add(self, spec: ToolSpec) -> None:
        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec:
        try:
            return self._tools[name]
        except KeyError:
            raise ProtocolError(
                f"unknown tool {name!r}; `amide tools list` shows what is available"
            ) from None

    def names(self) -> list[str]:
        return sorted(self._tools)

    def __iter__(self) -> Iterator[ToolSpec]:
        return iter(self._tools[name] for name in self.names())

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: object) -> bool:
        return name in self._tools

    @staticmethod
    def missing(spec: ToolSpec) -> list[str]:
        """Requirements of ``spec`` absent on this machine, as printable strings."""
        absent: list[str] = []
        for module in spec.requires.python:
            top = module.split(".")[0]
            try:
                found = importlib.util.find_spec(top) is not None
            except (ImportError, ValueError):
                found = False
            if not found:
                absent.append(f"python module {module}")
        for command in spec.requires.commands:
            if shutil.which(command) is None:
                absent.append(f"command {command}")
        if spec.runtime == "container" and _container_engine() is None:
            absent.append("command docker or podman")
        return absent


def builtin_tools() -> list[ToolSpec]:
    """Every tool declared in ``amide.tools``."""
    import amide.tools

    specs: list[ToolSpec] = []
    for info in pkgutil.iter_modules(amide.tools.__path__):
        if info.name.startswith("_"):
            continue
        module = importlib.import_module(f"amide.tools.{info.name}")
        specs.extend(_specs_in(module, "builtin"))
    return specs


def load_directory(directory: Path) -> list[ToolSpec]:
    """Python tools from ``*.py`` and command or container tools from ``*.yaml``."""
    specs: list[ToolSpec] = []
    for path in sorted(directory.iterdir()):
        if path.name.startswith(("_", ".")):
            continue
        if path.suffix == ".py":
            specs.extend(load_python_file(path))
        elif path.suffix in (".yaml", ".yml"):
            specs.append(load_manifest(path))
    return specs


def load_python_file(path: Path) -> list[ToolSpec]:
    name = f"amide_local_tools.{path.stem}_{abs(hash(str(path.resolve())))}"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise HarnessError(f"cannot load tools from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as error:
        raise HarnessError(f"{path}: {error}") from None
    return _specs_in(module, str(path))


def load_manifest(path: Path) -> ToolSpec:
    import yaml

    try:
        data = yaml.safe_load(path.read_text())
    except yaml.YAMLError as error:
        raise HarnessError(f"{path}: {error}") from None
    if not isinstance(data, dict):
        raise HarnessError(f"{path}: a tool manifest is a mapping")
    try:
        return manifest_tool(data, source=str(path))
    except (KeyError, TypeError, ValueError) as error:
        raise HarnessError(f"{path}: {error}") from None


def manifest_tool(data: dict[str, Any], source: str = "manifest") -> ToolSpec:
    """A command or container tool from its manifest mapping."""
    runtime = data.get("runtime", "command")
    if runtime not in ("command", "container"):
        raise ValueError(f"runtime must be command or container, not {runtime!r}")
    command = data.get("command")
    if not isinstance(command, list) or not all(isinstance(part, str) for part in command):
        raise ValueError("command must be a list of strings")
    image = data.get("image")
    if runtime == "container" and not image:
        raise ValueError("a container tool needs an image")
    requires = _requirements(data.get("requires") or {})
    if runtime == "command" and command and not requires.commands:
        requires = Requirements(
            python=requires.python, commands=(command[0],), gpu=requires.gpu, hint=requires.hint
        )
    inputs = tuple(_param(entry) for entry in data.get("inputs") or [])
    outputs = tuple(_param(entry) for entry in data.get("outputs") or [])

    def run(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
        return _run_command(ctx, args, command, runtime, image, inputs, outputs, data["name"])

    return ToolSpec(
        name=str(data["name"]),
        description=str(data.get("description", "")),
        run=run,
        inputs=inputs,
        outputs=outputs,
        cost=str(data.get("cost", "moderate")),
        requires=requires,
        timeout=data.get("timeout"),
        tags=tuple(data.get("tags") or ()),
        version=str(data.get("version", "1")),
        runtime=runtime,
        source=source,
    )


def _run_command(
    ctx: ToolContext,
    args: dict[str, Any],
    command: list[str],
    runtime: str,
    image: str | None,
    inputs: tuple[Param, ...],
    outputs: tuple[Param, ...],
    name: str,
) -> dict[str, Any]:
    host = {**args, "workdir": str(ctx.workdir), "run_dir": str(ctx.run_dir)}
    if runtime == "container":
        engine = _container_engine()
        if engine is None:
            raise ToolError(f"{name}: neither docker nor podman is on PATH")
        inside = {
            **{
                param.name: _translate(args[param.name], ctx)
                if param.type == "path" and args.get(param.name)
                else args.get(param.name)
                for param in inputs
            },
            "workdir": _CONTAINER_WORK,
            "run_dir": _CONTAINER_RUN,
        }
        argv = [
            engine,
            "run",
            "--rm",
            "-v",
            f"{ctx.workdir}:{_CONTAINER_WORK}",
            "-v",
            f"{ctx.run_dir}:{_CONTAINER_RUN}",
            "-w",
            _CONTAINER_WORK,
            str(image),
            *[_text(render(part, inside)) for part in command],
        ]
    else:
        argv = [_text(render(part, host)) for part in command]
    ctx.log("$ " + " ".join(argv))
    try:
        done = subprocess.run(
            argv,
            cwd=ctx.workdir,
            capture_output=True,
            text=True,
            timeout=ctx.timeout,
            check=False,
            env={**_environ(), **ctx.env},
        )
    except subprocess.TimeoutExpired:
        raise ToolError(f"{name} timed out after {ctx.timeout}s") from None
    except OSError as error:
        raise ToolError(f"{name}: {error}") from None
    result: dict[str, Any] = {
        "stdout": done.stdout,
        "stderr": done.stderr,
        "returncode": done.returncode,
    }
    if done.returncode != 0:
        tail = "\n".join(done.stderr.strip().splitlines()[-5:])
        raise ToolError(f"{name} exited {done.returncode}" + (f":\n{tail}" if tail else ""))
    for param in outputs:
        if param.value is not None:
            result[param.name] = render(param.value, host)
    return result


def _translate(path: Any, ctx: ToolContext) -> str:
    """A host path under the run directory as seen inside the container."""
    text = str(path)
    try:
        relative = Path(text).resolve().relative_to(ctx.run_dir.resolve())
    except ValueError:
        return text
    return f"{_CONTAINER_RUN}/{relative.as_posix()}"


def _container_engine() -> str | None:
    for engine in ("docker", "podman"):
        if shutil.which(engine):
            return engine
    return None


def _environ() -> dict[str, str]:
    import os

    return dict(os.environ)


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value).lower()
    return str(value)


def _param(entry: Any) -> Param:
    if isinstance(entry, str):
        return Param(entry)
    if not isinstance(entry, dict) or "name" not in entry:
        raise ValueError(f"a param needs a name: {entry!r}")
    choices = entry.get("choices") or ()
    return Param(
        name=str(entry["name"]),
        type=str(entry.get("type", "string")),
        description=str(entry.get("description", "")),
        required=bool(entry.get("required", False)),
        default=entry.get("default"),
        choices=tuple(choices),
        value=entry.get("value"),
    )


def _requirements(data: dict[str, Any]) -> Requirements:
    return Requirements(
        python=tuple(data.get("python") or ()),
        commands=tuple(data.get("commands") or ()),
        gpu=bool(data.get("gpu", False)),
        hint=str(data.get("hint", "")),
    )


def _specs_in(module: Any, source: str) -> list[ToolSpec]:
    specs: list[ToolSpec] = []
    for attr in vars(module).values():
        spec = getattr(attr, "spec", None)
        if isinstance(spec, ToolSpec):
            spec.source = source
            specs.append(spec)
    return specs
