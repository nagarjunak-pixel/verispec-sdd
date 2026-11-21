"""Unit tests for SPEC.md compiler, safe AST expression engine, and static FSM prover."""

from __future__ import annotations

import pytest

from verispec.compiler import (
    SpecCompileError,
    SpecExpressionError,
    compile_spec_markdown,
    evaluate_predicate,
    validate_expression,
)
from verispec.demo import BUGGY_ERP_ORDER_SPEC, GOVERNED_ERP_ORDER_SPEC
from verispec.verifier import verify_spec


def test_safe_expression_validator_and_evaluator() -> None:
    ctx = {
        "amount": 150,
        "fee": 10,
        "tags": ["vip", "gcc"],
        "meta": {"tier": 2},
        "ok": True,
    }
    assert evaluate_predicate("amount + fee * 2 >= 170 and 'vip' in tags", ctx) is True
    assert (
        evaluate_predicate("amount - fee == 140 and amount // 10 == 15 and amount % 10 == 0", ctx)
        is True
    )
    assert evaluate_predicate("amount / 2 == 75.0 and -fee < 0 and +fee == 10", ctx) is True
    assert evaluate_predicate("not false and true and null == None", ctx) is True
    assert (
        evaluate_predicate("False or (meta['tier'] in [1, 2] and 'bad' not in tags)", ctx) is True
    )
    assert evaluate_predicate("False or False", ctx) is False
    assert evaluate_predicate("10 < amount < 50", ctx) is False
    assert evaluate_predicate("max(abs(-5), min(3, len(tags))) == 5", ctx) is True

    with pytest.raises(SpecExpressionError, match="Empty"):
        validate_expression("   ")
    with pytest.raises(SpecExpressionError, match="Syntax error"):
        validate_expression("amount >=")
    with pytest.raises(SpecExpressionError, match="Dunder"):
        validate_expression("__import__ == 1")
    with pytest.raises(SpecExpressionError, match="Unsupported binary"):
        validate_expression("amount << 2 == 4")
    with pytest.raises(SpecExpressionError, match="Unsupported unary"):
        validate_expression("~amount == 1")
    with pytest.raises(SpecExpressionError, match="Unsupported comparator"):
        validate_expression("amount is 1")
    with pytest.raises(SpecExpressionError, match="Only pure calls"):
        validate_expression("eval('1')")
    with pytest.raises(SpecExpressionError, match="Disallowed AST"):
        validate_expression("[x for x in tags]")
    with pytest.raises(SpecExpressionError, match="Missing context"):
        evaluate_predicate("unknown_var > 0", ctx)


def test_compiler_handles_defaults_and_rejects_malformed_specs() -> None:
    minimal_md = """# ImplicitSpec
## Other Section
Ignored prose.

## Transitions
| From | Event | To |
| --- | --- | --- |
| INIT | finish | DONE |
"""
    spec = compile_spec_markdown(minimal_md)
    assert spec.name == "ImplicitSpec"
    assert spec.initial_state == "INIT"
    assert spec.terminal_states == []
    assert [s.name for s in spec.states] == ["INIT", "DONE"]

    missing_init_in_states = """# AutoCreateStates
- **Initial**: START_HERE
- **Terminal**: END_HERE
## States
- MIDDLE: intermediate
## Transitions
| From | Event | To |
| --- | --- | --- |
| START_HERE | step | MIDDLE |
"""
    spec2 = compile_spec_markdown(missing_init_in_states)
    assert {s.name for s in spec2.states} == {"START_HERE", "MIDDLE", "END_HERE"}

    with pytest.raises(SpecCompileError, match="zero states"):
        compile_spec_markdown("# EmptySpec\nJust text.")

    with pytest.raises(SpecCompileError, match="at least From"):
        compile_spec_markdown("# BadRow\n## Transitions\n| A | B |\n")

    with pytest.raises(SpecCompileError, match="Incomplete transition"):
        compile_spec_markdown("# EmptyCell\n## Transitions\n| A |  | B |\n")

    with pytest.raises(SpecCompileError, match="unknown state"):
        compile_spec_markdown(
            "# BadInv\n## States\n- A: init\n## Invariants\n- inv1 [GHOST]: x > 0\n"
        )


def test_prover_catches_deadlock_livelock_orphan_unreachable_and_nondet() -> None:
    buggy = compile_spec_markdown(BUGGY_ERP_ORDER_SPEC)
    rep_cpp = verify_spec(buggy, use_native=True)
    rep_py = verify_spec(buggy, use_native=False)
    assert rep_cpp.passed is False
    assert rep_cpp.deadlock_states == ["ON_HOLD"]
    assert set(rep_cpp.livelock_states) == {"DISPUTED", "UNDER_REVIEW"}
    assert rep_cpp.unreachable_states == ["LEGACY_REFUNDED"]
    assert rep_cpp.deadlock_states == rep_py.deadlock_states
    assert rep_cpp.livelock_states == rep_py.livelock_states

    gov = compile_spec_markdown(GOVERNED_ERP_ORDER_SPEC)
    gov_rep = verify_spec(gov)
    assert gov_rep.passed is True
    assert gov_rep.shortest_witness_to_terminal[0] == "CREATED"
    assert gov_rep.shortest_witness_to_terminal[-1] in {"COMPLETED", "CANCELLED"}

    complex_defects_md = """# Spec: NondetAndUnreachable
- **Initial**: `S0`
- **Terminal**: `S0`
## States
- `S0`: initial and already terminal
- `S1`: target 1
- `S2`: target 2
- `GHOST`: unreachable non-terminal
## Transitions
| From | Event | To | Guard |
| --- | --- | --- | --- |
| S0 | go | S1 | - |
| S0 | go | S2 | x > 0 |
| GHOST | ping | S1 | - |
"""
    c_spec = compile_spec_markdown(complex_defects_md)
    c_rep = verify_spec(c_spec)
    assert c_rep.passed is False
    assert "GHOST" in c_rep.unreachable_states
    assert "S0:go" in c_rep.nondeterministic_pairs
    assert c_rep.shortest_witness_to_terminal == ["S0"]
    codes = {d.code for d in c_rep.diagnostics}
    assert "UNREACHABLE_STATE" in codes
    assert "UNREACHABLE_TRANSITION" in codes
    assert "NONDETERMINISTIC_TRANSITION" in codes

    no_path_to_term_md = """# Spec: DisconnectedTerminal
- **Initial**: `A`
- **Terminal**: `Z`
## Transitions
| From | Event | To |
| --- | --- | --- |
| A | loop | A |
"""
    np_rep = verify_spec(compile_spec_markdown(no_path_to_term_md))
    assert np_rep.shortest_witness_to_terminal == []
    assert "A" in np_rep.livelock_states
