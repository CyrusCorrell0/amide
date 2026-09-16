"""Look up a compound in PubChem."""

from __future__ import annotations

import urllib.parse
from typing import Any

from amide.harness.errors import ToolError
from amide.harness.tool import Param, ToolContext, tool

_BASE = "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound"
_DEFAULT_PROPERTIES = (
    "MolecularFormula",
    "MolecularWeight",
    "SMILES",
    "ConnectivitySMILES",
    "IUPACName",
    "XLogP",
    "TPSA",
    "HBondDonorCount",
    "HBondAcceptorCount",
    "RotatableBondCount",
)


@tool(
    name="pubchem_fetch",
    description=(
        "Look up a small molecule in PubChem by name, CID, SMILES, or InChIKey and return "
        "its computed properties, optionally downloading a 3D (or 2D) SDF."
    ),
    inputs=[
        Param("query", "string", "The identifier to look up.", required=True),
        Param(
            "namespace",
            "string",
            "What kind of identifier the query is.",
            default="name",
            choices=("name", "cid", "smiles", "inchikey"),
        ),
        Param(
            "properties",
            "list",
            "PubChem property names to return.",
            default=list(_DEFAULT_PROPERTIES),
        ),
        Param("download_sdf", "boolean", "Also fetch an SDF of the first hit.", default=False),
    ],
    outputs=[
        Param("cid", "integer", "PubChem compound id of the first hit."),
        Param("properties", "object", "The requested properties of the first hit."),
        Param("hits", "integer", "How many compounds matched."),
        Param("sdf", "path", "The SDF file, when download_sdf is true."),
        Param("sdf_dimension", "string", "3d or 2d, when an SDF was downloaded."),
    ],
    cost="cheap",
    tags=("fetch", "ligand"),
)
def pubchem_fetch(
    ctx: ToolContext,
    query: str,
    namespace: str = "name",
    properties: list[str] | None = None,
    download_sdf: bool = False,
) -> dict[str, Any]:
    from amide.tools._http import download, fetch_json

    wanted = list(properties or _DEFAULT_PROPERTIES)
    quoted = urllib.parse.quote(query.strip(), safe="")
    url = f"{_BASE}/{namespace}/{quoted}/property/{','.join(wanted)}/JSON"
    ctx.log(f"GET {url}")
    data = fetch_json(url)
    hits = data.get("PropertyTable", {}).get("Properties", [])
    if not hits:
        raise ToolError(f"pubchem_fetch: no compound matches {query!r}")
    first = dict(hits[0])
    cid = int(first.pop("CID"))
    result: dict[str, Any] = {"cid": cid, "properties": first, "hits": len(hits)}
    if download_sdf:
        for dimension in ("3d", "2d"):
            sdf_url = f"{_BASE}/cid/{cid}/SDF?record_type={dimension}"
            ctx.log(f"GET {sdf_url}")
            try:
                dest = download(sdf_url, ctx.workdir / f"CID_{cid}_{dimension}.sdf")
            except ToolError as error:
                ctx.log(str(error))
                continue
            result["sdf"] = str(dest)
            result["sdf_dimension"] = dimension
            break
        else:
            raise ToolError(f"pubchem_fetch: PubChem has no SDF for CID {cid}")
    return result
