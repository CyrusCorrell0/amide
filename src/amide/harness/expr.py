"""The ``{{ }}`` template language and the check expression language.

Expressions are a small subset of Python evaluated by walking the AST, so
nothing outside the whitelist below can run: no calls except the listed
builtins, no attribute access on anything but plain mappings, no imports,
no comprehensions.
"""

from __future__ import annotations

import ast
import operator
import re
from collections.abc import Mapping
from typing import Any

from amide.harness.errors import ExpressionError

_TEMPLATE = re.compile(r"\{\{\s*(.*?)\s*\}\}", re.DOTALL)

_BUILTINS: dict[str, Any] = {
    "len": len,
    "min": min,
    "max": max,
    "abs": abs,
    "round": round,
    "int": int,
    "float": float,
    "str": str,
    "bool": bool,
    "sum": sum,
    "sorted": sorted,
    "any": any,
    "all": all,
    "None": None,
    "True": True,
    "False": False,
}

_BINARY = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_COMPARE = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
    ast.In: lambda a, b: a in b,
    ast.NotIn: lambda a, b: a not in b,
    ast.Is: operator.is_,
    ast.IsNot: operator.is_not,
}
_UNARY = {ast.Not: operator.not_, ast.USub: operator.neg, ast.UAdd: operator.pos}


def evaluate(source: str, context: Mapping[str, Any]) -> Any:
    """Evaluate one expression against ``context`` (names to values)."""
    try:
        tree = ast.parse(source.strip(), mode="eval")
    except SyntaxError as error:
        raise ExpressionError(f"cannot parse {source!r}: {error.msg}") from None
    try:
        return _Evaluator(context, source).visit(tree.body)
    except ExpressionError:
        raise
    except Exception as error:
        raise ExpressionError(f"{source!r}: {error}") from None


def render(template: Any, context: Mapping[str, Any]) -> Any:
    """Resolve every ``{{ }}`` in a string, or recursively in a list or dict.

    A string that is exactly one template yields the expression's value with
    its type intact; anything else is joined as text with ``None`` rendered
    as the empty string.
    """
    if isinstance(template, str):
        matches = list(_TEMPLATE.finditer(template))
        if len(matches) == 1 and matches[0].group(0) == template.strip():
            return evaluate(matches[0].group(1), context)
        return _TEMPLATE.sub(lambda m: _text(evaluate(m.group(1), context)), template)
    if isinstance(template, list):
        return [render(item, context) for item in template]
    if isinstance(template, dict):
        return {key: render(value, context) for key, value in template.items()}
    return template


def references(template: Any) -> set[str]:
    """Every dotted name an expression or template refers to, for validation."""
    found: set[str] = set()
    for source in _sources(template):
        try:
            tree = ast.parse(source.strip(), mode="eval")
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            dotted = _dotted(node)
            if dotted:
                found.add(dotted)
    return found


def _sources(template: Any) -> list[str]:
    if isinstance(template, str):
        return _TEMPLATE.findall(template)
    if isinstance(template, list):
        return [source for item in template for source in _sources(item)]
    if isinstance(template, dict):
        return [source for item in template.values() for source in _sources(item)]
    return []


def _dotted(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr}" if base else None
    return None


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value).lower()
    return str(value)


class _Evaluator(ast.NodeVisitor):
    def __init__(self, context: Mapping[str, Any], source: str) -> None:
        self.context = context
        self.source = source

    def generic_visit(self, node: ast.AST) -> Any:
        raise ExpressionError(
            f"{self.source!r}: {type(node).__name__} is not allowed in expressions"
        )

    def visit_Constant(self, node: ast.Constant) -> Any:
        return node.value

    def visit_Name(self, node: ast.Name) -> Any:
        if node.id in self.context:
            return self.context[node.id]
        if node.id in _BUILTINS:
            return _BUILTINS[node.id]
        raise ExpressionError(f"{self.source!r}: unknown name {node.id!r}")

    def visit_Attribute(self, node: ast.Attribute) -> Any:
        base = self.visit(node.value)
        if not isinstance(base, Mapping):
            raise ExpressionError(
                f"{self.source!r}: cannot take .{node.attr} of a {type(base).__name__}"
            )
        if node.attr not in base:
            known = ", ".join(sorted(map(str, base))) or "nothing"
            raise ExpressionError(
                f"{self.source!r}: {_dotted(node) or node.attr} is not defined; available: {known}"
            )
        return base[node.attr]

    def visit_Subscript(self, node: ast.Subscript) -> Any:
        base = self.visit(node.value)
        if not isinstance(base, (Mapping, list, tuple, str)):
            raise ExpressionError(f"{self.source!r}: cannot index a {type(base).__name__}")
        return base[self.visit(node.slice)]

    def visit_Compare(self, node: ast.Compare) -> bool:
        left = self.visit(node.left)
        for op, comparator in zip(node.ops, node.comparators, strict=True):
            right = self.visit(comparator)
            if not _COMPARE[type(op)](left, right):
                return False
            left = right
        return True

    def visit_BoolOp(self, node: ast.BoolOp) -> Any:
        if isinstance(node.op, ast.And):
            result: Any = True
            for value in node.values:
                result = self.visit(value)
                if not result:
                    return result
            return result
        result = False
        for value in node.values:
            result = self.visit(value)
            if result:
                return result
        return result

    def visit_UnaryOp(self, node: ast.UnaryOp) -> Any:
        return _UNARY[type(node.op)](self.visit(node.operand))

    def visit_BinOp(self, node: ast.BinOp) -> Any:
        op = _BINARY.get(type(node.op))
        if op is None:
            raise ExpressionError(f"{self.source!r}: operator not allowed")
        return op(self.visit(node.left), self.visit(node.right))

    def visit_IfExp(self, node: ast.IfExp) -> Any:
        return self.visit(node.body) if self.visit(node.test) else self.visit(node.orelse)

    def visit_Call(self, node: ast.Call) -> Any:
        if not isinstance(node.func, ast.Name) or node.func.id not in _BUILTINS:
            raise ExpressionError(
                f"{self.source!r}: only {', '.join(k for k in _BUILTINS if k[0].islower())}"
                " may be called"
            )
        if node.keywords:
            raise ExpressionError(f"{self.source!r}: keyword arguments are not allowed")
        return _BUILTINS[node.func.id](*(self.visit(arg) for arg in node.args))

    def visit_List(self, node: ast.List) -> list[Any]:
        return [self.visit(item) for item in node.elts]

    def visit_Tuple(self, node: ast.Tuple) -> tuple[Any, ...]:
        return tuple(self.visit(item) for item in node.elts)

    def visit_Dict(self, node: ast.Dict) -> dict[Any, Any]:
        return {
            self.visit(key): self.visit(value)
            for key, value in zip(node.keys, node.values, strict=True)
            if key is not None
        }
