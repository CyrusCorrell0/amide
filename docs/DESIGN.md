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
    remote.py       runners: local, and command runners over the user's own CLI
    report.py       report.md and results.json
  tools/            builtin tools, one module each
  protocols/        bundled protocol YAML files
  models/
    base.py         Message, Request, Reply, ToolCall, the Adapter interface
    http.py         urllib with retries and SSE parsing; tests fake it
    openai.py       OpenAI-compatible chat completions
    anthropic.py    Anthropic Messages API
    gemini.py       Gemini native API
    providers.py    builtin providers, config overlay, provider/model resolution
    loop.py         the tool-calling loop behind `amide ask`
  agents/
    roles.py        the six roles: prompts and tool selection
    session.py      Experiment state, Session (orchestrator, spawn, budgets, session tools)
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

## Models

Three hand-written adapters over plain HTTP (`urllib`), no vendor SDKs,
in `src/amide/models/`:

| kind        | covers                                                          |
|-------------|-----------------------------------------------------------------|
| `openai`    | OpenAI, DeepSeek, Mistral, Groq, xAI, Together, OpenRouter, Ollama, vLLM, anything with a compatible chat endpoint |
| `anthropic` | Anthropic Messages API                                          |
| `gemini`    | Google Gemini native API                                        |

All three speak one internal shape (`models/base.py`): a `Request` of
`Message`s plus tool definitions taken straight from `ToolSpec.to_schema`,
and a `Reply` with text, tool calls, a normalised stop reason (`end`,
`tool_calls`, `length`, `refusal`, `other`), and token usage. Each adapter
has `complete`, `stream` (text deltas to a callback), and `list_models`.
An assistant turn keeps the provider's raw content and replays it
verbatim, so reasoning blocks and signatures survive the round trip.

A model is named `provider/model-id`. Providers come from a builtin set
(`anthropic`, `openai`, `gemini`, `deepseek`, `mistral`, `groq`, `xai`,
`together`, `openrouter`, `local`) with `[providers.*]` in the config
layered on top; a config entry can change a builtin or add a new one.
`api_key_env` names the environment variable; empty means no key. Extra
keys in a provider table are adapter options: `headers`, the OpenAI
`max_tokens_param`, the Anthropic `fallbacks`.

Provider details that matter:

- Anthropic: thinking is left to the model's default (the parameter is
  omitted); `--effort` maps to `output_config.effort`; `fallbacks =
  "default"` is sent unless the provider sets `fallbacks = false`, so a
  request a safety classifier declines is re-run on a fallback model
  rather than stopping. Refusals surface as stop `refusal` with the
  category and explanation.
- OpenAI-compatible: `max_completion_tokens` against `api.openai.com`,
  `max_tokens` elsewhere; `--effort` maps to `reasoning_effort`; streaming
  asks for usage in the final chunk.
- Gemini: tool schemas lose `default` and arrays gain `items`; function
  calls get synthetic ids; consecutive tool results share one turn.

`models/loop.py` is the tool-calling loop: call the model, run every tool
it asks for through the registry (validation, requirement checks,
expensive-tool approval, errors all become tool results the model sees),
append the results, repeat until it stops or `max_turns` is spent.
`amide ask` is that loop once, from the command line, with a transcript
and tool outputs left under `.amide/scratch/ask-<time>/`.

## Agents

`amide experiment "question"` runs an open experiment (`src/amide/agents/`).
A session holds one orchestrator agent and can spawn sub-agents with
narrower tool sets and possibly different models. Each sub-agent runs one
task to completion and returns its report to its parent as a tool result;
sub-agents start with no memory of the parent's conversation, so the
orchestrator's brief has to be self-contained (its prompt says so).

| role              | job                                                          | registry tools        | session tools                                            |
|-------------------|--------------------------------------------------------------|-----------------------|----------------------------------------------------------|
| `orchestrate`     | own the experiment, delegate, decide when it is done         | fetch tools           | read, write, list, describe, run_protocol, spawn_agent, finish_experiment |
| `plan`            | turn a question into a protocol or a task list               | none                  | read, list, describe                                     |
| `find`            | locate data, structures, literature, prior runs              | fetch tools, shell    | read, write, list, describe                              |
| `fix`             | make a failing step or protocol work                         | everything            | read, write, list, describe, run_protocol                |
| `review`          | critique a plan, a result, or a report                       | none                  | read, list, describe                                     |
| `general-purpose` | anything else                                                | everything            | read, write, list, describe, run_protocol, spawn_agent   |

