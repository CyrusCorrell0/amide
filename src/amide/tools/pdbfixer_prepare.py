"""Make a structure simulation-ready: fix, protonate, strip, optionally solvate."""

from __future__ import annotations

from typing import Any

from amide.harness.errors import ToolError
from amide.harness.tool import Param, Requirements, ToolContext, tool

_STANDARD = {
    "ALA", "ARG", "ASN", "ASP", "CYS", "GLN", "GLU", "GLY", "HIS", "ILE", "LEU", "LYS",
    "MET", "PHE", "PRO", "SER", "THR", "TRP", "TYR", "VAL", "HID", "HIE", "HIP", "CYX",
    "ASH", "GLH", "LYN",
    "DA", "DC", "DG", "DT", "A", "C", "G", "U",
}  # fmt: skip
_WATER = {"HOH", "WAT", "TIP3", "SOL"}


@tool(
    name="pdbfixer_prepare",
    description=(
        "Prepare a structure for simulation: add missing residues and atoms (with PDBFixer "
        "when installed), remove heterogens, add hydrogens at a pH, and optionally solvate "
        "in a periodic water box with ions. Falls back to OpenMM's Modeller, which cannot "
        "repair missing residues, when PDBFixer is absent."
    ),
    inputs=[
        Param("structure", "path", "PDB or mmCIF to prepare.", required=True),
        Param("ph", "number", "pH for protonation.", default=7.0),
        Param("add_missing", "boolean", "Add missing residues and heavy atoms.", default=True),
        Param("remove_heterogens", "boolean", "Strip ligands and ions.", default=True),
        Param("keep_water", "boolean", "Keep crystal waters when stripping.", default=False),
        Param("add_hydrogens", "boolean", default=True),
        Param("solvate", "boolean", "Add a water box and neutralising ions.", default=False),
        Param("padding_nm", "number", "Water padding around the solute.", default=1.0),
        Param("ionic_strength_molar", "number", default=0.15),
        Param("water", "string", "Water model for solvation.", default="tip3p"),
        Param("forcefield", "string", "OpenMM force field XML.", default="amber14-all.xml"),
        Param("water_model", "string", "OpenMM water model XML.", default="amber14/tip3pfb.xml"),
    ],
    outputs=[
        Param("path", "path", "The prepared PDB."),
        Param("method", "string", "pdbfixer or modeller."),
        Param("atoms", "integer"),
        Param("residues", "integer"),
        Param("solvated", "boolean"),
        Param("missing_residues", "integer", "Residues added (pdbfixer only)."),
        Param("missing_atoms", "integer", "Heavy atoms added (pdbfixer only)."),
    ],
    cost="moderate",
    requires=Requirements(python=("openmm",), hint="Install with: pip install 'amide[sim]'"),
    tags=("prepare", "structure", "openmm"),
)
def pdbfixer_prepare(
    ctx: ToolContext,
    structure: str,
    ph: float = 7.0,
    add_missing: bool = True,
    remove_heterogens: bool = True,
    keep_water: bool = False,
    add_hydrogens: bool = True,
    solvate: bool = False,
    padding_nm: float = 1.0,
    ionic_strength_molar: float = 0.15,
    water: str = "tip3p",
    forcefield: str = "amber14-all.xml",
    water_model: str = "amber14/tip3pfb.xml",
) -> dict[str, Any]:
    from openmm import app, unit

    from amide.tools._openmm import load_structure, write_pdb

    result: dict[str, Any] = {"solvated": False, "missing_residues": 0, "missing_atoms": 0}
    try:
        from pdbfixer import PDBFixer
    except ImportError:
        PDBFixer = None

    if PDBFixer is not None:
        ctx.log("using PDBFixer")
        try:
            fixer = PDBFixer(filename=structure)
        except Exception as error:
            raise ToolError(f"pdbfixer_prepare: cannot read {structure}: {error}") from None
        if add_missing:
            fixer.findMissingResidues()
            result["missing_residues"] = sum(len(v) for v in fixer.missingResidues.values())
        else:
            fixer.missingResidues = {}
        fixer.findNonstandardResidues()
        fixer.replaceNonstandardResidues()
        if remove_heterogens:
            fixer.removeHeterogens(keepWater=keep_water)
        fixer.findMissingAtoms()
        result["missing_atoms"] = sum(len(v) for v in fixer.missingAtoms.values())
        if not add_missing:
            fixer.missingAtoms = {}
            fixer.missingTerminals = {}
        fixer.addMissingAtoms()
        if add_hydrogens:
            fixer.addMissingHydrogens(ph)
        topology, positions = fixer.topology, fixer.positions
        result["method"] = "pdbfixer"
    else:
        ctx.log("PDBFixer is not installed; using OpenMM Modeller (no missing-residue repair)")
        topology, positions = load_structure(structure)
        modeller = app.Modeller(topology, positions)
        if remove_heterogens:
            doomed = [
                residue
                for residue in modeller.topology.residues()
                if residue.name not in _STANDARD and not (keep_water and residue.name in _WATER)
            ]
            modeller.delete(doomed)
        if add_hydrogens:
            ff = app.ForceField(forcefield, water_model)
            modeller.addHydrogens(ff, pH=ph)
        topology, positions = modeller.topology, modeller.positions
        result["method"] = "modeller"

    if solvate:
        ff = app.ForceField(forcefield, water_model)
        modeller = app.Modeller(topology, positions)
        try:
            modeller.addSolvent(
                ff,
                model=water,
                padding=padding_nm * unit.nanometer,
                ionicStrength=ionic_strength_molar * unit.molar,
            )
        except Exception as error:
            raise ToolError(f"pdbfixer_prepare: solvation failed: {error}") from None
        topology, positions = modeller.topology, modeller.positions
        result["solvated"] = True

    dest = write_pdb(topology, positions, ctx.workdir / "prepared.pdb")
    result.update(
        {
            "path": str(dest),
            "atoms": topology.getNumAtoms(),
            "residues": topology.getNumResidues(),
        }
    )
    return result
