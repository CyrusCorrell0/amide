import json
from pathlib import Path

import pytest
import yaml

from amide import config as config_module
from amide.agents.roles import ROLES, system_prompt
from amide.agents.session import (
    Budget,
    Experiment,
    Session,
    _describe_protocol,
    _describe_tools,
    _list_files,
    _read_file,
    _write_file,
)
from amide.harness.errors import ToolError
from amide.harness.registry import Registry
from amide.harness.runs import RunStore
from amide.models import Reply, ToolCall, Usage
from amide.models.providers import Provider

ARITH_YAML = yaml.safe_dump(
    {
        "name": "double",
        "params": {"x": 4},
        "steps": [
            {"id": "twice", "tool": "add", "with": {"a": "{{ params.x }}", "b": "{{ params.x }}"}}
        ],
        "checks": [{"id": "even", "expr": "steps.twice.sum % 2 == 0"}],
        "outputs": {"value": "{{ steps.twice.sum }}"},
    }
)


class Scripted:
    """One queue of replies consumed in call order, whatever agent is calling."""

    def __init__(self):
        self.replies: list[Reply] = []
        self.requests: list[dict] = []

    def complete(self, request):
        self.requests.append(
            {
                "model": request.model,
                "system": request.system,
                "tools": sorted(t["name"] for t in request.tools),
                "messages": [(m.role, m.content) for m in request.messages],
            }
        )
        if not self.replies:
            raise AssertionError("the script ran out of replies")
        return self.replies.pop(0)

    def stream(self, request, on_text):
        reply = self.complete(request)
        if reply.text:
            on_text(reply.text)
        return reply

    def list_models(self):
        return []


@pytest.fixture
def script(monkeypatch):
    scripted = Scripted()
    monkeypatch.setattr(Provider, "adapter", lambda self: scripted)
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    return scripted


def _call(name, n="1", **arguments):
    return ToolCall(n, name, arguments)


def _tool_turn(*calls, text="", usage=None):
    return Reply(text=text, tool_calls=list(calls), stop="tool_calls", usage=usage or Usage(10, 5))


def _end(text, usage=None):
    return Reply(text=text, stop="end", usage=usage or Usage(10, 5))


FINISH = _call(
    "finish_experiment",
    abstract="We doubled four.",
    methodology="Ran protocol double with x=4.\n\n```yaml\n" + ARITH_YAML + "```",
    results="value = 8, check even passed (run see runs/).",
)


@pytest.fixture
def experiment(project: Path):
    return Experiment.create(project / ".amide" / "runs", "what is twice four?", "openai/gpt-x")


def _session(experiment, project, **kwargs):
    config = config_module.load(project / "config.toml")
    registry = Registry.load(config, cwd=project)
    return Session(experiment, registry, config, **kwargs)


