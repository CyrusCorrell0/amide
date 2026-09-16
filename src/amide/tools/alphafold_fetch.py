"""Download a predicted structure from the AlphaFold Protein Structure Database."""

from __future__ import annotations

from typing import Any

from amide.harness.errors import ToolError
from amide.harness.tool import Param, ToolContext, tool

_API = "https://alphafold.ebi.ac.uk/api/prediction/{accession}"


@tool(
    name="alphafold_fetch",
    description=(
        "Download the AlphaFold-predicted structure for a UniProt accession from the "
        "AlphaFold Protein Structure Database, as PDB or mmCIF."
    ),
    inputs=[
        Param("accession", "string", "UniProt accession, for example P69905.", required=True),
        Param("format", "string", "File format.", default="pdb", choices=("pdb", "cif")),
    ],
    outputs=[
        Param("path", "path", "The downloaded model."),
        Param("entry_id", "string", "The AlphaFold DB entry id."),
        Param("model_version", "integer", "The AlphaFold DB model version."),
        Param("sequence_length", "integer"),
        Param("organism", "string"),
    ],
    cost="cheap",
    tags=("fetch", "structure", "prediction"),
)
def alphafold_fetch(ctx: ToolContext, accession: str, format: str = "pdb") -> dict[str, Any]:
    from amide.tools._http import download, fetch_json

    acc = accession.strip().upper()
    url = _API.format(accession=acc)
    ctx.log(f"GET {url}")
    entries = fetch_json(url)
    if not isinstance(entries, list) or not entries:
        raise ToolError(f"alphafold_fetch: AlphaFold DB has no prediction for {acc}")
    entry = entries[0]
    key = "pdbUrl" if format == "pdb" else "cifUrl"
    file_url = entry.get(key)
    if not file_url:
        raise ToolError(f"alphafold_fetch: the entry for {acc} has no {format} file")
    ctx.log(f"GET {file_url}")
    dest = download(file_url, ctx.workdir / f"AF-{acc}.{format}")
    return {
        "path": str(dest),
        "entry_id": entry.get("entryId"),
        "model_version": entry.get("latestVersion"),
        "sequence_length": len(entry.get("uniprotSequence") or ""),
        "organism": entry.get("organismScientificName"),
    }
