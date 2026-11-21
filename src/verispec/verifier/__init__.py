"""Static FSM prover and runtime execution trace verifier."""

from __future__ import annotations

from verispec.verifier.prover import verify_spec
from verispec.verifier.trace import verify_trace

__all__ = ["verify_spec", "verify_trace"]
