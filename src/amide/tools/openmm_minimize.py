"""Energy-minimise a prepared structure with OpenMM."""

from __future__ import annotations

from typing import Any

from amide.harness.tool import Param, ToolContext, tool
from amide.tools._openmm import FORCEFIELD_PARAMS, REQUIRES


@tool(
    name="openmm_minimize",
    description=(
        "Energy-minimise a prepared structure with OpenMM's L-BFGS minimiser and report "
        "the potential energy before and after."
    ),
    inputs=[
        Param("structure", "path", "A prepared PDB (hydrogens present).", required=True),
        Param("max_iterations", "integer", "0 runs until converged.", default=0),
        Param("tolerance_kj_mol_nm", "number", "Convergence tolerance.", default=10.0),
        *FORCEFIELD_PARAMS,
    ],
    outputs=[
        Param("path", "path", "The minimised PDB."),
        Param("energy_before_kj_mol", "number"),
        Param("energy_after_kj_mol", "number"),
        Param("platform", "string", "The OpenMM platform used."),
        Param("nonbonded_method", "string"),
        Param("atoms", "integer"),
    ],
    cost="moderate",
    requires=REQUIRES,
    tags=("simulation", "openmm"),
)
def openmm_minimize(
    ctx: ToolContext,
    structure: str,
    max_iterations: int = 0,
    tolerance_kj_mol_nm: float = 10.0,
    forcefield: str = "amber14-all.xml",
    water_model: str = "amber14/tip3pfb.xml",
    nonbonded_method: str = "auto",
    cutoff_nm: float = 1.0,
    constraints: str = "HBonds",
    platform: str = "",
) -> dict[str, Any]:
    import openmm
    from openmm import unit

    from amide.tools._openmm import (
        build_system,
        load_structure,
        make_simulation,
        potential_energy_kj_mol,
        write_pdb,
    )

    topology, positions = load_structure(structure)
    system, method = build_system(
        topology, forcefield, water_model, nonbonded_method, cutoff_nm, constraints
    )
    integrator = openmm.VerletIntegrator(0.001 * unit.picoseconds)
    simulation = make_simulation(topology, system, integrator, platform)
    simulation.context.setPositions(positions)
    before = potential_energy_kj_mol(simulation)
    ctx.log(f"potential energy before: {before:.1f} kJ/mol")
    simulation.minimizeEnergy(
        tolerance=tolerance_kj_mol_nm * unit.kilojoule_per_mole / unit.nanometer,
        maxIterations=max_iterations,
    )
    after = potential_energy_kj_mol(simulation)
    ctx.log(f"potential energy after: {after:.1f} kJ/mol")
    state = simulation.context.getState(getPositions=True)
    dest = write_pdb(topology, state.getPositions(), ctx.workdir / "minimized.pdb")
    return {
        "path": str(dest),
        "energy_before_kj_mol": round(before, 3),
        "energy_after_kj_mol": round(after, 3),
        "platform": simulation.context.getPlatform().getName(),
        "nonbonded_method": method,
        "atoms": topology.getNumAtoms(),
    }
