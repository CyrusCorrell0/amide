import json
import tarfile
from pathlib import Path

from amide.harness.protocol import from_dict
from amide.harness.runner import RunOptions, execute
from amide.harness.runs import RunStore
from tests.conftest import STUB_PROTOCOL


def _run(tmp_path, protocol, registry, params=None, **options):
    store = RunStore(tmp_path / "runs")
    state = store.create(protocol, protocol.resolve_params(params or {}))
    return store, execute(state, protocol, registry, RunOptions(**options))


def test_passing_run_writes_everything(tmp_path, stub_protocol, stub_registry):
    store, state = _run(tmp_path, stub_protocol, stub_registry, {"a": 4})
    assert state.status == "passed"
    assert state.steps["first"].outputs == {"sum": 7}
    assert state.steps["second"].outputs == {"sum": 17}
    assert state.steps["annotate"].status == "skipped"
    assert state.outputs["total"] == 17
    assert Path(state.outputs["file"]).read_text() == "total=17"
    assert state.checks == [
        {
            "id": "small",
            "expr": "steps.second.sum < params.limit",
            "description": "Stays small.",
            "passed": True,
            "value": True,
        }
    ]

    assert (state.dir / "protocol.yaml").exists()
    results = json.loads((state.dir / "results.json").read_text())
    assert results["status"] == "passed"
    assert results["steps"]["second"]["outputs"] == {"sum": 17}
    report = (state.dir / "report.md").read_text()
    assert "Status: **passed**" in report
    assert "| small | pass |" in report
    assert (state.dir / "steps" / "first" / "log.txt").read_text().endswith("adding 4 and 3\n")
    assert json.loads((state.dir / "steps" / "first" / "outputs.json").read_text()) == {"sum": 7}
    assert "run " in (state.dir / "run.log").read_text()

    reloaded = store.get(state.id)
    assert reloaded.status == "passed"
    assert reloaded.steps["second"].seconds is not None


def test_when_runs_the_step(tmp_path, stub_protocol, stub_registry):
    _, state = _run(tmp_path, stub_protocol, stub_registry, {"note": "hello"})
    assert state.steps["annotate"].status == "done"
    assert Path(state.steps["annotate"].outputs["path"]).read_text() == "hello"


def test_failed_check(tmp_path, stub_protocol, stub_registry):
    _, state = _run(tmp_path, stub_protocol, stub_registry, {"limit": 5})
    assert state.status == "failed"
    assert state.error == "checks failed: small"
    assert state.checks[0]["passed"] is False
    assert "| small | FAIL |" in (state.dir / "report.md").read_text()


def test_step_error_stops_the_run(tmp_path, stub_registry):
    protocol = from_dict(
        {
            "name": "p",
            "steps": [
                {"id": "ok", "tool": "add", "with": {"a": 1}},
                {"id": "bad", "tool": "boom", "with": {"message": "kaboom"}},
                {"id": "never", "tool": "add", "with": {"a": 1}},
            ],
        }
    )
    _, state = _run(tmp_path, protocol, stub_registry)
    assert state.status == "error"
    assert "step bad (boom) failed: kaboom" in state.error
    assert state.steps["ok"].status == "done"
    assert state.steps["bad"].status == "error"
    assert state.steps["bad"].error == "RuntimeError: kaboom"
    assert state.steps["never"].status == "pending"
    assert "Traceback" in (state.dir / "steps" / "bad" / "log.txt").read_text()


def test_bad_inputs_name_the_step(tmp_path, stub_registry):
    protocol = from_dict(
        {"name": "p", "steps": [{"id": "s", "tool": "add", "with": {"a": "seven"}}]}
    )
    _, state = _run(tmp_path, protocol, stub_registry)
    assert state.status == "error"
    assert state.error == "step s: a must be integer, got 'seven' (str)"


def test_missing_requirements_fail_before_running(tmp_path, stub_registry):
    protocol = from_dict({"name": "p", "steps": [{"id": "s", "tool": "needs_unicorn"}]})
    _, state = _run(tmp_path, protocol, stub_registry)
    assert state.status == "error"
    assert (
        state.error
        == "step s (needs_unicorn) needs python module unicorn_module_xyz. There is no such thing."
    )
    assert state.steps["s"].status == "pending"


