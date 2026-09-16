"""Protocols: a YAML file of parameters, tool steps, checks, and outputs."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from amide.harness.errors import ProtocolError, ValidationError
from amide.harness.expr import references
from amide.harness.tool import Param

BUNDLED_DIR = Path(__file__).resolve().parent.parent / "protocols"
LOCAL_DIR = Path(".amide") / "protocols"


@dataclass
class Step:
    id: str
    tool: str
    with_: dict[str, Any] = field(default_factory=dict)
    when: str | None = None
    description: str = ""


@dataclass
class Check:
    id: str
    expr: str
    description: str = ""


@dataclass
class Protocol:
    name: str
    steps: list[Step]
    description: str = ""
    version: int = 1
    params: dict[str, Param] = field(default_factory=dict)
    checks: list[Check] = field(default_factory=list)
    outputs: dict[str, Any] = field(default_factory=dict)
    source: Path | None = None
    text: str = ""

    def resolve_params(self, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
        """Defaults with ``overrides`` applied and every value coerced."""
        given = dict(overrides or {})
        unknown = sorted(set(given) - set(self.params))
        if unknown:
            raise ValidationError(
                f"{self.name} has no parameter {', '.join(unknown)}; "
                f"it takes {', '.join(sorted(self.params)) or 'none'}"
            )
        resolved: dict[str, Any] = {}
        for name, param in self.params.items():
            value = given.get(name)
            if value is None:
                if param.required:
                    raise ValidationError(f"{self.name} needs --set {name}=...")
                resolved[name] = param.default
            else:
                resolved[name] = param.coerce(value)
        return resolved

    def step(self, step_id: str) -> Step:
        for step in self.steps:
            if step.id == step_id:
                return step
        raise ProtocolError(f"{self.name} has no step {step_id!r}")

    def problems(self, registry: Any | None = None) -> list[str]:
        """Everything wrong that can be found without running anything."""
        found: list[str] = []
        seen: set[str] = set()
        for step in self.steps:
            if step.id in seen:
                found.append(f"step {step.id!r} is defined twice")
            seen.add(step.id)
        earlier: set[str] = set()
        for step in self.steps:
            for ref in sorted(references(step.with_) | references(step.when or "")):
                found.extend(self._check_reference(ref, earlier, f"step {step.id!r}"))
            earlier.add(step.id)
            if registry is not None and step.tool in registry:
                spec = registry.get(step.tool)
                known = {param.name for param in spec.inputs}
                for name in sorted(set(step.with_) - known):
                    found.append(f"step {step.id!r}: {step.tool} does not take {name!r}")
                for param in spec.inputs:
                    if param.required and param.name not in step.with_:
                        found.append(f"step {step.id!r}: {step.tool} needs {param.name!r}")
            elif registry is not None:
                found.append(f"step {step.id!r}: unknown tool {step.tool!r}")
        for check in self.checks:
            for ref in sorted(references("{{ " + check.expr + " }}")):
                found.extend(self._check_reference(ref, earlier, f"check {check.id!r}"))
        for name, template in self.outputs.items():
            for ref in sorted(references(template)):
                found.extend(self._check_reference(ref, earlier, f"output {name!r}"))
        return found

    def _check_reference(self, ref: str, steps: set[str], where: str) -> list[str]:
        parts = ref.split(".")
        if parts[0] == "params" and len(parts) > 1 and parts[1] not in self.params:
            return [f"{where} refers to params.{parts[1]}, which is not declared"]
        if parts[0] == "steps" and len(parts) > 1 and parts[1] not in steps:
            return [f"{where} refers to steps.{parts[1]}, which is not an earlier step"]
        return []


def load(path: Path) -> Protocol:
    import yaml

    try:
        text = path.read_text()
        data = yaml.safe_load(text)
    except OSError as error:
        raise ProtocolError(f"cannot read {path}: {error.strerror}") from None
    except yaml.YAMLError as error:
        raise ProtocolError(f"{path}: {error}") from None
    protocol = from_dict(data, source=path)
    protocol.text = text
    return protocol


def from_dict(data: Any, source: Path | None = None) -> Protocol:
    where = str(source) if source else "protocol"
    if not isinstance(data, dict):
        raise ProtocolError(f"{where}: a protocol is a mapping with name and steps")
    name = data.get("name")
    if not isinstance(name, str) or not name:
        raise ProtocolError(f"{where}: name is required")
    steps_data = data.get("steps")
    if not isinstance(steps_data, list) or not steps_data:
        raise ProtocolError(f"{where}: steps must be a non-empty list")
    params: dict[str, Param] = {}
    for param_name, entry in (data.get("params") or {}).items():
        params[param_name] = _param(param_name, entry, where)
    steps = [_step(entry, index, where) for index, entry in enumerate(steps_data)]
    checks = [_check(entry, index, where) for index, entry in enumerate(data.get("checks") or [])]
    outputs = data.get("outputs") or {}
    if not isinstance(outputs, dict):
        raise ProtocolError(f"{where}: outputs must be a mapping")
    version = data.get("version", 1)
    if not isinstance(version, int):
        raise ProtocolError(f"{where}: version must be an integer")
    return Protocol(
        name=name,
        description=str(data.get("description", "")),
        version=version,
        params=params,
        steps=steps,
        checks=checks,
        outputs=outputs,
        source=source,
    )


def find(name_or_path: str, cwd: Path | None = None) -> Protocol:
    """A protocol by file path, or by name from ``./.amide/protocols`` or the bundle."""
    path = Path(name_or_path).expanduser()
    if path.suffix in (".yaml", ".yml") or path.exists():
        if not path.is_file():
            raise ProtocolError(f"{name_or_path} does not exist")
        return load(path)
    for directory in ((cwd or Path.cwd()) / LOCAL_DIR, BUNDLED_DIR):
        for suffix in (".yaml", ".yml"):
            candidate = directory / f"{name_or_path}{suffix}"
            if candidate.is_file():
                return load(candidate)
    names = ", ".join(p.name for p in available(cwd)) or "none"
    raise ProtocolError(f"no protocol named {name_or_path!r}; available: {names}")


def available(cwd: Path | None = None) -> list[Protocol]:
    """Bundled protocols plus those in ``./.amide/protocols``, local ones first."""
    found: dict[str, Protocol] = {}
    for directory in (BUNDLED_DIR, (cwd or Path.cwd()) / LOCAL_DIR):
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.y*ml")):
            protocol = load(path)
            found[protocol.name] = protocol
    return sorted(found.values(), key=lambda p: p.name)


def parse_overrides(pairs: list[str]) -> dict[str, Any]:
    """``--set name=value`` pairs; values are read as YAML so ``5`` is an int."""
    import yaml

    overrides: dict[str, Any] = {}
    for pair in pairs:
        name, sep, raw = pair.partition("=")
        if not sep or not name.strip():
            raise ValidationError(f"--set takes name=value, not {pair!r}")
        try:
            value = yaml.safe_load(raw) if raw.strip() else ""
        except yaml.YAMLError:
            value = raw
        overrides[name.strip()] = value
    return overrides


def _param(name: str, entry: Any, where: str) -> Param:
    if entry is None:
        entry = {}
    if not isinstance(entry, dict):
        # ``params: {steps: 5000}`` is shorthand for a default.
        entry = {"default": entry}
    type_ = entry.get("type") or _infer_type(entry.get("default"))
    try:
        return Param(
            name=name,
            type=str(type_),
            description=str(entry.get("description", "")),
            required=bool(entry.get("required", False)),
            default=entry.get("default"),
            choices=tuple(entry.get("choices") or ()),
        )
    except ValueError as error:
        raise ProtocolError(f"{where}: {error}") from None


def _infer_type(default: Any) -> str:
    if isinstance(default, bool):
        return "boolean"
    if isinstance(default, int):
        return "integer"
    if isinstance(default, float):
        return "number"
    if isinstance(default, list):
        return "list"
    if isinstance(default, dict):
        return "object"
    return "string"


def _step(entry: Any, index: int, where: str) -> Step:
    if not isinstance(entry, dict):
        raise ProtocolError(f"{where}: step {index} must be a mapping")
    step_id = entry.get("id")
    tool = entry.get("tool")
    if not isinstance(step_id, str) or not step_id:
        raise ProtocolError(f"{where}: step {index} needs an id")
    if not isinstance(tool, str) or not tool:
        raise ProtocolError(f"{where}: step {step_id!r} needs a tool")
    with_ = entry.get("with") or {}
    if not isinstance(with_, dict):
        raise ProtocolError(f"{where}: step {step_id!r}: with must be a mapping")
    when = entry.get("when")
    if when is not None and not isinstance(when, str):
        raise ProtocolError(f"{where}: step {step_id!r}: when must be an expression string")
    return Step(
        id=step_id,
        tool=tool,
        with_=with_,
        when=when,
        description=str(entry.get("description", "")),
    )


def _check(entry: Any, index: int, where: str) -> Check:
    if not isinstance(entry, dict) or not isinstance(entry.get("expr"), str):
        raise ProtocolError(f"{where}: check {index} needs an expr")
    return Check(
        id=str(entry.get("id") or f"check{index}"),
        expr=entry["expr"],
        description=str(entry.get("description", "")),
    )
