"""Shared OpenMM plumbing for the simulation tools. Imported lazily."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from amide.harness.errors import ToolError
from amide.harness.tool import Param, Requirements

REQUIRES = Requirements(python=("openmm",), hint="Install with: pip install 'amide[sim]'")

NONBONDED = ("auto", "PME", "NoCutoff", "CutoffNonPeriodic", "CutoffPeriodic")
CONSTRAINTS = ("HBonds", "None", "AllBonds", "HAngles")

FORCEFIELD_PARAMS = [
    Param("forcefield", "string", "OpenMM force field XML.", default="amber14-all.xml"),
    Param("water_model", "string", "OpenMM water model XML.", default="amber14/tip3pfb.xml"),
    Param(
        "nonbonded_method",
        "string",
        "auto picks PME when the structure has a periodic box and NoCutoff otherwise.",
        default="auto",
        choices=NONBONDED,
    ),
    Param("cutoff_nm", "number", "Nonbonded cutoff for cutoff methods.", default=1.0),
    Param("constraints", "string", "Bond constraints.", default="HBonds", choices=CONSTRAINTS),
    Param(
        "platform",
        "string",
        "OpenMM platform (CUDA, OpenCL, CPU, Reference); empty lets OpenMM choose.",
        default="",
    ),
]


def load_structure(path: str) -> tuple[Any, Any]:
    """Topology and positions from a PDB or mmCIF file."""
    from openmm import app

    file = Path(path)
    if not file.is_file():
        raise ToolError(f"{path} does not exist")
    if file.suffix.lower() in (".cif", ".mmcif", ".pdbx"):
        pdb = app.PDBxFile(str(file))
    else:
        pdb = app.PDBFile(str(file))
    return pdb.topology, pdb.positions


def write_pdb(topology: Any, positions: Any, path: Path) -> Path:
    from openmm import app

    with path.open("w") as handle:
        app.PDBFile.writeFile(topology, positions, handle, keepIds=True)
    return path


def build_system(
    topology: Any,
    forcefield: str,
    water_model: str,
    nonbonded_method: str,
    cutoff_nm: float,
    constraints: str,
) -> tuple[Any, str]:
    """A System for ``topology``; returns it with the nonbonded method actually used."""
    from openmm import app, unit

    try:
        ff = app.ForceField(forcefield, water_model) if water_model else app.ForceField(forcefield)
    except Exception as error:
        raise ToolError(f"cannot load force field {forcefield} + {water_model}: {error}") from None
    method = nonbonded_method
    if method == "auto":
        method = "PME" if topology.getPeriodicBoxVectors() is not None else "NoCutoff"
    if (
        method != "NoCutoff"
        and method != "CutoffNonPeriodic"
        and topology.getPeriodicBoxVectors() is None
    ):
        raise ToolError(f"{method} needs a periodic box; the structure has none (solvate it first)")
    kwargs: dict[str, Any] = {
        "nonbondedMethod": getattr(app, method),
        "constraints": None if constraints == "None" else getattr(app, constraints),
    }
    if method != "NoCutoff":
        kwargs["nonbondedCutoff"] = cutoff_nm * unit.nanometer
    try:
        system = ff.createSystem(topology, **kwargs)
    except Exception as error:
        raise ToolError(
            f"the force field cannot parametrise this structure: {error}. "
            "Prepare it first (pdbfixer_prepare adds hydrogens and fixes residues)."
        ) from None
    return system, method


def make_simulation(topology: Any, system: Any, integrator: Any, platform: str) -> Any:
    import openmm
    from openmm import app

    if platform:
        try:
            chosen = openmm.Platform.getPlatformByName(platform)
        except Exception:
            names = [
                openmm.Platform.getPlatform(i).getName()
                for i in range(openmm.Platform.getNumPlatforms())
            ]
            raise ToolError(
                f"no OpenMM platform {platform!r}; available: {', '.join(names)}"
            ) from None
        return app.Simulation(topology, system, integrator, chosen)
    return app.Simulation(topology, system, integrator)


def potential_energy_kj_mol(simulation: Any) -> float:
    from openmm import unit

    state = simulation.context.getState(getEnergy=True)
    return state.getPotentialEnergy().value_in_unit(unit.kilojoule_per_mole)
