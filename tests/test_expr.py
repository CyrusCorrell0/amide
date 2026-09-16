import pytest

from amide.harness.errors import ExpressionError
from amide.harness.expr import evaluate, references, render

CONTEXT = {
    "params": {"pdb_id": "1AKI", "steps": 5000, "structure": None, "flag": True},
    "steps": {"fetch": {"path": "/tmp/1AKI.pdb", "bytes": 12}, "analyze": {"rmsd_mean": 1.5}},
    "run": {"id": "abc", "dir": "/runs/abc"},
    "env": {"HOME": "/home/x"},
}


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("params.steps", 5000),
        ("params.steps * 2 + 1", 10001),
        ("steps.analyze.rmsd_mean < 3.0", True),
        ("params.structure is None", True),
        ("params.structure or steps.fetch.path", "/tmp/1AKI.pdb"),
        ("not params.flag", False),
        ("1 < 2 < 3", True),
        ("'AKI' in params.pdb_id", True),
        ("len(params.pdb_id)", 4),
        ("max(1, params.steps)", 5000),
        ("round(steps.analyze.rmsd_mean)", 2),
        ("[params.steps, 1][1]", 1),
        ("{'a': 1}['a']", 1),
        ("'yes' if params.flag else 'no'", "yes"),
        ("-params.steps", -5000),
        ("env.HOME", "/home/x"),
        ("run.id", "abc"),
    ],
)
def test_evaluate(source, expected):
    assert evaluate(source, CONTEXT) == expected


@pytest.mark.parametrize(
    "source",
    [
        "__import__('os')",
        "params.__class__",
        "open('x')",
        "(lambda: 1)()",
        "[x for x in params]",
        "params.steps.real",  # attribute on a non-mapping
        "steps.nope.path",
        "unknown_name",
        "len(x=1)",
        "1 +",
        "params.steps << 1",
    ],
)
def test_evaluate_rejects(source):
    with pytest.raises(ExpressionError):
        evaluate(source, CONTEXT)


def test_missing_attribute_names_what_exists():
    with pytest.raises(
        ExpressionError, match="steps.fetch.size is not defined; available: bytes, path"
    ):
        evaluate("steps.fetch.size", CONTEXT)


def test_render_keeps_type_for_whole_template():
    assert render("{{ params.steps }}", CONTEXT) == 5000
    assert render("  {{ params.flag }} ", CONTEXT) is True
    assert render("{{ params.structure }}", CONTEXT) is None


def test_render_two_templates_joined_by_text():
    assert render("{{ run.dir }}/{{ params.pdb_id }}.pdb", CONTEXT) == "/runs/abc/1AKI.pdb"


def test_render_joins_text_otherwise():
    assert render("id={{ params.pdb_id }}, n={{ params.steps }}", CONTEXT) == "id=1AKI, n=5000"
    assert render("x={{ params.structure }}", CONTEXT) == "x="
    assert render("{{ params.flag }}!", CONTEXT) == "true!"


def test_render_recurses_into_lists_and_dicts():
    template = {"a": ["{{ params.steps }}", "plain"], "b": {"c": "{{ run.id }}"}, "d": 7}
    assert render(template, CONTEXT) == {"a": [5000, "plain"], "b": {"c": "abc"}, "d": 7}


def test_references():
    found = references({"x": "{{ params.a or steps.fetch.path }}", "y": ["{{ len(params.b) }}"]})
    assert found == {
        "params",
        "params.a",
        "steps",
        "steps.fetch",
        "steps.fetch.path",
        "params.b",
        "len",
    }
    assert references("no templates here") == set()
