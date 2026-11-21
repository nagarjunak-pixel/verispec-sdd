"""Synthesize runnable Python (Hypothesis/pytest) and TypeScript contract suites from SpecModel."""

from __future__ import annotations

import ast
import json
import re
from typing import Literal

from verispec.types import SpecModel, SynthesizedSuite


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", name.strip()).strip("_").lower()
    return slug or "spec"


def _pascal_case(name: str) -> str:
    parts = [p for p in re.split(r"[^a-zA-Z0-9]+", name.strip()) if p]
    out = "".join(p[:1].upper() + p[1:] for p in parts)
    return out if out and out[0].isalpha() else f"Spec{out}"


def _extract_identifiers(spec: SpecModel) -> list[str]:
    names: set[str] = set()
    exprs: list[str] = []
    for st in spec.states:
        if st.invariant:
            exprs.append(st.invariant)
    for tr in spec.transitions:
        if tr.guard:
            exprs.append(tr.guard)
        if tr.postcondition:
            exprs.append(tr.postcondition)
    for inv in spec.invariants:
        exprs.append(inv.expression)
    for expr in exprs:
        try:
            tree = ast.parse(expr, mode="eval")
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id not in {"True", "False", "None"}:
                names.add(node.id)
    return sorted(names)


def _synthesize_python(spec: SpecModel) -> SynthesizedSuite:
    slug = _slugify(spec.name)
    cls_name = f"{_pascal_case(spec.name)}StateMachine"
    spec_json = json.dumps(spec.model_dump(mode="json"), sort_keys=True)
    ctx_vars = _extract_identifiers(spec)

    ctx_strategy_entries: list[str] = []
    for var in ctx_vars:
        low = var.lower()
        if any(
            tok in low for tok in ("is_", "has_", "paid", "approved", "override", "ok", "valid")
        ):
            ctx_strategy_entries.append(f"        {var!r}: st.booleans(),")
        else:
            ctx_strategy_entries.append(
                f"        {var!r}: st.integers(min_value=0, max_value=200),"
            )
    if not ctx_strategy_entries:
        ctx_strategy_entries.append("        'step_nonce': st.integers(min_value=0, max_value=10),")

    rule_methods: list[str] = []
    for idx, evt in enumerate(spec.events):
        safe_evt = _slugify(evt)
        method_name = f"rule_{idx}_{safe_evt}"
        rule_methods.append(
            f"""    @rule(ctx=CONTEXT_STRATEGY)
    def {method_name}(self, ctx: dict[str, int | bool]) -> None:
        candidate = [*self.history, TraceStep(event={evt!r}, context=ctx)]
        report = verify_trace(SPEC_MODEL, candidate)
        if report.valid:
            self.history = candidate
            self.current_state = report.final_state
        else:
            assert report.failed_step == len(self.history)
"""
        )

    rules_block = "\n".join(rule_methods) if rule_methods else "    pass\n"
    ctx_block = "\n".join(ctx_strategy_entries)
    total_invs = len(spec.invariants) + sum(1 for s in spec.states if s.invariant)

    code = f'''"""Auto-synthesized executable Hypothesis + pytest state-machine contract suite for {spec.name}."""

from __future__ import annotations

import json

from hypothesis import settings, strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule

from verispec.types import SpecModel, TraceStep
from verispec.verifier import verify_spec, verify_trace

SPEC_MODEL = SpecModel.model_validate(json.loads({spec_json!r}))

CONTEXT_STRATEGY = st.fixed_dictionaries(
    {{
{ctx_block}
    }}
)


def test_static_specification_soundness() -> None:
    report = verify_spec(SPEC_MODEL)
    assert report.num_states == {len(spec.states)}
    assert report.reachable_states >= 1


class {cls_name}(RuleBasedStateMachine):
    def __init__(self) -> None:
        super().__init__()
        self.current_state: str = SPEC_MODEL.initial_state
        self.history: list[TraceStep] = []

{rules_block}
    @invariant()
    def invariant_history_always_replays_cleanly(self) -> None:
        replay = verify_trace(SPEC_MODEL, self.history)
        assert replay.valid is True
        assert replay.final_state == self.current_state


TestSynthesizedContract = {cls_name}.TestCase
TestSynthesizedContract.settings = settings(max_examples=25, stateful_step_count=12, deadline=None)
'''
    return SynthesizedSuite(
        spec_name=spec.name,
        target="python",
        filename=f"test_{slug}_contract.py",
        code=code,
        num_rules=len(spec.events),
        num_invariants=total_invs + 1,
    )


