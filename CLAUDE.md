# amide

amide is a molecular dynamics CLI.

## Layout

`src/amide/` holds the package, `tests/` the tests, and `cli.py` the one typer app (`app`).

## Rules

- Lazy-import anything heavy: `cli.py` imports only `typer` and `amide` at module level.
- Every command has a test.
- `amide --help` stays under 100ms; CI enforces it.

## Commands

- `uv sync` -- install dependencies.
- `uv run pytest` -- run tests.
- `uv run ruff check` -- lint.
- `uv tool install . --force` -- reinstall the command locally.
