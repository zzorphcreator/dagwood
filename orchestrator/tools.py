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
        return str(eval(compile(tree, "<calc>", "eval"), {"__builtins__": {}}, _MATH_NAMES))  # noqa: S307
    except Exception as e:
        return f"error: {e}"


REGISTRY: dict[str, tuple[Any, str]] = {
    "add_note": (add_note, "Save a short note to this step's execution log."),
    "word_count": (word_count, "Count words/chars in text."),
    "calc": (calc, "Evaluate a numeric expression (math subset)."),
}