def test_full_experiment(project: Path, experiment, script):
    script.replies = [
        _tool_turn(_call("spawn_agent", role="find", task="Find four."), text="Delegating."),
        # the find agent
        _tool_turn(_call("shell", command="echo four"), _call("add", n="2", a=1)),
        _end("Found: four (echo). add is not my tool."),
        # back to the orchestrator
        _tool_turn(_call("run_protocol", protocol=ARITH_YAML, params={"x": 4})),
        _tool_turn(_call("describe_protocol"), _call("describe_tools", n="2", names=["add"])),
        _tool_turn(FINISH),
        _end("Done: eight."),
    ]
    text, events = [], []
    session = _session(
        experiment,
        project,
        on_text=text.append,
        on_event=lambda kind, record, payload: events.append((kind, record.n)),
    )
    state = session.run()

    assert state.status == "done", state.error
    assert state.complete()
    assert "".join(text) == "Delegating.Done: eight."
    assert (state.dir / "abstract.md").read_text() == "We doubled four.\n"
    assert "```yaml" in (state.dir / "methodology.md").read_text()
    assert state.input_tokens == 70 and state.output_tokens == 35
    assert [a.role for a in state.agents] == ["orchestrate", "find"]
    orchestrator, finder = state.agents
    assert orchestrator.status == "done" and orchestrator.turns == 5 and orchestrator.calls == 5
    assert finder.parent == 1 and finder.status == "done" and finder.calls == 2
    assert finder.result.startswith("Found: four")
    assert finder.model == "openai/gpt-x"
    assert ("agent_start", 2) in events and ("agent_done", 2) in events
    assert ("call", 2) in events and ("run", 1) in events

    # The find agent had only its role's tools, so `add` was refused.
    find_request = script.requests[1]
    assert "shell" in find_request["tools"] and "rcsb_fetch" in find_request["tools"]
    assert "add" not in find_request["tools"] and "spawn_agent" not in find_request["tools"]
    assert "Your role: find" in find_request["system"]
    find_transcript = json.loads((state.agent_dir(finder) / "transcript.json").read_text())
    results = [m for m in find_transcript["messages"] if m["role"] == "tool"]
    assert "four" in results[0]["content"] and results[0]["is_error"] is False
    assert "no tool named 'add'" in results[1]["content"] and results[1]["is_error"] is True

    # The orchestrator saw the sub-agent's report, then the protocol run.
    orchestrator_request = script.requests[3]
    tool_messages = [c for r, c in orchestrator_request["messages"] if r == "tool"]
    spawn_result = json.loads(tool_messages[0])
    assert spawn_result["role"] == "find" and spawn_result["status"] == "done"
    assert spawn_result["result"].startswith("Found: four")
    run_result = json.loads([c for r, c in script.requests[4]["messages"] if r == "tool"][-1])
    assert run_result["status"] == "passed" and run_result["outputs"] == {"value": 8}
    assert run_result["checks"][0]["passed"] is True
    assert (state.dir / "protocols" / "double.yaml").read_text() == ARITH_YAML
    (run_dir,) = (state.dir / "runs").iterdir()
    assert run_result["run"] == run_dir.name and (run_dir / "report.md").exists()
    described = [c for r, c in script.requests[5]["messages"] if r == "tool"][-2:]
    assert "arith" in described[0] and "openmm-control" in described[0]
    assert json.loads(described[1])["tools"][0]["inputs"][0]["name"] == "a"

    # It is listed next to protocol runs and reloads intact.
    store = RunStore(project / ".amide" / "runs")
    reloaded = store.get_any(state.id)
    assert isinstance(reloaded, Experiment)
    assert reloaded.agents[1].task == "Find four."
    assert Experiment.list(store.root)[0].id == state.id
    assert reloaded.summary()["steps"] == "2 agents"
    archive = store.export(state.id, project / "exp.tar.gz")
    assert archive.exists()


def test_nudge_then_paused_and_resume(project: Path, experiment, script):
    script.replies = [_end("I think it is eight."), _end("Still thinking.")]
    state = _session(experiment, project).run()
    assert state.status == "paused"
    assert "finish_experiment was not called" in state.error
    nudge = script.requests[1]["messages"][-1]
    assert nudge[0] == "user" and "Finish now" in nudge[1]

    # Resume with a follow-up: the transcript comes back, then it finishes.
    script.replies = [_tool_turn(FINISH), _end("Finished.")]
    resumed = Experiment.load(state.dir)
    state = _session(resumed, project).run("Please finish with what you have.")
    assert state.status == "done"
    roles = [r for r, _ in script.requests[2]["messages"]]
    assert roles == ["user", "assistant", "user", "assistant", "user"]
    assert script.requests[2]["messages"][-1][1] == "Please finish with what you have."
    assert len(state.agents) == 1 and state.agents[0].turns == 4


def test_interactive_follow_ups(project: Path, experiment, script):
    script.replies = [_end("Shall I run it?"), _tool_turn(FINISH), _end("Done.")]
    answers = iter(["yes, go", None])
    state = _session(experiment, project, ask_user=lambda: next(answers)).run()
    assert state.status == "done"
    assert script.requests[1]["messages"][-1] == ("user", "yes, go")
    assert "The user is present" in script.requests[0]["system"]


