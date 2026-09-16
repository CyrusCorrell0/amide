from pathlib import Path

import pytest
import yaml

from amide.harness.errors import ProtocolError, ValidationError
from amide.harness.protocol import BUNDLED_DIR, available, find, from_dict, load, parse_overrides
from tests.conftest import STUB_PROTOCOL


def test_from_dict_parses_everything(stub_protocol):
    assert stub_protocol.name == "arith"
    assert [s.id for s in stub_protocol.steps] == ["first", "second", "annotate", "save"]
    assert stub_protocol.params["a"].type == "integer"
    assert stub_protocol.step("annotate").when == "params.note is not None"
    assert stub_protocol.checks[0].expr == "steps.second.sum < params.limit"
    assert stub_protocol.outputs["total"] == "{{ steps.second.sum }}"


def test_param_shorthand_infers_type():
    protocol = from_dict(
        {
            "name": "p",
            "params": {"n": 5, "f": 1.5, "b": True, "s": "x", "l": [1]},
            "steps": [{"id": "a", "tool": "t"}],
        }
    )
    assert {k: v.type for k, v in protocol.params.items()} == {
        "n": "integer",
        "f": "number",
        "b": "boolean",
        "s": "string",
        "l": "list",
    }


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ("just a string", "a protocol is a mapping"),
        ({"steps": [{"id": "a", "tool": "t"}]}, "name is required"),
        ({"name": "p"}, "steps must be a non-empty list"),
        ({"name": "p", "steps": ["x"]}, "step 0 must be a mapping"),
        ({"name": "p", "steps": [{"tool": "t"}]}, "step 0 needs an id"),
        ({"name": "p", "steps": [{"id": "a"}]}, "step 'a' needs a tool"),
        ({"name": "p", "steps": [{"id": "a", "tool": "t", "with": [1]}]}, "with must be a mapping"),
        (
            {"name": "p", "steps": [{"id": "a", "tool": "t", "when": 5}]},
            "when must be an expression",
        ),
        (
            {"name": "p", "steps": [{"id": "a", "tool": "t"}], "checks": [{"id": "c"}]},
            "check 0 needs an expr",
        ),
        (
            {"name": "p", "steps": [{"id": "a", "tool": "t"}], "outputs": [1]},
            "outputs must be a mapping",
        ),
        (
            {"name": "p", "steps": [{"id": "a", "tool": "t"}], "version": "one"},
            "version must be an integer",
        ),
        (
            {"name": "p", "params": {"x": {"type": "blob"}}, "steps": [{"id": "a", "tool": "t"}]},
            "unknown type",
        ),
    ],
)
def test_from_dict_rejects(data, message):
    with pytest.raises(ProtocolError, match=message):
        from_dict(data)


def test_problems_finds_bad_references_and_tools(stub_registry):
    protocol = from_dict(
        {
            "name": "bad",
            "params": {"a": 1},
            "steps": [
                {"id": "one", "tool": "add", "with": {"a": "{{ params.zzz }}", "extra": 1}},
                {"id": "two", "tool": "add", "with": {"a": "{{ steps.three.sum }}"}},
                {"id": "one", "tool": "nope"},
                {"id": "three", "tool": "add"},
            ],
            "checks": [{"id": "c", "expr": "steps.missing.sum > 1"}],
            "outputs": {"o": "{{ params.q }}"},
        }
    )
    problems = protocol.problems(stub_registry)
    assert "step 'one' is defined twice" in problems
    assert "step 'one' refers to params.zzz, which is not declared" in problems
    assert "step 'one': add does not take 'extra'" in problems
    assert "step 'two' refers to steps.three, which is not an earlier step" in problems
    assert "step 'one': unknown tool 'nope'" in problems
    assert "step 'three': add needs 'a'" in problems
    assert "check 'c' refers to steps.missing, which is not an earlier step" in problems
    assert "output 'o' refers to params.q, which is not declared" in problems


def test_problems_is_empty_for_a_good_protocol(stub_protocol, stub_registry):
    assert stub_protocol.problems(stub_registry) == []
    assert stub_protocol.problems() == []


def test_resolve_params(stub_protocol):
    assert stub_protocol.resolve_params()["a"] == 2
    assert stub_protocol.resolve_params({"a": "9"})["a"] == 9
    with pytest.raises(ValidationError, match="no parameter zzz"):
        stub_protocol.resolve_params({"zzz": 1})
    with pytest.raises(ValidationError, match="a must be integer"):
        stub_protocol.resolve_params({"a": "nine"})


def test_required_param():
    protocol = from_dict(
        {"name": "p", "params": {"x": {"required": True}}, "steps": [{"id": "a", "tool": "t"}]}
    )
    with pytest.raises(ValidationError, match="--set x="):
        protocol.resolve_params()


def test_parse_overrides_types_values():
    assert parse_overrides(["n=5", "f=1.5", "b=true", "s=hello", "e=", "l=[1, 2]"]) == {
        "n": 5,
        "f": 1.5,
        "b": True,
        "s": "hello",
        "e": "",
        "l": [1, 2],
    }
    with pytest.raises(ValidationError, match="name=value"):
        parse_overrides(["novalue"])


def test_load_keeps_text_and_source(tmp_path):
    path = tmp_path / "p.yaml"
    path.write_text(yaml.safe_dump(STUB_PROTOCOL))
    protocol = load(path)
    assert protocol.source == path
    assert protocol.text == path.read_text()


def test_load_reports_bad_yaml(tmp_path):
    path = tmp_path / "p.yaml"
    path.write_text("name: [unclosed\n")
    with pytest.raises(ProtocolError, match="p.yaml"):
        load(path)
    with pytest.raises(ProtocolError, match="cannot read"):
        load(tmp_path / "missing.yaml")


def test_find_by_path_name_and_bundle(project: Path):
    assert find(".amide/protocols/arith.yaml").name == "arith"
    assert find("arith").name == "arith"
    assert find("openmm-control").source == BUNDLED_DIR / "openmm-control.yaml"
    with pytest.raises(
        ProtocolError, match="no protocol named 'zzz'; available: arith, openmm-control"
    ):
        find("zzz")
    with pytest.raises(ProtocolError, match="does not exist"):
        find("missing.yaml")


def test_available_lists_bundle_and_local(project: Path):
    assert [p.name for p in available()] == ["arith", "openmm-control"]


def test_bundled_protocols_are_valid():
    from amide.harness.registry import Registry

    registry = Registry.load(builtin=True, cwd=Path("/nonexistent"))
    for path in BUNDLED_DIR.glob("*.yaml"):
        protocol = load(path)
        assert protocol.problems(registry) == [], path.name
