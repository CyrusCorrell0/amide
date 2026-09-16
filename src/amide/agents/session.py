"""Open experiments: an orchestrator, the sub-agents it spawns, budgets, and
the three documents (abstract, methodology, results) it must leave behind.

An experiment lives in the runs root next to protocol runs:

    <runs>/<id>/
      experiment.json          state, agents, usage
      experiment.log
      agents/01-orchestrate/   transcript.json, log.txt
      agents/02-find/
      protocols/*.yaml         protocols the agents wrote
      runs/<run-id>/           protocol runs the agents made (ordinary run dirs)
      files/                   where tools write
      abstract.md  methodology.md  results.md
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from amide.agents.roles import ROLES, Role, system_prompt
from amide.harness.errors import HarnessError, ToolError
from amide.harness.registry import Registry
from amide.harness.runs import new_run_id, now
from amide.harness.tool import Param, ToolContext, ToolSpec
from amide.models.base import Adapter, Message, Reply, Request, ToolCall, Usage
from amide.models.loop import converse
from amide.models.providers import resolve

STATUSES = ("running", "done", "paused", "budget_exceeded", "error")
DOCUMENTS = ("abstract", "methodology", "results")
MAX_DEPTH = 2
FINISH_NUDGE = (
    "The turn ended without finish_experiment. Finish now: call finish_experiment with the "
    "abstract, methodology, and results based on what was actually done and observed."
)

Approver = Callable[[ToolSpec, dict[str, Any]], bool]
EventSink = Callable[[str, "AgentRecord", Any], None]


@dataclass
class Budget:
    max_tokens: int | None = None
    max_seconds: float | None = None
    max_dollars: float | None = None

    def fractions(self, tokens: int, seconds: float, dollars: float) -> dict[str, float]:
        out = {}
        if self.max_tokens:
            out["tokens"] = tokens / self.max_tokens
        if self.max_seconds:
            out["seconds"] = seconds / self.max_seconds
        if self.max_dollars:
            out["dollars"] = dollars / self.max_dollars
        return out

    def exceeded(self, tokens: int, seconds: float, dollars: float) -> str | None:
        if self.max_tokens and tokens >= self.max_tokens:
            return f"token budget of {self.max_tokens} spent ({tokens} used)"
        if self.max_seconds and seconds >= self.max_seconds:
            return f"wall-clock budget of {self.max_seconds:g}s spent ({seconds:.0f}s elapsed)"
        if self.max_dollars and dollars >= self.max_dollars:
            return f"dollar budget of ${self.max_dollars:g} spent (${dollars:.2f} used)"
        return None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AgentRecord:
    n: int
    role: str
    task: str
    model: str
    status: str = "running"
    parent: int | None = None
    started: str | None = None
    finished: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    calls: int = 0
    turns: int = 0
    result: str = ""
    error: str | None = None

    @property
    def dirname(self) -> str:
        return f"{self.n:02d}-{self.role}"


@dataclass
class Experiment:
    id: str
    dir: Path
    question: str
    model: str | None
    status: str = "running"
    created: str = ""
    started: str | None = None
    finished: str | None = None
    budget: dict[str, Any] = field(default_factory=dict)
    agents: list[AgentRecord] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    dollars: float = 0.0
    seconds: float = 0.0
    documents: dict[str, str] = field(default_factory=dict)
    error: str | None = None

    protocol_name = "experiment"

    @property
    def path(self) -> Path:
        return self.dir / "experiment.json"

    @property
    def log_path(self) -> Path:
        return self.dir / "experiment.log"

    @property
    def workdir(self) -> Path:
        return self.dir / "files"

    def agent_dir(self, record: AgentRecord) -> Path:
        return self.dir / "agents" / record.dirname

    @classmethod
    def create(
        cls, root: Path, question: str, model: str | None, budget: Budget | None = None
    ) -> Experiment:
        root = Path(root).expanduser().resolve()
        run_id = new_run_id()
        while (root / run_id).exists():
            run_id = new_run_id()
        directory = root / run_id
        (directory / "agents").mkdir(parents=True)
        (directory / "files").mkdir()
        experiment = cls(
            id=run_id,
            dir=directory,
            question=question,
            model=model,
            created=now(),
            budget=(budget or Budget()).to_dict(),
        )
        experiment.save()
        return experiment

    @classmethod
    def load(cls, directory: Path) -> Experiment:
        path = directory / "experiment.json"
        try:
            data = json.loads(path.read_text())
        except OSError:
            raise HarnessError(
                f"{directory.name} is not an experiment (no experiment.json)"
            ) from None
        except json.JSONDecodeError as error:
            raise HarnessError(f"{path}: {error}") from None
        agents = [AgentRecord(**entry) for entry in data.pop("agents", [])]
        data["dir"] = directory
        return cls(agents=agents, **data)

    @staticmethod
    def list(root: Path) -> list[Experiment]:
        root = Path(root)
        if not root.is_dir():
            return []
        found = [Experiment.load(d) for d in root.iterdir() if (d / "experiment.json").is_file()]
        return sorted(found, key=lambda e: (e.created, e.id), reverse=True)

    def save(self) -> None:
        data = asdict(self)
        data["dir"] = str(self.dir)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2, default=str))
        tmp.replace(self.path)

    def log(self, message: str) -> None:
        with self.log_path.open("a") as handle:
            handle.write(f"{now()} {message}\n")

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status,
            "protocol": f"experiment: {self.question}",
            "created": self.created,
            "steps": f"{len(self.agents)} agent{'s' if len(self.agents) != 1 else ''}",
        }

    def complete(self) -> bool:
        return all(name in self.documents for name in DOCUMENTS)


class Session:
    """Runs the agents of one experiment. Construct, then ``run`` (again, to resume)."""

    def __init__(
        self,
        experiment: Experiment,
        registry: Registry,
        config: Any = None,
        *,
        model: str | None = None,
        yes: bool = False,
        approve: Approver | None = None,
        on_text: Callable[[str], None] | None = None,
        on_event: EventSink | None = None,
        ask_user: Callable[[], str | None] | None = None,
        max_turns: int = 40,
        stream: bool = True,
        runner: str | None = None,
        remote_cost: str = "moderate",
    ) -> None:
        self.experiment = experiment
        self.registry = registry
        self.config = config
        self.runner = runner
        self.remote_cost = remote_cost
        self.model = model or experiment.model
        self.yes = yes
        self.approve = approve
        self.on_text = on_text or (lambda piece: None)
        self.on_event = on_event or (lambda kind, record, payload: None)
        self.ask_user = ask_user
        self.max_turns = max_turns
        self.stream = stream
        self.budget = Budget(**experiment.budget) if experiment.budget else Budget()
        self._adapters: dict[str, Adapter] = {}
        self._started_at = time.monotonic() - experiment.seconds

    # --- the orchestrator ---------------------------------------------------------

    def run(self, message: str | None = None) -> Experiment:
        """Start or resume the orchestrator; ``message`` is the user's follow-up on resume."""
        experiment = self.experiment
        experiment.status = "running"
        experiment.started = experiment.started or now()
        experiment.error = None
        experiment.save()
        record = self._orchestrator()
        role = ROLES["orchestrate"]
        messages = self._load_transcript(record)
        if message:
            messages.append(Message.user(message))
        elif not messages:
            messages.append(Message.user(experiment.question))
        nudged = False
        try:
            while True:
                outcome = self._run_agent(record, role, messages, depth=0)
                if outcome.stop == "stopped":
                    experiment.status = "budget_exceeded"
                    experiment.error = outcome.detail
                    break
                if outcome.stop != "end":
                    experiment.status = "error"
                    experiment.error = self._describe_stop(outcome)
                    break
                if self.ask_user is not None:
                    follow_up = self.ask_user()
                    if follow_up is None or follow_up.strip() in ("", "/quit", "/exit", "/q"):
                        break
                    messages.append(Message.user(follow_up))
                    continue
                if not experiment.complete() and not nudged:
                    nudged = True
                    messages.append(Message.user(FINISH_NUDGE))
                    continue
                break
        except HarnessError as error:
            experiment.status = "error"
            experiment.error = str(error)
        if experiment.status == "running":
            experiment.status = "done" if experiment.complete() else "paused"
            if experiment.status == "paused":
                experiment.error = (
                    "finish_experiment was not called; resume with "
                    f"`amide experiment --resume {experiment.id}` to continue"
                )
        experiment.finished = now()
        self._account()
        experiment.save()
        experiment.log(f"experiment {experiment.id}: {experiment.status}")
        return experiment

    def _orchestrator(self) -> AgentRecord:
        for record in self.experiment.agents:
            if record.role == "orchestrate":
                return record
        return self._new_record("orchestrate", self.experiment.question, None, None)

    # --- running one agent --------------------------------------------------------------

    def _run_agent(self, record: AgentRecord, role: Role, messages: list[Message], depth: int):
        experiment = self.experiment
        adapter, model_name, model_id = self._adapter_for(role, record.model or None)
        record.model = model_name
        record.status = "running"
        record.started = record.started or now()
        record.error = None
        agent_dir = experiment.agent_dir(record)
        agent_dir.mkdir(parents=True, exist_ok=True)
        experiment.save()
        self.on_event("agent_start", record, None)

        def log(message: str) -> None:
            with (agent_dir / "log.txt").open("a") as handle:
                handle.write(f"{now()} {message}\n")

        registry = self._registry_for(role, record, depth)
        request = Request(
            model=model_id,
            messages=messages,
            system=system_prompt(
                role,
                experiment.question,
                str(experiment.dir),
                str(experiment.workdir),
                interactive=self.ask_user is not None,
            ),
            tools=[spec.to_schema() for spec in registry],
            max_tokens=self._max_tokens(),
        )
        self._save_transcript(record, request)
        ctx = ToolContext(workdir=experiment.workdir, run_dir=experiment.dir, log=log)
        seen_usage = Usage()

        def after_turn(reply: Reply) -> None:
            nonlocal seen_usage
            seen_usage = seen_usage + reply.usage
            record.input_tokens += reply.usage.input_tokens
            record.output_tokens += reply.usage.output_tokens
            record.turns += 1
            experiment.input_tokens += reply.usage.input_tokens
            experiment.output_tokens += reply.usage.output_tokens
            experiment.dollars += self._cost(model_name, reply.usage)
            self._account()
            self._save_transcript(record, request)
            experiment.save()

        def on_call(call: ToolCall, result: str, ok: bool) -> None:
            record.calls += 1
            self.on_event("call", record, (call, result, ok))

        outcome = converse(
            adapter,
            request,
            registry,
            ctx,
            yes=self.yes,
            approve=self.approve,
            on_text=self.on_text
            if depth == 0
            else (lambda piece: self.on_event("text", record, piece)),
            on_call=on_call,
            max_turns=self.max_turns,
            stream=self.stream,
            stop_if=self._budget_reason,
            after_turn=after_turn,
            after_results=lambda: self._save_transcript(record, request),
            annotate=self._budget_note,
        )
        record.result = outcome.reply.text
        if outcome.stop == "end":
            record.status = "done"
        elif outcome.stop == "stopped":
            record.status = "budget_exceeded"
            record.error = outcome.detail
        else:
            record.status = "error"
            record.error = self._describe_stop(outcome)
        record.finished = now()
        experiment.save()
        self.on_event("agent_done", record, outcome)
        return outcome

    def _spawn(self, role_name: str, task: str, model: str | None, parent: AgentRecord, depth: int):
        if role_name not in ROLES:
            raise ToolError(f"no role named {role_name!r}; roles: {', '.join(ROLES)}")
        role = ROLES[role_name]
        record = self._new_record(role_name, task, model, parent.n)
        self._run_agent(record, role, [Message.user(task)], depth)
        return {
            "agent": record.n,
            "role": role_name,
            "status": record.status,
            "result": record.result or record.error or "",
            "tool_calls": record.calls,
        }

    def _new_record(
        self, role: str, task: str, model: str | None, parent: int | None
    ) -> AgentRecord:
        record = AgentRecord(
            n=len(self.experiment.agents) + 1,
            role=role,
            task=task,
            model=model or "",
            parent=parent,
        )
        self.experiment.agents.append(record)
        return record

    # --- models, budgets, accounting -----------------------------------------------

    def _adapter_for(self, role: Role, override: str | None) -> tuple[Adapter, str, str]:
        agents_config = self.config.agents if self.config is not None else {}
        models = agents_config.get("models") or {}
        name = override or models.get(role.name) or self.model or agents_config.get("model")
        provider, model_id = resolve(name, self.config)
        if provider.name not in self._adapters:
            self._adapters[provider.name] = provider.adapter()
        return self._adapters[provider.name], f"{provider.name}/{model_id}", model_id

    def _max_tokens(self) -> int:
        defaults = self.config.defaults if self.config is not None else {}
        return int(defaults.get("max_tokens") or 16000)

    def _cost(self, model_name: str, usage: Usage) -> float:
        pricing = self.config.pricing if self.config is not None else {}
        entry = pricing.get(model_name)
        if not entry:
            return 0.0
        if isinstance(entry, dict):
            price_in, price_out = entry.get("input", 0), entry.get("output", 0)
        else:
            price_in, price_out = entry[0], entry[1]
        return (usage.input_tokens * float(price_in) + usage.output_tokens * float(price_out)) / 1e6

    def _account(self) -> None:
        self.experiment.seconds = round(time.monotonic() - self._started_at, 1)

    def _spent(self) -> tuple[int, float, float]:
        experiment = self.experiment
        self._account()
        return (
            experiment.input_tokens + experiment.output_tokens,
            experiment.seconds,
            experiment.dollars,
        )

    def _budget_reason(self) -> str | None:
        return self.budget.exceeded(*self._spent())

    def _budget_note(self) -> str | None:
        fractions = self.budget.fractions(*self._spent())
        if not fractions:
            return None
        kind, fraction = max(fractions.items(), key=lambda item: item[1])
        if fraction < 0.8:
            return None
        return (
            f"{min(int(fraction * 100), 100)}% of the {kind} budget is spent; "
            "stop exploring and finish with what you have"
        )

    @staticmethod
    def _describe_stop(outcome) -> str:
        return {
            "refusal": f"the model declined: {outcome.reply.detail}",
            "length": "a reply hit the max_tokens cap",
            "max_turns": "the agent used every turn it was allowed without finishing",
        }.get(outcome.stop, f"the model stopped with {outcome.stop}")

    # --- transcripts ------------------------------------------------------------------

    def _transcript_path(self, record: AgentRecord) -> Path:
        return self.experiment.agent_dir(record) / "transcript.json"

    def _save_transcript(self, record: AgentRecord, request: Request) -> None:
        data = {
            "agent": record.n,
            "role": record.role,
            "model": record.model,
            "system": request.system,
            "messages": [_message_dict(m) for m in request.messages],
        }
        path = self._transcript_path(record)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, default=str))

    def _load_transcript(self, record: AgentRecord) -> list[Message]:
        path = self._transcript_path(record)
        if not path.exists():
            return []
        data = json.loads(path.read_text())
        return [_message_from(entry) for entry in data.get("messages", [])]

    # --- the tools a role gets --------------------------------------------------------

    def _registry_for(self, role: Role, record: AgentRecord, depth: int) -> Registry:
        registry = Registry()
        for spec in self.registry:
            if role.selects(spec) and not Registry.missing(spec):
                registry.add(spec)
        available = self._session_tools(record, role, depth)
        for name in role.session:
            if name in available:
                registry.add(available[name])
        return registry

    def _session_tools(self, record: AgentRecord, role: Role, depth: int) -> dict[str, ToolSpec]:
        experiment = self.experiment
        tools: dict[str, ToolSpec] = {}

        def add(name, description, inputs, outputs, run, cost="cheap"):
            tools[name] = ToolSpec(
                name=name,
                description=description,
                run=run,
                inputs=tuple(inputs),
                outputs=tuple(outputs),
                cost=cost,
                source="session",
            )

        add(
            "read_file",
            "Read a text file. Relative paths are under the experiment directory.",
            [
                Param("path", "path", required=True),
                Param(
                    "max_chars", "integer", "Truncate after this many characters.", default=20000
                ),
            ],
            [Param("path", "path"), Param("text", "string"), Param("truncated", "boolean")],
            lambda ctx, args: _read_file(experiment.dir, **args),
        )
        add(
            "write_file",
            "Write a text file under the experiment directory (relative paths, no escaping it).",
            [Param("path", "path", required=True), Param("text", "string", required=True)],
            [Param("path", "path"), Param("bytes", "integer")],
            lambda ctx, args: _write_file(experiment.dir, **args),
        )
        add(
            "list_files",
            "List a directory under the experiment directory.",
            [Param("path", "path", default=".")],
            [Param("path", "path"), Param("entries", "list")],
            lambda ctx, args: _list_files(experiment.dir, **args),
        )
        add(
            "describe_tools",
            "Manifests of registry tools: inputs, outputs, cost, and whether they can run here. "
            "No names lists every tool briefly.",
            [Param("names", "list", "Tool names; empty for all.", default=[])],
            [Param("tools", "list")],
            lambda ctx, args: _describe_tools(self.registry, **args),
        )
        add(
            "describe_protocol",
            "Bundled and local protocols. No name lists them; a name returns its full YAML.",
            [Param("name", "string", default="")],
            [Param("protocols", "list"), Param("text", "string")],
            lambda ctx, args: _describe_protocol(**args),
        )
        add(
            "run_protocol",
            "Run a protocol: a bundled or local name, or complete protocol YAML written for this "
            "experiment. Returns the run's status, checks, and outputs; the run directory keeps "
            "every file and a report.md. Expensive steps need the user's approval.",
            [
                Param("protocol", "string", "A protocol name or YAML text.", required=True),
                Param("params", "object", "Parameter overrides.", default={}),
                Param("name", "string", "File name for YAML text; default: its name field."),
            ],
            [
                Param("run", "string"),
                Param("status", "string"),
                Param("error", "string"),
                Param("checks", "list"),
                Param("outputs", "object"),
                Param("steps", "object"),
                Param("report", "path"),
                Param("dir", "path"),
            ],
            lambda ctx, args: self._run_protocol(record, **args),
            cost="moderate",
        )
        if role.can_spawn and depth < MAX_DEPTH:
            add(
                "spawn_agent",
                "Run a sub-agent to completion on one self-contained task and get its report. "
                f"Roles: {', '.join(f'{r.name} ({r.description})' for r in ROLES.values())}. "
                "It starts with no memory of this conversation: include everything it needs.",
                [
                    Param("role", "string", required=True, choices=tuple(ROLES)),
                    Param("task", "string", required=True),
                    Param("model", "string", "provider/model to use instead of the default."),
                ],
                [
                    Param("agent", "integer"),
                    Param("role", "string"),
                    Param("status", "string"),
                    Param("result", "string"),
                    Param("tool_calls", "integer"),
                ],
                lambda ctx, args: self._spawn(
                    args["role"], args["task"], args.get("model") or None, record, depth + 1
                ),
                cost="moderate",
            )
        add(
            "finish_experiment",
            "Write the experiment's three documents and end it. Call once, at the end.",
            [
                Param(
                    "abstract",
                    "string",
                    "One paragraph: question, method, result, conclusion.",
                    required=True,
                ),
                Param(
                    "methodology",
                    "string",
                    "Every step and parameter, with the final protocol YAML verbatim.",
                    required=True,
                ),
                Param(
                    "results",
                    "string",
                    "Numbers and checks with their run ids, interpretation, limitations.",
                    required=True,
                ),
            ],
            [Param("paths", "list")],
            lambda ctx, args: self._finish(**args),
        )
        return tools

    def _run_protocol(
        self,
        record: AgentRecord,
        protocol: str,
        params: dict | None = None,
        name: str | None = None,
    ) -> dict[str, Any]:
        from amide.harness.protocol import find, load
        from amide.harness.runner import RunOptions, execute
        from amide.harness.runs import RunStore

        experiment = self.experiment
        if _looks_like_yaml(protocol):
            import yaml

            try:
                data = yaml.safe_load(protocol)
            except yaml.YAMLError as error:
                raise ToolError(f"protocol YAML does not parse: {error}") from None
            if not isinstance(data, dict) or not data.get("name"):
                raise ToolError("protocol YAML must be a mapping with a name")
            base = _slug(name or str(data["name"]))
            directory = experiment.dir / "protocols"
            directory.mkdir(exist_ok=True)
            path = directory / f"{base}.yaml"
            counter = 2
            while path.exists() and path.read_text() != protocol:
                path = directory / f"{base}-{counter}.yaml"
                counter += 1
            path.write_text(protocol)
            spec = load(path)
        else:
            spec = find(protocol)
        problems = spec.problems(self.registry)
        if problems:
            raise ToolError("protocol is not valid: " + "; ".join(problems))
        resolved = spec.resolve_params(params or {})
        store = RunStore(experiment.dir / "runs")
        state = store.create(spec, resolved)
        record_ref = record

        def approve(step, tool, args) -> bool:
            return self.approve is not None and self.approve(tool, args)

        from amide.harness.remote import runners_from_config

        options = RunOptions(
            yes=self.yes,
            approve=approve,
            echo=lambda message: self.on_event("run", record_ref, message),
            runners=runners_from_config(self.config),
            runner=self.runner,
            remote_cost=self.remote_cost,
        )
        state = execute(state, spec, self.registry, options)
        return {
            "run": state.id,
            "status": state.status,
            "error": state.error or "",
            "checks": [
                {k: entry.get(k) for k in ("id", "passed", "value", "error", "description")}
                for entry in state.checks
            ],
            "outputs": state.outputs,
            "steps": {
                step_id: {"status": step.status, "error": step.error, "outputs": step.outputs}
                for step_id, step in state.steps.items()
            },
            "report": str(state.dir / "report.md"),
            "dir": str(state.dir),
        }

    def _finish(self, abstract: str, methodology: str, results: str) -> dict[str, Any]:
        experiment = self.experiment
        paths = []
        for name, text in (
            ("abstract", abstract),
            ("methodology", methodology),
            ("results", results),
        ):
            if not text.strip():
                raise ToolError(f"{name} is empty")
            path = experiment.dir / f"{name}.md"
            path.write_text(text.rstrip() + "\n")
            experiment.documents[name] = str(path)
            paths.append(str(path))
        experiment.save()
        return {"paths": paths}


