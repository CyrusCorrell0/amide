from pathlib import Path

import typer

import amide

_VIEW_EXAMPLES = (
    "Examples:\n"
    "  amide view protein.pdb                  open a trajectory\n"
    "  amide view ./runs                       browse a directory for .pdb files"
)
_INDEX_EXAMPLES = (
    "Examples:\n"
    "  amide index ./runs                      summarize every .pdb under ./runs"
)
_SEARCH_EXAMPLES = (
    "Examples:\n"
    "  amide search                            browse everything indexed\n"
    "  amide search 1ubq frames:2..            start narrowed to id, title or path\n"
    "  amide search --sort frames -r --json    JSON rows, for pipes and agents"
)
_EXAMPLES = "\n".join(
    [
        _VIEW_EXAMPLES,
        "  amide info protein.pdb                  frames, atoms, chains; no terminal",
        _INDEX_EXAMPLES.removeprefix("Examples:\n"),
        _SEARCH_EXAMPLES.removeprefix("Examples:\n"),
    ]
)

app = typer.Typer(
    add_completion=False,
    help="A molecular dynamics CLI.",
    epilog=_EXAMPLES,
    context_settings={"help_option_names": ["-h", "--help"]},
)


def _version(value: bool) -> None:
    if value:
        typer.echo(f"amide {amide.__version__}")
        raise typer.Exit()


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: bool = typer.Option(
        False,
        "-v",
        "--version",
        callback=_version,
        is_eager=True,
        help="Show the version and exit.",
    ),
) -> None:
    if ctx.invoked_subcommand is None:
        from amide.banner import show

        show(amide.__version__)


def _interactive() -> bool:
    """A person at a terminal: both ends are TTYs and this is not CI."""
    import os
    import sys

    return not os.environ.get("CI") and sys.stdin.isatty() and sys.stdout.isatty()


@app.command(epilog=_VIEW_EXAMPLES)
def view(
    path: Path = typer.Argument(  # noqa: B008 -- typer reads the call, not its result
        Path("."), help="A PDB file to open, or a directory to browse."
    ),
) -> None:
    """Open the molecular viewer. Needs an interactive terminal."""
    target = path.resolve()
    if not target.exists():
        typer.echo(f"amide view: `{path}` does not exist", err=True)
        raise typer.Exit(2)

    if not _interactive():
        typer.echo(
            "amide view: needs an interactive terminal; "
            f"run it for the person, or run `amide info {path}`",
            err=True,
        )
        raise typer.Exit(4)

    if target.is_file():
        from amide.pdb import has_atoms

        if not has_atoms(target):
            typer.echo(
                f"amide view: `{target.name}` has no ATOM or HETATM records; "
                "amide reads PDB files only",
                err=True,
            )
            raise typer.Exit(5)

    _run_viewer([str(target)])


def _run_viewer(args: list[str]) -> None:
    """Hand the terminal to the viewer binary with args; exits 3 if there is none."""
    import os
    import sys

    from amide.tui import TuiUnavailable, binary_path

    try:
        binary = binary_path()
    except TuiUnavailable as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(3) from None

    import subprocess

    if sys.platform == "win32":
        # os.execv on Windows spawns a child and returns, handing the terminal
        # back to the shell while the TUI is still drawing.
        done = subprocess.run([str(binary), *args], check=False)
        raise typer.Exit(done.returncode)
    os.execv(str(binary), [str(binary), *args])


def _stdout_is_terminal() -> bool:
    """A person is reading stdout; otherwise a pipe or an agent is."""
    import sys

    return sys.stdout.isatty()


@app.command()
def info(
    path: Path = typer.Argument(..., help="A PDB file."),  # noqa: B008
    json_: bool = typer.Option(False, "--json", help="Print JSON."),
) -> None:
    """Print frames, atoms, chains, residues and extent of a PDB file.

    JSON when --json is passed or stdout is not a terminal.
    """
    if not path.exists():
        typer.echo(f"amide info: `{path}` does not exist", err=True)
        raise typer.Exit(2)
    if path.is_dir():
        typer.echo(f"amide info: `{path}` is a directory; pass a PDB file", err=True)
        raise typer.Exit(2)

    from amide.pdb import NotPdb, summarize

    try:
        summary = summarize(path)
    except NotPdb as exc:
        typer.echo(f"amide info: {exc}; amide reads PDB files only", err=True)
        raise typer.Exit(5) from None

    if json_ or not _stdout_is_terminal():
        import json

        typer.echo(json.dumps(summary.to_dict(), indent=2))
        return

    hydrogens = (
        f"{summary.hydrogens:,} hydrogens" if summary.hydrogens else "no hydrogens"
    )
    known = [("id", summary.id), ("title", summary.title)]
    labels = "".join(f"  {k:<8}  {v}\n" for k, v in known if v is not None)
    typer.echo(
        f"{summary.path}\n"
        f"{labels}"
        f"  frames    {summary.frames:,}\n"
        f"  atoms     {summary.atoms:,} per frame ({hydrogens})\n"
        f"  chains    {', '.join(summary.chains) or 'none'}\n"
        f"  residues  {summary.residues:,}\n"
        f"  extent    {' x '.join(f'{v:.1f}' for v in summary.extent)} A"
    )


