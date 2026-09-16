"""Builtin tools. Network tools are exercised against a fake fetch; simulation tools run for
real when their dependencies are installed and are skipped otherwise; executables that are
not on PyPI (lmp, gmx, w_run) are stubbed with shell scripts on PATH."""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest

from amide.harness.errors import ToolError
from amide.harness.registry import Registry
from amide.harness.tool import ToolContext
from amide.tools import _http

DATA = Path(__file__).parent / "data"


@pytest.fixture
def registry():
    return Registry.load(cwd=Path("/nonexistent"))


@pytest.fixture
def ctx(tmp_path):
    workdir = tmp_path / "run" / "steps" / "s"
    workdir.mkdir(parents=True)
    return ToolContext(workdir=workdir, run_dir=tmp_path / "run")


def _call(registry, name, ctx, /, **inputs):
    spec = registry.get(name)
    return spec.run(ctx, spec.validate_inputs(inputs))


@pytest.fixture
def fake_http(monkeypatch):
    responses: dict[str, bytes | Exception] = {}
    seen: list[str] = []

    def fetch(url, timeout=60):
        seen.append(url)
        if url not in responses:
            raise ToolError(f"{url} returned 404")
        body = responses[url]
        if isinstance(body, Exception):
            raise body
        return body

    monkeypatch.setattr(_http, "fetch", fetch)
    responses["seen"] = seen  # type: ignore[assignment]
    return responses


# --- fetch tools -------------------------------------------------------------


def test_rcsb_fetch(registry, ctx, fake_http):
    fake_http["https://files.rcsb.org/download/1AKI.pdb"] = b"ATOM      1  N   GLY\n"
    out = _call(registry, "rcsb_fetch", ctx, id="1aki")
    assert out["id"] == "1AKI" and out["format"] == "pdb" and out["bytes"] == 21
    assert Path(out["path"]).read_bytes().startswith(b"ATOM")
    with pytest.raises(ToolError, match="not a four-character"):
        _call(registry, "rcsb_fetch", ctx, id="12345")
    with pytest.raises(ToolError, match="returned 404"):
        _call(registry, "rcsb_fetch", ctx, id="9ZZZ", format="cif")


def test_uniprot_fetch_parses_fasta(registry, ctx, fake_http):
    fake_http["https://rest.uniprot.org/uniprotkb/P69905.fasta"] = (
        b">sp|P69905|HBA_HUMAN Hemoglobin\nMVLSPAD\nKTNVKAAW\n"
    )
    out = _call(registry, "uniprot_fetch", ctx, accession="p69905")
    assert out["sequence"] == "MVLSPADKTNVKAAW" and out["length"] == 15
    assert out["header"].startswith("sp|P69905")
    fake_http["https://rest.uniprot.org/uniprotkb/P69905.json"] = b"{}"
    assert "sequence" not in _call(
        registry, "uniprot_fetch", ctx, accession="P69905", format="json"
    )
    fake_http["https://rest.uniprot.org/uniprotkb/BAD.fasta"] = b"<html>"
    with pytest.raises(ToolError, match="did not return FASTA"):
        _call(registry, "uniprot_fetch", ctx, accession="BAD")


def test_pubchem_fetch(registry, ctx, fake_http):
    props = {"PropertyTable": {"Properties": [{"CID": 2244, "MolecularWeight": "180.16"}]}}
    url = "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/aspirin/property/MolecularWeight/JSON"
    fake_http[url] = json.dumps(props).encode()
    fake_http["https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/2244/SDF?record_type=2d"] = (
        b"sdf"
    )
    out = _call(
        registry,
        "pubchem_fetch",
        ctx,
        query="aspirin",
        properties=["MolecularWeight"],
        download_sdf=True,
    )
    assert (
        out["cid"] == 2244
        and out["properties"] == {"MolecularWeight": "180.16"}
        and out["hits"] == 1
    )
    assert out["sdf_dimension"] == "2d" and Path(out["sdf"]).read_bytes() == b"sdf"
    empty = "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/nothing/property/MolecularWeight/JSON"
    fake_http[empty] = json.dumps({"PropertyTable": {"Properties": []}}).encode()
    with pytest.raises(ToolError, match="no compound matches"):
        _call(registry, "pubchem_fetch", ctx, query="nothing", properties=["MolecularWeight"])


