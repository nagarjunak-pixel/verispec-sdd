"""Runtime execution trace verifier checking transitions, guards, postconditions & invariants."""

from __future__ import annotations

from verispec.compiler.expr import SpecExpressionError, evaluate_predicate
from verispec.kernels import backend_name, verify_event_trace_kernel
from verispec.types import SpecModel, TraceStep, TraceVerificationReport, TransitionSpec


def _check_state_invariants(
    spec: SpecModel, state_name: str, ctx: dict[str, object]
) -> tuple[bool, str | None]:
    if not ctx:
        return True, None
    for s_obj in spec.states:
        if s_obj.name == state_name and s_obj.invariant:
            if not evaluate_predicate(s_obj.invariant, ctx):
                return False, s_obj.invariant
    for inv in spec.invariants:
        if inv.scope_state in (None, state_name):
            if not evaluate_predicate(inv.expression, ctx):
                return False, f"{inv.name}: {inv.expression}"
    return True, None


def verify_trace(
    spec: SpecModel,
    steps: list[TraceStep],
    *,
    require_terminal: bool = False,
    use_native: bool = True,
) -> TraceVerificationReport:
    """Verify an execution trace against `spec` transitions, guards, postconditions, and invariants."""
    has_guards_or_ctx = (
        any(t.guard or t.postcondition for t in spec.transitions)
        or bool(spec.invariants)
        or any(s.invariant for s in spec.states)
        or any(step.context or step.expected_state for step in steps)
    )

    state_idx = {s.name: i for i, s in enumerate(spec.states)}
    event_idx = {e: i for i, e in enumerate(spec.events)}

    # Pure C++20 kernel fast path for unguarded structural event traces
    if not has_guards_or_ctx:
        evt_ids = [event_idx.get(st.event, -1) for st in steps]
        trans_tuples = [
            (state_idx[t.from_state], event_idx[t.event], state_idx[t.to_state])
            for t in spec.transitions
        ]
        term_ids = {state_idx[t] for t in spec.terminal_states if t in state_idx}
        rc, final_id, failed_idx = verify_event_trace_kernel(
            len(spec.states),
            len(spec.events),
            trans_tuples,
            state_idx[spec.initial_state],
            term_ids,
            evt_ids,
            require_terminal=require_terminal,
            use_native=use_native,
        )
        # Reconstruct visited states for audit clarity
        visited = [spec.initial_state]
        cur = spec.initial_state
        limit = len(steps) if rc != 1 else max(0, failed_idx)
        lookup = {(t.from_state, t.event): t.to_state for t in spec.transitions}
        for i in range(limit):
            cur = lookup[(cur, steps[i].event)]
            visited.append(cur)

        if rc == 0:
            return TraceVerificationReport(
                spec_name=spec.name,
                valid=True,
                backend=backend_name(),
                initial_state=spec.initial_state,
                final_state=spec.states[final_id].name,
                steps_verified=len(steps),
                visited_states=visited,
            )
        if rc == 1:
            bad_evt = steps[failed_idx].event if 0 <= failed_idx < len(steps) else "<unknown>"
            return TraceVerificationReport(
                spec_name=spec.name,
                valid=False,
                backend=backend_name(),
                initial_state=spec.initial_state,
                final_state=spec.states[final_id].name,
                steps_verified=failed_idx,
                visited_states=visited,
                failed_step=failed_idx,
                failure_reason=f"Illegal event {bad_evt!r} from state {spec.states[final_id].name!r}",
            )
        return TraceVerificationReport(
            spec_name=spec.name,
            valid=False,
            backend=backend_name(),
            initial_state=spec.initial_state,
            final_state=spec.states[final_id].name,
            steps_verified=len(steps),
            visited_states=visited,
            failed_step=len(steps),
            failure_reason=f"Trace ended in non-terminal state {spec.states[final_id].name!r}",
        )

    # Full semantic evaluator (guards + postconditions + state/global invariants)
    cur_state = spec.initial_state
    visited_states = [cur_state]
    by_src_evt: dict[tuple[str, str], list[TransitionSpec]] = {}
    for tr in spec.transitions:
        by_src_evt.setdefault((tr.from_state, tr.event), []).append(tr)

    for idx, step in enumerate(steps):
        candidates = by_src_evt.get((cur_state, step.event), [])
        if not candidates:
            return TraceVerificationReport(
                spec_name=spec.name,
                valid=False,
                backend=backend_name(),
                initial_state=spec.initial_state,
                final_state=cur_state,
                steps_verified=idx,
                visited_states=visited_states,
                failed_step=idx,
                failure_reason=f"No transition defined from {cur_state!r} on event {step.event!r}",
            )

        chosen: TransitionSpec | None = None
        last_guard_err: str | None = None
        for cand in candidates:
            if cand.guard is None:
                chosen = cand
                break
            try:
                if evaluate_predicate(cand.guard, step.context):
                    chosen = cand
                    break
                last_guard_err = cand.guard
            except SpecExpressionError as exc:
                last_guard_err = f"{cand.guard} ({exc})"

        if chosen is None:
            return TraceVerificationReport(
                spec_name=spec.name,
                valid=False,
                backend=backend_name(),
                initial_state=spec.initial_state,
                final_state=cur_state,
                steps_verified=idx,
                visited_states=visited_states,
                failed_step=idx,
                failure_reason=f"Guard rejected event {step.event!r} from {cur_state!r}",
                violated_expression=last_guard_err,
            )

        if chosen.postcondition is not None:
            try:
                post_ok = evaluate_predicate(chosen.postcondition, step.context)
            except SpecExpressionError as exc:
                return TraceVerificationReport(
                    spec_name=spec.name,
                    valid=False,
                    backend=backend_name(),
                    initial_state=spec.initial_state,
                    final_state=cur_state,
                    steps_verified=idx,
                    visited_states=visited_states,
                    failed_step=idx,
                    failure_reason=f"Postcondition error on {step.event!r}: {exc}",
                    violated_expression=chosen.postcondition,
                )
            if not post_ok:
                return TraceVerificationReport(
                    spec_name=spec.name,
                    valid=False,
                    backend=backend_name(),
                    initial_state=spec.initial_state,
                    final_state=cur_state,
                    steps_verified=idx,
                    visited_states=visited_states,
                    failed_step=idx,
                    failure_reason=f"Postcondition violated after {step.event!r}",
                    violated_expression=chosen.postcondition,
                )

        cur_state = chosen.to_state
        visited_states.append(cur_state)

        if step.expected_state is not None and step.expected_state != cur_state:
            return TraceVerificationReport(
                spec_name=spec.name,
                valid=False,
                backend=backend_name(),
                initial_state=spec.initial_state,
                final_state=cur_state,
                steps_verified=idx,
                visited_states=visited_states,
                failed_step=idx,
                failure_reason=f"Expected state {step.expected_state!r}, got {cur_state!r}",
            )

        try:
            inv_ok, bad_inv = _check_state_invariants(spec, cur_state, step.context)
        except SpecExpressionError as exc:
            return TraceVerificationReport(
                spec_name=spec.name,
                valid=False,
                backend=backend_name(),
                initial_state=spec.initial_state,
                final_state=cur_state,
                steps_verified=idx,
                visited_states=visited_states,
                failed_step=idx,
                failure_reason=f"Invariant evaluation error in state {cur_state!r}: {exc}",
            )
        if not inv_ok:
            return TraceVerificationReport(
                spec_name=spec.name,
                valid=False,
                backend=backend_name(),
                initial_state=spec.initial_state,
                final_state=cur_state,
                steps_verified=idx,
                visited_states=visited_states,
                failed_step=idx,
                failure_reason=f"Invariant violated in state {cur_state!r}",
                violated_expression=bad_inv,
            )

    if require_terminal and cur_state not in spec.terminal_states:
        return TraceVerificationReport(
            spec_name=spec.name,
            valid=False,
            backend=backend_name(),
            initial_state=spec.initial_state,
            final_state=cur_state,
            steps_verified=len(steps),
            visited_states=visited_states,
            failed_step=len(steps),
            failure_reason=f"Trace ended in non-terminal state {cur_state!r}",
        )

    return TraceVerificationReport(
        spec_name=spec.name,
        valid=True,
        backend=backend_name(),
        initial_state=spec.initial_state,
        final_state=cur_state,
        steps_verified=len(steps),
        visited_states=visited_states,
    )
