# amide

Molecular dynamics in your terminal: amide opens a PDB trajectory as a shaded pixel molecule you can rotate, play, and step through frame by frame.

```
uv tool install amide
amide view protein.pdb
```

Or `pip install amide`. Python 3.11 or newer. The wheel is pure Python with one dependency (typer).

- Version: 0.2.0
- License: GPL-3.0-or-later
- Package: https://pypi.org/project/amide/
- Source: https://github.com/CyrusCorrell0/amide
- Site: https://amide.cyruscorrell.com/

The first `view` downloads the viewer binary for your platform from the GitHub release `v0.2.0`, verifies its sha256 against `checksums.txt` from the same release, and caches it at `$XDG_CACHE_HOME/amide/tui/0.2.0/` (default `~/.cache/amide/tui/0.2.0/`). Later runs are instant. `amide view` with no path opens a file picker on the current directory.

## Commands

| Command | What it does |
|---|---|
| `amide` | Prints the banner. Static, with no escape codes, when stdout is not a terminal. |
| `amide view [PATH]` | Opens the viewer on a PDB file, or the file picker on a directory. PATH defaults to `.`. Needs an interactive terminal. |
| `amide info <PATH> [--json]` | Prints frames, atoms, chains, residues and extent of a PDB file. No terminal, network or viewer needed. JSON when `--json` is passed or stdout is not a terminal. |
| `amide index [PATH] [--json]` | Finds every PDB file under PATH and adds it to the global index. PATH defaults to `.`. |
| `amide search [FILTERS] [--sort KEY] [-r] [--json]` | Searches the index. Opens the search screen on a terminal. JSON when `--json` is passed or stdout is not a terminal. |
| `amide doctor [--json]` | Checks everything `view` needs and prints the fix for each failure. Makes no network requests. |
| `amide -v`, `amide --version` | Prints `amide 0.2.0`. |
| `amide -h`, `amide --help` | Prints help with examples. Under 100 ms. |

### amide info

```
$ amide info protein.pdb
protein.pdb
  frames    3
  atoms     1,204 per frame (no hydrogens)
  chains    A, B
  residues  150
  extent    42.1 x 38.7 x 55.2 A
```

Atom, hydrogen, chain, residue and extent figures come from the first frame. `--json` prints the same as one object with stable keys, in this order:

```json
{
  "path": "protein.pdb",
  "id": null,
  "title": null,
  "frames": 3,
  "atoms": 1204,
  "hydrogens": 0,
  "chains": [
    "A",
    "B"
  ],
  "residues": 150,
  "extent": [
    42.1,
    38.7,
    55.2
  ]
}
```

`id` is the four-character code from the `HEADER` record and `title` the text of the `TITLE` records, joined across lines. MD output usually has neither, so both are `null` there, and the human output prints them only when present.

### amide index

```
amide index ./runs                      walk ./runs, summarize every .pdb, store the rows
amide search                            open the search screen on everything indexed
amide search 1ubq frames:2..            start it narrowed: words match id, title or path
amide search --sort frames -r --json    the same rows as JSON, for pipes and agents
```

`amide index` walks PATH, runs `amide info` on every `.pdb` file, and keeps the rows in one JSON file at `$XDG_CACHE_HOME/amide/index.json` (default `~/.cache/amide/index.json`). A row has the keys of `amide info --json` plus `size`, `modified` and `indexed`.

Unchanged files are skipped by size and modification time, so a second run over the same tree is instant. Files that vanished from under PATH are removed. Windows debug symbols are also `.pdb` files; they have no ATOM records, so they are counted as skipped instead of failing the run. The exit code is 0 even when files were skipped.

The report is two lines: the counts, then where the index lives. `--json` prints the same counts as one object.

```
$ amide index C:\runs
indexed 1,204 files under C:\runs: 1,180 new, 20 updated, 4 unchanged, 2 skipped (no ATOM records), 0 removed
~/.cache/amide/index.json
```

### amide search

On a terminal, `amide search` opens the search screen: the matching files on the left, the highlighted one drawn and slowly spinning on the right, its facts underneath.

```
+-- amide search  frames:2.. ------------------------------------------------+
| > 1ubq_                                   | +-------------------------------+ |
|                                           | |                               | |
|   1UBQ   1 frame    660 atoms  A          | |        (half-block pixel      | |
| > -     500 frames  1204 atoms AB         | |         molecule, spinning    | |
|   2LYZ   1 frame   1001 atoms  A          | |         slowly)               | |
|   -      20 frames  3312 atoms ABCD       | |                               | |
|                                           | +-------------------------------+ |
|                                           | traj.pdb                          |
|                                           | 500 frames  1,204 atoms  AB       |
|                                           | 150 residues  42.1 x 38.7 x 55.2  |
|                                           | 18.2 MB  C:\runs\prod\traj.pdb    |
| type to filter  enter open  s sort: frames  r reverse  esc quit                |
+----------------------------------------------------------------------------------+
```

Typing narrows the list on id, title and filename. `enter` opens the viewer on the highlighted file, and `q` in the viewer comes back to the list with the cursor where it was. See "Keys in search" below.

Filters are `key:value` tokens. They need no quoting in any shell, and all of them must match:

| Token | Matches |
|---|---|
| `word` | `id`, `title` or `path` contains `word`, case-insensitive |
| `id:1ubq` `title:lysozyme` `path:runs` | that field contains the value |
| `chains:A` | the chain list contains `A` |
| `frames:3` | equals |
| `frames:2..` `frames:..10` `frames:2..10` | at least, at most, between |

