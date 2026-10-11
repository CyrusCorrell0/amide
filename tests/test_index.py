import json
import os

import pytest
from typer.testing import CliRunner

from amide import cli, tui
from amide import index as idx
from amide.cli import app
from amide.index import BadFilter, Report, search

ATOM = "ATOM  {n:5d}  CA  GLY {chain}{n:4d}    {x:8.3f}   0.000   0.000  1.00  0.00           C\n"


def _pdb(path, frames=1, chain="A", pdb_id=None, title=None):
    """Write a small PDB file: optional HEADER/TITLE, then `frames` MODEL blocks."""
    lines = []
    if pdb_id:
        lines.append(f"HEADER    TEST{' ' * 48}{pdb_id}\n")
    if title:
        lines.append(f"TITLE     {title}\n")
    for f in range(frames):
        lines.append(f"MODEL     {f + 1:4d}\n")
        lines += [ATOM.format(n=n, chain=chain, x=float(n)) for n in (1, 2)]
        lines.append("ENDMDL\n")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(lines) + "END\n")
    return path


@pytest.fixture(autouse=True)
def cache(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    return tmp_path / "cache"


@pytest.fixture
def tree(tmp_path):
    root = tmp_path / "runs"
    _pdb(root / "a.pdb")
    _pdb(root / "deep" / "er" / "b.PDB", frames=3)
    (root / "notes.txt").write_text("not a pdb\n")
    (root / "c.pdb.bak").write_text("nope\n")
    return root


def test_path_honours_xdg_cache_home(cache):
    assert idx.path() == cache / "amide" / "index.json"


def test_path_defaults_to_home_cache(monkeypatch, tmp_path):
    monkeypatch.delenv("XDG_CACHE_HOME")
    monkeypatch.setattr(idx.Path, "home", lambda: tmp_path)
    assert idx.path() == tmp_path / ".cache" / "amide" / "index.json"


def test_load_before_first_index_is_empty():
    assert idx.load() == []


def test_index_walks_subdirectories_and_ignores_other_suffixes(tree):
    report = idx.index(tree)
    assert isinstance(report, Report)
    assert (report.indexed, report.new, report.updated, report.unchanged) == (
        2,
        2,
        0,
        0,
    )
    assert (report.skipped, report.removed) == (0, 0)
    assert report.index == str(idx.path())
    rows = {os.path.basename(r["path"]): r for r in idx.load()}
    assert set(rows) == {"a.pdb", "b.PDB"}
    b = rows["b.PDB"]
    assert b["frames"] == 3
    assert b["path"] == str((tree / "deep" / "er" / "b.PDB").resolve())
    assert b["size"] == (tree / "deep" / "er" / "b.PDB").stat().st_size
    assert isinstance(b["modified"], float) and isinstance(b["indexed"], float)


def test_progress_is_called_per_file(tree):
    calls = []
    idx.index(tree, progress=lambda done, total: calls.append((done, total)))
    assert calls == [(1, 2), (2, 2)]


def test_second_run_is_unchanged_and_summarizes_nothing(tree, monkeypatch):
    idx.index(tree)

    def boom(path):
        raise AssertionError(f"summarize called for {path}")

    monkeypatch.setattr(idx, "summarize", boom)
    report = idx.index(tree)
    assert (report.indexed, report.new, report.updated, report.unchanged) == (
        2,
        0,
        0,
        2,
    )


def test_changed_mtime_resummarizes_as_updated(tree):
    idx.index(tree)
    a = tree / "a.pdb"
    _pdb(a, frames=2)
    st = a.stat()
    os.utime(a, (st.st_atime, st.st_mtime + 10))
    report = idx.index(tree)
    assert (report.new, report.updated, report.unchanged) == (0, 1, 1)
    row = next(r for r in idx.load() if r["path"].endswith("a.pdb"))
    assert row["frames"] == 2


def test_deleted_file_is_removed(tree):
    idx.index(tree)
    (tree / "a.pdb").unlink()
    report = idx.index(tree)
    assert (report.indexed, report.removed) == (1, 1)
    assert [os.path.basename(r["path"]) for r in idx.load()] == ["b.PDB"]


def test_rows_outside_root_are_kept(tree, tmp_path):
    other = tmp_path / "other"
    _pdb(other / "x.pdb")
    idx.index(other)
    report = idx.index(tree)
    assert (report.indexed, report.removed) == (2, 0)
    assert len(idx.load()) == 3


def test_atomless_pdb_is_skipped(tree):
    (tree / "symbols.pdb").write_bytes(b"Microsoft C/C++ MSF 7.00\r\n\x1aDS\x00\x00")
    report = idx.index(tree)
    assert (report.indexed, report.new, report.skipped) == (2, 2, 1)


def test_save_is_atomic_and_leaves_no_temp_file(tree):
    idx.index(tree)
    siblings = os.listdir(idx.path().parent)
    assert siblings == ["index.json"]
    assert len(json.loads(idx.path().read_text())) == 2


def test_index_reads_id_and_title(tree):
    _pdb(tree / "1ubq.pdb", pdb_id="1UBQ", title="UBIQUITIN")
    idx.index(tree)
    row = next(r for r in idx.load() if r["path"].endswith("1ubq.pdb"))
    assert (row["id"], row["title"]) == ("1UBQ", "UBIQUITIN")


# search


def _row(path, id=None, title=None, frames=1, chains=("A",), size=100):
    return {
        "path": path,
        "id": id,
        "title": title,
        "frames": frames,
        "atoms": 10,
        "hydrogens": 0,
        "chains": list(chains),
        "residues": 5,
        "extent": [1.0, 1.0, 1.0],
        "size": size,
        "modified": 0.0,
        "indexed": 0.0,
    }


ROWS = [
    _row("/data/runs/md1.pdb", frames=50, chains=("A", "B"), size=900),
    _row("/data/xtal/1ubq.pdb", id="1UBQ", title="Ubiquitin", chains=("A",), size=300),
    _row(
        "/data/xtal/Lyso.pdb", id="2LZM", title="T4 lysozyme", frames=3, chains=("B",)
    ),
    _row("/data/runs/md2.pdb", frames=10, chains=("C",), size=500),
]


def _names(rows):
    return [r["path"].rsplit("/", 1)[1] for r in rows]


def test_no_tokens_returns_all_sorted_by_path_case_insensitively():
    assert _names(search(ROWS, [])) == ["md1.pdb", "md2.pdb", "1ubq.pdb", "Lyso.pdb"]


def test_bare_word_matches_id_title_and_path():
    assert _names(search(ROWS, ["1ubq"])) == ["1ubq.pdb"]  # id and path
    assert _names(search(ROWS, ["LYSOZYME"])) == ["Lyso.pdb"]  # title
    assert _names(search(ROWS, ["runs"])) == ["md1.pdb", "md2.pdb"]  # path
    assert _names(search(ROWS, ["2lzm"])) == ["Lyso.pdb"]  # id only


def test_field_filters_are_contains():
    assert _names(search(ROWS, ["id:lz"])) == ["Lyso.pdb"]
    assert _names(search(ROWS, ["title:ubiq"])) == ["1ubq.pdb"]
    assert _names(search(ROWS, ["path:xtal"])) == ["1ubq.pdb", "Lyso.pdb"]
    assert search(ROWS, ["id:md"]) == []  # None ids never match


@pytest.mark.parametrize(
    "token, expected",
    [
        ("frames:3", ["Lyso.pdb"]),
        ("frames:2..", ["md1.pdb", "md2.pdb", "Lyso.pdb"]),
        ("frames:..10", ["md2.pdb", "1ubq.pdb", "Lyso.pdb"]),
        ("frames:2..10", ["md2.pdb", "Lyso.pdb"]),
        ("size:..300", ["1ubq.pdb", "Lyso.pdb"]),
    ],
)
def test_numeric_filters(token, expected):
    assert _names(search(ROWS, [token])) == expected


def test_chains_is_membership():
    assert _names(search(ROWS, ["chains:A"])) == ["md1.pdb", "1ubq.pdb"]
    assert search(ROWS, ["chains:Z"]) == []


def test_tokens_and_together():
    assert _names(search(ROWS, ["runs", "chains:B"])) == ["md1.pdb"]
    assert _names(search(ROWS, ["path:data", "frames:2..", "chains:C"])) == ["md2.pdb"]


@pytest.mark.parametrize(
    "token", ["color:red", "frames:abc", "frames:..", "frames:1.5", "atoms:"]
)
def test_bad_filters_raise(token):
    with pytest.raises(BadFilter):
        search(ROWS, [token])


def test_unknown_key_message_lists_valid_keys():
    with pytest.raises(BadFilter, match="color") as exc:
        search(ROWS, ["color:red"])
    assert "frames" in str(exc.value) and "chains" in str(exc.value)


def test_bad_number_message_names_the_token():
    with pytest.raises(BadFilter, match="frames:abc"):
        search(ROWS, ["frames:abc"])


def test_unknown_sort_raises():
    with pytest.raises(BadFilter, match="title"):
        search(ROWS, [], sort="title")


def test_sort_by_frames_reversed():
    assert _names(search(ROWS, [], sort="frames", reverse=True)) == [
        "md1.pdb",
        "md2.pdb",
        "Lyso.pdb",
        "1ubq.pdb",
    ]


@pytest.mark.parametrize("reverse", [False, True])
def test_none_ids_sort_last_in_both_directions(reverse):
    names = _names(search(ROWS, [], sort="id", reverse=reverse))
    assert set(names[2:]) == {"md1.pdb", "md2.pdb"}
    assert names[:2] == (
        ["Lyso.pdb", "1ubq.pdb"] if reverse else ["1ubq.pdb", "Lyso.pdb"]
    )


def test_windows_drive_path_is_a_bare_word():
    rows = [{"path": r"C:\runs\md1.pdb", "id": None, "title": None}]
    assert search(rows, [r"C:\runs"]) == rows
    assert search(rows, ["D:/other"]) == []


# CLI


runner = CliRunner()

INFO_KEYS = [
    "path",
    "id",
    "title",
    "frames",
    "atoms",
    "hydrogens",
    "chains",
    "residues",
    "extent",
]
REPORT_KEYS = ["indexed", "new", "updated", "unchanged", "skipped", "removed", "index"]


def test_cli_index_on_a_file_exits_2(tree):
    a = tree / "a.pdb"
    result = runner.invoke(app, ["index", str(a)])
    assert result.exit_code == 2
    assert result.stdout == ""
    assert result.stderr.strip() == (
        f"amide index: `{a}` is not a directory; pass a directory to walk"
    )


def test_cli_index_prints_two_lines_on_a_terminal(tree, monkeypatch):
    monkeypatch.setattr(cli, "_stdout_is_terminal", lambda: True)
    (tree / "symbols.pdb").write_bytes(b"Microsoft C/C++ MSF 7.00\r\n\x1aDS\x00\x00")
    result = runner.invoke(app, ["index", str(tree)])
    assert result.exit_code == 0
    assert result.stdout == (
        f"indexed 2 files under {tree.resolve()}: 2 new, 0 updated, 0 unchanged, "
        "1 skipped (no ATOM records), 0 removed\n"
        f"{idx.path()}\n"
    )
    assert "indexing 3/3" in result.stderr


def test_cli_index_without_skips_has_no_parenthetical(tree, monkeypatch):
    monkeypatch.setattr(cli, "_stdout_is_terminal", lambda: True)
    result = runner.invoke(app, ["index", str(tree)])
    assert "0 skipped, 0 removed\n" in result.stdout


def test_cli_index_json_is_the_report(tree):
    result = runner.invoke(app, ["index", str(tree), "--json"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert list(data) == REPORT_KEYS
    assert (data["indexed"], data["new"], data["index"]) == (2, 2, str(idx.path()))
    assert result.stderr == "", "no progress counter off a terminal"


def test_cli_search_before_indexing_exits_2():
    result = runner.invoke(app, ["search"])
    assert result.exit_code == 2
    assert result.stderr.strip() == (
        "amide search: nothing indexed yet; run `amide index <directory>`"
    )


def test_cli_search_piped_prints_json_rows(tree):
    idx.index(tree)
    result = runner.invoke(app, ["search", "frames:2.."])
    assert result.exit_code == 0
    rows = json.loads(result.stdout)
    assert [os.path.basename(r["path"]) for r in rows] == ["b.PDB"]
    assert list(rows[0]) == INFO_KEYS + ["size", "modified", "indexed"]


def test_cli_search_with_no_match_prints_an_empty_list(tree):
    idx.index(tree)
    result = runner.invoke(app, ["search", "nothing-like-this", "--json"])
    assert result.exit_code == 0
    assert json.loads(result.stdout) == []


@pytest.mark.parametrize(
    "args, needle",
    [(["--sort", "nope"], "unknown sort key `nope`"), (["frames:abc"], "frames:abc")],
)
def test_cli_search_bad_input_exits_2(tree, args, needle):
    idx.index(tree)
    result = runner.invoke(app, ["search", *args])
    assert result.exit_code == 2
    assert result.stdout == ""
    assert result.stderr.startswith("amide search: ")
    assert needle in result.stderr


@pytest.fixture
def viewer(tree, monkeypatch, tmp_path):
    """A terminal on Windows with a fake viewer that records argv and the rows file."""
    import subprocess
    import sys
    from pathlib import Path

    idx.index(tree)
    monkeypatch.setattr(cli, "_stdout_is_terminal", lambda: True)
    monkeypatch.setattr(sys, "platform", "win32")
    binary = tmp_path / "amide-tui"
    monkeypatch.setattr(tui, "binary_path", lambda: binary)
    calls = []

    def run(argv, check):
        calls.append((argv, json.loads(Path(argv[2]).read_text(encoding="utf-8"))))
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(subprocess, "run", run)
    return binary, calls


def test_cli_search_on_a_terminal_opens_the_viewer_on_the_rows(viewer):
    binary, calls = viewer
    result = runner.invoke(
        app, ["search", "frames:2..", "path:er", "--sort", "atoms", "-r"]
    )
    assert result.exit_code == 0, result.output
    [(argv, payload)] = calls
    assert argv[:2] == [str(binary), "--search"]
    assert payload == {
        "query": "frames:2.. path:er",
        "sort": "atoms",
        "reverse": True,
        "rows": search(
            idx.load(), ["frames:2..", "path:er"], sort="atoms", reverse=True
        ),
    }
    assert len(payload["rows"]) == 1
    assert not os.path.exists(argv[2])


def test_cli_search_without_a_viewer_exits_3(viewer, monkeypatch):
    def unavailable():
        raise tui.TuiUnavailable("amide view: no viewer here; set `AMIDE_TUI`")

    monkeypatch.setattr(tui, "binary_path", unavailable)
    result = runner.invoke(app, ["search"])
    assert result.exit_code == 3
    assert result.stderr.strip() == "amide view: no viewer here; set `AMIDE_TUI`"
    assert viewer[1] == []
