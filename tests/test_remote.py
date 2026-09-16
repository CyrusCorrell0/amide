import json
import sys
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from amide import config as config_module
from amide.cli import app
from amide.harness.errors import HarnessError, ProtocolError
from amide.harness.protocol import from_dict
from amide.harness.remote import CommandRunner, Runner, _translate, runners_from_config
from amide.harness.runner import RunOptions
from tests.conftest import STUB_PROTOCOL

runner = CliRunner()


def _fake_runner_config(project: Path, remote_root: Path, exec_template: str | None = None) -> str:
    """A runner whose 'remote' is a directory on this machine, driven by cp and sh."""
    return (
        f'[tools]\npaths = ["{project / ".amide" / "tools"}"]\n'
        "[runners.fake]\n"
        'host = "box"\n'
        'copy_to = "cp -r {src} {dst}"\n'
        'copy_from = "cp -r {src} {dst}"\n'
        f'exec = "{exec_template or "sh -c {command}"}"\n'
        f'python = "{sys.executable}"\n'
        f'root = "{remote_root}"\n'
    )


def test_translate_rewrites_only_paths_under_the_root():
    value = {"a": "/local/run/steps/x/f.pdb", "b": ["/local/run", "/local/runner/x"], "c": 3}
    assert _translate(value, "/local/run", "/remote/r") == {
        "a": "/remote/r/steps/x/f.pdb",
        "b": ["/remote/r", "/local/runner/x"],
        "c": 3,
    }


def test_runners_from_config(tmp_path: Path):
    path = tmp_path / "c.toml"
    path.write_text(
        '[runners.gpu]\ncopy_to = "a {src} {dst}"\ncopy_from = "b {src} {dst}"\nexec = "c {command}"\n'
        '[runners.blank]\nhost = "x"\n'
    )
    runners = runners_from_config(config_module.load(path))
    assert sorted(runners) == ["gpu", "local"]
    assert isinstance(runners["local"], Runner) and runners["local"].name == "local"
    gpu = runners["gpu"]
    assert isinstance(gpu, CommandRunner)
    assert gpu.python == "python3" and gpu.root == "/tmp/amide"
    assert runners_from_config(None).keys() == {"local"}
    with pytest.raises(HarnessError, match=r"\[runners.half\] needs copy_from, exec"):
        CommandRunner.from_config("half", {"copy_to": "x"})


def test_runner_for_uses_cost_threshold_and_step_override(stub_registry):
    protocol = from_dict(
        {
            "name": "p",
            "steps": [
                {"id": "a", "tool": "add"},
                {"id": "b", "tool": "pricey"},
                {"id": "c", "tool": "pricey", "runner": "local"},
                {"id": "d", "tool": "add", "runner": "nope"},
            ],
        }
    )
    fake = CommandRunner("fake", "x", "y", "z")
    options = RunOptions(runners={"local": Runner(), "fake": fake}, runner="fake")
    steps = {step.id: step for step in protocol.steps}
    assert options.runner_for(steps["a"], stub_registry.get("add")).name == "local"
    assert options.runner_for(steps["b"], stub_registry.get("pricey")).name == "fake"
    assert options.runner_for(steps["c"], stub_registry.get("pricey")).name == "local"
    with pytest.raises(HarnessError, match="no runner named 'nope'; configured: fake, local"):
        options.runner_for(steps["d"], stub_registry.get("add"))
    options.remote_cost = "cheap"
    assert options.runner_for(steps["a"], stub_registry.get("add")).name == "fake"
    assert RunOptions().runner_for(steps["b"], stub_registry.get("pricey")).name == "local"


def test_protocol_step_runner_field():
    protocol = from_dict({"name": "p", "steps": [{"id": "a", "tool": "t", "runner": "gpu"}]})
    assert protocol.steps[0].runner == "gpu"
    with pytest.raises(ProtocolError, match="runner must be a name"):
        from_dict({"name": "p", "steps": [{"id": "a", "tool": "t", "runner": 5}]})


