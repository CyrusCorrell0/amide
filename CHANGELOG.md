# Changelog

## 0.2.0 - 2026-10-11

- `amide view` exits 4 instead of hanging when there is no interactive terminal or `CI` is set.
- `amide view` exits 5 when the file has no ATOM or HETATM records, before the viewer opens.
- New `amide info <PATH> [--json]`: frames, atoms, chains, residues and extent of a PDB file, offline and non-interactive.
- New `amide doctor [--json]`: checks platform, viewer and terminal, with a fix for each failure.
- `amide -h` and `amide view -h` end with runnable examples.
- Every error is one line on stderr starting with `amide <command>:` and ends with the next step.
- `amide` with no arguments prints the banner, animated on a terminal and static otherwise.
- New `amide index [PATH] [--json]`: finds every PDB file under PATH and keeps its `amide info` row in one global index, skipping unchanged files.
- New `amide search [FILTERS] [--sort KEY] [-r] [--json]`: filters and sorts the index with `key:value` tokens; opens a search screen with a spinning preview on a terminal, prints JSON otherwise.
- `amide info` prints the PDB id and title from the `HEADER` and `TITLE` records; `--json` gains `id` and `title`.
- The viewer binary accepts `--search <rows.json>` and returns to the search screen from the viewer.
- README, CHANGELOG and SKILL.md added; the PyPI page now has a description, keywords and project links.

## 0.1.0 - 2026-09-03

- New `amide view [PATH]`: opens the terminal molecular viewer on a PDB file, or a file picker on a directory.
- The viewer binary is downloaded on first use from the matching GitHub release, verified by sha256, and cached.
- Release pipeline builds viewer binaries for darwin, linux and windows and publishes the package to PyPI.

## 0.0.1

- Registered the `amide` name on PyPI with a placeholder CLI.