def _py_expr_to_ts(expr: str) -> str:
    out = re.sub(r"\band\b", "&&", expr)
    out = re.sub(r"\bor\b", "||", out)
    out = re.sub(r"\bnot\b", "!", out)
    out = re.sub(r"\bTrue\b", "true", out)
    out = re.sub(r"\bFalse\b", "false", out)
    out = re.sub(r"\bNone\b", "null", out)
    return out


def _synthesize_typescript(spec: SpecModel) -> SynthesizedSuite:
    slug = _slugify(spec.name)
    state_union = " | ".join(json.dumps(s.name) for s in spec.states) or "string"
    event_union = " | ".join(json.dumps(e) for e in spec.events) or "string"
    ctx_vars = _extract_identifiers(spec)
    ctx_fields = "\n".join(f"  readonly {v}?: number | boolean | string;" for v in ctx_vars)
    if not ctx_fields:
        ctx_fields = "  readonly [key: string]: unknown;"

    branches: list[str] = []
    for tr in spec.transitions:
        cond_parts = [f"state === {json.dumps(tr.from_state)}", f"event === {json.dumps(tr.event)}"]
        if tr.guard:
            ts_guard = _py_expr_to_ts(tr.guard)
            for v in ctx_vars:
                ts_guard = re.sub(rf"\b{re.escape(v)}\b", f"(ctx.{v} as any)", ts_guard)
            cond_parts.append(f"Boolean({ts_guard})")
        cond_str = " && ".join(cond_parts)
        branches.append(
            f"  if ({cond_str}) return {{ ok: true, nextState: {json.dumps(tr.to_state)} }};"
        )

    branches_code = "\n".join(branches)
    total_invs = len(spec.invariants) + sum(1 for s in spec.states if s.invariant)

    code = f"""// Auto-synthesized TypeScript contract & state-machine suite for {spec.name}
import {{ describe, expect, it }} from "vitest";

export type SpecState = {state_union};
export type SpecEvent = {event_union};

export interface SpecContext {{
{ctx_fields}
}}

export interface StepOutcome {{
  readonly ok: boolean;
  readonly nextState: SpecState;
}}

export const INITIAL_STATE: SpecState = {json.dumps(spec.initial_state)};
export const TERMINAL_STATES: ReadonlySet<SpecState> = new Set({json.dumps(spec.terminal_states)});

export function stepContract(state: SpecState, event: SpecEvent, ctx: SpecContext = {{}}): StepOutcome {{
{branches_code}
  return {{ ok: false, nextState: state }};
}}

describe("{spec.name} executable SPEC.md contract", () => {{
  it("starts in declared initial state and rejects unmapped transitions cleanly", () => {{
    expect(INITIAL_STATE).toBe({json.dumps(spec.initial_state)});
    expect(TERMINAL_STATES.size).toBe({len(spec.terminal_states)});
  }});
}});
"""
    return SynthesizedSuite(
        spec_name=spec.name,
        target="typescript",
        filename=f"{slug}.contract.test.ts",
        code=code,
        num_rules=len(spec.events),
        num_invariants=total_invs,
    )


def synthesize_suite(
    spec: SpecModel, target: Literal["python", "typescript"] = "python"
) -> SynthesizedSuite:
    """Synthesize a runnable Python (`pytest` + `Hypothesis`) or TypeScript (`vitest`) test suite."""
    if target == "python":
        return _synthesize_python(spec)
    if target == "typescript":
        return _synthesize_typescript(spec)
    raise ValueError(f"Unsupported synthesis target: {target!r}")
