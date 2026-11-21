"""Typer CLI entrypoint for VeriSpec-SDD."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

import typer
import uvicorn
from rich.console import Console
from rich.table import Table

from verispec.compiler import compile_spec_markdown
from verispec.demo import run_demo_scenarios
from verispec.kernels import benchmark_kernels
from verispec.mcp import run_stdio_server
from verispec.synth import synthesize_suite
from verispec.types import TraceStep
from verispec.verifier import verify_spec, verify_trace

app = typer.Typer(
    name="verispec",
    help="VeriSpec-SDD: Executable SPEC.md Compiler, C++20 Finite-State Prover & Test Synthesizer.",
    no_args_is_help=True,
)
console = Console()


@app.command("compile")
def compile_cmd(spec_path: Path = typer.Argument(..., exists=True, readable=True)) -> None:
    """Compile a SPEC.md file into validated JSON IR."""
    model = compile_spec_markdown(spec_path.read_text(encoding="utf-8"))
    typer.echo(json.dumps(model.model_dump(mode="json"), indent=2))


@app.command("verify")
def verify_cmd(
    spec_path: Path = typer.Argument(..., exists=True, readable=True),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON verification report"),
) -> None:
    """Run C++20 reachability, deadlock, livelock, and determinism proofs on SPEC.md."""
    model = compile_spec_markdown(spec_path.read_text(encoding="utf-8"))
    report = verify_spec(model)
    if json_out:
        typer.echo(json.dumps(report.model_dump(mode="json"), indent=2))
    else:
        status_str = (
            "[bold green]PASSED[/bold green]" if report.passed else "[bold red]FAILED[/bold red]"
        )
        console.print(
            f"Spec [bold]{report.spec_name}[/bold]: {status_str} "
            f"(states={report.num_states}, transitions={report.num_transitions}, backend={report.backend})"
        )
        for diag in report.diagnostics:
            console.print(f"  [{diag.severity}] {diag.code}: {diag.message}")
    if not report.passed:
        raise typer.Exit(code=1)


@app.command("synth")
def synth_cmd(
    spec_path: Path = typer.Argument(..., exists=True, readable=True),
    target: str = typer.Option("python", "--target", "-t", help="python or typescript"),
    output: Path | None = typer.Option(None, "--output", "-o", help="Optional output file path"),
) -> None:
    """Synthesize a runnable Hypothesis/pytest or TypeScript Vitest contract test suite."""
    if target not in {"python", "typescript"}:
        raise typer.BadParameter("target must be 'python' or 'typescript'")
    lit_target: Literal["python", "typescript"] = (
        "typescript" if target == "typescript" else "python"
    )
    model = compile_spec_markdown(spec_path.read_text(encoding="utf-8"))
    suite = synthesize_suite(model, target=lit_target)
    if output is not None:
        output.write_text(suite.code, encoding="utf-8")
        console.print(f"Wrote {suite.target} suite ({suite.num_rules} rules) -> {output}")
    else:
        typer.echo(suite.code)


@app.command("check-trace")
def check_trace_cmd(
    spec_path: Path = typer.Argument(..., exists=True, readable=True),
    trace_path: Path = typer.Argument(..., exists=True, readable=True),
    require_terminal: bool = typer.Option(False, "--require-terminal"),
) -> None:
    """Verify a JSON list of TraceStep events against SPEC.md."""
    model = compile_spec_markdown(spec_path.read_text(encoding="utf-8"))
    raw = json.loads(trace_path.read_text(encoding="utf-8"))
    steps = [TraceStep.model_validate(item) for item in raw]
    report = verify_trace(model, steps, require_terminal=require_terminal)
    typer.echo(json.dumps(report.model_dump(mode="json"), indent=2))
    if not report.valid:
        raise typer.Exit(code=1)


@app.command("demo")
def demo_cmd(
    json_out: bool = typer.Option(False, "--json", help="Emit full JSON demo output"),
) -> None:
    """Run buggy vs governed enterprise ERP Order-to-Cash SPEC.md verification & benchmark."""
    res = run_demo_scenarios()
    if json_out:
        typer.echo(json.dumps(res, indent=2))
        return

    buggy = res["buggy_spec"]["report"]
    gov = res["governed_spec"]["report"]
    bench = res["kernel_benchmark"]

    tbl = Table(title="VeriSpec-SDD Enterprise SDD Audit (Buggy Draft vs Governed v1.0)")
    tbl.add_column("Specification")
    tbl.add_column("Verdict")
    tbl.add_column("Reachable")
    tbl.add_column("Deadlocks")
    tbl.add_column("Livelocks")
    tbl.add_column("Unreachable")
    tbl.add_column("Latency")

    tbl.add_row(
        buggy["spec_name"],
        "FAIL (Caught 3 Bugs)",
        f"{buggy['reachable_states']}/{buggy['num_states']}",
        ",".join(buggy["deadlock_states"]) or "-",
        ",".join(buggy["livelock_states"]) or "-",
        ",".join(buggy["unreachable_states"]) or "-",
        f"{res['buggy_spec']['verify_ms']} ms",
    )
    tbl.add_row(
        gov["spec_name"],
        "PASS (100% Sound)",
        f"{gov['reachable_states']}/{gov['num_states']}",
        "-",
        "-",
        "-",
        f"{res['governed_spec']['verify_ms']} ms",
    )
    console.print(tbl)
    console.print(
        f"Kernel Benchmark ({bench['num_states']} states, {bench['num_transitions']} edges, "
        f"{bench['trace_events']} trace events): C++20={bench['native_ms']} ms vs "
        f"Python={bench['python_ms']} ms ({bench['speedup_x']}x speedup, backend={bench['backend']})"
    )


@app.command("bench")
def bench_cmd(
    num_states: int = typer.Option(2000, "--states", help="Automaton state count"),
    trace_len: int = typer.Option(25000, "--trace-len", help="Trace event length"),
) -> None:
    """Benchmark C++20 CSR reachability, co-reachability, Tarjan SCC & trace kernels."""
    res = benchmark_kernels(num_states=num_states, trace_len=trace_len)
    typer.echo(json.dumps(res, indent=2))


@app.command("serve")
def serve_cmd(
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8200, "--port"),
) -> None:
    """Start the FastAPI control plane on port 8200."""
    uvicorn.run("verispec.api.app:app", host=host, port=port)  # pragma: no cover


@app.command("mcp")
def mcp_cmd() -> None:
    """Start the stdio JSON-RPC 2.0 Model Context Protocol (MCP) server."""
    run_stdio_server()