@app.command()
def doctor(json_: bool = typer.Option(False, "--json", help="Print JSON.")) -> None:
    """Check everything `amide view` needs and print the fix for each failure.

    Makes no network requests. JSON when --json is passed or stdout is not a terminal.
    Exits 1 when any check fails.
    """
    from amide.doctor import checks, render

    results = checks(interactive=_interactive())
    ok = all(check.ok for check in results)

    if json_ or not _stdout_is_terminal():
        import json

        payload = {"ok": ok, "checks": [check.to_dict() for check in results]}
        typer.echo(json.dumps(payload, indent=2))
    else:
        typer.echo(render(results))

    if not ok:
        raise typer.Exit(1)


@app.command(epilog=_INDEX_EXAMPLES)
def index(
    path: Path = typer.Argument(  # noqa: B008
        Path("."), help="A directory to walk for .pdb files."
    ),
    json_: bool = typer.Option(False, "--json", help="Print JSON."),
) -> None:
    """Find every PDB file under PATH and add it to the global index.

    Unchanged files are skipped, so a second run is fast. JSON when --json is
    passed or stdout is not a terminal.
    """
    if not path.is_dir():
        typer.echo(
            f"amide index: `{path}` is not a directory; pass a directory to walk",
            err=True,
        )
        raise typer.Exit(2)

    from amide import index as idx

    terminal = _stdout_is_terminal()
    width = 0

    def progress(done: int, total: int) -> None:
        nonlocal width
        line = f"indexing {done:,}/{total:,}"
        width = len(line)
        typer.echo(f"\r{line}", err=True, nl=False)

    root = path.resolve()
    report = idx.index(root, progress=progress if terminal else None)
    if width:
        typer.echo("\r" + " " * width + "\r", err=True, nl=False)

    if json_ or not terminal:
        import dataclasses
        import json

        typer.echo(json.dumps(dataclasses.asdict(report), indent=2))
        return

    files = "file" if report.indexed == 1 else "files"
    why = " (no ATOM records)" if report.skipped else ""
    typer.echo(
        f"indexed {report.indexed:,} {files} under {root}: "
        f"{report.new:,} new, {report.updated:,} updated, "
        f"{report.unchanged:,} unchanged, {report.skipped:,} skipped{why}, "
        f"{report.removed:,} removed\n"
        f"{report.index}"
    )


@app.command(epilog=_SEARCH_EXAMPLES)
def search(
    filters: list[str] = typer.Argument(  # noqa: B008
        None, help="Words and key:value filters, such as 1ubq frames:2.. chains:A"
    ),
    sort: str = typer.Option("path", "--sort", help="Sort by this key."),
    reverse: bool = typer.Option(False, "-r", "--reverse", help="Reverse the sort."),
    json_: bool = typer.Option(False, "--json", help="Print JSON."),
) -> None:
    """Search the index. Words match id, title and path; key:value filters narrow it.

    Opens the search screen on a terminal. JSON when --json is passed or stdout is
    not a terminal.
    """
    from amide import index as idx

    filters = filters or []
    rows = idx.load()
    if not rows:
        typer.echo(
            "amide search: nothing indexed yet; run `amide index <directory>`",
            err=True,
        )
        raise typer.Exit(2)

    try:
        rows = idx.search(rows, filters, sort=sort, reverse=reverse)
    except idx.BadFilter as exc:
        typer.echo(f"amide search: {exc}", err=True)
        raise typer.Exit(2) from None

    import json

    if json_ or not _stdout_is_terminal():
        typer.echo(json.dumps(rows, indent=2))
        return

    import tempfile

    with tempfile.NamedTemporaryFile(
        "w", suffix=".json", delete=False, encoding="utf-8"
    ) as f:
        json.dump(
            {
                "query": " ".join(filters),
                "sort": sort,
                "reverse": reverse,
                "rows": rows,
            },
            f,
        )
    try:
        _run_viewer(["--search", f.name])
    finally:
        # os.execv never returns on POSIX, so this runs only on Windows or on an
        # error; elsewhere the file stays in the temp directory, which is fine.
        Path(f.name).unlink(missing_ok=True)
