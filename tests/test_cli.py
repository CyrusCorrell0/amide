import contextlib
import json
import os
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

import amide
from amide.cli import app
from tests.conftest import STUB_PROTOCOL

runner = CliRunner()

STARTUP_BUDGET_S = 0.150


def test_no_args_greets():
    result = runner.invoke(app, [])
    assert result.exit_code == 0
    assert "hello, world" in result.stdout


def test_version():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == f"amide {amide.__version__}"


def _output(result):
    """Click separates stderr in 8.2+ and merges it before; read whatever exists."""
    text = result.output
    with contextlib.suppress(ValueError):
        text += result.stderr
    return text


# --- view ------------------------------------------------------------------


def test_view_missing_path_exits_2():
    result = runner.invoke(app, ["view", "missing.pdb"])
    assert result.exit_code == 2
    assert "missing.pdb" in _output(result)


def _stub_binary(tmp_path):
    """A fake TUI that prints the path it was handed."""
    script = tmp_path / "stub.py"
    script.write_text("import sys\nprint(sys.argv[1])\n")
    if sys.platform == "win32":
        stub = tmp_path / "stub.cmd"
        stub.write_text(f'@"{sys.executable}" "{script}" %*\n')
    else:
        stub = tmp_path / "stub.sh"
        stub.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n')
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return stub


@pytest.mark.parametrize("arg", [None, "somedir", "file.pdb"])
def test_view_passes_absolute_path(tmp_path, arg):
    (tmp_path / "somedir").mkdir()
    (tmp_path / "file.pdb").write_text("")
    expected = (tmp_path if arg is None else tmp_path / arg).resolve()

    env = {**os.environ, "AMIDE_TUI": str(_stub_binary(tmp_path))}
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from amide.cli import app; app()",
            "view",
            *([] if arg is None else [arg]),
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(expected)


# --- startup ---------------------------------------------------------------


def test_help_does_not_import_tui_or_harness():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys, amide.cli; "
            "heavy = [m for m in sys.modules if m.startswith(('amide.tui', 'amide.harness', "
            "'amide.tools', 'amide.config', 'yaml'))]; "
            "print(heavy); sys.exit(1 if heavy else 0)",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout


def _best_of(argv, n=3):
    """Fastest of n runs -- a cold filesystem cache should not decide a timing test."""
    runs = []
    for _ in range(n):
        start = time.perf_counter()
        result = subprocess.run(argv, capture_output=True, check=False)
        runs.append((time.perf_counter() - start, result))
    return min(runs, key=lambda run: run[0])


def test_help_startup_under_budget():
    """Exercise the installed entry point, not the module, so the real cost is measured."""
    floor, _ = _best_of([sys.executable, "-c", "pass"])
    if floor > STARTUP_BUDGET_S / 2:
        pytest.skip(
            f"a bare interpreter costs {floor * 1000:.0f}ms here, over half the "
            f"{STARTUP_BUDGET_S * 1000:.0f}ms budget; no headroom to measure amide's own startup"
        )

    elapsed, result = _best_of(["amide", "--help"])
    assert result.returncode == 0
    assert elapsed < STARTUP_BUDGET_S, f"amide --help took {elapsed * 1000:.0f}ms"


# --- run -------------------------------------------------------------------


def _runs(project: Path) -> list[Path]:
    root = project / ".amide" / "runs"
    return sorted(p for p in root.iterdir() if p.is_dir()) if root.exists() else []


def test_run_protocol(project: Path):
    result = runner.invoke(app, ["run", "arith", "--set", "a=5"])
    assert result.exit_code == 0, _output(result)
    assert "passed" in result.stdout
    (run_dir,) = _runs(project)
    assert f"results: {run_dir / 'report.md'}" in result.stdout
    results = json.loads((run_dir / "results.json").read_text())
    assert results["outputs"]["total"] == 18
    assert results["params"]["a"] == 5


def test_run_by_path_with_failed_check_exits_1(project: Path):
    result = runner.invoke(app, ["run", ".amide/protocols/arith.yaml", "--set", "limit=1"])
    assert result.exit_code == 1
    assert "checks failed: small" in result.stdout


