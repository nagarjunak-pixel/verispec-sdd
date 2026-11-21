"""Zero-`eval()` safe AST expression validator and evaluator for guards & invariants."""

from __future__ import annotations

import ast
import operator
from collections.abc import Callable
from typing import Any


class SpecExpressionError(ValueError):
    """Raised when a guard, postcondition, or invariant expression is invalid or unsafe."""


_SAFE_BINOPS: dict[type[ast.operator], Callable[[Any, Any], Any]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
}

_SAFE_UNARYOPS: dict[type[ast.unaryop], Callable[[Any], Any]] = {
    ast.Not: operator.not_,
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}

_SAFE_CMPOPS: dict[type[ast.cmpop], Callable[[Any, Any], bool]] = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
    ast.In: lambda a, b: a in b,
    ast.NotIn: lambda a, b: a not in b,
}

_SAFE_FUNCS: dict[str, Callable[..., Any]] = {
    "abs": abs,
    "len": len,
    "max": max,
    "min": min,
}


def validate_expression(expr_text: str) -> ast.Expression:
    """Parse and whitelist-validate a boolean/arithmetic contract expression without `eval()`."""
    cleaned = expr_text.strip()
    if not cleaned:
        raise SpecExpressionError("Empty contract expression")
    try:
        tree = ast.parse(cleaned, mode="eval")
    except SyntaxError as exc:
        raise SpecExpressionError(f"Syntax error in expression {cleaned!r}: {exc.msg}") from exc

    for node in ast.walk(tree):
        if isinstance(
            node,
            (
                ast.Expression,
                ast.BoolOp,
                ast.And,
                ast.Or,
                ast.Load,
                ast.Constant,
                ast.Tuple,
                ast.List,
                ast.BinOp,
                ast.UnaryOp,
                ast.Compare,
                ast.Subscript,
            ),
        ):
            continue
        if isinstance(node, ast.Name):
            if node.id.startswith("__"):
                raise SpecExpressionError(f"Dunder identifier forbidden: {node.id}")
            continue
        if isinstance(node, ast.operator):
            if type(node) not in _SAFE_BINOPS:
                raise SpecExpressionError(f"Unsupported binary operator: {type(node).__name__}")
            continue
        if isinstance(node, ast.unaryop):
            if type(node) not in _SAFE_UNARYOPS:
                raise SpecExpressionError(f"Unsupported unary operator: {type(node).__name__}")
            continue
        if isinstance(node, ast.cmpop):
            if type(node) not in _SAFE_CMPOPS:
                raise SpecExpressionError(f"Unsupported comparator: {type(node).__name__}")
            continue
        if isinstance(node, ast.Call):
            if (
                not isinstance(node.func, ast.Name)
                or node.func.id not in _SAFE_FUNCS
                or node.keywords
            ):
                raise SpecExpressionError(
                    "Only pure calls to len/min/max/abs without kwargs are allowed"
                )
            continue
        raise SpecExpressionError(
            f"Disallowed AST construct in contract expression: {type(node).__name__}"
        )

    return tree


def _eval_node(node: ast.AST, env: dict[str, Any]) -> Any:
    if isinstance(node, ast.Expression):
        return _eval_node(node.body, env)
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if node.id == "true":
            return True
        if node.id == "false":
            return False
        if node.id == "null":
            return None
        if node.id not in env:
            raise SpecExpressionError(f"Missing context variable {node.id!r}")
        return env[node.id]
    if isinstance(node, (ast.Tuple, ast.List)):
        return [_eval_node(elt, env) for elt in node.elts]
    if isinstance(node, ast.UnaryOp):
        return _SAFE_UNARYOPS[type(node.op)](_eval_node(node.operand, env))
    if isinstance(node, ast.BinOp):
        return _SAFE_BINOPS[type(node.op)](_eval_node(node.left, env), _eval_node(node.right, env))
    if isinstance(node, ast.BoolOp):
        if isinstance(node.op, ast.And):
            for val in node.values:
                if not bool(_eval_node(val, env)):
                    return False
            return True
        for val in node.values:
            if bool(_eval_node(val, env)):
                return True
        return False
    if isinstance(node, ast.Compare):
        left_val = _eval_node(node.left, env)
        for op, comparator in zip(node.ops, node.comparators, strict=True):
            right_val = _eval_node(comparator, env)
            if not _SAFE_CMPOPS[type(op)](left_val, right_val):
                return False
            left_val = right_val
        return True
    if isinstance(node, ast.Subscript):
        container = _eval_node(node.value, env)
        key = _eval_node(node.slice, env)
        return container[key]
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        fn = _SAFE_FUNCS[node.func.id]
        args = [_eval_node(arg, env) for arg in node.args]
        return fn(*args)
    raise SpecExpressionError(f"Unhandled AST node: {type(node).__name__}")


def evaluate_predicate(expr_text: str, context: dict[str, Any]) -> bool:
    """Safely evaluate a validated contract predicate to a boolean."""
    tree = validate_expression(expr_text)
    return bool(_eval_node(tree, context))
