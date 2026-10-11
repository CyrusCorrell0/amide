from pathlib import Path

import pytest

from amide import pdb
from amide.pdb import NotPdb, Summary, has_atoms, summarize

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = ROOT / "tests" / "data" / "sample.pdb"


def _expected(path: Path) -> dict:
    """Recompute the summary by plain slicing, independent of amide.pdb."""
    text = path.read_text().splitlines()
    frames = sum(line.startswith("MODEL") for line in text)
    end = next(i for i, line in enumerate(text) if line.startswith("ENDMDL"))
    first = [line for line in text[:end] if line.startswith(("ATOM", "HETATM"))]
    xyz = [[float(line[s : s + 8]) for s in (30, 38, 46)] for line in first]
    return {
        "path": str(path),
        "id": None,
        "title": None,
        "frames": frames,
        "atoms": len(first),
        "hydrogens": sum(line[76:78].strip() == "H" for line in first),
        "chains": sorted({line[21] for line in first if line[21] != " "}),
        "residues": len({(line[21], line[22:27]) for line in first}),
        "extent": [round(max(c) - min(c), 1) for c in zip(*xyz)],
    }


def test_summarize_sample_matches_independent_count():
    summary = summarize(SAMPLE)
    assert isinstance(summary, Summary)
    assert summary.to_dict() == _expected(SAMPLE)


def test_to_dict_key_order_and_extent_list():
    d = summarize(SAMPLE).to_dict()
    assert list(d) == [
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
    assert isinstance(d["extent"], list) and len(d["extent"]) == 3


def test_atoms_outside_model_form_one_frame(tmp_path):
    atom = (
        "ATOM      1  HA  GLY   1       0.000   1.000   2.000  1.00  0.00            \n"
    )
    far = (
        "HETATM    2  O   HOH   2       3.000   1.000   7.500  1.00  0.00           O\n"
    )
    f = tmp_path / "bare.pdb"
    f.write_text(atom + far + "END\n")
    s = summarize(f)
    assert (s.frames, s.atoms, s.hydrogens, s.chains, s.residues) == (1, 2, 1, [], 2)
    assert s.extent == (3.0, 0.0, 5.5)


def test_header_id_and_title(tmp_path):
    f = tmp_path / "1ubq.pdb"
    lines = [
        "HEADER    CHROMOSOMAL PROTEIN                     02-JAN-87   1UBQ",
        "TITLE     STRUCTURE OF UBIQUITIN REFINED AT 1.8 ANGSTROMS RESOLUTION",
        "TITLE    2 AND OTHER THINGS",
        "ATOM      1  N   MET A   1      27.340  24.430   2.614  1.00  9.67           N",
    ]
    f.write_text("\n".join(lines) + "\n")
    s = summarize(f)
    assert s.id == "1UBQ"
    assert s.title == (
        "STRUCTURE OF UBIQUITIN REFINED AT 1.8 ANGSTROMS RESOLUTION AND OTHER THINGS"
    )


def test_has_atoms():
    assert has_atoms(SAMPLE)
    assert not has_atoms(ROOT / "pyproject.toml")


def test_summarize_raises_not_pdb():
    with pytest.raises(NotPdb) as exc:
        summarize(ROOT / "pyproject.toml")
    assert str(exc.value) == "`pyproject.toml` has no ATOM or HETATM records"


def test_has_atoms_stops_at_first_atom(tmp_path, monkeypatch):
    f = tmp_path / "big.pdb"
    lines = [
        "REMARK\n",
        "REMARK\n",
        "ATOM      1  N   GLY A   1       0.0     0.0     0.0\n",
    ]
    lines += ["REMARK\n"] * (10_000 - 3)
    f.write_text("".join(lines))

    read = []
    real_open = pdb._open

    class Counting:
        def __init__(self, path):
            self.f = real_open(path)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.f.close()

        def __iter__(self):
            for line in self.f:
                read.append(line)
                yield line

    monkeypatch.setattr(pdb, "_open", Counting)
    assert has_atoms(f)
    assert len(read) == 3
