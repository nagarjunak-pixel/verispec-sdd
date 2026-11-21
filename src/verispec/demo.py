"""Built-in enterprise Order-to-Cash SDD scenarios demonstrating bug catching & governance."""

from __future__ import annotations

import time
from typing import Any

from verispec.compiler import compile_spec_markdown
from verispec.kernels import benchmark_kernels
from verispec.synth import synthesize_suite
from verispec.types import TraceStep
from verispec.verifier import verify_spec, verify_trace

BUGGY_ERP_ORDER_SPEC = """# Spec: UngovernedAgentOrderFlow
- **Version**: 0.9.0-draft
- **Initial**: `CREATED`
- **Terminal**: `COMPLETED`, `CANCELLED`, `LEGACY_REFUNDED`

## States
- `CREATED`: Order drafted by checkout agent
- `PAYMENT_AUTHORIZED`: Payment gateway hold succeeded
- `ON_HOLD`: Fraud hold missing any resolution transition
- `DISPUTED`: Chargeback dispute opened
- `UNDER_REVIEW`: Compliance analyst reviewing dispute
- `COMPLETED`: Order fulfilled and settled
- `CANCELLED`: Order voided cleanly
- `LEGACY_REFUNDED`: Deprecated terminal state with zero incoming edges

## Transitions
| From | Event | To | Guard | Postcondition |
| --- | --- | --- | --- | --- |
| CREATED | authorize_payment | PAYMENT_AUTHORIZED | amount > 0 and fraud_score < 80 | paid == True |
| CREATED | flag_hold | ON_HOLD | fraud_score >= 80 | - |
| CREATED | cancel_order | CANCELLED | - | - |
| PAYMENT_AUTHORIZED | ship_order | COMPLETED | paid == True | - |
| PAYMENT_AUTHORIZED | open_dispute | DISPUTED | - | - |
| DISPUTED | escalate_review | UNDER_REVIEW | - | - |
| UNDER_REVIEW | request_more_docs | DISPUTED | - | - |

## Invariants
- `non_negative_amount`: `amount >= 0`
"""

GOVERNED_ERP_ORDER_SPEC = """# Spec: GovernedEnterpriseOrderFlow
- **Version**: 1.0.0
- **Initial**: `CREATED`
- **Terminal**: `COMPLETED`, `CANCELLED`

## States
- `CREATED`: Order initialized [invariant: amount >= 0]
- `PAYMENT_AUTHORIZED`: Payment captured [invariant: paid == True]
- `RISK_HOLD`: High-risk order awaiting supervisor review
- `FULFILLED`: Warehouse shipment confirmed [invariant: paid == True]
- `COMPLETED`: Settlement finalized [invariant: paid == True]
- `CANCELLED`: Order cancelled or rejected

## Transitions
| From | Event | To | Guard | Postcondition |
| --- | --- | --- | --- | --- |
| CREATED | authorize_payment | PAYMENT_AUTHORIZED | amount > 0 and fraud_score < 80 | paid == True |
| CREATED | route_risk_hold | RISK_HOLD | fraud_score >= 80 | - |
| CREATED | cancel_order | CANCELLED | - | - |
| RISK_HOLD | approve_override | PAYMENT_AUTHORIZED | manual_override == True | paid == True |
| RISK_HOLD | reject_risk | CANCELLED | manual_override == False | - |
| PAYMENT_AUTHORIZED | confirm_shipment | FULFILLED | paid == True | - |
| PAYMENT_AUTHORIZED | cancel_order | CANCELLED | - | - |
| FULFILLED | finalize_settlement | COMPLETED | paid == True | - |

## Invariants
- `non_negative_amount`: `amount >= 0`
- `completed_requires_payment` [COMPLETED]: `paid == True`
"""


def run_demo_scenarios() -> dict[str, Any]:
    """Run buggy vs governed enterprise SPEC.md verification, trace checks, and synthesis."""
    t0 = time.perf_counter()
    buggy_model = compile_spec_markdown(BUGGY_ERP_ORDER_SPEC)
    buggy_report = verify_spec(buggy_model)
    buggy_ms = round((time.perf_counter() - t0) * 1000.0, 3)

    t1 = time.perf_counter()
    gov_model = compile_spec_markdown(GOVERNED_ERP_ORDER_SPEC)
    gov_report = verify_spec(gov_model)
    gov_ms = round((time.perf_counter() - t1) * 1000.0, 3)

    happy_trace = verify_trace(
        gov_model,
        [
            TraceStep(
                event="route_risk_hold",
                context={"amount": 12500, "fraud_score": 92, "paid": False},
                expected_state="RISK_HOLD",
            ),
            TraceStep(
                event="approve_override",
                context={"amount": 12500, "manual_override": True, "paid": True},
                expected_state="PAYMENT_AUTHORIZED",
            ),
            TraceStep(
                event="confirm_shipment",
                context={"amount": 12500, "paid": True},
                expected_state="FULFILLED",
            ),
            TraceStep(
                event="finalize_settlement",
                context={"amount": 12500, "paid": True},
                expected_state="COMPLETED",
            ),
        ],
        require_terminal=True,
    )

    rejected_trace = verify_trace(
        gov_model,
        [
            TraceStep(
                event="authorize_payment",
                context={"amount": 4500, "fraud_score": 95, "paid": True},
            )
        ],
    )

    py_suite = synthesize_suite(gov_model, target="python")
    ts_suite = synthesize_suite(gov_model, target="typescript")
    bench = benchmark_kernels(num_states=2000, trace_len=25000)

    return {
        "buggy_spec": {
            "verify_ms": buggy_ms,
            "report": buggy_report.model_dump(mode="json"),
        },
        "governed_spec": {
            "verify_ms": gov_ms,
            "report": gov_report.model_dump(mode="json"),
            "happy_trace": happy_trace.model_dump(mode="json"),
            "rejected_trace": rejected_trace.model_dump(mode="json"),
            "synthesized_python_lines": len(py_suite.code.splitlines()),
            "synthesized_typescript_lines": len(ts_suite.code.splitlines()),
            "python_preview": "\n".join(py_suite.code.splitlines()[:32]),
            "typescript_preview": "\n".join(ts_suite.code.splitlines()[:28]),
        },
        "kernel_benchmark": bench,
        "presets": {
            "buggy_markdown": BUGGY_ERP_ORDER_SPEC,
            "governed_markdown": GOVERNED_ERP_ORDER_SPEC,
        },
    }
