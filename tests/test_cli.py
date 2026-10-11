import os
import re
import stat
import subprocess
import sys
import time

import pytest
from typer.testing import CliRunner

import amide
from amide import banner, cli
from amide.cli import app

runner = CliRunner()

STARTUP_BUDGET_S = 0.150

ANSI = re.compile("\x1b\\[[0-9?;]*[A-Za-z]")


def _plain_text(text):
    return ANSI.sub("", text)


def _output(result):
    """Click separates stderr in 8.2+ and merges it before; read whatever exists."""
    text = result.output
    try:
        text += result.stderr
    except ValueError:
        pass
    return text


def test_no_args_draws_the_banner():
    result = runner.invoke(app, [])
    assert result.exit_code == 0
    assert banner.TAGLINE in result.stdout
    assert f"v{amide.__version__}" in result.stdout
    assert banner.HINT in result.stdout


def test_banner_is_ascii():
    """cp1252 is what a piped Windows stdout gets, and it has no box drawing."""
    assert banner.logo(amide.__version__).isascii()


def test_no_args_never_sleeps_off_a_terminal(monkeypatch):
    slept = []
    monkeypatch.setattr(time, "sleep", slept.append)

    start = time.perf_counter()
    result = runner.invoke(app, [])
    elapsed = time.perf_counter() - start

    assert result.exit_code == 0
    assert slept == []
    assert elapsed < STARTUP_BUDGET_S, f"the static banner took {elapsed * 1000:.0f}ms"


def test_no_args_writes_no_escape_codes_off_a_terminal():
    result = runner.invoke(app, [])
    assert "\x1b" not in result.stdout


@pytest.mark.parametrize("var", ["NO_COLOR", "CI", "AMIDE_NO_ANIMATION"])
def test_plain_when_told_to_be(monkeypatch, var):
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True, raising=False)
    for name in ("NO_COLOR", "CI", "AMIDE_NO_ANIMATION"):
        monkeypatch.delenv(name, raising=False)
    assert banner._plain() is False
    monkeypatch.setenv(var, "1")
    assert banner._plain() is True


def test_animation_is_a_flourish_not_a_wait():
    assert banner.DURATION < 0.8
    opening = _plain_text("\n".join(banner._at(amide.__version__, 0.0)))
    closing = _plain_text("\n".join(banner._at(amide.__version__, 1.0)))
    assert opening != closing
    assert closing.rstrip() == banner.logo(amide.__version__).rstrip()


def test_animation_lands_the_atoms_before_anything_else():
    opening = _plain_text("\n".join(banner._at(amide.__version__, 0.0)))
    assert "O" in opening and "N" in opening
    assert "--" not in opening, "bonds form after the atoms land"
    assert banner.WORDMARK[1] not in opening
    assert banner.TAGLINE not in opening


@pytest.fixture
def terminal(monkeypatch, capsys):
    """Make the animated path the one that runs, with no real terminal to hand.

    pytest hands the test a fresh stdout after fixtures run, so faking isatty on
    the object here would not stick. `_plain` is the seam instead, and it has its
    own test above.
    """
    monkeypatch.setenv("COLUMNS", "100")
    monkeypatch.setenv("LINES", "40")
    monkeypatch.setattr(banner, "_plain", lambda: False)
    monkeypatch.setattr(banner, "_enable_ansi", lambda: True)
    return capsys


def test_animation_runs_on_a_terminal(terminal):
    start = time.perf_counter()
    banner.show(amide.__version__)
    elapsed = time.perf_counter() - start

    raw = terminal.readouterr().out
    assert banner.HIDE_CURSOR in raw, "this is the static path, not the animated one"
    assert elapsed < 1.0, f"the flourish took {elapsed * 1000:.0f}ms"

    out = _plain_text(raw)
    assert banner.TAGLINE in out
    assert f"v{amide.__version__}" in out
    assert banner.HINT in out


