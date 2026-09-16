"""Run directories: one per execution, checkpointed after every step."""

from __future__ import annotations

import hashlib
import json
import secrets
import tarfile
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from amide.harness.errors import HarnessError
from amide.harness.protocol import Protocol

STATUSES = (
    "pending",
    "running",
    "paused",
    "passed",
    "failed",
    "error",
    "budget_exceeded",
)
STEP_STATUSES = ("pending", "running", "done", "skipped", "error")


@dataclass
class StepState:
    id: str
    tool: str
    status: str = "pending"
    started: str | None = None
    finished: str | None = None
    seconds: float | None = None
    outputs: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    runner: str = "local"


@dataclass
class RunState:
    id: str
    dir: Path
    protocol_name: str
    protocol_source: str | None
    params: dict[str, Any]
    status: str = "pending"
    created: str = ""
    started: str | None = None
    finished: str | None = None
    steps: dict[str, StepState] = field(default_factory=dict)
    error: str | None = None
    checks: list[dict[str, Any]] = field(default_factory=list)
    outputs: dict[str, Any] = field(default_factory=dict)

    @property
    def path(self) -> Path:
        return self.dir / "run.json"

    @property
    def log_path(self) -> Path:
        return self.dir / "run.log"

    @property
    def protocol_path(self) -> Path:
        return self.dir / "protocol.yaml"

    def step_dir(self, step_id: str) -> Path:
        return self.dir / "steps" / step_id

    def save(self) -> None:
        data = asdict(self)
        data["dir"] = str(self.dir)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2, default=str))
        tmp.replace(self.path)

    @classmethod
    def load(cls, directory: Path) -> RunState:
        path = directory / "run.json"
        try:
            data = json.loads(path.read_text())
        except OSError:
            raise HarnessError(f"{directory.name} is not a run directory (no run.json)") from None
        except json.JSONDecodeError as error:
            raise HarnessError(f"{path}: {error}") from None
        steps = {name: StepState(**entry) for name, entry in data.pop("steps", {}).items()}
        data["dir"] = directory
        return cls(steps=steps, **data)

    def log(self, message: str) -> None:
        line = f"{now()} {message}\n"
        with self.log_path.open("a") as handle:
            handle.write(line)

    def summary(self) -> dict[str, Any]:
        done = sum(1 for step in self.steps.values() if step.status == "done")
        return {
            "id": self.id,
            "status": self.status,
            "protocol": self.protocol_name,
            "created": self.created,
            "steps": f"{done}/{len(self.steps)}",
        }


class RunStore:
    """The runs root: ``.amide/runs`` by default."""

    def __init__(self, root: Path) -> None:
        # Absolute, so paths recorded in outputs stay valid from any directory.
        self.root = Path(root).expanduser().resolve()

    def create(self, protocol: Protocol, params: dict[str, Any]) -> RunState:
        run_id = new_run_id()
        directory = self.root / run_id
        while directory.exists():
            run_id = new_run_id()
            directory = self.root / run_id
        (directory / "steps").mkdir(parents=True)
        state = RunState(
            id=run_id,
            dir=directory,
            protocol_name=protocol.name,
            protocol_source=str(protocol.source) if protocol.source else None,
            params=params,
            created=now(),
            steps={step.id: StepState(id=step.id, tool=step.tool) for step in protocol.steps},
        )
        if protocol.text:
            state.protocol_path.write_text(protocol.text)
        else:
            import yaml

            state.protocol_path.write_text(
                yaml.safe_dump(_protocol_dict(protocol), sort_keys=False)
            )
        state.save()
        return state

    def resolve(self, run_id: str) -> Path:
        """The directory for a run id or a unique prefix of one."""
        directory = self.root / run_id
        if directory.is_dir():
            return directory
        matches = (
            [p for p in self.root.glob(f"{run_id}*") if p.is_dir()] if self.root.is_dir() else []
        )
        if len(matches) == 1:
            return matches[0]
        if matches:
            raise HarnessError(f"{run_id} is ambiguous: {', '.join(m.name for m in matches)}")
        raise HarnessError(f"no run {run_id} under {self.root}")

    def get(self, run_id: str) -> RunState:
        return RunState.load(self.resolve(run_id))

    def get_any(self, run_id: str):
        """A RunState, or an Experiment when the directory holds one."""
        directory = self.resolve(run_id)
        if (directory / "experiment.json").is_file():
            from amide.agents.session import Experiment

            return Experiment.load(directory)
        return RunState.load(directory)

    def list(self) -> list[RunState]:
        if not self.root.is_dir():
            return []
        runs = []
        for directory in self.root.iterdir():
            if (directory / "run.json").is_file():
                runs.append(RunState.load(directory))
        return sorted(runs, key=lambda run: (run.created, run.id), reverse=True)

    def export(self, run_id: str, out: Path | None = None) -> Path:
        """Write manifest.json and pack the run (or experiment) into a tarball."""
        state = self.get_any(run_id)
        manifest = {
            "run": state.id,
            "protocol": state.protocol_name,
            "status": state.status,
            "exported": now(),
            "files": {},
        }
        for path in sorted(state.dir.rglob("*")):
            if path.is_file() and path.name != "manifest.json":
                relative = path.relative_to(state.dir).as_posix()
                manifest["files"][relative] = _sha256(path)
        (state.dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
        target = out or Path.cwd() / f"{state.id}.tar.gz"
        with tarfile.open(target, "w:gz") as archive:
            archive.add(state.dir, arcname=state.id)
        return target


def new_run_id() -> str:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{secrets.token_hex(2)}"


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _protocol_dict(protocol: Protocol) -> dict[str, Any]:
    return {
        "name": protocol.name,
        "description": protocol.description,
        "version": protocol.version,
        "params": {
            name: {
                "type": param.type,
                "default": param.default,
                "description": param.description,
                "required": param.required,
            }
            for name, param in protocol.params.items()
        },
        "steps": [
            {
                "id": step.id,
                "tool": step.tool,
                **({"when": step.when} if step.when else {}),
                **({"with": step.with_} if step.with_ else {}),
                **({"runner": step.runner} if step.runner else {}),
            }
            for step in protocol.steps
        ],
        "checks": [
            {"id": c.id, "expr": c.expr, "description": c.description} for c in protocol.checks
        ],
        "outputs": protocol.outputs,
    }
