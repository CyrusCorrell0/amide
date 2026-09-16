"""Fetch a UniProtKB entry."""

from __future__ import annotations

from typing import Any

from amide.harness.errors import ToolError
from amide.harness.tool import Param, ToolContext, tool

_URL = "https://rest.uniprot.org/uniprotkb/{accession}.{ext}"


@tool(
    name="uniprot_fetch",
    description=(
        "Fetch a protein entry from UniProtKB by accession, as FASTA (sequence) or "
        "JSON (the full annotated record)."
    ),
    inputs=[
        Param("accession", "string", "UniProt accession, for example P69905.", required=True),
        Param("format", "string", "fasta or json.", default="fasta", choices=("fasta", "json")),
    ],
    outputs=[
        Param("path", "path", "The downloaded file."),
        Param("accession", "string"),
        Param("sequence", "string", "The amino-acid sequence (FASTA only)."),
        Param("length", "integer", "Sequence length (FASTA only)."),
        Param("header", "string", "The FASTA header line (FASTA only)."),
    ],
    cost="cheap",
    tags=("fetch", "sequence"),
)
def uniprot_fetch(ctx: ToolContext, accession: str, format: str = "fasta") -> dict[str, Any]:
    from amide.tools._http import download

    acc = accession.strip().upper()
    if not acc or not acc.replace("-", "").isalnum():
        raise ToolError(f"uniprot_fetch: {accession!r} is not a UniProt accession")
    url = _URL.format(accession=acc, ext=format)
    ctx.log(f"GET {url}")
    dest = download(url, ctx.workdir / f"{acc}.{format}")
    result: dict[str, Any] = {"path": str(dest), "accession": acc}
    if format == "fasta":
        lines = dest.read_text().splitlines()
        if not lines or not lines[0].startswith(">"):
            raise ToolError(f"uniprot_fetch: {url} did not return FASTA")
        sequence = "".join(line.strip() for line in lines[1:])
        result.update({"header": lines[0][1:], "sequence": sequence, "length": len(sequence)})
    return result