def test_run_dry_run(project: Path):
    result = runner.invoke(app, ["run", "arith", "--dry-run", "--set", "b=7"])
    assert result.exit_code == 0
    assert "arith: 4 steps" in result.stdout
    assert "b = 7" in result.stdout
    assert _runs(project) == []


def test_run_rejects_bad_input(project: Path):
    result = runner.invoke(app, ["run", "arith", "--set", "zzz=1"])
    assert result.exit_code == 2
    assert "no parameter zzz" in _output(result)
    result = runner.invoke(app, ["run", "nope"])
    assert result.exit_code == 2
    assert "no protocol named 'nope'" in _output(result)
    result = runner.invoke(app, ["run"])
    assert result.exit_code == 2
    assert "give a protocol, or --resume" in _output(result)


def test_run_refuses_invalid_protocol(project: Path):
    bad = {"name": "bad", "steps": [{"id": "s", "tool": "add", "with": {"a": "{{ params.q }}"}}]}
    (project / "bad.yaml").write_text(yaml.safe_dump(bad))
    result = runner.invoke(app, ["run", "bad.yaml"])
    assert result.exit_code == 2
    assert "refers to params.q" in _output(result)


def test_run_pauses_on_expensive_step_then_resumes(project: Path):
    costly = {
        "name": "costly",
        "steps": [
            {"id": "cheap", "tool": "add", "with": {"a": 1}},
            {"id": "big", "tool": "pricey"},
        ],
    }
    (project / "costly.yaml").write_text(yaml.safe_dump(costly))
    result = runner.invoke(app, ["run", "costly.yaml"])
    assert result.exit_code == 3
    assert "paused" in result.stdout
    (run_dir,) = _runs(project)
    run_id = run_dir.name

    result = runner.invoke(app, ["run", "--resume", run_id, "--yes"])
    assert result.exit_code == 0, _output(result)
    assert "big: pricey" in result.stdout and "passed" in result.stdout

    result = runner.invoke(app, ["run", "--resume", run_id])
    assert result.exit_code == 0
    assert "already finished" in result.stdout


def test_run_detached(project: Path):
    result = runner.invoke(app, ["run", "arith", "--detach"])
    assert result.exit_code == 0, _output(result)
    run_id = result.stdout.strip()
    run_json = project / ".amide" / "runs" / run_id / "run.json"
    deadline = time.time() + 60
    while time.time() < deadline:
        state = json.loads(run_json.read_text())
        if state["status"] not in ("pending", "running"):
            break
        time.sleep(0.2)
    assert state["status"] == "passed", (
        project / ".amide" / "runs" / run_id / "run.log"
    ).read_text()


def test_run_honours_runs_dir(project: Path, tmp_path: Path):
    elsewhere = tmp_path / "elsewhere"
    result = runner.invoke(app, ["run", "arith", "--runs-dir", str(elsewhere)])
    assert result.exit_code == 0, _output(result)
    assert _runs(project) == []
    assert len(list(elsewhere.iterdir())) == 1


# --- tools -----------------------------------------------------------------


def test_tools_list(project: Path):
    result = runner.invoke(app, ["tools", "list"])
    assert result.exit_code == 0
    lines = result.stdout.splitlines()
    assert any(line.startswith("add ") and "ready" in line for line in lines)
    assert any(line.startswith("rcsb_fetch ") for line in lines)
    result = runner.invoke(app, ["tools", "list", "--tag", "fetch"])
    assert "rcsb_fetch" in result.stdout and "add" not in result.stdout.split()


def test_tools_show(project: Path):
    result = runner.invoke(app, ["tools", "show", "add"])
    assert result.exit_code == 0
    manifest = json.loads(result.stdout)
    assert manifest["name"] == "add" and manifest["inputs"][0]["name"] == "a"
    result = runner.invoke(app, ["tools", "show", "nope"])
    assert result.exit_code == 2


