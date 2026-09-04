import os
import stat
import subprocess
import sys
import time

import pytest
from typer.testing import CliRunner

import amide
from amide.cli import app

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
    try:
        text += result.stderr
    except ValueError:
        pass
    return text


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


def test_help_does_not_import_tui():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys, amide.cli; sys.exit(1 if 'amide.tui' in sys.modules else 0)",
        ],
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0


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