def test_a_narrow_terminal_gets_the_static_logo(terminal, monkeypatch):
    monkeypatch.setenv("COLUMNS", "40")
    slept = []
    monkeypatch.setattr(time, "sleep", slept.append)

    banner.show(amide.__version__)

    out = terminal.readouterr().out
    assert slept == []
    assert "\x1b" not in out
    assert banner.TAGLINE in out


def test_ctrl_c_mid_animation_leaves_the_cursor_visible(terminal, monkeypatch):
    def interrupt(_seconds):
        raise KeyboardInterrupt

    monkeypatch.setattr(time, "sleep", interrupt)

    with pytest.raises(KeyboardInterrupt):
        banner.show(amide.__version__)
    assert terminal.readouterr().out.endswith(banner.SHOW_CURSOR)


def test_ctrl_c_exits_without_a_traceback(monkeypatch):
    def interrupt(_version):
        raise KeyboardInterrupt

    monkeypatch.setattr(banner, "show", interrupt)
    result = runner.invoke(app, [])
    assert result.exit_code == 130
    assert "Traceback" not in _output(result)


def test_version():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == f"amide {amide.__version__}"


def test_version_short_flag():
    result = runner.invoke(app, ["-v"])
    assert result.exit_code == 0
    assert result.stdout.strip() == f"amide {amide.__version__}"


def test_dash_h_is_help():
    short = runner.invoke(app, ["-h"])
    long = runner.invoke(app, ["--help"])
    assert short.exit_code == 0
    assert short.stdout == long.stdout


@pytest.mark.parametrize("args", [["-h"], ["view", "-h"]])
def test_help_ends_with_examples(args):
    result = runner.invoke(app, args)
    assert result.exit_code == 0
    assert "Examples:" in result.stdout
    assert "amide view protein.pdb" in result.stdout


@pytest.fixture
def at_a_terminal(monkeypatch):
    """CliRunner swaps stdin and stdout for pipes, so fake the person at the seam."""
    monkeypatch.setattr(cli, "_interactive", lambda: True)


def test_view_missing_path_exits_2(at_a_terminal):
    result = runner.invoke(app, ["view", "missing.pdb"])
    assert result.exit_code == 2
    assert result.stderr.strip() == "amide view: `missing.pdb` does not exist"


def test_view_off_a_terminal_exits_4_before_reading_the_file(tmp_path):
    gro = tmp_path / "frame.gro"
    gro.write_text("not a pdb\n")
    result = runner.invoke(app, ["view", str(gro)])
    assert result.exit_code == 4
    assert result.stderr.strip() == (
        "amide view: needs an interactive terminal; "
        f"run it for the person, or run `amide info {gro}`"
    )


def test_view_without_atoms_exits_5(tmp_path, at_a_terminal):
    gro = tmp_path / "frame.gro"
    gro.write_text("not a pdb\n")
    result = runner.invoke(app, ["view", str(gro)])
    assert result.exit_code == 5
    assert result.stderr.strip() == (
        "amide view: `frame.gro` has no ATOM or HETATM records; amide reads PDB files only"
    )


def test_interactive_needs_both_ttys_and_no_ci(monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True, raising=False)
    assert cli._interactive()

    monkeypatch.setenv("CI", "true")
    assert not cli._interactive()

    monkeypatch.delenv("CI")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)
    assert not cli._interactive()


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
    (tmp_path / "file.pdb").write_text(
        "ATOM      1  N   ALA A   1       0.000   0.000   0.000\n"
    )
    expected = (tmp_path if arg is None else tmp_path / arg).resolve()

    env = {**os.environ, "AMIDE_TUI": str(_stub_binary(tmp_path))}
    env.pop("CI", None)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from amide import cli; cli._interactive = lambda: True; cli.app()",
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


@pytest.mark.parametrize(
    "module", ["amide.tui", "amide.pdb", "amide.doctor", "amide.index"]
)
def test_help_does_not_import(module):
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys\n"
                "from amide.cli import app\n"
                "try:\n"
                "    app(['--help'])\n"
                "except SystemExit:\n"
                "    pass\n"
                f"sys.exit(1 if {module!r} in sys.modules else 0)"
            ),
        ],
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0


