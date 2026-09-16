"""Trajectory analysis with MDAnalysis."""

from __future__ import annotations

import csv
from typing import Any

from amide.harness.errors import ToolError
from amide.harness.tool import Param, Requirements, ToolContext, tool

METRICS = ("rmsd", "rmsf", "rg")


@tool(
    name="mdanalysis_analyze",
    description=(
        "Compute RMSD to a reference, per-residue RMSF, and radius of gyration over a "
        "trajectory for an atom selection, in angstrom. Writes per-frame and per-atom CSVs."
    ),
    inputs=[
        Param("topology", "path", "PDB, GRO, or other topology MDAnalysis reads.", required=True),
        Param(
            "trajectory",
            "path",
            "DCD, XTC, TRR, or similar; omit to analyse the topology alone.",
            default=None,
        ),
        Param("metrics", "list", "Any of rmsd, rmsf, rg.", default=["rmsd", "rg"]),
        Param("selection", "string", "MDAnalysis selection string.", default="protein and name CA"),
        Param(
            "reference",
            "path",
            "Structure to compute RMSD against; default is the first frame.",
            default=None,
        ),
        Param("stride", "integer", "Analyse every n-th frame.", default=1),
    ],
    outputs=[
        Param("n_frames", "integer"),
        Param("n_atoms", "integer", "Atoms in the selection."),
        Param("rmsd_mean", "number"),
        Param("rmsd_max", "number"),
        Param("rmsd_final", "number"),
        Param("rg_mean", "number"),
        Param("rg_min", "number"),
        Param("rg_max", "number"),
        Param("rmsf_mean", "number"),
        Param("rmsf_max", "number"),
        Param("timeseries", "path", "CSV with one row per frame."),
        Param("rmsf_csv", "path", "CSV with one row per selected atom."),
    ],
    cost="cheap",
    requires=Requirements(python=("MDAnalysis",), hint="Install with: pip install 'amide[sim]'"),
    tags=("analysis", "trajectory"),
)
def mdanalysis_analyze(
    ctx: ToolContext,
    topology: str,
    trajectory: str | None = None,
    metrics: list[str] | None = None,
    selection: str = "protein and name CA",
    reference: str | None = None,
    stride: int = 1,
) -> dict[str, Any]:
    import warnings

    import numpy as np

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        import MDAnalysis as mda
        from MDAnalysis.analysis import align, rms
    warnings.filterwarnings("ignore", category=DeprecationWarning, module="MDAnalysis")

    wanted = [m.lower() for m in (metrics or ["rmsd", "rg"])]
    unknown = sorted(set(wanted) - set(METRICS))
    if unknown:
        raise ToolError(
            f"mdanalysis_analyze: unknown metrics {', '.join(unknown)}; use {', '.join(METRICS)}"
        )
    try:
        universe = mda.Universe(topology, trajectory) if trajectory else mda.Universe(topology)
    except Exception as error:
        raise ToolError(
            f"mdanalysis_analyze: cannot load {trajectory or topology}: {error}"
        ) from None
    atoms = universe.select_atoms(selection)
    if len(atoms) == 0:
        raise ToolError(f"mdanalysis_analyze: {selection!r} selects no atoms")
    ref = (
        mda.Universe(reference)
        if reference
        else mda.Universe(topology, trajectory)
        if trajectory
        else mda.Universe(topology)
    )
    ref_atoms = ref.select_atoms(selection)
    if len(ref_atoms) != len(atoms):
        raise ToolError(
            f"mdanalysis_analyze: the selection picks {len(atoms)} atoms in the trajectory "
            f"but {len(ref_atoms)} in the reference"
        )
    frames = range(0, len(universe.trajectory), max(1, stride))
    result: dict[str, Any] = {"n_frames": len(frames), "n_atoms": len(atoms)}
    rows: list[dict[str, Any]] = [{"frame": i} for i in frames]

    if "rmsd" in wanted:
        ctx.log(f"RMSD of {len(atoms)} atoms over {len(frames)} frames")
        analysis = rms.RMSD(atoms, ref_atoms, ref_frame=0)
        analysis.run(step=max(1, stride))
        values = analysis.results.rmsd[:, 2]
        for row, value in zip(rows, values, strict=False):
            row["rmsd"] = float(value)
        result.update(
            rmsd_mean=round(float(np.mean(values)), 4),
            rmsd_max=round(float(np.max(values)), 4),
            rmsd_final=round(float(values[-1]), 4),
        )
    if "rg" in wanted:
        ctx.log("radius of gyration")
        values = []
        for i in frames:
            universe.trajectory[i]
            values.append(float(atoms.radius_of_gyration()))
        for row, value in zip(rows, values, strict=False):
            row["rg"] = value
        result.update(
            rg_mean=round(float(np.mean(values)), 4),
            rg_min=round(float(np.min(values)), 4),
            rg_max=round(float(np.max(values)), 4),
        )
    if "rmsf" in wanted:
        ctx.log("RMSF after aligning to the average structure")
        aligner = align.AlignTraj(universe, ref, select=selection, in_memory=True)
        aligner.run(step=max(1, stride))
        rmsf = rms.RMSF(atoms).run(step=max(1, stride))
        values = rmsf.results.rmsf
        rmsf_path = ctx.workdir / "rmsf.csv"
        with rmsf_path.open("w", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["index", "resid", "resname", "name", "rmsf"])
            for atom, value in zip(atoms, values, strict=False):
                writer.writerow([atom.index, atom.resid, atom.resname, atom.name, f"{value:.4f}"])
        result.update(
            rmsf_mean=round(float(np.mean(values)), 4),
            rmsf_max=round(float(np.max(values)), 4),
            rmsf_csv=str(rmsf_path),
        )
    timeseries = ctx.workdir / "timeseries.csv"
    fields = ["frame"] + [m for m in ("rmsd", "rg") if m in wanted]
    with timeseries.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {k: (f"{v:.4f}" if isinstance(v, float) else v) for k, v in row.items()}
            )
    result["timeseries"] = str(timeseries)
    return result
