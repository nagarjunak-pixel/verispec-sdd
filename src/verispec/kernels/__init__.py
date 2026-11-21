"""Native C++20 and pure-Python automaton verification kernels."""

from __future__ import annotations

from verispec.kernels.native import (
    backend_name,
    benchmark_kernels,
    edges_to_csr,
    forward_reachability,
    liveness_co_reachability,
    tarjan_scc_cycles,
    verify_event_trace_kernel,
)

__all__ = [
    "backend_name",
    "benchmark_kernels",
    "edges_to_csr",
    "forward_reachability",
    "liveness_co_reachability",
    "tarjan_scc_cycles",
    "verify_event_trace_kernel",
]
