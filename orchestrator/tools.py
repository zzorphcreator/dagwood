"""Tool registry. Workers receive a subset of these by name.

Add new tools here as plain functions with typed signatures, then register
them in REGISTRY. Tools run inside the worker's event loop.
"""

from __future__ import annotations

import ast
import math
from dataclasses import dataclass
from typing import Any

from pydantic_ai import RunContext


@dataclass
class NodeDeps:
    record: Any  # RunRecord (Any to avoid a circular import)
    node_id: str


def add_note(ctx: RunContext[NodeDeps], note: str) -> str:
    """Save a short note to this step's execution log."""
    ctx.deps.record.nodes[ctx.deps.node_id].logs.append(f"note: {note}")
    return "noted"


def word_count(ctx: RunContext[NodeDeps], text: str) -> str:
    """Count words and characters in a piece of text."""
    words = len(text.split())
    return f"{words} words, {len(text)} chars"


_MATH_FUNCS = {name: getattr(math, name) for name in ("sqrt", "log", "log10", "sin", "cos", "tan", "floor", "ceil", "fabs")}
_MATH_CONSTS = {"pi": math.pi, "e": math.e}
_MATH_NAMES = _MATH_FUNCS | _MATH_CONSTS
_ALLOWED_NODES = (
    ast.Expression,
    ast.BinOp,
    ast.UnaryOp,
    ast.Call,
    ast.Name,
    ast.Load,
    ast.Constant,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.Mod,
    ast.Pow,
    ast.USub,
    ast.UAdd,
)


_MAX_EXPONENT = 1000  # ints are unbounded in Python: 9**9**9 would never finish
_MAX_DIGITS = 2000


def _evaluate(node: ast.AST) -> float | int:
    """Evaluate an already-whitelisted expression tree, refusing results too large to be useful."""
    if isinstance(node, ast.Expression):
        return _evaluate(node.body)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise ValueError("only numbers are supported")
        return node.value
    if isinstance(node, ast.Name):
        return _MATH_CONSTS[node.id]
    if isinstance(node, ast.UnaryOp):
        value = _evaluate(node.operand)
        return -value if isinstance(node.op, ast.USub) else +value
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in _MATH_FUNCS or node.keywords:
            raise ValueError("unsupported function call")
        return _MATH_FUNCS[node.func.id](*(_evaluate(a) for a in node.args))
    if isinstance(node, ast.BinOp):
        left, right = _evaluate(node.left), _evaluate(node.right)
        if isinstance(node.op, ast.Pow):
            if isinstance(left, int) and isinstance(right, int) and abs(right) > _MAX_EXPONENT:
                raise ValueError(f"exponent too large (limit {_MAX_EXPONENT})")
            result = left**right
        elif isinstance(node.op, ast.Add):
            result = left + right
        elif isinstance(node.op, ast.Sub):
            result = left - right
        elif isinstance(node.op, ast.Mult):
            result = left * right
        elif isinstance(node.op, ast.Div):
            result = left / right
        else:
            result = left % right
        if isinstance(result, int) and abs(result) >= 10**_MAX_DIGITS:
            raise ValueError("result too large")
        return result
    raise ValueError("unsupported expression")


def calc(ctx: RunContext[NodeDeps], expression: str) -> str:
    """Evaluate a numeric expression, e.g. 'sqrt(16) + 2**3'. No imports or attributes."""
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError:
        return "error: invalid expression"
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODES):
            return "error: unsupported expression"
        if isinstance(node, ast.Name) and node.id not in _MATH_NAMES:
            return f"error: unknown name {node.id!r}"
    try:
        return str(_evaluate(tree))
    except Exception as e:
        return f"error: {e}"


REGISTRY: dict[str, tuple[Any, str]] = {
    "add_note": (add_note, "Save a short note to this step's execution log."),
    "word_count": (word_count, "Count words/chars in text."),
    "calc": (calc, "Evaluate a numeric expression (math subset)."),
}