Numeric keys: `frames`, `atoms`, `hydrogens`, `residues`, `size`. `--sort KEY` orders the rows by `path` (the default), `id`, `frames`, `atoms`, `hydrogens`, `residues`, `size` or `modified`, and `-r` reverses. Rows without an id sort last.

`--json`, or stdout that is not a terminal, prints the matching rows as a JSON list, `[]` when nothing matches. An unknown key, a non-numeric value or an unknown sort key exits 2 and names the valid ones. An empty index exits 2:

```
amide search: nothing indexed yet; run `amide index <directory>`
```

### amide doctor

```
$ amide doctor
  ok    amide 0.2.0, python 3.12.4
  ok    platform windows/amd64 is supported
  ok    viewer 0.2.0 cached at C:\Users\you\.cache\amide\tui\0.2.0\amide-tui.exe
  ok    terminal is interactive (120x40)
```

A failing check prints `fail` and a second line starting `fix:`. Exit 0 when every check passes, 1 otherwise. `--json` prints `{"ok": bool, "checks": [{"name", "ok", "detail", "fix"}]}`.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Done. |
| 2 | Usage error, or PATH does not exist. For `search`, a bad filter or an empty index. |
| 3 | The viewer is unavailable (no build, offline, checksum mismatch). |
| 4 | `view` was run without an interactive terminal. |
| 5 | The file has no ATOM or HETATM records (not a PDB amide can read). |
| 130 | Interrupted with ctrl+c. |

`doctor` also exits 1 when any check fails. Errors are one line on stderr, start with `amide <command>:`, and end with the next step when there is one.

## Environment

| Variable | Effect |
|---|---|
| `AMIDE_TUI` | Path to a viewer binary to use instead of downloading one. |
| `XDG_CACHE_HOME` | Cache root for the viewer binary and the index. Default `~/.cache`. |
| `AMIDE_NO_ANIMATION` | Print the banner in one go, no flourish. |
| `NO_COLOR` | Honoured. Also prints the banner in one go. |
| `CI` | Prints the banner in one go, and makes `view` exit 4. |

## Platforms

Viewer builds: darwin/arm64, darwin/amd64, linux/amd64, linux/arm64, windows/amd64. Not windows/arm64. `amide info` runs everywhere Python does.

## Keys in the viewer

| Action | Keys |
|---|---|
| Rotate | arrows, `h` `j` `k` `l`, or `w` `a` `s` `d` |
| Pan | `H` `J` `K` `L` |
| Zoom | `+` `-` |
| Play or pause | `space` |
| Previous or next frame | `,` `.` |
| Slower or faster | `[` `]` |
| Cycle render mode (space fill, ball and stick, wireframe, cartoon) | `m` |
| Show or hide hydrogens | `e` |
| Toggle side panel | `tab` |
| Toggle pixel rendering | `p` |
| Toggle cartoon | `c` |
| Reset view | `0` |
| Back to picker or search | `q`, `esc` |
| Quit | `ctrl+c` |

## Keys in search

| Action | Keys |
|---|---|
| Filter | type |
| Move | arrows, `j` `k` |
| Open in the viewer | `enter` |
| Cycle sort (path, id, frames, atoms) | `s` |
| Reverse sort | `r` |
| Quit | `esc`, `q` |

## For agents

**Do not run `amide view` in a captured shell.** It is a full-screen program. Off a terminal it refuses with exit 4 instead of hanging, but it never shows you anything. The same goes for `amide search` without `--json`: on a terminal it opens the interactive search screen, like `view`.

Paths, in order of preference:

1. `amide info <file> --json` to learn what is in a file. It is non-interactive, offline, and cheap.
2. Hand `amide view <file>` to the person, to run in their own terminal.
3. `amide index <dir>` once, then `amide search frames:2.. --json` to find files by frames, id or title without opening each.
4. `amide doctor --json` when any of the above fails and the cause is unclear.

Decision rule: amide reads PDB files only. Convert other formats (`.gro`, `.xtc`, `.dcd`, `.cif`) to PDB first, for example with MDAnalysis or `gmx trjconv`, then run amide on the result.

What to do on each exit code:

| Code | Do this |
|---|---|
| 2 | Fix the path or the arguments. stderr names the problem. From `search`, fix the filter, or run `amide index <dir>` when nothing is indexed yet. |
| 3 | The viewer is unavailable to `view` or `search`. Retry once online, or set `AMIDE_TUI` to a local viewer binary. Run `amide doctor` to see which. |
| 4 | Use `amide info` instead, or give the `view` command to the person. |
| 5 | Convert the file to PDB, then retry. |

Verify the install:

```
amide -v
amide info tests/data/sample.pdb --json
```

The second line works from a checkout of the source repository. Any PDB file will do elsewhere.

Suggested line for AGENTS.md or CLAUDE.md:

```
amide: molecular dynamics CLI for PDB trajectories. Install with `uv tool install amide`.
Use `amide info <file.pdb> --json` to inspect a file. `amide view <file.pdb>` is an
interactive terminal viewer: give it to the user, never run it in a captured shell.
Docs: https://amide.cyruscorrell.com/docs.md
```

## Not yet

- PDB only. No other trajectory formats.
- No shell completion.
- No viewer for Windows on arm64.
- No parallel indexing. A first `amide index` over a large tree summarizes one file at a time.
- No `amide view <id>`. Open a file by id through `amide search`.
- No watching directories. Run `amide index` again after files change.
- The first `view` needs the network once. Offline it exits 3; set `AMIDE_TUI` to a local binary.