Session tools live in `agents/session.py`, not the registry, because they
need the experiment: `read_file`, `write_file`, `list_files` (confined to
the experiment directory), `describe_tools` and `describe_protocol`
(manifests and bundled protocols), `run_protocol` (a name or complete
protocol YAML, written under `protocols/`, validated, run through the
ordinary runner into `runs/`, with status, checks, and outputs returned),
`spawn_agent` (depth-limited to two), and `finish_experiment`. Registry
tools whose requirements are missing are left out of a role's tool list;
`describe_tools` still reports them, with what is missing.

An experiment ends with three files in its directory: `abstract.md`,
`methodology.md` (with the protocol it converged on, verbatim, so the
experiment can be re-run as a predefined protocol), and `results.md`,
written by `finish_experiment`. If the orchestrator stops without calling
it, it is nudged once; if it still does not, the experiment is `paused`
and `amide experiment --resume ID "follow-up"` continues it with the
transcript intact.

Budgets are tokens (input plus output across every agent), wall-clock
seconds, and dollars (from a `[pricing]` table in the config, per
`provider/model`). At 80% a harness note is appended to the next tool
result; at 100% no further model call is made and the experiment is
`budget_exceeded`, resumable with a larger budget. Every model call and
tool call is checkpointed to `experiment.json` and the agent's
`transcript.json`.

`--interactive` turns the CLI into a chat loop: after each orchestrator
turn the user can reply, and the orchestrator is told it may end a turn
with a question. Expensive tools and expensive protocol steps ask on a
terminal, are approved by `--yes`, and are otherwise reported to the model
as not approved. `amide runs list` shows experiments next to protocol
runs; `amide runs show` and `amide runs export` work on both. The Go
binary stays the structure viewer.

## Runners

The runner interface is "execute this step here" (`harness/remote.py`):
`Runner.run_step(spec, ctx, args)`. The local runner calls the tool. A
`CommandRunner` comes from a `[runners.<name>]` table in the config and
shells out to a CLI the user already has; amide manages no cloud.

```toml
[runners.gpu]
host = "my-gpu-box"
copy_to = "rsync -a {src}/ {host}:{dst}/"
copy_from = "rsync -a {host}:{src}/ {dst}/"
exec = "ssh {host} {command}"
python = "python3"      # remote interpreter with amide installed
root = "/tmp/amide"     # remote directory that holds copied runs
```

Per step it clears and recreates `<root>/<run-id>` on the remote, copies
the whole run directory over, runs `amide tools run <tool> --inputs
steps/<id>/inputs.json --workdir steps/<id>` there (a command anyone can
also use by hand), and copies the step directory back. Paths in the
inputs that point into the local run directory are rewritten to the
remote copy and back, so tools see ordinary paths on both sides, and the
run layout is identical whichever machine ran a step; `run.json` records
the runner per step.

`amide run --runner gpu` sends every step whose cost is at least
`--remote-cost` (default `moderate`, so fetches stay local); a step can
pin `runner: local` or `runner: gpu` in the protocol. `amide experiment
--runner gpu` does the same for the protocols agents run. The remote
machine needs amide and the tool's requirements installed, and any local
tools the protocol uses reachable through its own config.

## Milestones

| #  | name                  | deliverable                                                                | status  |
|----|-----------------------|----------------------------------------------------------------------------|---------|
| 0  | CLI registration      | `amide` on PyPI                                                            | done    |
| 1  | viewer                | `amide view`                                                               | done    |
| 2  | protocols             | tool registry, 14 builtin tools, protocol runner, runs, resume, detach, export, `openmm-control` bundled | done    |
| 3  | models                | three adapters, `amide models list`, `amide ask` for a one-shot tool-using call | done    |
| 4  | agents                | roles, orchestrator, interactive session, abstract/methodology/results     | done    |
| 5  | remote runner         | run steps through a user-supplied CLI                                      | this branch |
| 6  | tool publishing       | a shared index and `amide tools publish`                                   | later   |

## Licensing

GPL-3.0-or-later for the harness and the builtin tools. Locally added
tools are the user's own files and are not distributed with amide.
