# amide experimentation harness: design

amide is a local-first harness for running biomolecular experiments with
bring-your-own models and bring-your-own compute. A person picks a model
(or several), a set of tools, and either a predefined protocol or an open
question, and amide runs the experiment and leaves the results on disk.

This document is the reference for the harness. It describes what exists
today and what is planned, and marks each part with its milestone.

## Principles

- **Models are for intelligence, tools are for everything else.** Any
  action that touches the world (fetching a structure, running a
  simulation, computing an RMSD) is a tool with a declared interface.
  Protocols with enough parameters run with no model at all.
- **Local first.** Everything runs on the user's machine by default. A
  run is a directory. Sharing results means handing someone that
  directory. amide manages no cloud; a remote runner drives the user's
  own CLI (`brev`, `ssh`, a SLURM client) when they bring one.
- **Bring your own everything.** Keys stay in the environment or the
  local config file. Heavy dependencies are optional extras, and every
  tool declares what it needs so `amide tools check` can say what is
  missing instead of failing halfway through a run.
- **The CLI stays fast.** `amide --help` is under 100ms and CI enforces
  it. `cli.py` imports only `typer` and `amide`; every command imports
  what it needs inside its function.
- **Every command has a test.**

## Layout

```
src/amide/
  cli.py            the one typer app; lazy imports only
  tui.py            locate/download the Go viewer binary
  config.py         ~/.config/amide/config.toml
  harness/
    tool.py         ToolSpec, Param, ToolContext, the @tool decorator
    registry.py     discovers builtin and local tools, checks requirements
    protocol.py     protocol YAML schema, loading, validation
    expr.py         the {{ }} template language and check expressions
    runs.py         run directories: create, checkpoint, list, export
    runner.py       executes a protocol step by step
    report.py       report.md and results.json
  tools/            builtin tools, one module each
  protocols/        bundled protocol YAML files
  models/           provider adapters (milestone 3)
  agents/           agent roles and the orchestrator (milestone 4)
tui/                the Go viewer (existing)
docs/DESIGN.md      this file
```

## Tools

A tool is a named, typed, side-effecting operation. There are three
runtimes:

| runtime     | what runs                                | declared in            |
|-------------|------------------------------------------|------------------------|
| `python`    | a Python callable                        | `@tool` in a `.py`     |
| `command`   | an argv on the host                      | a `.yaml` manifest     |
| `container` | an argv inside a container image         | a `.yaml` manifest     |

MCP servers are deliberately not a runtime.

### The manifest

Every tool, whatever its runtime, has the same manifest:

| field         | meaning                                                        |
|---------------|----------------------------------------------------------------|
| `name`        | unique, `snake_case`                                           |
| `description` | one paragraph a model can choose the tool from                 |
| `version`     | free-form string, default `"1"`                                |
| `inputs`      | list of params: `name`, `type`, `description`, `required`, `default`, `choices` |
| `outputs`     | list of params the tool promises to return                     |
| `cost`        | `cheap`, `moderate`, or `expensive`                            |
| `requires`    | `python` modules, `commands` on PATH, `gpu` flag               |
| `timeout`     | seconds, or null                                               |
| `tags`        | free-form, for listing and filtering                           |

Param types are `string`, `integer`, `number`, `boolean`, `path`, `list`,
`object`. The runner coerces and validates inputs against the manifest
before the tool runs, so a tool body can trust its arguments.

`cost` drives approval. `expensive` steps prompt for confirmation in an
interactive session and fail in a non-interactive run unless `--yes` is
passed. `requires` drives `amide tools check`; a step whose requirements
are missing fails before it starts, with the install hint.

### Python tools

```python
from amide.harness.tool import Param, tool

@tool(
    name="rcsb_fetch",
    description="Download a structure from the RCSB Protein Data Bank.",
    inputs=[Param("id", "string", "Four-character PDB id.", required=True)],
    outputs=[Param("path", "path", "The downloaded file.")],
    cost="cheap",
)
def rcsb_fetch(ctx, id: str) -> dict:
    ...
    return {"path": str(dest)}
```