def test_budget_stops_the_run_and_warns_first(project: Path, experiment, script, monkeypatch):
    experiment.budget = Budget(max_tokens=100).to_dict()
    experiment.save()
    script.replies = [
        _tool_turn(_call("list_files"), usage=Usage(70, 15)),  # 85% after this turn
        _tool_turn(_call("list_files"), usage=Usage(10, 10)),  # over
        _end("never reached"),
    ]
    state = _session(experiment, project).run()
    assert state.status == "budget_exceeded"
    assert "token budget of 100 spent" in state.error
    note = script.requests[1]["messages"][-1][1]
    assert "harness note: 85% of the tokens budget is spent" in note
    assert state.agents[0].status == "budget_exceeded"
    assert len(script.replies) == 1


def test_dollar_budget_uses_pricing(project: Path, experiment, script):
    (project / "config.toml").write_text(
        '[pricing]\n"openai/gpt-x" = { input = 1000000.0, output = 0.0 }\n'
    )
    experiment.budget = Budget(max_dollars=15).to_dict()
    experiment.save()
    free = Usage(0, 0)
    script.replies = [
        _tool_turn(_call("list_files"), usage=Usage(10, 0)),
        _end("x", usage=free),
        _end("x", usage=free),  # after the finish nudge
    ]
    state = _session(experiment, project).run()
    assert state.dollars == 10.0
    assert state.status == "paused"  # under budget, just never finished
    script.replies = [_tool_turn(_call("list_files"), usage=Usage(10, 0)), _end("x", usage=free)]
    state = _session(Experiment.load(state.dir), project).run("more")
    assert state.status == "budget_exceeded" and "dollar budget of $15" in state.error


def test_model_choice_per_role_and_spawn_override(project: Path, experiment, script, monkeypatch):
    (project / "config.toml").write_text(
        '[agents]\nmodel = "openai/base"\n[agents.models]\nfind = "deepseek/finder"\n'
    )
    experiment.model = None
    script.replies = [
        _tool_turn(
            _call("spawn_agent", role="find", task="a"),
            _call("spawn_agent", n="2", role="review", task="b", model="groq/fast"),
            _call("spawn_agent", n="3", role="nope", task="c"),
        ),
        _end("found"),
        _end("reviewed"),
        _tool_turn(FINISH),
        _end("done"),
    ]
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
    monkeypatch.setenv("GROQ_API_KEY", "k")
    state = _session(experiment, project).run()
    assert state.status == "done"
    assert [a.model for a in state.agents] == ["openai/base", "deepseek/finder", "groq/fast"]
    assert script.requests[0]["model"] == "base"
    assert script.requests[1]["model"] == "finder"
    bad = [c for r, c in script.requests[3]["messages"] if r == "tool"][-1]
    assert "role must be one of orchestrate" in bad and "got 'nope'" in bad


def test_sub_agents_cannot_spawn_forever(project: Path, experiment, script):
    script.replies = [
        _tool_turn(_call("spawn_agent", role="general-purpose", task="a")),
        _tool_turn(_call("spawn_agent", role="general-purpose", task="b")),
        _end("inner"),
        _end("outer"),
        _tool_turn(FINISH),
        _end("done"),
    ]
    state = _session(experiment, project).run()
    assert state.status == "done"
    assert [a.role for a in state.agents] == ["orchestrate", "general-purpose", "general-purpose"]
    assert "spawn_agent" in script.requests[1]["tools"]
    assert "spawn_agent" not in script.requests[2]["tools"]


def test_run_protocol_errors_reach_the_model(project: Path, experiment, script):
    script.replies = [
        _tool_turn(
            _call("run_protocol", protocol="name: [broken"),
            _call("run_protocol", n="2", protocol="name: p\nsteps:\n  - id: s\n    tool: nope\n"),
            _call("run_protocol", n="3", protocol="arith", params={"zzz": 1}),
            _call("run_protocol", n="4", protocol="arith", params={"limit": 1}),
        ),
        _tool_turn(FINISH),
        _end("done"),
    ]
    state = _session(experiment, project).run()
    results = [c for r, c in script.requests[1]["messages"] if r == "tool"]
    assert "does not parse" in results[0]
    assert "unknown tool 'nope'" in results[1]
    assert "no parameter zzz" in results[2]
    failed = json.loads(results[3])
    assert failed["status"] == "failed" and "checks failed: small" in failed["error"]
    assert state.status == "done"


