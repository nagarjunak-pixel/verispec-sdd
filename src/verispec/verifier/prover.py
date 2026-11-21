"""Static finite-state reachability, deadlock, livelock & determinism prover."""

from __future__ import annotations

from collections import deque

from verispec.kernels import (
    backend_name,
    forward_reachability,
    liveness_co_reachability,
    tarjan_scc_cycles,
)
from verispec.types import (
    DiagnosticItem,
    SpecModel,
    StateAnalysisRow,
    VerificationReport,
)


def _shortest_witness(spec: SpecModel, state_idx: dict[str, int]) -> list[str]:
    if not spec.terminal_states:
        return []
    term_set = set(spec.terminal_states)
    if spec.initial_state in term_set:
        return [spec.initial_state]

    adj: dict[str, list[tuple[str, str]]] = {s.name: [] for s in spec.states}
    for tr in spec.transitions:
        adj[tr.from_state].append((tr.event, tr.to_state))

    visited = {spec.initial_state}
    q: deque[tuple[str, list[str]]] = deque([(spec.initial_state, [spec.initial_state])])
    while q:
        cur, path = q.popleft()
        for evt, nxt in adj[cur]:
            if nxt in term_set:
                return [*path, f"--({evt})-->", nxt]
            if nxt not in visited and nxt in state_idx:
                visited.add(nxt)
                q.append((nxt, [*path, f"--({evt})-->", nxt]))
    return []


def verify_spec(spec: SpecModel, *, use_native: bool = True) -> VerificationReport:
    """Run C++20/Python reachability, co-reachability, Tarjan cycle, and determinism checks."""
    state_names = [s.name for s in spec.states]
    state_idx = {name: i for i, name in enumerate(state_names)}
    num_states = len(state_names)

    edges = [(state_idx[t.from_state], state_idx[t.to_state]) for t in spec.transitions]
    out_deg = [0] * num_states
    for u, _ in edges:
        out_deg[u] += 1

    init_id = state_idx[spec.initial_state]
    term_ids = {state_idx[t] for t in spec.terminal_states if t in state_idx}

    fwd_hops = forward_reachability(num_states, edges, init_id, use_native=use_native)
    term_hops = liveness_co_reachability(num_states, edges, term_ids, use_native=use_native)
    scc_ids, in_cycle = tarjan_scc_cycles(num_states, edges, use_native=use_native)

    diagnostics: list[DiagnosticItem] = []
    unreachable_states: list[str] = []
    deadlock_states: list[str] = []
    livelock_states: list[str] = []
    nondet_pairs: list[str] = []

    for i, s_obj in enumerate(spec.states):
        sname = s_obj.name
        reachable = fwd_hops[i] >= 0
        can_terminate = term_hops[i] >= 0

        if not reachable:
            unreachable_states.append(sname)
            if s_obj.is_terminal:
                diagnostics.append(
                    DiagnosticItem(
                        code="ORPHAN_TERMINAL",
                        severity="ERROR",
                        state=sname,
                        message=f"Declared terminal state {sname!r} is unreachable from initial state {spec.initial_state!r}.",
                    )
                )
            else:
                diagnostics.append(
                    DiagnosticItem(
                        code="UNREACHABLE_STATE",
                        severity="ERROR",
                        state=sname,
                        message=f"State {sname!r} is unreachable from initial state {spec.initial_state!r}.",
                    )
                )
            continue

        if not s_obj.is_terminal and out_deg[i] == 0:
            deadlock_states.append(sname)
            diagnostics.append(
                DiagnosticItem(
                    code="DEADLOCK_STATE",
                    severity="ERROR",
                    state=sname,
                    message=f"Reachable non-terminal state {sname!r} has 0 outgoing transitions (deadlock).",
                )
            )
        elif not s_obj.is_terminal and spec.terminal_states and not can_terminate:
            livelock_states.append(sname)
            diagnostics.append(
                DiagnosticItem(
                    code="LIVELOCK_TRAP",
                    severity="ERROR",
                    state=sname,
                    message=f"Reachable state {sname!r} has outgoing transitions (in_cycle={in_cycle[i]}) but zero path to any terminal state {spec.terminal_states}.",
                )
            )

    # Check unreachable transitions & nondeterministic (state, event) pairs
    by_pair: dict[tuple[str, str], list[tuple[int, str | None, str]]] = {}
    for tr in spec.transitions:
        src_i = state_idx[tr.from_state]
        if fwd_hops[src_i] < 0:
            diagnostics.append(
                DiagnosticItem(
                    code="UNREACHABLE_TRANSITION",
                    severity="WARNING",
                    state=tr.from_state,
                    event=tr.event,
                    transition_index=tr.index,
                    message=f"Transition #{tr.index} ({tr.from_state} --{tr.event}--> {tr.to_state}) originates from unreachable state.",
                )
            )
        norm_guard = (tr.guard or "").strip() or None
        by_pair.setdefault((tr.from_state, tr.event), []).append(
            (tr.index, norm_guard, tr.to_state)
        )

    for (from_st, evt), entries in by_pair.items():
        if len(entries) <= 1:
            continue
        guards = [g for _, g, _ in entries]
        targets = {dst for _, _, dst in entries}
        # Flag if any transition on the same (state, event) is unguarded OR two share the exact same guard to different targets
        has_unguarded = any(g is None for g in guards)
        duplicate_guard = len(set(guards)) < len(guards)
        if (has_unguarded or duplicate_guard) and len(targets) > 1:
            label = f"{from_st}:{evt}"
            nondet_pairs.append(label)
            diagnostics.append(
                DiagnosticItem(
                    code="NONDETERMINISTIC_TRANSITION",
                    severity="ERROR",
                    state=from_st,
                    event=evt,
                    message=f"Ambiguous transitions on ({from_st!r}, {evt!r}) to multiple targets {sorted(targets)} without mutually exclusive guards.",
                )
            )

    state_rows = [
        StateAnalysisRow(
            state=s_obj.name,
            is_initial=s_obj.is_initial,
            is_terminal=s_obj.is_terminal,
            hops_from_initial=fwd_hops[i],
            hops_to_terminal=term_hops[i],
            scc_id=scc_ids[i],
            in_cycle=in_cycle[i],
            out_degree=out_deg[i],
        )
        for i, s_obj in enumerate(spec.states)
    ]

    has_errors = any(d.severity == "ERROR" for d in diagnostics)
    reachable_cnt = sum(1 for d in fwd_hops if d >= 0)

    return VerificationReport(
        spec_name=spec.name,
        passed=not has_errors,
        backend=backend_name(),
        num_states=num_states,
        num_transitions=len(spec.transitions),
        reachable_states=reachable_cnt,
        deadlock_states=deadlock_states,
        livelock_states=livelock_states,
        unreachable_states=unreachable_states,
        nondeterministic_pairs=nondet_pairs,
        shortest_witness_to_terminal=_shortest_witness(spec, state_idx),
        diagnostics=diagnostics,
        state_table=state_rows,
    )