`ctx` is a `ToolContext` with `workdir` (the step's own directory inside
the run), `run_dir`, and `log(message)`. Tools write only under
`workdir` and return a dict matching their declared outputs.

### Command and container tools

```yaml
name: gmx_pdb2gmx
description: Convert a PDB to GROMACS topology and coordinates.
runtime: command                 # or container
image: ghcr.io/example/gromacs   # container only
command: ["gmx", "pdb2gmx", "-f", "{{ structure }}", "-o", "conf.gro", "-ff", "{{ forcefield }}", "-water", "tip3p"]
inputs:
  - {name: structure, type: path, required: true}
  - {name: forcefield, type: string, default: amber99sb-ildn}
outputs:
  - {name: coordinates, type: path, value: "{{ workdir }}/conf.gro"}
cost: moderate
requires: {commands: [gmx]}
```

The command runs with `workdir` as its working directory. Every input
is available to the templates in `command` and in output `value`s, as
are `workdir` and `run_dir`. `stdout`, `stderr`, and `returncode` are
always added to the outputs. A container tool mounts `workdir` at
`/work` and runs the same way through `docker` (or `podman` when that is
what is on PATH).

### Where tools come from

The registry loads, in order, later entries shadowing earlier ones:

1. builtin tools in `amide.tools`
2. each directory in `tools.paths` from the config file
3. `./.amide/tools` in the working directory

A directory contributes every `*.py` (Python tools) and `*.yaml`
(command and container tools) it holds. Publishing tools to a shared
registry is a later milestone; today "publish" means committing the
directory to a repo.

### Builtin tools

| name                 | cost      | needs                     | does                                              |
|----------------------|-----------|---------------------------|---------------------------------------------------|
| `rcsb_fetch`         | cheap     | network                   | PDB or mmCIF from RCSB                            |
| `uniprot_fetch`      | cheap     | network                   | FASTA or JSON entry from UniProt                  |
| `pubchem_fetch`      | cheap     | network                   | properties and optional SDF from PubChem          |
| `alphafold_fetch`    | cheap     | network                   | predicted structure from the AlphaFold DB         |
| `pdbfixer_prepare`   | moderate  | openmm (+pdbfixer)        | fix, protonate, strip, optionally solvate         |
| `rdkit_ligand`       | cheap     | rdkit                     | SMILES to 3D SDF plus descriptors                 |
| `openmm_minimize`    | moderate  | openmm                    | energy minimisation                               |
| `openmm_simulate`    | expensive | openmm                    | MD run with DCD, state log, final structure       |
| `lammps_run`         | expensive | `lmp`                     | run a LAMMPS input script                         |
| `gromacs_run`        | moderate  | `gmx`                     | run any `gmx` subcommand                          |
| `westpa_run`         | expensive | westpa                    | `w_init` then `w_run` on a west.cfg               |
| `mdanalysis_analyze` | cheap     | MDAnalysis                | RMSD, RMSF, radius of gyration over a trajectory  |
| `python`             | moderate  |                           | run a script in a subprocess inside the workdir   |
| `shell`              | moderate  |                           | run a shell command inside the workdir            |

`pdbfixer_prepare` uses PDBFixer when it is installed and falls back to
OpenMM's `Modeller` (hydrogens, water removal, solvation, but no missing
residue repair) when it is not, and says which it used in its outputs.

## Protocols

A protocol is a YAML file: parameters, a list of tool steps, checks that
assert on the results, and named outputs. It is the unit of
reproducibility. An agent can author one, edit one, and run one; a
person can run one with no model involved.