def test_pubchem_fetch_quotes_the_query(registry, ctx, fake_http):
    url = "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/smiles/CC%28%3DO%29O/property/XLogP/JSON"
    fake_http[url] = json.dumps({"PropertyTable": {"Properties": [{"CID": 176}]}}).encode()
    assert (
        _call(
            registry,
            "pubchem_fetch",
            ctx,
            query="CC(=O)O",
            namespace="smiles",
            properties=["XLogP"],
        )["cid"]
        == 176
    )


def test_alphafold_fetch(registry, ctx, fake_http):
    entry = [
        {
            "entryId": "AF-P69905-F1",
            "latestVersion": 4,
            "pdbUrl": "https://x/af.pdb",
            "uniprotSequence": "MV",
            "organismScientificName": "Homo sapiens",
        }
    ]
    fake_http["https://alphafold.ebi.ac.uk/api/prediction/P69905"] = json.dumps(entry).encode()
    fake_http["https://x/af.pdb"] = b"ATOM"
    out = _call(registry, "alphafold_fetch", ctx, accession="P69905")
    assert (
        out["entry_id"] == "AF-P69905-F1"
        and out["model_version"] == 4
        and out["sequence_length"] == 2
    )
    assert Path(out["path"]).name == "AF-P69905.pdb"
    fake_http["https://alphafold.ebi.ac.uk/api/prediction/NOPE"] = b"[]"
    with pytest.raises(ToolError, match="no prediction"):
        _call(registry, "alphafold_fetch", ctx, accession="NOPE")


# --- python and shell --------------------------------------------------------


def test_python_tool_merges_outputs_json(registry, ctx):
    code = (
        "import json, os\n"
        "args = json.loads(os.environ['AMIDE_ARGS'])\n"
        "print('hi', args['n'])\n"
        "json.dump({'double': args['n'] * 2}, open('outputs.json', 'w'))\n"
    )
    out = _call(registry, "python", ctx, code=code, args={"n": 21})
    assert out["stdout"] == "hi 21\n" and out["double"] == 42 and out["returncode"] == 0
    assert json.loads((ctx.workdir / "args.json").read_text()) == {"n": 21}


def test_python_tool_failure_shows_traceback_tail(registry, ctx):
    with pytest.raises(ToolError, match="ZeroDivisionError"):
        _call(registry, "python", ctx, code="1/0\n")


def test_python_tool_timeout(registry, ctx):
    with pytest.raises(ToolError, match="timed out"):
        _call(registry, "python", ctx, code="import time; time.sleep(5)\n", timeout=1)


@pytest.mark.skipif(os.name == "nt", reason="POSIX shell")
def test_shell_tool(registry, ctx):
    out = _call(registry, "shell", ctx, command="echo hello > f.txt && cat f.txt && pwd")
    assert out["stdout"].splitlines() == ["hello", str(ctx.workdir)]
    with pytest.raises(ToolError, match="exited 7"):
        _call(registry, "shell", ctx, command="exit 7")


# --- executables stubbed on PATH ---------------------------------------------


def _stub_executable(directory: Path, name: str, body: str) -> None:
    path = directory / name
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


