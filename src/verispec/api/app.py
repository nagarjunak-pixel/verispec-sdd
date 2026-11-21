"""FastAPI endpoints for SPEC.md compilation, C++20 verification, synthesis, and trace checks."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from verispec import __version__
from verispec.compiler import SpecCompileError, SpecExpressionError, compile_spec_markdown
from verispec.demo import run_demo_scenarios
from verispec.kernels import backend_name, benchmark_kernels
from verispec.synth import synthesize_suite
from verispec.types import (
    SpecModel,
    SynthesizedSuite,
    TraceStep,
    TraceVerificationReport,
    VerificationReport,
)
from verispec.verifier import verify_spec, verify_trace


class MarkdownSpecRequest(BaseModel):
    markdown: str = Field(..., min_length=1)


class SynthRequest(BaseModel):
    markdown: str = Field(..., min_length=1)
    target: Literal["python", "typescript"] = "python"


class CheckTraceRequest(BaseModel):
    markdown: str = Field(..., min_length=1)
    steps: list[TraceStep]
    require_terminal: bool = False


class _RuntimeMetrics:
    def __init__(self) -> None:
        self.compilations: int = 0
        self.verifications: int = 0
        self.failed_specs: int = 0
        self.syntheses: int = 0
        self.trace_checks: int = 0


def create_app() -> FastAPI:
    api = FastAPI(
        title="VeriSpec-SDD Control Plane",
        version=__version__,
        description="Executable SPEC.md State-Machine Compiler, C++20 Finite-State Prover & Property-Test Synthesizer",
    )
    api.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    metrics = _RuntimeMetrics()

    def _compile_or_422(md: str) -> SpecModel:
        try:
            model = compile_spec_markdown(md)
            metrics.compilations += 1
            return model
        except (SpecCompileError, SpecExpressionError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @api.get("/health")
    def health() -> dict[str, str]:
        return {
            "status": "ok",
            "service": "verispec-sdd",
            "version": __version__,
            "backend": backend_name(),
        }

    @api.get("/metrics", response_class=PlainTextResponse)
    def prometheus_metrics() -> str:
        lines = [
            "# HELP verispec_compilations_total Total SPEC.md compilations",
            "# TYPE verispec_compilations_total counter",
            f"verispec_compilations_total {metrics.compilations}",
            "# HELP verispec_verifications_total Total static FSM verifications",
            "# TYPE verispec_verifications_total counter",
            f"verispec_verifications_total {metrics.verifications}",
            "# HELP verispec_failed_specs_total Total specs with formal verification errors",
            "# TYPE verispec_failed_specs_total counter",
            f"verispec_failed_specs_total {metrics.failed_specs}",
            "# HELP verispec_syntheses_total Total test suites synthesized",
            "# TYPE verispec_syntheses_total counter",
            f"verispec_syntheses_total {metrics.syntheses}",
            "# HELP verispec_trace_checks_total Total runtime traces checked",
            "# TYPE verispec_trace_checks_total counter",
            f"verispec_trace_checks_total {metrics.trace_checks}",
        ]
        return "\n".join(lines) + "\n"

    @api.post("/compile", response_model=SpecModel)
    @api.post("/api/v1/compile", response_model=SpecModel)
    def compile_endpoint(req: MarkdownSpecRequest) -> SpecModel:
        return _compile_or_422(req.markdown)

    @api.post("/verify", response_model=VerificationReport)
    @api.post("/api/v1/verify", response_model=VerificationReport)
    def verify_endpoint(req: MarkdownSpecRequest) -> VerificationReport:
        spec = _compile_or_422(req.markdown)
        report = verify_spec(spec)
        metrics.verifications += 1
        if not report.passed:
            metrics.failed_specs += 1
        return report

    @api.post("/synth", response_model=SynthesizedSuite)
    @api.post("/api/v1/synth", response_model=SynthesizedSuite)
    def synth_endpoint(req: SynthRequest) -> SynthesizedSuite:
        spec = _compile_or_422(req.markdown)
        metrics.syntheses += 1
        return synthesize_suite(spec, target=req.target)

    @api.post("/check-trace", response_model=TraceVerificationReport)
    @api.post("/api/v1/check-trace", response_model=TraceVerificationReport)
    def check_trace_endpoint(req: CheckTraceRequest) -> TraceVerificationReport:
        spec = _compile_or_422(req.markdown)
        metrics.trace_checks += 1
        return verify_trace(spec, req.steps, require_terminal=req.require_terminal)

    @api.get("/api/v1/demo")
    def demo_endpoint() -> dict[str, Any]:
        return run_demo_scenarios()

    @api.get("/api/v1/bench")
    def bench_endpoint(
        num_states: int = 2000, trace_len: int = 25000
    ) -> dict[str, float | int | str]:
        return benchmark_kernels(num_states=num_states, trace_len=trace_len)

    return api


app = create_app()
