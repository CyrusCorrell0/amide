import json
import subprocess
import sys
from pathlib import Path

import pytest

from amide.harness.errors import HarnessError, ProtocolError, ToolError
from amide.harness.registry import Registry, builtin_tools, load_directory, manifest_tool
from amide.harness.tool import ToolContext

BUILTIN = {
    "rcsb_fetch",
    "uniprot_fetch",
    "pubchem_fetch",
    "alphafold_fetch",
    "pdbfixer_prepare",
    "rdkit_ligand",
    "openmm_minimize",
    "openmm_simulate",
    "lammps_run",
    "gromacs_run",
    "westpa_run",
    "mdanalysis_analyze",
    "python",
    "shell",
}


def test_builtin_tools_are_all_present():
    names = {spec.name for spec in builtin_tools()}
    assert names == BUILTIN
    for spec in builtin_tools():
        assert spec.description
        assert spec.source == "builtin"


def test_builtin_tools_import_nothing_heavy():
    code = (
        "import sys; from amide.harness.registry import builtin_tools; builtin_tools(); "
        "heavy = [m for m in ('openmm', 'MDAnalysis', 'rdkit', 'numpy', 'pdbfixer') if m in sys.modules]; "
        "print(heavy); sys.exit(1 if heavy else 0)"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stdout


def test_load_local_python_and_manifest_tools(tmp_path):
    (tmp_path / "hello.py").write_text(
        "from amide.harness.tool import Param, tool\n"
        "@tool(name='hello', description='hi', outputs=[Param('greeting')])\n"
        "def hello(ctx):\n    return {'greeting': 'hi'}\n"
    )
    (tmp_path / "echo.yaml").write_text(
        "name: echo_tool\ndescription: echo\ncommand: [echo, '{{ text }}']\n"
        "inputs: [{name: text, required: true}]\n"
    )
    (tmp_path / "_private.py").write_text("raise RuntimeError('should not load')\n")
    specs = {spec.name: spec for spec in load_directory(tmp_path)}
    assert set(specs) == {"hello", "echo_tool"}
    assert specs["hello"].source.endswith("hello.py")
    assert specs["echo_tool"].runtime == "command"
    assert specs["echo_tool"].requires.commands == ("echo",)


def test_load_registry_layers_local_over_builtin(tmp_path):
    local = tmp_path / ".amide" / "tools"
    local.mkdir(parents=True)
    (local / "override.py").write_text(
        "from amide.harness.tool import tool\n"
        "@tool(name='shell', description='mine')\n"
        "def shell(ctx):\n    return {}\n"
    )
    registry = Registry.load(cwd=tmp_path)
    assert registry.get("shell").description == "mine"
    assert "rcsb_fetch" in registry
    assert len(registry) == len(BUILTIN)


def test_bad_local_tool_file_is_reported(tmp_path):
    (tmp_path / "broken.py").write_text("this is not python\n")
    with pytest.raises(HarnessError, match="broken.py"):
        load_directory(tmp_path)
    (tmp_path / "broken.py").unlink()
    (tmp_path / "broken.yaml").write_text("name: x\ncommand: not-a-list\n")
    with pytest.raises(HarnessError, match="command must be a list"):
        load_directory(tmp_path)


def test_get_unknown_tool_points_at_list():
    with pytest.raises(ProtocolError, match="amide tools list"):
        Registry().get("nope")


def test_missing_reports_python_modules_and_commands(stub_registry):
    assert Registry.missing(stub_registry.get("add")) == []
    assert Registry.missing(stub_registry.get("needs_unicorn")) == [
        "python module unicorn_module_xyz"
    ]
    gmx = manifest_tool({"name": "g", "command": ["definitely_not_a_command_xyz"]})
    assert Registry.missing(gmx) == ["command definitely_not_a_command_xyz"]


def test_command_tool_runs_and_fills_outputs(tmp_path):
    spec = manifest_tool(
        {
            "name": "writer",
            "description": "write a file with the shell",
            "command": [sys.executable, "-c", "open('{{ name }}', 'w').write('{{ text }}')"],
            "inputs": [{"name": "text", "required": True}, {"name": "name", "default": "o.txt"}],
            "outputs": [{"name": "path", "type": "path", "value": "{{ workdir }}/{{ name }}"}],
        }
    )
    ctx = ToolContext(workdir=tmp_path, run_dir=tmp_path)
    outputs = spec.run(ctx, spec.validate_inputs({"text": "hello"}))
    assert outputs["returncode"] == 0
    assert Path(outputs["path"]).read_text() == "hello"


def test_command_tool_nonzero_exit_is_a_tool_error(tmp_path):
    spec = manifest_tool(
        {
            "name": "fail",
            "command": [
                sys.executable,
                "-c",
                "import sys; print('bad', file=sys.stderr); sys.exit(3)",
            ],
        }
    )
    with pytest.raises(ToolError, match="exited 3:\nbad"):
        spec.run(ToolContext(workdir=tmp_path, run_dir=tmp_path), {})


def test_container_tool_builds_docker_argv(tmp_path, monkeypatch):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="ok", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr("amide.harness.registry._container_engine", lambda: "docker")
    run_dir = tmp_path / "run"
    workdir = run_dir / "steps" / "s"
    workdir.mkdir(parents=True)
    spec = manifest_tool(
        {
            "name": "boxed",
            "runtime": "container",
            "image": "example/img:1",
            "command": ["tool", "--in", "{{ structure }}", "--out", "{{ workdir }}/out.gro"],
            "inputs": [{"name": "structure", "type": "path", "required": True}],
            "outputs": [{"name": "out", "type": "path", "value": "{{ workdir }}/out.gro"}],
        }
    )
    structure = run_dir / "steps" / "prev" / "in.pdb"
    outputs = spec.run(ToolContext(workdir=workdir, run_dir=run_dir), {"structure": str(structure)})
    argv = calls[0]
    assert argv[:3] == ["docker", "run", "--rm"]
    assert f"{workdir}:/work" in argv and f"{run_dir}:/run" in argv
    assert argv[-5:] == ["tool", "--in", "/run/steps/prev/in.pdb", "--out", "/work/out.gro"]
    assert outputs["out"] == f"{workdir}/out.gro"


def test_container_tool_needs_an_image():
    with pytest.raises(ValueError, match="needs an image"):
        manifest_tool({"name": "x", "runtime": "container", "command": ["a"]})


def test_manifest_to_dict_is_json(tmp_path):
    spec = manifest_tool({"name": "j", "command": ["echo"], "inputs": ["plain"]})
    json.dumps(spec.to_dict())