def test_help_does_not_import_the_banner():
    """The banner is for the bare command; --help must not pay for it."""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys, amide.cli;"
                " sys.exit(1 if 'amide.banner' in sys.modules else 0)"
            ),
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


SAMPLE = os.path.join(os.path.dirname(__file__), "data", "sample.pdb")
INFO_KEYS = [
    "path",
    "id",
    "title",
    "frames",
    "atoms",
    "hydrogens",
    "chains",
    "residues",
    "extent",
]


def test_info_human_on_a_terminal(monkeypatch):
    monkeypatch.setattr(cli, "_stdout_is_terminal", lambda: True)
    result = runner.invoke(app, ["info", SAMPLE])
    assert result.exit_code == 0
    assert result.stdout == (
        f"{SAMPLE}\n"
        "  frames    3\n"
        "  atoms     138 per frame (61 hydrogens)\n"
        "  chains    A\n"
        "  residues  10\n"
        "  extent    16.0 x 13.9 x 11.6 A\n"
    )


def test_info_human_prints_id_and_title_when_the_file_has_them(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "_stdout_is_terminal", lambda: True)
    f = tmp_path / "1ubq.pdb"
    f.write_text(
        f"HEADER    CHROMOSOMAL PROTEIN{' ' * 33}1UBQ\n"
        "TITLE     STRUCTURE OF UBIQUITIN\n"
        "ATOM      1  N   MET A   1      27.340  24.430   2.614  1.00  9.67           N\n"
    )
    result = runner.invoke(app, ["info", str(f)])
    assert result.exit_code == 0
    assert result.stdout.startswith(
        f"{f}\n  id        1UBQ\n  title     STRUCTURE OF UBIQUITIN\n  frames    1\n"
    )


def test_info_human_without_hydrogens_or_chains(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "_stdout_is_terminal", lambda: True)
    f = tmp_path / "bare.pdb"
    lines = [
        f"HETATM{i:5d}  O   HOH  {i:4d}    {i / 100:8.3f}{0:8.3f}{0:8.3f}  1.00  0.00           O\n"
        for i in range(1, 1201)
    ]
    f.write_text("".join(lines))
    result = runner.invoke(app, ["info", str(f)])
    assert result.exit_code == 0
    assert "  atoms     1,200 per frame (no hydrogens)\n" in result.stdout
    assert "  chains    none\n" in result.stdout


