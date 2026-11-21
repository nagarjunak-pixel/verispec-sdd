"""Tests for runtime trace verifier (C++20 fast-path + semantic guards) and test synthesizer."""

from __future__ import annotations

import unittest

import pytest

from verispec.compiler import compile_spec_markdown
from verispec.demo import GOVERNED_ERP_ORDER_SPEC
from verispec.synth import synthesize_suite
from verispec.types import TraceStep
from verispec.verifier import verify_trace


def test_unguarded_trace_cpp20_fast_path() -> None:
    md = """# Spec: UnguardedDoor
- **Initial**: `LOCKED`
- **Terminal**: `ARCHIVED`
## Transitions
| From | Event | To |
| --- | --- | --- |
| LOCKED | unlock | OPEN |
| OPEN | lock | LOCKED |
| LOCKED | decommission | ARCHIVED |
"""
    spec = compile_spec_markdown(md)
    ok_rep = verify_trace(
        spec,
        [TraceStep(event="unlock"), TraceStep(event="lock"), TraceStep(event="decommission")],
        require_terminal=True,
    )
    assert ok_rep.valid is True
    assert ok_rep.visited_states == ["LOCKED", "OPEN", "LOCKED", "ARCHIVED"]

    bad_evt_rep = verify_trace(spec, [TraceStep(event="decommission"), TraceStep(event="unlock")])
    assert bad_evt_rep.valid is False
    assert bad_evt_rep.failed_step == 1

    non_term_rep = verify_trace(spec, [TraceStep(event="unlock")], require_terminal=True)
    assert non_term_rep.valid is False
    assert non_term_rep.failed_step == 1


def test_guarded_trace_postconditions_and_invariants() -> None:
    spec = compile_spec_markdown(GOVERNED_ERP_ORDER_SPEC)

    # Missing transition from state
    r_no_tr = verify_trace(spec, [TraceStep(event="finalize_settlement", context={"amount": 10})])
    assert r_no_tr.valid is False
    assert "No transition" in (r_no_tr.failure_reason or "")

    # Guard error (missing context variable) & guard false
    r_guard_err = verify_trace(spec, [TraceStep(event="authorize_payment", context={"amount": 10})])
    assert r_guard_err.valid is False
    assert "Guard rejected" in (r_guard_err.failure_reason or "")

    # Postcondition false (`paid == False`)
    r_post_false = verify_trace(
        spec,
        [
            TraceStep(
                event="authorize_payment", context={"amount": 50, "fraud_score": 10, "paid": False}
            )
        ],
    )
    assert r_post_false.valid is False
    assert "Postcondition violated" in (r_post_false.failure_reason or "")

    # Postcondition missing variable error
    r_post_err = verify_trace(
        spec,
        [TraceStep(event="authorize_payment", context={"amount": 50, "fraud_score": 10})],
    )
    assert r_post_err.valid is False
    assert "Postcondition error" in (r_post_err.failure_reason or "")

    # Expected state mismatch
    r_exp = verify_trace(
        spec,
        [
            TraceStep(
                event="authorize_payment",
                context={"amount": 50, "fraud_score": 10, "paid": True},
                expected_state="COMPLETED",
            )
        ],
    )
    assert r_exp.valid is False
    assert "Expected state" in (r_exp.failure_reason or "")

    # Invariant violation: cancel_order unguarded, but global invariant `amount >= 0` fails when amount == -5
    r_inv_fail = verify_trace(
        spec,
        [TraceStep(event="cancel_order", context={"amount": -5})],
    )
    assert r_inv_fail.valid is False
    assert "Invariant violated" in (r_inv_fail.failure_reason or "")

    # State invariant violation (`[invariant: amount >= 0]` or state-specific invariant)
    state_inv_md = """# Spec: StateInvCheck
- **Initial**: `S0`
- **Terminal**: `S1`
## States
- `S0`: start
- `S1`: end [invariant: balance >= 100]
## Transitions
| From | Event | To | Guard |
| --- | --- | --- | --- |
| S0 | finish | S1 | ok == True |
"""
    s_spec = compile_spec_markdown(state_inv_md)
    r_sinv_fail = verify_trace(
        s_spec, [TraceStep(event="finish", context={"ok": True, "balance": 20})]
    )
    assert r_sinv_fail.valid is False
    assert r_sinv_fail.violated_expression == "balance >= 100"

    r_sinv_err = verify_trace(s_spec, [TraceStep(event="finish", context={"ok": True})])
    assert r_sinv_err.valid is False
    assert "Invariant evaluation error" in (r_sinv_err.failure_reason or "")

    # Non-terminal end on guarded spec
    r_nonterm = verify_trace(
        spec,
        [
            TraceStep(
                event="authorize_payment", context={"amount": 50, "fraud_score": 10, "paid": True}
            )
        ],
        require_terminal=True,
    )
    assert r_nonterm.valid is False
    assert "non-terminal" in (r_nonterm.failure_reason or "")


def test_synthesized_python_and_typescript_suites_execute() -> None:
    spec = compile_spec_markdown(GOVERNED_ERP_ORDER_SPEC)
    py_suite = synthesize_suite(spec, target="python")
    assert py_suite.filename == "test_governedenterpriseorderflow_contract.py"
    assert py_suite.num_rules == len(spec.events)

    # Actually compile and execute the synthesized Python Hypothesis state machine!
    ns: dict[str, object] = {}
    exec(compile(py_suite.code, py_suite.filename, "exec"), ns)
    fn = ns["test_static_specification_soundness"]
    assert callable(fn)
    fn()
    test_case_cls = ns["TestSynthesizedContract"]
    assert isinstance(test_case_cls, type) and issubclass(test_case_cls, unittest.TestCase)
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(test_case_cls)
    result = unittest.TextTestRunner().run(suite)
    assert result.wasSuccessful()

    ts_suite = synthesize_suite(spec, target="typescript")
    assert ts_suite.filename.endswith(".contract.test.ts")
    assert "export function stepContract" in ts_suite.code
    assert "&&" in ts_suite.code

    # Also test unguarded spec synthesis fallback context strategy
    plain_spec = compile_spec_markdown(
        "# Spec: 123Plain\n## Transitions\n| From | Event | To |\n|---|---|---|\n| A | go | B |\n"
    )
    plain_py = synthesize_suite(plain_spec, target="python")
    assert "step_nonce" in plain_py.code
    plain_ts = synthesize_suite(plain_spec, target="typescript")
    assert "[key: string]: unknown" in plain_ts.code

    with pytest.raises(ValueError, match="Unsupported synthesis target"):
        synthesize_suite(spec, target="rust")  # type: ignore[arg-type]
