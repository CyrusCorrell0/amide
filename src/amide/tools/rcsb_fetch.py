"""Download a structure from the RCSB Protein Data Bank."""

from __future__ import annotations

from typing import Any

from amide.harness.errors import ToolError
from amide.harness.tool import Param, ToolContext, tool

_URL = "https://files.rcsb.org/download/{id}.{ext}"
_EXT = {"pdb": "pdb", "cif": "cif"}


@tool(
    name="rcsb_fetch",
    description=(
        "Download an experimental structure from the RCSB Protein Data Bank by its "
        "four-character id, as PDB or mmCIF."
    ),
    inputs=[
        Param("id", "string", "Four-character PDB id, for example 1AKI.", required=True),
        Param("format", "string", "File format.", default="pdb", choices=("pdb", "cif")),
    ],
    outputs=[
        Param("path", "path", "The downloaded structure file."),
        Param("id", "string", "The id, upper-cased."),
        Param("format", "string", "pdb or cif."),
        Param("bytes", "integer", "File size."),
    ],
    cost="cheap",
    tags=("fetch", "structure"),
)
def rcsb_fetch(ctx: ToolContext, id: str, format: str = "pdb") -> dict[str, Any]:
    from amide.tools._http import download

    pdb_id = id.strip().upper()
    if len(pdb_id) != 4 or not pdb_id.isalnum():
        raise ToolError(f"rcsb_fetch: {id!r} is not a four-character PDB id")
    url = _URL.format(id=pdb_id, ext=_EXT[format])
    ctx.log(f"GET {url}")
    dest = download(url, ctx.workdir / f"{pdb_id}.{_EXT[format]}")
    return {
        "path": str(dest),
        "id": pdb_id,
        "format": format,
        "bytes": dest.stat().st_size,
    }