@pytest.fixture
def fake_bin(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return bin_dir


@pytest.mark.skipif(os.name == "nt", reason="shell-script stubs")
def test_lammps_run(registry, ctx, fake_bin, tmp_path):
    _stub_executable(fake_bin, "lmp", 'echo "args: $*" > log.lammps\necho ran\n')
    script = tmp_path / "in.lj"
    script.write_text("units lj\n")
    data = tmp_path / "data.lj"
    data.write_text("data\n")
    out = _call(
        registry,
        "lammps_run",
        ctx,
        input=str(script),
        data=str(data),
        variables={"temp": 1.5},
        extra_args=["-echo", "none"],
    )
    assert out["returncode"] == 0 and out["stdout"] == "ran\n"
    assert (
        Path(out["log"]).read_text() == "args: -in in.lj -log log.lammps -var temp 1.5 -echo none\n"
    )
    assert {"in.lj", "data.lj", "log.lammps"} <= set(out["files"])
    with pytest.raises(ToolError, match="does not exist"):
        _call(registry, "lammps_run", ctx, input=str(tmp_path / "nope.in"))


@pytest.mark.skipif(os.name == "nt", reason="shell-script stubs")
def test_gromacs_run(registry, ctx, fake_bin):
    _stub_executable(
        fake_bin, "gmx", 'read choice\necho "$1 $2 $3 choice=$choice"\ntouch conf.gro\n'
    )
    out = _call(
        registry, "gromacs_run", ctx, subcommand="pdb2gmx", args=["-f", "in.pdb"], stdin="6\n"
    )
    assert out["stdout"] == "pdb2gmx -f in.pdb choice=6\n"
    assert "conf.gro" in out["files"]


@pytest.mark.skipif(os.name == "nt", reason="shell-script stubs")
def test_westpa_run(registry, ctx, fake_bin, tmp_path):
    _stub_executable(fake_bin, "w_init", 'echo "init $*" >> calls.txt\n')
    _stub_executable(
        fake_bin, "w_run", 'echo "run $* root=$WEST_SIM_ROOT" >> calls.txt\ntouch west.h5\n'
    )
    project = tmp_path / "proj"
    project.mkdir()
    (project / "west.cfg").write_text("west: {}\n")
    out = _call(registry, "westpa_run", ctx, directory=str(project), max_iterations=3, n_workers=2)
    copied = Path(out["project"])
    assert copied == ctx.workdir / "project" and (copied / "west.cfg").exists()
    calls = (copied / "calls.txt").read_text().splitlines()
    assert calls[0] == "init --rcfile west.cfg"
    assert (
        calls[1]
        == f"run --rcfile west.cfg --work-manager processes --n-workers 2 --max-iterations 3 root={copied}"
    )
    assert out["h5"] == str(copied / "west.h5")
    with pytest.raises(ToolError, match="has no west.cfg"):
        _call(registry, "westpa_run", ctx, directory=str(tmp_path))


# --- real simulation stack, when installed ------------------------------------


def test_rdkit_ligand(registry, ctx):
    pytest.importorskip("rdkit")
    out = _call(registry, "rdkit_ligand", ctx, smiles="CC(=O)Oc1ccccc1C(=O)O", name="aspirin")
    assert out["formula"] == "C9H8O4"
    assert abs(out["descriptors"]["molecular_weight"] - 180.16) < 0.01
    assert out["descriptors"]["hbd"] == 1 and out["lipinski_violations"] == 0
    sdf = Path(out["sdf"]).read_text()
    assert sdf.startswith("aspirin") and "V2000" in sdf
    with pytest.raises(ToolError, match="not valid SMILES"):
        _call(registry, "rdkit_ligand", ctx, smiles="C(C")


def test_pdbfixer_prepare_both_paths(registry, ctx, monkeypatch):
    pytest.importorskip("openmm")
    out = _call(registry, "pdbfixer_prepare", ctx, structure=str(DATA / "sample.pdb"))
    assert out["method"] in ("pdbfixer", "modeller")
    assert out["residues"] == 10 and out["atoms"] > 100 and out["solvated"] is False
    assert Path(out["path"]).name == "prepared.pdb"

    monkeypatch.setitem(sys.modules, "pdbfixer", None)  # simulate it being absent
    out = _call(
        registry,
        "pdbfixer_prepare",
        ctx,
        structure=str(DATA / "sample.pdb"),
        solvate=True,
        padding_nm=0.5,
    )
    assert out["method"] == "modeller" and out["solvated"] is True
    assert out["atoms"] > 1000  # water was added


def test_openmm_minimize_and_simulate_and_analyze(registry, ctx):
    pytest.importorskip("openmm")
    pytest.importorskip("MDAnalysis")
    prepared = _call(registry, "pdbfixer_prepare", ctx, structure=str(DATA / "sample.pdb"))["path"]
    minimized = _call(
        registry, "openmm_minimize", ctx, structure=prepared, platform="CPU", max_iterations=50
    )
    assert minimized["energy_after_kj_mol"] < minimized["energy_before_kj_mol"]
    assert minimized["nonbonded_method"] == "NoCutoff" and minimized["platform"] == "CPU"

    sim = _call(
        registry,
        "openmm_simulate",
        ctx,
        structure=minimized["path"],
        steps=50,
        report_interval=10,
        minimize=False,
        platform="CPU",
        seed=7,
    )
    assert sim["frames"] == 5 and sim["time_ps"] == 0.1 and sim["ensemble"] == "NVT"
    assert Path(sim["trajectory"]).stat().st_size > 0 and Path(sim["final"]).exists()
    assert sim["temperature_mean_k"] > 0

    with pytest.raises(ToolError, match="needs a periodic box"):
        _call(
            registry, "openmm_simulate", ctx, structure=minimized["path"], steps=1, pressure_bar=1.0
        )
    with pytest.raises(ToolError, match="no OpenMM platform"):
        _call(registry, "openmm_minimize", ctx, structure=prepared, platform="Abacus")

    analysis = _call(
        registry,
        "mdanalysis_analyze",
        ctx,
        topology=sim["topology"],
        trajectory=sim["trajectory"],
        metrics=["rmsd", "rg", "rmsf"],
    )
    assert analysis["n_frames"] == 5 and analysis["n_atoms"] == 10
    assert analysis["rmsd_mean"] >= 0 and analysis["rg_mean"] > 0 and analysis["rmsf_mean"] >= 0
    rows = Path(analysis["timeseries"]).read_text().splitlines()
    assert rows[0] == "frame,rmsd,rg" and len(rows) == 6
    assert Path(analysis["rmsf_csv"]).read_text().splitlines()[0] == "index,resid,resname,name,rmsf"
    with pytest.raises(ToolError, match="selects no atoms"):
        _call(
            registry, "mdanalysis_analyze", ctx, topology=sim["topology"], selection="resname XYZ"
        )
    with pytest.raises(ToolError, match="unknown metrics"):
        _call(registry, "mdanalysis_analyze", ctx, topology=sim["topology"], metrics=["dihedral"])


def test_bundled_control_protocol_end_to_end(tmp_path):
    """Milestone 2: run the control, change something, re-run, compare, export."""
    pytest.importorskip("openmm")
    pytest.importorskip("MDAnalysis")
    from amide.harness.protocol import find
    from amide.harness.runner import RunOptions, execute
    from amide.harness.runs import RunStore

    registry = Registry.load(cwd=Path("/nonexistent"))
    protocol = find("openmm-control")
    store = RunStore(tmp_path / "runs")
    base = {
        "structure": str(DATA / "sample.pdb"),
        "solvate": False,
        "steps": 100,
        "platform": "CPU",
    }

    control = execute(
        store.create(protocol, protocol.resolve_params(base)),
        protocol,
        registry,
        RunOptions(yes=True),
    )
    assert control.status == "passed", control.error
    assert control.steps["fetch"].status == "skipped"
    assert all(
        control.steps[s].status == "done" for s in ("prepare", "minimize", "simulate", "analyze")
    )
    assert control.outputs["rmsd_mean"] < 3.0

    variant = execute(
        store.create(
            protocol, protocol.resolve_params({**base, "temperature": 400, "rmsd_limit": 0.0})
        ),
        protocol,
        registry,
        RunOptions(yes=True),
    )
    assert variant.status == "failed"
    assert variant.error == "checks failed: stable"
    assert variant.params["temperature"] == 400.0

    archive = store.export(control.id, tmp_path / "control.tar.gz")
    assert archive.exists()
