"""Answer "what is in this PDB file" with the standard library alone.

Frames follow the viewer's rule (tui/pdb/parser.go): MODEL opens a frame,
ENDMDL closes it, and atoms outside any MODEL block form one trailing
frame. Per-frame statistics describe the first frame. The id comes from
columns 63-66 of HEADER and the title from columns 11-80 of every TITLE
record, joined by one space; MD output usually has neither, so both may be None.
"""

from dataclasses import asdict, dataclass
from pathlib import Path

_ATOM_RECORDS = ("ATOM", "HETATM")


class NotPdb(Exception):
    """The file has no ATOM or HETATM records."""

    def __init__(self, path: Path) -> None:
        super().__init__(f"`{Path(path).name}` has no ATOM or HETATM records")


@dataclass(frozen=True)
class Summary:
    path: str
    id: str | None
    title: str | None
    frames: int
    atoms: int
    hydrogens: int
    chains: list[str]
    residues: int
    extent: tuple[float, float, float]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["extent"] = list(self.extent)
        return d


def _open(path: Path):
    return Path(path).open(encoding="ascii", errors="replace")


def has_atoms(path: Path) -> bool:
    """True at the first ATOM or HETATM line; reads no further."""
    with _open(path) as f:
        return any(line.startswith(_ATOM_RECORDS) for line in f)


def _element(line: str) -> str:
    element = line[76:78].strip()
    if not element:
        element = line[12:16].strip().lstrip("0123456789")[:1]
    return element.upper()


def summarize(path: Path) -> Summary:
    """Summarize a PDB file in one pass. Raises NotPdb when it has no atoms."""
    pdb_id: str | None = None
    title: list[str] = []
    frames = 0
    open_atoms = 0  # atoms in the frame being read
    atoms = hydrogens = 0
    chains: set[str] = set()
    residues: set[tuple[str, str, str]] = set()
    lo = [float("inf")] * 3
    hi = [float("-inf")] * 3

    with _open(path) as f:
        for line in f:
            record = line[:6].strip()
            if record == "HEADER":
                pdb_id = line[62:66].strip() or None
            elif record == "TITLE":
                title.append(line[10:80].strip())
            elif record in ("MODEL", "ENDMDL"):
                if open_atoms:
                    frames += 1
                open_atoms = 0
            elif record in _ATOM_RECORDS and len(line) >= 54:
                try:
                    xyz = [float(line[s : s + 8]) for s in (30, 38, 46)]
                except ValueError:
                    continue  # the viewer skips unparseable atoms too
                open_atoms += 1
                if frames:
                    continue  # statistics come from the first frame only
                atoms += 1
                hydrogens += _element(line) == "H"
                chain = line[21:22].strip()
                if chain:
                    chains.add(chain)
                residues.add((chain, line[22:26].strip(), line[26:27].strip()))
                for i, v in enumerate(xyz):
                    lo[i] = min(lo[i], v)
                    hi[i] = max(hi[i], v)
    if open_atoms:
        frames += 1
    if not frames:
        raise NotPdb(path)

    return Summary(
        path=str(path),
        id=pdb_id,
        title=" ".join(t for t in title if t) or None,
        frames=frames,
        atoms=atoms,
        hydrogens=hydrogens,
        chains=sorted(chains),
        residues=len(residues),
        extent=tuple(round(h - lo_, 1) for lo_, h in zip(lo, hi)),
    )
