from pathlib import Path

import typer

import amide

app = typer.Typer(add_completion=False, help="A molecular dynamics CLI.")


def _version(value: bool) -> None:
    if value:
        typer.echo(f"amide {amide.__version__}")
        raise typer.Exit()


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version,
        is_eager=True,
        help="Show the version and exit.",
    ),
) -> None:
    if ctx.invoked_subcommand is None:
        typer.echo(f"amide {amide.__version__}: hello, world")


@app.command()
def view(
    path: Path = typer.Argument(  # noqa: B008 -- typer reads the call, not its result
        Path("."), help="A PDB file to open, or a directory to browse."
    ),
) -> None:
    """Open the molecular viewer."""
    target = path.resolve()
    if not target.exists():
        typer.echo(f"amide view: {path} does not exist", err=True)
        raise typer.Exit(2)

    from amide.tui import TuiUnavailable, binary_path

    try:
        binary = binary_path()
    except TuiUnavailable as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(3) from None

    import os
    import subprocess
    import sys

    if sys.platform == "win32":
        # os.execv on Windows spawns a child and returns, handing the terminal
        # back to the shell while the TUI is still drawing.
        done = subprocess.run([str(binary), str(target)], check=False)
        raise typer.Exit(done.returncode)
    os.execv(str(binary), [str(binary), str(target)])
