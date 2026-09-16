import pytest

from amide.harness.errors import ValidationError
from amide.harness.tool import Param, Requirements, ToolContext, ToolSpec, tool


@pytest.mark.parametrize(
    ("type_", "given", "expected"),
    [
        ("string", 5, "5"),
        ("path", "/x/y", "/x/y"),
        ("integer", "7", 7),
        ("integer", 7.0, 7),
        ("number", "1.5", 1.5),
        ("number", 2, 2.0),
        ("boolean", "yes", True),
        ("boolean", "off", False),
        ("boolean", True, True),
        ("list", "a, b,c", ["a", "b", "c"]),
        ("list", ("x",), ["x"]),
        ("object", {"k": 1}, {"k": 1}),
    ],
)
def test_param_coerces(type_, given, expected):
    assert Param("p", type_).coerce(given) == expected


@pytest.mark.parametrize(
    ("type_", "given"),
    [
        ("integer", "seven"),
        ("integer", 7.5),
        ("integer", True),
        ("number", "x"),
        ("boolean", "maybe"),
        ("list", 3),
        ("object", "not a mapping"),
        ("string", ["no"]),
    ],
)
def test_param_rejects(type_, given):
    with pytest.raises(ValidationError):
        Param("p", type_).coerce(given)


def test_param_choices():
    param = Param("fmt", "string", choices=("pdb", "cif"))
    assert param.coerce("pdb") == "pdb"
    with pytest.raises(ValidationError, match="one of pdb, cif"):
        param.coerce("xyz")


def test_param_unknown_type():
    with pytest.raises(ValueError, match="unknown type"):
        Param("p", "blob")


def _spec():
    return ToolSpec(
        name="t",
        description="d",
        run=lambda ctx, args: args,
        inputs=(Param("a", "integer", required=True), Param("b", "string", default="x")),
    )


def test_validate_inputs_applies_defaults_and_coerces():
    assert _spec().validate_inputs({"a": "3"}) == {"a": 3, "b": "x"}


def test_validate_inputs_rejects_unknown_and_missing():
    with pytest.raises(ValidationError, match="does not take zzz"):
        _spec().validate_inputs({"a": 1, "zzz": 2})
    with pytest.raises(ValidationError, match="needs a"):
        _spec().validate_inputs({})


def test_spec_rejects_bad_cost_runtime_and_duplicates():
    with pytest.raises(ValueError, match="cost"):
        ToolSpec(name="t", description="", run=lambda c, a: {}, cost="free")
    with pytest.raises(ValueError, match="runtime"):
        ToolSpec(name="t", description="", run=lambda c, a: {}, runtime="wasm")
    with pytest.raises(ValueError, match="duplicate input"):
        ToolSpec(name="t", description="", run=lambda c, a: {}, inputs=(Param("a"), Param("a")))


def test_to_schema_is_json_schema_shaped():
    schema = _spec().to_schema()
    assert schema["name"] == "t"
    assert schema["input_schema"]["required"] == ["a"]
    assert schema["input_schema"]["properties"]["a"] == {"type": "integer"}
    assert schema["input_schema"]["properties"]["b"] == {"type": "string", "default": "x"}


def test_to_dict_round_trips_requirements():
    spec = ToolSpec(
        name="t",
        description="d",
        run=lambda c, a: {},
        requires=Requirements(python=("openmm",), commands=("gmx",), gpu=True, hint="h"),
        tags=("x",),
    )
    data = spec.to_dict()
    assert data["requires"] == {"python": ["openmm"], "commands": ["gmx"], "gpu": True, "hint": "h"}
    assert data["tags"] == ["x"]
    assert data["inputs"] == []


def test_decorator_attaches_spec_and_runs(tmp_path):
    @tool(name="double", description="x2", inputs=[Param("n", "integer", required=True)])
    def double(ctx, n):
        return {"n": n * 2}

    ctx = ToolContext(workdir=tmp_path, run_dir=tmp_path)
    assert double.spec.name == "double"
    assert double.spec.run(ctx, {"n": 4}) == {"n": 8}
    assert double(ctx, n=1) == {"n": 2}