def test_run_through_a_command_runner_end_to_end(project: Path, tmp_path: Path):
    remote_root = tmp_path / "remote"
    (project / "config.toml").write_text(_fake_runner_config(project, remote_root))
    result = runner.invoke(
        app, ["run", "arith", "--set", "note=hi", "--runner", "fake", "--remote-cost", "cheap"]
    )
    assert result.exit_code == 0, result.output
    assert "first: add on fake ..." in result.stdout and "passed" in result.stdout
    (run_dir,) = [p for p in (project / ".amide" / "runs").iterdir() if p.is_dir()]
    state = json.loads((run_dir / "run.json").read_text())
    assert {s["runner"] for s in state["steps"].values()} == {"fake"}
    assert state["outputs"]["total"] == 15
    # The write tool ran remotely, and its path came back translated to this machine.
    note = Path(state["steps"]["annotate"]["outputs"]["path"])
    assert note == run_dir / "steps" / "annotate" / "note.txt"
    assert note.read_text() == "hi"
    assert not (run_dir / "steps" / "annotate.remote").exists()
    # The remote copy holds what was sent over and what the remote step produced.
    remote_run = remote_root / run_dir.name
    assert (remote_run / "protocol.yaml").exists()
    inputs = json.loads((remote_run / "steps" / "save" / "inputs.json").read_text())
    assert inputs["text"] == "total=15"
    assert (remote_run / "steps" / "save" / "outputs.json").exists()
    log = (run_dir / "steps" / "first" / "log.txt").read_text()
    assert "runner fake: copying" in log and "-m amide tools run add" in log

    # The default threshold keeps cheap steps local.
    result = runner.invoke(app, ["run", "arith", "--runner", "fake"])
    assert result.exit_code == 0, result.output
    assert "on fake" not in result.stdout


def test_command_runner_reports_remote_failures(project: Path, tmp_path: Path):
    (project / "config.toml").write_text(
        _fake_runner_config(project, tmp_path / "remote", exec_template="sh -c {command}; false")
    )
    result = runner.invoke(app, ["run", "arith", "--runner", "fake", "--remote-cost", "cheap"])
    assert result.exit_code == 1
    assert "runner fake:" in result.stdout and "exited 1" in result.stdout
    result = runner.invoke(app, ["run", "arith", "--runner", "zzz"])
    assert result.exit_code == 2
    assert "no runner named 'zzz'" in result.output
    result = runner.invoke(app, ["run", "arith", "--runner", "fake", "--remote-cost", "huge"])
    assert result.exit_code == 2


def test_command_runner_needs_a_step_inside_the_run(tmp_path: Path, stub_registry):
    from amide.harness.tool import ToolContext

    fake = CommandRunner("fake", "x", "y", "z")
    ctx = ToolContext(workdir=tmp_path / "elsewhere", run_dir=tmp_path / "run")
    with pytest.raises(HarnessError, match="not inside the run directory"):
        fake.run_step(stub_registry.get("add"), ctx, {"a": 1})
    bad = CommandRunner("bad", "x", "y", "{nope}", root=str(tmp_path / "r"))
    ctx = ToolContext(workdir=tmp_path / "run" / "steps" / "s", run_dir=tmp_path / "run")
    with pytest.raises(HarnessError, match="bad exec template"):
        bad.run_step(stub_registry.get("add"), ctx, {"a": 1})


def test_tools_run_command(project: Path):
    result = runner.invoke(app, ["tools", "run", "add", "--set", "a=2", "--workdir", "w"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {"sum": 3}
    assert json.loads((project / "w" / "outputs.json").read_text()) == {"sum": 3}

    (project / "in.json").write_text('{"a": 5, "b": 5}')
    result = runner.invoke(app, ["tools", "run", "add", "--inputs", "in.json", "-s", "b=1", "-q"])
    assert result.exit_code == 0 and json.loads(result.stdout) == {"sum": 6}

    assert runner.invoke(app, ["tools", "run", "nope"]).exit_code == 2
    assert runner.invoke(app, ["tools", "run", "add", "--set", "a=x"]).exit_code == 2
    (project / "bad.json").write_text("[1]")
    assert runner.invoke(app, ["tools", "run", "add", "--inputs", "bad.json"]).exit_code == 2
    result = runner.invoke(app, ["tools", "run", "boom", "--set", "message=kaboom"])
    assert result.exit_code == 1 and "kaboom" in result.output

    (project / ".amide" / "tools" / "needy.yaml").write_text(
        "name: needy\ncommand: [no_such_command_xyz]\n"
    )
    result = runner.invoke(app, ["tools", "run", "needy"])
    assert result.exit_code == 2 and "needs command no_such_command_xyz" in result.output


def test_stub_protocol_has_a_cheap_write_step():
    assert any(step["tool"] == "write" for step in STUB_PROTOCOL["steps"])
    yaml.safe_dump(STUB_PROTOCOL)