# --- helpers ------------------------------------------------------------------------------


def _looks_like_yaml(text: str) -> bool:
    stripped = text.strip()
    return "\n" in stripped or stripped.startswith(("name:", "{"))


def _slug(name: str) -> str:
    import re

    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", name).strip("-")
    return slug or "protocol"


def _inside(root: Path, path: str) -> Path:
    target = (root / path).resolve() if not Path(path).is_absolute() else Path(path).resolve()
    if root.resolve() not in (target, *target.parents):
        raise ToolError(f"{path} is outside the experiment directory {root}")
    return target


def _read_file(root: Path, path: str, max_chars: int = 20000) -> dict[str, Any]:
    target = (root / path) if not Path(path).is_absolute() else Path(path)
    if not target.is_file():
        raise ToolError(f"{path} is not a file")
    try:
        text = target.read_text(errors="replace")
    except OSError as error:
        raise ToolError(f"cannot read {path}: {error}") from None
    truncated = len(text) > max_chars
    return {"path": str(target), "text": text[:max_chars], "truncated": truncated}


def _write_file(root: Path, path: str, text: str) -> dict[str, Any]:
    target = _inside(root, path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)
    return {"path": str(target), "bytes": len(text.encode())}


def _list_files(root: Path, path: str = ".") -> dict[str, Any]:
    target = _inside(root, path)
    if not target.is_dir():
        raise ToolError(f"{path} is not a directory")
    entries = []
    for entry in sorted(target.iterdir())[:200]:
        entries.append(
            {
                "name": entry.name,
                "kind": "dir" if entry.is_dir() else "file",
                "size": entry.stat().st_size if entry.is_file() else None,
            }
        )
    return {"path": str(target), "entries": entries}


