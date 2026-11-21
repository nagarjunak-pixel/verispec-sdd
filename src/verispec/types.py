"""Strict Pydantic v2 domain models for VeriSpec-SDD."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class StateSpec(BaseModel):
    """Single finite-state definition parsed from `SPEC.md`."""

    name: str
    description: str = ""
    is_initial: bool = False
    is_terminal: bool = False
    invariant: str | None = None


class TransitionSpec(BaseModel):
    """Directed guarded state transition parsed from `SPEC.md`."""

    index: int
    from_state: str
    event: str
    to_state: str
    guard: str | None = None
    postcondition: str | None = None


class InvariantSpec(BaseModel):
    """Global or state-scoped invariant expression."""

    name: str
    expression: str
    scope_state: str | None = None


class SpecModel(BaseModel):
    """Compiled intermediate representation of an executable `SPEC.md` contract."""

    name: str
    version: str = "1.0.0"
    initial_state: str
    terminal_states: list[str]
    states: list[StateSpec]
    transitions: list[TransitionSpec]
    invariants: list[InvariantSpec] = Field(default_factory=list)
    events: list[str]


DiagnosticSeverity = Literal["ERROR", "WARNING"]
DiagnosticCode = Literal[
    "UNREACHABLE_STATE",
    "UNREACHABLE_TRANSITION",
    "DEADLOCK_STATE",
    "LIVELOCK_TRAP",
    "NONDETERMINISTIC_TRANSITION",
    "ORPHAN_TERMINAL",
]


class DiagnosticItem(BaseModel):
    """Formal verification finding on a compiled `SpecModel`."""

    code: DiagnosticCode
    severity: DiagnosticSeverity
    state: str | None = None
    event: str | None = None
    transition_index: int | None = None
    message: str


class StateAnalysisRow(BaseModel):
    """Per-state reachability, co-reachability, and SCC cycle metrics."""

    state: str
    is_initial: bool
    is_terminal: bool
    hops_from_initial: int
    hops_to_terminal: int
    scc_id: int
    in_cycle: bool
    out_degree: int


class VerificationReport(BaseModel):
    """Complete static verification report produced by the C++20/Python prover."""

    spec_name: str
    passed: bool
    backend: str
    num_states: int
    num_transitions: int
    reachable_states: int
    deadlock_states: list[str]
    livelock_states: list[str]
    unreachable_states: list[str]
    nondeterministic_pairs: list[str]
    shortest_witness_to_terminal: list[str]
    diagnostics: list[DiagnosticItem]
    state_table: list[StateAnalysisRow]


class TraceStep(BaseModel):
    """Single observed execution event with optional context payload and expected state."""

    event: str
    context: dict[str, Any] = Field(default_factory=dict)
    expected_state: str | None = None


class TraceVerificationReport(BaseModel):
    """Step-by-step execution trace verification result against `SpecModel`."""

    spec_name: str
    valid: bool
    backend: str
    initial_state: str
    final_state: str
    steps_verified: int
    visited_states: list[str]
    failed_step: int | None = None
    failure_reason: str | None = None
    violated_expression: str | None = None


class SynthesizedSuite(BaseModel):
    """Generated runnable contract/property test suite."""

    spec_name: str
    target: Literal["python", "typescript"]
    filename: str
    code: str
    num_rules: int
    num_invariants: int
