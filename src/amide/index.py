"""Keep a searchable index of every PDB file under a directory.

The index is one JSON list at $XDG_CACHE_HOME/amide/index.json (default
~/.cache). Each row is an `amide info` row plus `size`, `modified` (mtime)
and `indexed` (when it was summarized). It is a cache: derived from disk,
rebuilt by `index()`, and read as-is by the Go viewer.
"""

import json
import os
import re
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from amide.pdb import NotPdb, summarize

SORT_KEYS = (
    "path",
    "id",
    "frames",
    "atoms",
    "hydrogens",
    "residues",
    "size",
    "modified",
)
_TEXT_KEYS = ("id", "title", "path")
_NUMERIC_KEYS = ("frames", "atoms", "hydrogens", "residues", "size")
_FILTER_KEYS = (*_TEXT_KEYS, "chains", *_NUMERIC_KEYS)
_TOKEN = re.compile(r"^(\w+):(.*)$")
_DRIVE = re.compile(r"^[A-Za-z]:[\\/]")  # C:\runs is a path, not a filter
_RANGE = re.compile(r"^(\d+)?\.\.(\d+)?$")


class BadFilter(ValueError):
    """A search token or sort key that does not parse; the message says why."""


@dataclass(frozen=True)
class Report:
    indexed: int  # rows under the root after this run
    new: int
    updated: int
    unchanged: int
    skipped: int  # .pdb files with no atoms, such as Windows debug symbols
    removed: int  # rows whose file vanished
    index: str  # where the index was saved


def path() -> Path:
    """Where the index lives."""
    root = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(root) / "amide" / "index.json"


def load() -> list[dict]:
    """Every indexed row; [] before the first `index()`."""
    try:
        return json.loads(path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []


def _save(rows: list[dict]) -> Path:
    """Write the index atomically: a sibling temp file renamed over it."""
    dest = path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=dest.parent, prefix=".index-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(rows, f)
        os.replace(tmp, dest)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return dest


def _walk(root: Path) -> list[Path]:
    return [
        Path(top, name)
        for top, _, names in os.walk(root)
        for name in names
        if name.lower().endswith(".pdb")
    ]


def index(root: Path, progress=None) -> Report:
    """Summarize every .pdb under root into the index, skipping unchanged files."""
    root = Path(root).resolve()
    prefix = os.path.join(str(root), "")
    rows = {row["path"]: row for row in load()}
    files = _walk(root)
    new = updated = unchanged = skipped = 0
    seen: set[str] = set()

    for done, file in enumerate(files, 1):
        key = str(file.resolve())
        seen.add(key)
        st = os.stat(key)
        old = rows.get(key)
        if old and old.get("size") == st.st_size and old.get("modified") == st.st_mtime:
            unchanged += 1
        else:
            try:
                row = summarize(Path(key)).to_dict()
            except NotPdb:
                skipped += 1
                rows.pop(key, None)
            else:
                row.update(
                    path=key, size=st.st_size, modified=st.st_mtime, indexed=time.time()
                )
                rows[key] = row
                if old:
                    updated += 1
                else:
                    new += 1
        if progress:
            progress(done, len(files))

    gone = [p for p in rows if p.startswith(prefix) and p not in seen]
    for p in gone:
        del rows[p]
    dest = _save(list(rows.values()))
    return Report(
        indexed=sum(p.startswith(prefix) for p in rows),
        new=new,
        updated=updated,
        unchanged=unchanged,
        skipped=skipped,
        removed=len(gone),
        index=str(dest),
    )


def _contains(value, needle: str) -> bool:
    return value is not None and needle in str(value).lower()


def _number(key: str, value: str):
    """A predicate on an integer field: `3`, `2..`, `..10` or `2..10`."""
    if value.isdigit():
        n = int(value)
        return lambda row: row.get(key) == n
    m = _RANGE.match(value)
    if not m or m.groups() == (None, None):
        raise BadFilter(
            f"`{key}:{value}` needs a whole number or a range: 3, 2.., ..10, 2..10"
        )
    lo, hi = (int(g) if g else None for g in m.groups())
    return lambda row: (
        row.get(key) is not None
        and (lo is None or row[key] >= lo)
        and (hi is None or row[key] <= hi)
    )


def _predicate(token: str):
    m = None if _DRIVE.match(token) else _TOKEN.match(token)
    if not m:
        word = token.lower()
        return lambda row: any(_contains(row.get(k), word) for k in _TEXT_KEYS)
    key, value = m.groups()
    if key in _TEXT_KEYS:
        needle = value.lower()
        return lambda row: _contains(row.get(key), needle)
    if key == "chains":
        return lambda row: value in (row.get("chains") or ())
    if key in _NUMERIC_KEYS:
        return _number(key, value)
    raise BadFilter(f"unknown filter `{key}:`; filters are {', '.join(_FILTER_KEYS)}")


def search(rows, tokens, sort="path", reverse=False) -> list[dict]:
    """Rows matching every token, sorted by `sort`; rows missing it come last."""
    if sort not in SORT_KEYS:
        raise BadFilter(
            f"unknown sort key `{sort}`; sort keys are {', '.join(SORT_KEYS)}"
        )
    predicates = [_predicate(t) for t in tokens]
    hits = [row for row in rows if all(p(row) for p in predicates)]
    present = [row for row in hits if row.get(sort) is not None]
    missing = [row for row in hits if row.get(sort) is None]

    def key(row):
        v = row[sort]
        return v.lower() if isinstance(v, str) else v

    return sorted(present, key=key, reverse=reverse) + missing
