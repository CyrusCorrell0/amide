import subprocess
import sys
import time

import pytest
from typer.testing import CliRunner

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
    assert result.stdout.strip() == "amide 0.0.1"


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