def test_info_json_flag_has_its_keys_in_order(monkeypatch):
    import json

    monkeypatch.setattr(cli, "_stdout_is_terminal", lambda: True)
    result = runner.invoke(app, ["info", SAMPLE, "--json"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert list(data) == INFO_KEYS
    assert data["extent"] == [16.0, 13.9, 11.6]


def test_info_piped_is_json_without_the_flag():
    import json

    result = runner.invoke(app, ["info", SAMPLE])
    assert result.exit_code == 0
    assert list(json.loads(result.stdout)) == INFO_KEYS


def test_info_without_atoms_exits_5(tmp_path):
    gro = tmp_path / "frame.gro"
    gro.write_text("not a pdb\n")
    result = runner.invoke(app, ["info", str(gro)])
    assert result.exit_code == 5
    assert result.stdout == ""
    assert result.stderr.strip() == (
        "amide info: `frame.gro` has no ATOM or HETATM records; amide reads PDB files only"
    )


def test_info_missing_path_exits_2():
    result = runner.invoke(app, ["info", "missing.pdb"])
    assert result.exit_code == 2
    assert result.stdout == ""
    assert result.stderr.strip() == "amide info: `missing.pdb` does not exist"


def test_info_directory_exits_2(tmp_path):
    result = runner.invoke(app, ["info", str(tmp_path)])
    assert result.exit_code == 2
    assert result.stdout == ""
    assert (
        result.stderr.strip()
        == f"amide info: `{tmp_path}` is a directory; pass a PDB file"
    )


@pytest.fixture
def healthy(monkeypatch, tmp_path):
    """A supported Linux box at an 80x24 terminal with the viewer cached under tmp_path."""
    import platform

    from amide import tui

    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr(platform, "machine", lambda: "x86_64")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.delenv("AMIDE_TUI", raising=False)
    monkeypatch.setenv("COLUMNS", "80")
    monkeypatch.setenv("LINES", "24")
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    monkeypatch.setattr(cli, "_stdout_is_terminal", lambda: True)
    binary = tui.cached_binary()
    binary.parent.mkdir(parents=True)
    binary.write_text("")
    return binary


def _doctor_json(*args):
    import json

    result = runner.invoke(app, ["doctor", "--json", *args])
    return result, json.loads(result.stdout)


def test_doctor_all_ok_prints_four_lines_and_exits_0(healthy):
    import platform

    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert result.stdout == (
        f"  ok    amide {amide.__version__}, python {platform.python_version()}\n"
        "  ok    platform linux/amd64 is supported\n"
        f"  ok    viewer {amide.__version__} cached at {healthy}\n"
        "  ok    terminal is interactive (80x24)\n"
    )


def test_doctor_json_shape(healthy):
    result, data = _doctor_json()
    assert result.exit_code == 0
    assert list(data) == ["ok", "checks"]
    assert data["ok"] is True
    assert [c["name"] for c in data["checks"]] == [
        "amide",
        "platform",
        "viewer",
        "terminal",
    ]
    for check in data["checks"]:
        assert list(check) == ["name", "ok", "detail", "fix"]
        assert check["fix"] is None


def test_doctor_reports_a_missing_cache_with_the_fix(healthy):
    healthy.unlink()
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 1
    assert (
        f"  fail  viewer {amide.__version__} not downloaded yet\n"
        "        fix: run `amide view` once while online\n"
    ) in result.stdout


def test_doctor_honours_the_override(healthy, monkeypatch, tmp_path):
    healthy.unlink()
    built = tmp_path / "my-viewer"
    built.write_text("")
    monkeypatch.setenv("AMIDE_TUI", str(built))
    result, data = _doctor_json()
    assert result.exit_code == 0
    assert data["checks"][2]["detail"] == f"viewer override at {built}"


def test_doctor_override_to_nothing_fails(healthy, monkeypatch, tmp_path):
    missing = tmp_path / "gone"
    monkeypatch.setenv("AMIDE_TUI", str(missing))
    result, data = _doctor_json()
    assert result.exit_code == 1
    assert data["ok"] is False
    assert data["checks"][2] == {
        "name": "viewer",
        "ok": False,
        "detail": f"viewer override at {missing} does not exist",
        "fix": "unset `AMIDE_TUI` or point it at an existing file",
    }


def test_doctor_unsupported_platform_fails_with_the_fix(healthy, monkeypatch):
    import platform

    monkeypatch.setattr(platform, "system", lambda: "Windows")
    monkeypatch.setattr(platform, "machine", lambda: "ARM64")
    result, data = _doctor_json()
    assert result.exit_code == 1
    check = data["checks"][1]
    assert check["ok"] is False
    assert check["detail"].startswith("found no viewer build for Windows/ARM64")
    assert check["fix"].startswith("use a supported platform")


def test_doctor_off_a_terminal_fails_the_terminal_check_and_prints_json():
    """CliRunner pipes stdout, so the real seams say: no terminal, JSON."""
    import json

    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 1
    data = json.loads(result.stdout)
    assert data["ok"] is False
    assert data["checks"][3] == {
        "name": "terminal",
        "ok": False,
        "detail": "no interactive terminal",
        "fix": "use `amide info` here, or run `amide view` in a terminal",
    }


def test_doctor_makes_no_network_requests(healthy, monkeypatch):
    import socket

    def refuse(*_args, **_kwargs):
        raise AssertionError("doctor opened a socket")

    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    healthy.unlink()
    assert runner.invoke(app, ["doctor"]).exit_code == 1