```yaml
name: openmm-control
description: Fetch a structure, prepare it, minimise, run short MD, check stability.
version: 1

params:
  pdb_id:      {type: string,  default: 1AKI, description: RCSB id to fetch.}
  structure:   {type: path,    default: null, description: Local file; skips the fetch.}
  steps:       {type: integer, default: 5000}
  temperature: {type: number,  default: 300}
  solvate:     {type: boolean, default: true}
  rmsd_limit:  {type: number,  default: 3.0, description: Max mean CA RMSD in angstrom.}

steps:
  - id: fetch
    tool: rcsb_fetch
    when: "params.structure is None"
    with: {id: "{{ params.pdb_id }}"}
  - id: prepare
    tool: pdbfixer_prepare
    with:
      structure: "{{ params.structure or steps.fetch.path }}"
      solvate: "{{ params.solvate }}"
  - id: simulate
    tool: openmm_simulate
    with:
      structure: "{{ steps.prepare.path }}"
      steps: "{{ params.steps }}"
      temperature_k: "{{ params.temperature }}"
  - id: analyze
    tool: mdanalysis_analyze
    with:
      topology: "{{ steps.simulate.topology }}"
      trajectory: "{{ steps.simulate.trajectory }}"
      metrics: [rmsd, rg]

checks:
  - id: stable
    expr: "steps.analyze.rmsd_mean < params.rmsd_limit"
    description: The backbone stayed near the starting structure.

outputs:
  trajectory: "{{ steps.simulate.trajectory }}"
  rmsd_mean:  "{{ steps.analyze.rmsd_mean }}"
```

### Schema

| key           | required | meaning                                                      |
|---------------|----------|--------------------------------------------------------------|
| `name`        | yes      | identifier                                                   |
| `description` | no       |                                                              |
| `version`     | no       | integer, default 1                                           |
| `params`      | no       | map of name to `{type, default, description, required, choices}` |
| `steps`       | yes      | ordered list                                                 |
| `steps[].id`  | yes      | unique within the protocol                                   |
| `steps[].tool`| yes      | a registry name                                              |
| `steps[].with`| no       | inputs; values may be templates                              |
| `steps[].when`| no       | expression; the step is skipped when it is falsy             |
| `checks`      | no       | list of `{id, expr, description}`                            |
| `outputs`     | no       | map of name to template                                      |

### Templates and expressions

A template is a string containing `{{ expr }}`. When the whole string is
one template the value keeps its type (an integer stays an integer);
otherwise the pieces are joined as text. Expressions are a small, safe
subset of Python: literals, names, attribute and index access,
comparisons, `and`/`or`/`not`, arithmetic, and a handful of builtins
(`len`, `min`, `max`, `abs`, `round`, `int`, `float`, `str`). Nothing
else parses. The names available are:

| name              | value                                                   |
|-------------------|---------------------------------------------------------|
| `params.<name>`   | the resolved parameter                                  |
| `steps.<id>.<out>`| an output of a completed step (skipped steps have none) |
| `run.dir`, `run.id` | the run directory and id                              |
| `env.<NAME>`      | an environment variable                                 |

## Runs

Every execution, whether of a protocol or an open session, is a run.

```
.amide/runs/<run-id>/
  run.json            status, protocol name, params, per-step state, timings
  protocol.yaml       the protocol exactly as loaded
  steps/<step-id>/    the step's workdir: whatever the tool wrote
  steps/<step-id>/outputs.json
  steps/<step-id>/log.txt
  results.json        every step's outputs, check results, named outputs
  report.md           human-readable summary
  run.log             the runner's own log
  manifest.json       sha256 of every file, written by export
```

The run id is `YYYYMMDD-HHMMSS-<4 hex>`. The runs root is `./.amide/runs`
unless `runs.dir` in the config or `--runs-dir` says otherwise.

- **Checkpointing.** `run.json` is rewritten after every step. Status
  moves through `pending`, `running`, and one of `passed`, `failed`
  (a check failed), `error` (a step failed), `budget_exceeded`.
- **Resume.** `amide run --resume <id>` reloads a run, skips steps that
  completed, and continues from the first that did not.
- **Detach.** `amide run --detach` creates the run, starts a background
  process that resumes it, prints the id, and returns. Output goes to
  `run.log`. `amide runs show <id>` follows progress.
- **Budgets.** `--max-seconds` stops the run before starting any step
  that would begin after the budget. Token and dollar budgets belong to
  the agent layer (milestone 3).
- **Approval.** Expensive steps ask first. `--yes` answers for all of
  them, which detached runs always pass.
- **Export.** `amide runs export <id>` writes `manifest.json` and packs
  the directory into `<id>.tar.gz`. That archive is the shareable
  result.

## Configuration