def test_model_error_marks_experiment(project: Path, experiment, script, monkeypatch):
    from amide.models import ModelError

    def boom(request, on_text):
        raise ModelError("https://x returned 500: down", 500)

    monkeypatch.setattr(script, "stream", boom)
    state = _session(experiment, project).run()
    assert state.status == "error" and "500: down" in state.error


def test_refusal_and_max_turns(project: Path, experiment, script):
    script.replies = [Reply(stop="refusal", detail="bio")]
    state = _session(experiment, project).run()
    assert state.status == "error" and "declined: bio" in state.error
    script.replies = [_tool_turn(_call("list_files"))] * 3
    state = _session(Experiment.create(project / "r", "q", "openai/m"), project, max_turns=2).run()
    assert state.status == "error" and "every turn" in state.error


# --- roles and prompts -------------------------------------------------------


def test_roles_select_tools(stub_registry):
    from amide.tools.rcsb_fetch import rcsb_fetch

    fetch = rcsb_fetch.spec
    add = stub_registry.get("add")
    assert ROLES["orchestrate"].selects(fetch) and not ROLES["orchestrate"].selects(add)
    assert ROLES["fix"].selects(add) and ROLES["general-purpose"].selects(fetch)
    assert not ROLES["plan"].selects(fetch) and not ROLES["review"].selects(add)
    assert ROLES["find"].selects(fetch)
    assert {r.name for r in ROLES.values()} == {
        "orchestrate",
        "plan",
        "find",
        "fix",
        "review",
        "general-purpose",
    }


def test_system_prompt_mentions_protocols_where_relevant():
    text = system_prompt(ROLES["orchestrate"], "q", "/e", "/e/files", interactive=False)
    assert "Experiment: q" in text and "run_protocol" in text and "```yaml" in text
    assert "{{ params.pdb_id }}" in text
    assert "The user is present" not in text
    assert "```yaml" not in system_prompt(ROLES["find"], "q", "/e", "/e/files", interactive=True)


# --- session tools -----------------------------------------------------------


def test_file_tools_stay_inside_the_experiment(tmp_path: Path):
    root = tmp_path / "exp"
    root.mkdir()
    assert _write_file(root, "notes/a.txt", "hello")["bytes"] == 5
    assert _read_file(root, "notes/a.txt")["text"] == "hello"
    assert _read_file(root, "notes/a.txt", max_chars=2) == {
        "path": str(root / "notes/a.txt"),
        "text": "he",
        "truncated": True,
    }
    listing = _list_files(root, "notes")
    assert listing["entries"] == [{"name": "a.txt", "kind": "file", "size": 5}]
    with pytest.raises(ToolError, match="outside the experiment"):
        _write_file(root, "../escape.txt", "x")
    with pytest.raises(ToolError, match="outside the experiment"):
        _list_files(root, "..")
    with pytest.raises(ToolError, match="is not a file"):
        _read_file(root, "missing.txt")
    with pytest.raises(ToolError, match="is not a directory"):
        _list_files(root, "notes/a.txt")


def test_describe_tools_and_protocols(project: Path, stub_registry):
    brief = _describe_tools(stub_registry)["tools"]
    assert {t["name"] for t in brief} >= {"add", "needs_unicorn"}
    assert "inputs" not in brief[0]
    needy = next(t for t in brief if t["name"] == "needs_unicorn")
    assert needy["missing"] == ["python module unicorn_module_xyz"]
    full = _describe_tools(stub_registry, ["add"])["tools"][0]
    assert full["inputs"][0] == {"name": "a", "type": "integer", "required": True}
    with pytest.raises(ToolError, match="no such tool: zzz"):
        _describe_tools(stub_registry, ["zzz"])
    listing = _describe_protocol()["protocols"]
    assert [p["name"] for p in listing] == ["arith", "openmm-control"]
    assert listing[0]["params"]["a"]["default"] == 2
    assert "steps:" in _describe_protocol("arith")["text"]
