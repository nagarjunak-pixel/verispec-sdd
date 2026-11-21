"""SPEC.md compiler and zero-eval safe expression evaluator."""

from __future__ import annotations

from verispec.compiler.expr import SpecExpressionError, evaluate_predicate, validate_expression
from verispec.compiler.parser import SpecCompileError, compile_spec_markdown

__all__ = [
    "SpecCompileError",
    "SpecExpressionError",
    "compile_spec_markdown",
    "evaluate_predicate",
    "validate_expression",
]
