"""Run molecular dynamics with OpenMM."""

from __future__ import annotations

import csv
from typing import Any

from amide.harness.errors import ToolError
from amide.harness.tool import Param, ToolContext, tool
from amide.tools._openmm import FORCEFIELD_PARAMS, REQUIRES


@tool(
    name="openmm_simulate",
    description=(
        "Run molecular dynamics on a prepared structure with OpenMM: optional minimisation "
        "and equilibration, then a Langevin production run at constant temperature (and "
        "constant pressure when pressure_bar is set). Writes a DCD trajectory, a CSV state "
        "log, and the final structure."
    ),
    inputs=[
        Param("structure", "path", "A prepared PDB (hydrogens present).", required=True),
        Param("steps", "integer", "Production steps.", required=True),
        Param("temperature_k", "number", default=300.0),
        Param("timestep_fs", "number", default=2.0),
        Param("friction_per_ps", "number", "Langevin friction.", default=1.0),
        Param("pressure_bar", "number", "Set for NPT; needs a periodic box.", default=None),
        Param("minimize", "boolean", "Minimise before dynamics.", default=True),
        Param("equilibration_steps", "integer", "Steps before reporting starts.", default=0),
        Param(
            "report_interval", "integer", "Steps between frames; 0 picks ~100 frames.", default=0
        ),
        Param("seed", "integer", "Random seed; 0 leaves it random.", default=0),
        *FORCEFIELD_PARAMS,
    ],
    outputs=[
        Param("topology", "path", "PDB of the system at the start of production."),
        Param("trajectory", "path", "DCD trajectory."),
        Param("log", "path", "CSV of step, time, energies, temperature."),
        Param("final", "path", "PDB of the last frame."),
        Param("steps", "integer"),
        Param("frames", "integer"),
        Param("time_ps", "number", "Production length."),
        Param("platform", "string"),
        Param("nonbonded_method", "string"),
        Param("ensemble", "string", "NVT or NPT."),
        Param("temperature_mean_k", "number"),
        Param("potential_energy_final_kj_mol", "number"),
        Param("ns_per_day", "number", "Production throughput."),
    ],
    cost="expensive",
    requires=REQUIRES,
    tags=("simulation", "openmm", "md"),
)
def openmm_simulate(
    ctx: ToolContext,
    structure: str,
    steps: int,
    temperature_k: float = 300.0,
    timestep_fs: float = 2.0,
    friction_per_ps: float = 1.0,
    pressure_bar: float | None = None,
    minimize: bool = True,
    equilibration_steps: int = 0,
    report_interval: int = 0,
    seed: int = 0,
    forcefield: str = "amber14-all.xml",
    water_model: str = "amber14/tip3pfb.xml",
    nonbonded_method: str = "auto",
    cutoff_nm: float = 1.0,
    constraints: str = "HBonds",
    platform: str = "",
) -> dict[str, Any]:
    import time

    import openmm
    from openmm import app, unit

    from amide.tools._openmm import (
        build_system,
        load_structure,
        make_simulation,
        potential_energy_kj_mol,
        write_pdb,
    )

    if steps <= 0:
        raise ToolError("openmm_simulate: steps must be positive")
    topology, positions = load_structure(structure)
    system, method = build_system(
        topology, forcefield, water_model, nonbonded_method, cutoff_nm, constraints
    )
    ensemble = "NVT"
    if pressure_bar is not None:
        if topology.getPeriodicBoxVectors() is None:
            raise ToolError("openmm_simulate: pressure_bar needs a periodic box; solvate first")
        system.addForce(
            openmm.MonteCarloBarostat(pressure_bar * unit.bar, temperature_k * unit.kelvin)
        )
        ensemble = "NPT"
    integrator = openmm.LangevinMiddleIntegrator(
        temperature_k * unit.kelvin,
        friction_per_ps / unit.picosecond,
        timestep_fs * unit.femtoseconds,
    )
    if seed:
        integrator.setRandomNumberSeed(seed)
    simulation = make_simulation(topology, system, integrator, platform)
    simulation.context.setPositions(positions)
    if minimize:
        ctx.log("minimising")
        simulation.minimizeEnergy()
    if seed:
        simulation.context.setVelocitiesToTemperature(temperature_k * unit.kelvin, seed)
    else:
        simulation.context.setVelocitiesToTemperature(temperature_k * unit.kelvin)
    if equilibration_steps > 0:
        ctx.log(f"equilibrating for {equilibration_steps} steps")
        simulation.step(equilibration_steps)

    interval = report_interval or max(1, steps // 100)
    start_state = simulation.context.getState(getPositions=True)
    topology_path = write_pdb(topology, start_state.getPositions(), ctx.workdir / "topology.pdb")
    trajectory_path = ctx.workdir / "trajectory.dcd"
    log_path = ctx.workdir / "state.csv"
    simulation.reporters.append(app.DCDReporter(str(trajectory_path), interval))
    simulation.reporters.append(
        app.StateDataReporter(
            str(log_path),
            interval,
            step=True,
            time=True,
            potentialEnergy=True,
            kineticEnergy=True,
            temperature=True,
            volume=ensemble == "NPT",
            speed=False,
        )
    )
    ctx.log(
        f"production: {steps} steps of {timestep_fs} fs on {simulation.context.getPlatform().getName()}"
    )
    clock = time.monotonic()
    simulation.step(steps)
    elapsed = time.monotonic() - clock
    del simulation.reporters[:]  # close the DCD file

    final_state = simulation.context.getState(getPositions=True)
    final_path = write_pdb(topology, final_state.getPositions(), ctx.workdir / "final.pdb")
    time_ps = steps * timestep_fs / 1000.0
    temperatures = _column(log_path, "Temperature (K)")
    return {
        "topology": str(topology_path),
        "trajectory": str(trajectory_path),
        "log": str(log_path),
        "final": str(final_path),
        "steps": steps,
        "frames": steps // interval,
        "time_ps": time_ps,
        "platform": simulation.context.getPlatform().getName(),
        "nonbonded_method": method,
        "ensemble": ensemble,
        "temperature_mean_k": round(sum(temperatures) / len(temperatures), 2)
        if temperatures
        else None,
        "potential_energy_final_kj_mol": round(potential_energy_kj_mol(simulation), 3),
        "ns_per_day": round((time_ps / 1000.0) / (elapsed / 86400.0), 3) if elapsed > 0 else None,
    }


def _column(path: Any, header: str) -> list[float]:
    with open(path, newline="") as handle:
        reader = csv.DictReader(handle)
        key = next((name for name in reader.fieldnames or [] if header in name), None)
        if key is None:
            return []
        return [float(row[key]) for row in reader if row.get(key)]