def test_tools_check(project: Path):
    result = runner.invoke(app, ["tools", "check", "shell"])
    assert result.exit_code == 0 and result.stdout.strip() == "shell: ready"
    (project / ".amide" / "tools" / "needy.yaml").write_text(
        "name: needy\ncommand: [no_such_command_xyz]\nrequires: {hint: 'Install it.'}\n"
    )
    result = runner.invoke(app, ["tools", "check"])
    assert result.exit_code == 1
    assert "needy: missing command no_such_command_xyz" in result.stdout
    assert "Install it." in result.stdout


# --- protocols -------------------------------------------------------------


def test_protocols_list(project: Path):
    result = runner.invoke(app, ["protocols", "list"])
    assert result.exit_code == 0
    assert result.stdout.startswith("arith ")
    assert "openmm-control" in result.stdout


def test_protocols_show(project: Path):
    result = runner.invoke(app, ["protocols", "show", "arith"])
    assert result.exit_code == 0
    assert "a: integer = 2" in result.stdout
    assert "annotate: write  when params.note is not None" in result.stdout
    assert "small: steps.second.sum < params.limit" in result.stdout


def test_protocols_validate(project: Path):
    result = runner.invoke(app, ["protocols", "validate", "openmm-control"])
    assert result.exit_code == 0 and "ok" in result.stdout
    (project / "bad.yaml").write_text(
        yaml.safe_dump({"name": "bad", "steps": [{"id": "s", "tool": "nope"}]})
    )
    result = runner.invoke(app, ["protocols", "validate", "bad.yaml"])
    assert result.exit_code == 1
    assert "unknown tool 'nope'" in result.stdout


# --- runs ------------------------------------------------------------------


def test_runs_list_show_export(project: Path):
    assert runner.invoke(app, ["runs", "list"]).stdout == ""
    runner.invoke(app, ["run", "arith"])
    runner.invoke(app, ["run", "arith", "--set", "limit=1"])
    result = runner.invoke(app, ["runs", "list"])
    lines = result.stdout.splitlines()
    assert len(lines) == 2 and "failed" in lines[0] and "passed" in lines[1]
    run_id = lines[1].split()[0]

    result = runner.invoke(app, ["runs", "show", run_id])
    assert result.exit_code == 0, _output(result)
    assert f"{run_id}: arith, passed" in result.stdout
    assert "annotate" in result.stdout and "skipped" in result.stdout
    assert "check small: pass" in result.stdout
    assert "log:" in result.stdout

    result = runner.invoke(app, ["runs", "export", run_id, "--out", str(project / "x.tar.gz")])
    assert result.exit_code == 0 and (project / "x.tar.gz").exists()
    result = runner.invoke(app, ["runs", "show", "nope"])
    assert result.exit_code == 2


# --- config ----------------------------------------------------------------


def test_config_commands(project: Path):
    path = project / "config.toml"
    assert runner.invoke(app, ["config", "path"]).stdout.strip() == str(path)
    result = runner.invoke(app, ["config", "show"])
    assert "(not found; defaults)" in result.stdout
    assert runner.invoke(app, ["config", "init"]).exit_code == 0
    assert path.exists()
    assert runner.invoke(app, ["config", "init"]).exit_code == 1
    assert runner.invoke(app, ["config", "init", "--force"]).exit_code == 0
    result = runner.invoke(app, ["config", "show"])
    assert result.exit_code == 0
    effective = json.loads(result.stdout.split("\n", 1)[1])
    assert effective["providers"]["gemini"]["kind"] == "gemini"


def test_config_tool_paths_are_loaded(project: Path, tmp_path: Path):
    extra = tmp_path / "extra_tools"
    extra.mkdir()
    (extra / "t.yaml").write_text("name: from_config\ncommand: [echo]\n")
    (project / "config.toml").write_text(f'[tools]\npaths = ["{extra}"]\n')
    result = runner.invoke(app, ["tools", "list"])
    assert "from_config" in result.stdout


def test_stub_protocol_matches_conftest():
    assert STUB_PROTOCOL["name"] == "arith"