def _describe_tools(registry: Registry, names: list | None = None) -> dict[str, Any]:
    names = [str(n) for n in (names or [])]
    unknown = [n for n in names if n not in registry]
    if unknown:
        raise ToolError(
            f"no such tool: {', '.join(unknown)}; available: {', '.join(registry.names())}"
        )
    specs = [registry.get(n) for n in names] if names else list(registry)
    tools = []
    for spec in specs:
        entry: dict[str, Any] = {
            "name": spec.name,
            "description": spec.description,
            "cost": spec.cost,
            "missing": Registry.missing(spec),
        }
        if names:
            manifest = spec.to_dict()
            entry["inputs"] = manifest["inputs"]
            entry["outputs"] = manifest["outputs"]
            entry["tags"] = manifest["tags"]
        tools.append(entry)
    return {"tools": tools}


def _describe_protocol(name: str = "") -> dict[str, Any]:
    from amide.harness.protocol import available, find

    if not name:
        listing = []
        for protocol in available():
            listing.append(
                {
                    "name": protocol.name,
                    "description": protocol.description,
                    "params": {
                        pname: {"type": p.type, "default": p.default, "description": p.description}
                        for pname, p in protocol.params.items()
                    },
                    "steps": [f"{s.id}: {s.tool}" for s in protocol.steps],
                }
            )
        return {"protocols": listing, "text": ""}
    protocol = find(name)
    return {"protocols": [{"name": protocol.name}], "text": protocol.text or ""}


def _message_dict(message: Message) -> dict[str, Any]:
    entry: dict[str, Any] = {"role": message.role, "content": message.content}
    if message.tool_calls:
        entry["tool_calls"] = [
            {"id": c.id, "name": c.name, "arguments": c.arguments, "error": c.error}
            for c in message.tool_calls
        ]
    if message.tool_call_id is not None:
        entry["tool_call_id"] = message.tool_call_id
        entry["name"] = message.name
        entry["is_error"] = message.is_error
    if message.raw is not None:
        entry["raw"] = message.raw
    return entry


def _message_from(entry: dict[str, Any]) -> Message:
    calls = [
        ToolCall(c["id"], c["name"], dict(c.get("arguments") or {}), error=c.get("error"))
        for c in entry.get("tool_calls") or []
    ]
    return Message(
        entry["role"],
        entry.get("content") or "",
        tool_calls=calls,
        tool_call_id=entry.get("tool_call_id"),
        name=entry.get("name"),
        is_error=bool(entry.get("is_error")),
        raw=entry.get("raw"),
    )
