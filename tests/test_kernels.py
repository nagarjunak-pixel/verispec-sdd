"""Hypothesis property-based parity & edge-case tests for C++20 and Python kernels."""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from verispec.kernels import (
    backend_name,
    benchmark_kernels,
    edges_to_csr,
    forward_reachability,
    liveness_co_reachability,
    tarjan_scc_cycles,
    verify_event_trace_kernel,
)


def test_native_backend_is_active() -> None:
    assert "c++20-native" in backend_name()


def test_empty_and_invalid_kernel_inputs() -> None:
    assert forward_reachability(0, [], 0) == []
    assert liveness_co_reachability(0, [], set()) == []
    assert tarjan_scc_cycles(0, []) == ([], [])
    with pytest.raises(ValueError, match="initial_state"):
        forward_reachability(3, [], 5)
    with pytest.raises(ValueError, match="Invalid automaton"):
        verify_event_trace_kernel(0, 2, [], 0, set(), [])
    row_ptr, col_idx = edges_to_csr(3, [(0, 1), (-1, 2), (1, 99)])
    assert row_ptr == [0, 1, 1, 1]
    assert col_idx == [1]


@given(
    num_states=st.integers(min_value=1, max_value=30),
    raw_edges=st.lists(
        st.tuples(st.integers(min_value=0, max_value=29), st.integers(min_value=0, max_value=29)),
        max_size=90,
    ),
    init_seed=st.integers(min_value=0, max_value=29),
    term_seeds=st.lists(st.integers(min_value=0, max_value=29), max_size=10),
)
@settings(max_examples=80, deadline=None)
def test_hypothesis_parity_reachability_liveness_and_tarjan(
    num_states: int,
    raw_edges: list[tuple[int, int]],
    init_seed: int,
    term_seeds: list[int],
) -> None:
    edges = [(u % num_states, v % num_states) for u, v in raw_edges]
    init_state = init_seed % num_states
    terminals = {t % num_states for t in term_seeds}

    fwd_cpp = forward_reachability(num_states, edges, init_state, use_native=True)
    fwd_py = forward_reachability(num_states, edges, init_state, use_native=False)
    assert fwd_cpp == fwd_py

    live_cpp = liveness_co_reachability(num_states, edges, terminals, use_native=True)
    live_py = liveness_co_reachability(num_states, edges, terminals, use_native=False)
    assert live_cpp == live_py

    scc_cpp, cyc_cpp = tarjan_scc_cycles(num_states, edges, use_native=True)
    scc_py, cyc_py = tarjan_scc_cycles(num_states, edges, use_native=False)
    assert cyc_cpp == cyc_py
    assert scc_cpp == scc_py


@given(
    num_states=st.integers(min_value=1, max_value=16),
    num_events=st.integers(min_value=1, max_value=8),
    raw_trans=st.lists(
        st.tuples(
            st.integers(min_value=0, max_value=15),
            st.integers(min_value=0, max_value=7),
            st.integers(min_value=0, max_value=15),
        ),
        max_size=40,
    ),
    trace=st.lists(st.integers(min_value=-1, max_value=8), max_size=30),
    require_terminal=st.booleans(),
)
@settings(max_examples=80, deadline=None)
def test_hypothesis_parity_event_trace_kernel(
    num_states: int,
    num_events: int,
    raw_trans: list[tuple[int, int, int]],
    trace: list[int],
    require_terminal: bool,
) -> None:
    transitions = [(s % num_states, e % num_events, d % num_states) for s, e, d in raw_trans]
    terminals = {num_states - 1}
    cpp_res = verify_event_trace_kernel(
        num_states,
        num_events,
        transitions,
        0,
        terminals,
        trace,
        require_terminal=require_terminal,
        use_native=True,
    )
    py_res = verify_event_trace_kernel(
        num_states,
        num_events,
        transitions,
        0,
        terminals,
        trace,
        require_terminal=require_terminal,
        use_native=False,
    )
    assert cpp_res == py_res


def test_benchmark_kernels_returns_valid_metrics() -> None:
    res = benchmark_kernels(num_states=120, trace_len=1000)
    assert res["num_states"] == 120
    assert res["trace_events"] == 1000
    assert float(res["native_ms"]) >= 0.0
    assert float(res["python_ms"]) >= 0.0
