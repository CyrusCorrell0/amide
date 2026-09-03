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
