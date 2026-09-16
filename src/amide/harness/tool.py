"""Tool manifests: what a tool is called, what it takes, what it returns, what it needs."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from amide.harness.errors import ValidationError

TYPES = ("string", "integer", "number", "boolean", "path", "list", "object")
COSTS = ("cheap", "moderate", "expensive")
RUNTIMES = ("python", "command", "container")

_JSON_TYPES = {
    "string": "string",
    "integer": "integer",
    "number": "number",
    "boolean": "boolean",
    "path": "string",
    "list": "array",
    "object": "object",
}
_TRUE = {"true", "yes", "on", "1"}
_FALSE = {"false", "no", "off", "0"}


@dataclass(frozen=True)
class Param:
    """One input or output of a tool."""

    name: str
    type: str = "string"
    description: str = ""
    required: bool = False
    default: Any = None
    choices: tuple[Any, ...] = ()
    # Command tools declare each output's value as a template over the inputs
    # and ``workdir``; Python tools leave this empty and return the value.
    value: str | None = None

    def __post_init__(self) -> None:
        if self.type not in TYPES:
            raise ValueError(f"param {self.name!r}: unknown type {self.type!r}; use one of {TYPES}")

    def coerce(self, value: Any) -> Any:
        """Turn ``value`` into this param's type, or raise ValidationError."""
        try:
            coerced = _coerce(self.type, value)
        except (TypeError, ValueError):
            raise ValidationError(
                f"{self.name} must be {self.type}, got {value!r} ({type(value).__name__})"
            ) from None
        if self.choices and coerced not in self.choices:
            raise ValidationError(
                f"{self.name} must be one of {', '.join(map(str, self.choices))}, got {coerced!r}"
            )
        return coerced

    def to_schema(self) -> dict[str, Any]:
        schema: dict[str, Any] = {"type": _JSON_TYPES[self.type]}
        if self.description:
            schema["description"] = self.description
        if self.choices:
            schema["enum"] = list(self.choices)
        if self.default is not None:
            schema["default"] = self.default
        return schema


def _coerce(type_: str, value: Any) -> Any:
    if type_ == "string":
        if isinstance(value, (list, dict)):
            raise TypeError
        return str(value)
    if type_ == "path":
        if isinstance(value, (list, dict, bool)):
            raise TypeError
        return str(value)
    if type_ == "integer":
        if isinstance(value, bool):
            raise TypeError
        if isinstance(value, float) and not value.is_integer():
            raise ValueError
        return int(value)
    if type_ == "number":
        if isinstance(value, bool):
            raise TypeError
        return float(value)
    if type_ == "boolean":
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.lower() in _TRUE:
            return True
        if isinstance(value, str) and value.lower() in _FALSE:
            return False
        raise TypeError
    if type_ == "list":
        if isinstance(value, (list, tuple)):
            return list(value)
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        raise TypeError
    if type_ == "object":
        if isinstance(value, dict):
            return dict(value)
        raise TypeError
    raise ValueError(type_)


@dataclass(frozen=True)
class Requirements:
    """What must be present on the machine for the tool to run."""

    python: tuple[str, ...] = ()
    commands: tuple[str, ...] = ()
    gpu: bool = False
    hint: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "python": list(self.python),
            "commands": list(self.commands),
            "gpu": self.gpu,
            "hint": self.hint,
        }


@dataclass
class ToolContext:
    """What a running tool gets besides its inputs."""

    workdir: Path
    run_dir: Path
    log: Callable[[str], None] = lambda message: None
    env: dict[str, str] = field(default_factory=dict)
    timeout: float | None = None


@dataclass
class ToolSpec:
    """A tool's manifest plus the callable that runs it."""

    name: str
    description: str
    run: Callable[[ToolContext, dict[str, Any]], dict[str, Any]]
    inputs: tuple[Param, ...] = ()
    outputs: tuple[Param, ...] = ()
    cost: str = "cheap"
    requires: Requirements = field(default_factory=Requirements)
    timeout: float | None = None
    tags: tuple[str, ...] = ()
    version: str = "1"
    runtime: str = "python"
    source: str = "builtin"

    def __post_init__(self) -> None:
        if self.cost not in COSTS:
            raise ValueError(f"tool {self.name!r}: cost must be one of {COSTS}, not {self.cost!r}")
        if self.runtime not in RUNTIMES:
            raise ValueError(
                f"tool {self.name!r}: runtime must be one of {RUNTIMES}, not {self.runtime!r}"
            )
        seen: set[str] = set()
        for param in self.inputs:
            if param.name in seen:
                raise ValueError(f"tool {self.name!r}: duplicate input {param.name!r}")
            seen.add(param.name)

    def validate_inputs(self, given: dict[str, Any]) -> dict[str, Any]:
        """Apply defaults, coerce types, reject unknown or missing inputs."""
        known = {param.name for param in self.inputs}
        unknown = sorted(set(given) - known)
        if unknown:
            raise ValidationError(
                f"{self.name} does not take {', '.join(unknown)}; "
                f"its inputs are {', '.join(sorted(known)) or 'none'}"
            )
        resolved: dict[str, Any] = {}
        for param in self.inputs:
            value = given.get(param.name)
            if value is None:
                if param.required:
                    raise ValidationError(f"{self.name} needs {param.name}")
                resolved[param.name] = param.default
                continue
            resolved[param.name] = param.coerce(value)
        return resolved

    def to_schema(self) -> dict[str, Any]:
        """A JSON-schema tool definition, the shape model adapters send."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": {
                "type": "object",
                "properties": {param.name: param.to_schema() for param in self.inputs},
                "required": [param.name for param in self.inputs if param.required],
            },
        }

    def to_dict(self) -> dict[str, Any]:
        """The manifest, serialisable."""
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "runtime": self.runtime,
            "cost": self.cost,
            "tags": list(self.tags),
            "timeout": self.timeout,
            "source": self.source,
            "requires": self.requires.to_dict(),
            "inputs": [_param_dict(param) for param in self.inputs],
            "outputs": [_param_dict(param) for param in self.outputs],
        }


def _param_dict(param: Param) -> dict[str, Any]:
    entry: dict[str, Any] = {"name": param.name, "type": param.type}
    if param.description:
        entry["description"] = param.description
    if param.required:
        entry["required"] = True
    if param.default is not None:
        entry["default"] = param.default
    if param.choices:
        entry["choices"] = list(param.choices)
    return entry


def tool(
    *,
    name: str,
    description: str,
    inputs: Iterable[Param] = (),
    outputs: Iterable[Param] = (),
    cost: str = "cheap",
    requires: Requirements | None = None,
    timeout: float | None = None,
    tags: Iterable[str] = (),
    version: str = "1",
) -> Callable[[Callable[..., dict[str, Any]]], Callable[..., dict[str, Any]]]:
    """Declare a Python tool.

    The decorated function takes a ``ToolContext`` followed by its inputs as
    keyword arguments, and returns a dict of its outputs. The registry finds
    it through the ``spec`` attribute this decorator attaches.
    """

    def decorate(func: Callable[..., dict[str, Any]]) -> Callable[..., dict[str, Any]]:
        def run(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
            return func(ctx, **args)

        func.spec = ToolSpec(  # type: ignore[attr-defined]
            name=name,
            description=description,
            run=run,
            inputs=tuple(inputs),
            outputs=tuple(outputs),
            cost=cost,
            requires=requires or Requirements(),
            timeout=timeout,
            tags=tuple(tags),
            version=version,
        )
        return func

    return decorate
