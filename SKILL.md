---
name: amide
description: amide is a molecular dynamics CLI that summarizes PDB files and trajectories as JSON and opens them in an interactive terminal viewer. Use it when a user wants to inspect, summarize, or look at a PDB structure or molecular dynamics trajectory from the command line.
---

# amide

Install:

```
uv tool install amide
```

## Rules

1. Never run `amide view` in a captured shell. It is a full-screen terminal viewer; off a terminal it exits 4. Give the command to the user to run.
2. Use `amide info <file.pdb> --json` to learn what is in a file: frames, atoms, hydrogens, chains, residues, extent.
3. To find files across a tree, run `amide index <dir>` once, then `amide search frames:2.. --json`. Never run `amide search` without `--json` in a captured shell; on a terminal it opens an interactive screen.

amide reads PDB files only. Convert other formats first. When something fails, run `amide doctor --json`.

## Exit codes

| Code | Do this |
|---|---|
| 0 | Done. |
| 2 | Fix the path or arguments. From `search`: fix the filter, or run `amide index <dir>` first. |
| 3 | Viewer unavailable: retry online, or set `AMIDE_TUI`. |
| 4 | No terminal: use `amide info`, or hand `view` to the user. |
| 5 | Not a PDB file: convert it, then retry. |

Full docs: https://github.com/CyrusCorrell0/amide#readme