def test_expensive_step_pauses_then_resumes_with_yes(tmp_path, stub_registry):
    protocol = from_dict(
        {
            "name": "p",
            "steps": [
                {"id": "cheap", "tool": "add", "with": {"a": 1}},
                {"id": "costly", "tool": "pricey"},
                {"id": "after", "tool": "add", "with": {"a": 2}},
            ],
        }
    )
    store, state = _run(tmp_path, protocol, stub_registry)
    assert state.status == "paused"
    assert f"--resume {state.id} --yes" in state.error
    assert state.steps["cheap"].status == "done"
    assert state.steps["costly"].status == "pending"

    resumed = execute(store.get(state.id), protocol, stub_registry, RunOptions(yes=True))
    assert resumed.status == "passed"
    assert resumed.steps["costly"].outputs == {"ok": True}
    assert resumed.steps["after"].status == "done"
    assert "done (resumed)" in resumed.log_path.read_text()


def test_approver_decides(tmp_path, stub_registry):
    protocol = from_dict({"name": "p", "steps": [{"id": "costly", "tool": "pricey"}]})
    asked = []

    def approve(step, spec, args):
        asked.append((step.id, spec.name, args))
        return True

    _, state = _run(tmp_path, protocol, stub_registry, approve=approve)
    assert state.status == "passed"
    assert asked == [("costly", "pricey", {})]

    _, state = _run(tmp_path, protocol, stub_registry, approve=lambda *a: False)
    assert state.status == "paused"


def test_budget_stops_before_next_step(tmp_path, stub_registry, monkeypatch):
    import time

    protocol = from_dict(
        {
            "name": "p",
            "steps": [
                {"id": "a", "tool": "add", "with": {"a": 1}},
                {"id": "b", "tool": "add", "with": {"a": 1}},
            ],
        }
    )
    clock = iter([0.0, 0.0, 0.0, 100.0, 100.0, 100.0, 100.0, 100.0])
    monkeypatch.setattr(time, "monotonic", lambda: next(clock, 100.0))
    store, state = _run(tmp_path, protocol, stub_registry, max_seconds=10)
    assert state.status == "budget_exceeded"
    assert "before b" in state.error
    assert state.steps["a"].status == "done"
    monkeypatch.undo()
    resumed = execute(store.get(state.id), protocol, stub_registry, RunOptions())
    assert resumed.status == "passed"


def test_skipped_step_outputs_are_not_available(tmp_path, stub_registry):
    protocol = from_dict(
        {
            "name": "p",
            "steps": [
                {"id": "maybe", "tool": "add", "when": "False", "with": {"a": 1}},
                {"id": "uses", "tool": "add", "with": {"a": "{{ steps.maybe.sum }}"}},
            ],
        }
    )
    _, state = _run(tmp_path, protocol, stub_registry)
    assert state.status == "error"
    assert "steps.maybe is not defined" in state.error


def test_store_list_get_prefix_and_export(tmp_path, stub_protocol, stub_registry):
    store, state = _run(tmp_path, stub_protocol, stub_registry)
    assert [s.id for s in store.list()] == [state.id]
    assert store.get(state.id[:8]).id == state.id
    archive = store.export(state.id, tmp_path / "out.tar.gz")
    manifest = json.loads((state.dir / "manifest.json").read_text())
    assert "run.json" in manifest["files"]
    assert "steps/save/out.txt" in manifest["files"]
    assert len(manifest["files"]["run.json"]) == 64
    with tarfile.open(archive) as tar:
        names = tar.getnames()
    assert f"{state.id}/report.md" in names
    assert f"{state.id}/manifest.json" in names


def test_protocol_copy_is_reloadable_when_built_in_memory(tmp_path, stub_registry):
    from amide.harness.protocol import load

    protocol = from_dict(STUB_PROTOCOL)
    store = RunStore(tmp_path / "runs")
    state = store.create(protocol, protocol.resolve_params())
    reloaded = load(state.protocol_path)
    assert [s.id for s in reloaded.steps] == [s.id for s in protocol.steps]
    assert reloaded.params["a"].default == 2
