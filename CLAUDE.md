# amide

amide is a molecular dynamics CLI and an experimentation harness: bring your
own model, bring your own compute, run predefined protocols or open
experiments with a registry of biomolecular tools. `docs/DESIGN.md` is the
design reference and milestone plan; read it before changing the harness.

## Layout

- `src/amide/cli.py` is the one typer app (`app`); sub-apps `tools`,
  `protocols`, `runs`, `config`, `models` live in the same file.
- `src/amide/harness/` is the core: `tool.py` (manifests), `registry.py`
  (discovery), `protocol.py` (YAML schema), `expr.py` (templates and checks),
  `runs.py` (run directories), `runner.py` (execution), `report.py`.
- `src/amide/tools/` holds one builtin tool per module; `src/amide/protocols/`
  the bundled protocol YAML files; `src/amide/config.py` the config file.
- `src/amide/models/` is bring-your-own-model: `base.py` (the one message
  and reply shape), `http.py` (urllib, retries, SSE), one adapter module per
  provider kind (`openai.py`, `anthropic.py`, `gemini.py`), `providers.py`
  (builtin providers, config overlay, `provider/model` resolution), and
  `loop.py` (the tool-calling loop `amide ask` and the agents use). No
  vendor SDKs; tests fake the three `http` functions.
- `src/amide/agents/` is the open-experiment session: `roles.py` (the six
  roles, their prompts, which tools each gets) and `session.py`
  (`Experiment` state under the runs root, `Session` running the
  orchestrator, spawning sub-agents, budgets, and the session-only tools
  such as `run_protocol` and `finish_experiment`). Tests script the model
  by patching `Provider.adapter`.
- `tui/` is the Go viewer; `src/amide/tui.py` fetches its binary.
- `tests/` mirrors the package. `tests/conftest.py` has stub tools and a stub
  protocol most tests use.

## Rules

- Lazy-import anything heavy: `cli.py` imports only `typer` and `amide` at
  module level, and every tool module imports its scientific dependencies
  inside the tool function, never at the top. A test enforces both.
- Every command has a test. Every builtin tool has a test; tools that need
  an executable not on PyPI (`lmp`, `gmx`, `w_run`) are tested against stub
  scripts on PATH, and tools that need `openmm`, `MDAnalysis`, or `rdkit`
  skip when those are absent.
- `amide --help` stays under 100ms; CI enforces it.
- Tools write only under their `ctx.workdir` and return a dict matching
  their declared outputs. Anything that can fail raises `ToolError` with a
  message fit to print.
- Keys never go in protocols, run directories, or the repo. The config file
  names the environment variable that holds each key.
- Heavy dependencies are optional extras (`sim`, `chem`, `westpa`, `full`),
  never base dependencies.

## Commands

- `uv sync` -- install the lite package plus dev tools.
- `uv sync --extra sim --extra chem` -- also install OpenMM, PDBFixer,
  MDAnalysis, and RDKit so the simulation tests run instead of skipping.
- `uv run pytest` -- run tests.
- `uv run ruff check && uv run ruff format --check` -- lint and formatting.
- `uv tool install . --force` -- reinstall the command locally.
- `uv run amide run openmm-control --set structure=tests/data/sample.pdb --set solvate=false --set steps=200 --yes`
  -- a full protocol run in a few seconds, results under `.amide/runs/`.
- `uv run amide models list` -- providers and whether their key is set;
  `--remote` asks each one for its model ids.
- `ANTHROPIC_API_KEY=... uv run amide ask -m anthropic/claude-opus-5 "fetch 1AKI and tell me its size"`
  -- one tool-using call; transcript under `.amide/scratch/`.
- `uv run amide experiment -m anthropic/claude-opus-5 --max-dollars 5 "Is lysozyme 1AKI stable at 350 K in a short OpenMM run?"`
  -- an open experiment; agents, protocols, runs, and the three documents
  land under `.amide/runs/<id>/`. Add `-i` to chat with the orchestrator.
