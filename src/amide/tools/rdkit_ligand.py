"""Build a 3D ligand from SMILES and compute drug-likeness descriptors."""

from __future__ import annotations

from typing import Any

from amide.harness.errors import ToolError
from amide.harness.tool import Param, Requirements, ToolContext, tool


@tool(
    name="rdkit_ligand",
    description=(
        "Turn a SMILES string into a 3D conformer written as SDF, and compute descriptors: "
        "molecular weight, logP, H-bond donors and acceptors, TPSA, rotatable bonds, and "
        "Lipinski rule-of-five violations."
    ),
    inputs=[
        Param("smiles", "string", "The molecule as SMILES.", required=True),
        Param("name", "string", "Name written into the SDF.", default="ligand"),
        Param("embed", "boolean", "Generate a 3D conformer.", default=True),
        Param("optimize", "boolean", "MMFF-optimise the conformer.", default=True),
        Param("seed", "integer", "Random seed for embedding.", default=42),
    ],
    outputs=[
        Param("sdf", "path", "The SDF file (2D when embed is false)."),
        Param("canonical_smiles", "string"),
        Param("formula", "string"),
        Param(
            "descriptors",
            "object",
            "molecular_weight, logp, hbd, hba, tpsa, rotatable_bonds, heavy_atoms, rings",
        ),
        Param("lipinski_violations", "integer"),
    ],
    cost="cheap",
    requires=Requirements(python=("rdkit",), hint="Install with: pip install 'amide[chem]'"),
    tags=("ligand", "chem"),
)
def rdkit_ligand(
    ctx: ToolContext,
    smiles: str,
    name: str = "ligand",
    embed: bool = True,
    optimize: bool = True,
    seed: int = 42,
) -> dict[str, Any]:
    from rdkit import Chem
    from rdkit.Chem import AllChem, Crippen, Descriptors, Lipinski, rdMolDescriptors

    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        raise ToolError(f"rdkit_ligand: {smiles!r} is not valid SMILES")
    mol.SetProp("_Name", name)
    descriptors = {
        "molecular_weight": round(Descriptors.MolWt(mol), 3),
        "logp": round(Crippen.MolLogP(mol), 3),
        "hbd": Lipinski.NumHDonors(mol),
        "hba": Lipinski.NumHAcceptors(mol),
        "tpsa": round(rdMolDescriptors.CalcTPSA(mol), 3),
        "rotatable_bonds": Lipinski.NumRotatableBonds(mol),
        "heavy_atoms": mol.GetNumHeavyAtoms(),
        "rings": rdMolDescriptors.CalcNumRings(mol),
    }
    violations = sum(
        [
            descriptors["molecular_weight"] > 500,
            descriptors["logp"] > 5,
            descriptors["hbd"] > 5,
            descriptors["hba"] > 10,
        ]
    )
    if embed:
        mol = Chem.AddHs(mol)
        params = AllChem.ETKDGv3()
        params.randomSeed = seed
        if AllChem.EmbedMolecule(mol, params) != 0:
            raise ToolError(f"rdkit_ligand: could not embed {smiles!r} in 3D")
        if optimize:
            ctx.log("MMFF optimisation")
            AllChem.MMFFOptimizeMolecule(mol)
    else:
        AllChem.Compute2DCoords(mol)
    dest = ctx.workdir / f"{name}.sdf"
    with Chem.SDWriter(str(dest)) as writer:
        writer.write(mol)
    return {
        "sdf": str(dest),
        "canonical_smiles": Chem.MolToSmiles(Chem.RemoveHs(mol)),
        "formula": rdMolDescriptors.CalcMolFormula(mol),
        "descriptors": descriptors,
        "lipinski_violations": violations,
    }