`~/.config/amide/config.toml` (honouring `XDG_CONFIG_HOME` and
`AMIDE_CONFIG`). Keys never go in protocols or runs.

```toml
[runs]
dir = ".amide/runs"          # relative to the working directory

[tools]
paths = ["~/amide-tools"]    # extra tool directories

[defaults]
model = "anthropic/claude-opus-5"

[providers.anthropic]
kind = "anthropic"
api_key_env = "ANTHROPIC_API_KEY"

[providers.openai]
kind = "openai"
api_key_env = "OPENAI_API_KEY"

[providers.deepseek]
kind = "openai"                          # OpenAI-compatible
base_url = "https://api.deepseek.com"
api_key_env = "DEEPSEEK_API_KEY"

[providers.gemini]
kind = "gemini"
api_key_env = "GEMINI_API_KEY"

[providers.local]
kind = "openai"
base_url = "http://localhost:11434/v1"   # Ollama, vLLM, anything compatible
api_key_env = "OLLAMA_API_KEY"
```

`amide config init` writes this template. `amide config show` prints
the effective config with keys redacted.

## Models (milestone 3)

Three hand-written adapters over plain HTTP, no vendor SDKs:

| kind        | covers                                                          |
|-------------|-----------------------------------------------------------------|
| `openai`    | OpenAI, DeepSeek, Mistral, Groq, xAI, Together, OpenRouter, Ollama, vLLM, anything with a compatible chat endpoint |
| `anthropic` | Anthropic Messages API                                          |
| `gemini`    | Google Gemini native API                                        |

All three normalise to one internal shape: a list of messages, a list of
tool definitions generated from tool manifests, streaming text deltas,
and tool-call events. A model is named `provider/model-id`; the provider
half selects the config entry, which selects the adapter.

## Agents (milestone 4)

Open experiments run as an interactive session. The session holds one
orchestrator agent and can spawn sub-agents with narrower tool sets and
possibly different models. Each sub-agent runs one task and returns a
result to its parent.

| role              | job                                                          | default tools                 |
|-------------------|--------------------------------------------------------------|-------------------------------|
| `orchestrate`     | own the experiment, delegate, decide when it is done         | spawn, protocol authoring     |
| `plan`            | turn a question into a protocol or a task list               | registry lookup, read-only    |
| `find`            | locate data, structures, literature, prior runs              | fetch tools, shell (read)     |
| `fix`             | make a failing step or protocol work                         | python, shell, the failing tool |
| `review`          | critique a plan, a result, or a report                       | read-only                     |
| `general-purpose` | anything else                                                | everything                    |

An open experiment ends with three files in its run directory:
`abstract.md`, `methodology.md` (with the protocol it converged on, so
the experiment can be re-run as a predefined protocol), and
`results.md`. Sessions have token, dollar, and wall-clock budgets; the
orchestrator is told when it is close and stopped when it is over.

The session UI is a Python chat loop with streaming output, tool-call
display, and approval prompts. The Go binary stays the structure
viewer; the agent hands it a file with `amide view`.

## Runners

The runner interface is "execute this step here". The local runner is
the only implementation today. A remote runner (milestone 5) will shell
out to the user's own CLI to copy the step's workdir, execute, and copy
results back, with the same manifest and the same run layout.

## Milestones

| #  | name                  | deliverable                                                                | status  |
|----|-----------------------|----------------------------------------------------------------------------|---------|
| 0  | CLI registration      | `amide` on PyPI                                                            | done    |
| 1  | viewer                | `amide view`                                                               | done    |
| 2  | protocols             | tool registry, 14 builtin tools, protocol runner, runs, resume, detach, export, `openmm-control` bundled | this branch |
| 3  | models                | three adapters, `amide models list`, `amide ask` for a one-shot tool-using call | next    |
| 4  | agents                | roles, orchestrator, interactive session, abstract/methodology/results     |         |
| 5  | remote runner         | run steps through a user-supplied CLI                                      |         |
| 6  | tool publishing       | a shared index and `amide tools publish`                                   |         |

## Licensing

GPL-3.0-or-later for the harness and the builtin tools. Locally added
tools are the user's own files and are not distributed with amide.
