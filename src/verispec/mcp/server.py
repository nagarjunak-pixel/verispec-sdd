"""Stdio JSON-RPC 2.0 MCP server giving AI coding agents formal SPEC.md verification tools."""

from __future__ import annotations

import json
import sys
from typing import Any, Literal, TextIO

from verispec import __version__
from verispec.compiler import SpecCompileError, SpecExpressionError, compile_spec_markdown
from verispec.synth import synthesize_suite
from verispec.types import TraceStep
from verispec.verifier import verify_spec, verify_trace

MCP_TOOLS: list[dict[str, Any]] = [
    {
        "name": "verispec_compile_spec",
        "description": "Compile a structured SPEC.md document into a validated finite-state SpecModel IR.",
        "inputSchema": {
            "type": "object",
            "properties": {"markdown": {"type": "string"}},
            "required": ["markdown"],
        },
    },
    {
        "name": "verispec_verify_spec",
        "description": "Prove reachability, deadlock freedom, livelock freedom (co-reachability + Tarjan SCC), and determinism on SPEC.md.",
        "inputSchema": {
            "type": "object",
            "properties": {"markdown": {"type": "string"}},
            "required": ["markdown"],
        },
    },
    {
        "name": "verispec_synthesize_tests",
        "description": "Synthesize a runnable Python (Hypothesis/pytest) or TypeScript (Vitest) contract test suite from SPEC.md.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "markdown": {"type": "string"},
                "target": {"type": "string", "enum": ["python", "typescript"]},
            },
            "required": ["markdown"],
        },
    },
    {
        "name": "verispec_check_trace",
        "description": "Verify an observed execution trace against SPEC.md transitions, guards, postconditions, and invariants.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "markdown": {"type": "string"},
                "steps": {"type": "array", "items": {"type": "object"}},
                "require_terminal": {"type": "boolean"},
            },
            "required": ["markdown", "steps"],
        },
    },
]


def _call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    markdown = str(arguments.get("markdown", ""))
    spec = compile_spec_markdown(markdown)
    if name == "verispec_compile_spec":
        return spec.model_dump(mode="json")
    if name == "verispec_verify_spec":
        return verify_spec(spec).model_dump(mode="json")
    if name == "verispec_synthesize_tests":
        raw_target = str(arguments.get("target", "python"))
        target: Literal["python", "typescript"] = (
            "typescript" if raw_target == "typescript" else "python"
        )
        return synthesize_suite(spec, target=target).model_dump(mode="json")
    if name == "verispec_check_trace":
        raw_steps = arguments.get("steps", [])
        steps = [TraceStep.model_validate(item) for item in raw_steps]
        req_term = bool(arguments.get("require_terminal", False))
        return verify_trace(spec, steps, require_terminal=req_term).model_dump(mode="json")
    raise ValueError(f"Unknown MCP tool: {name!r}")


def handle_mcp_request(req: dict[str, Any]) -> dict[str, Any] | None:
    """Process a single JSON-RPC 2.0 MCP message."""
    method = req.get("method", "")
    req_id = req.get("id")

    if method == "notifications/initialized" or req_id is None:
        return None

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "verispec-sdd", "version": __version__},
            },
        }

    if method == "ping":
        return {"jsonrpc": "2.0", "id": req_id, "result": {}}

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": MCP_TOOLS}}

    if method == "tools/call":
        params = req.get("params") or {}
        tool_name = str(params.get("name", ""))
        arguments = dict(params.get("arguments") or {})
        try:
            payload = _call_tool(tool_name, arguments)
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": json.dumps(payload)}],
                    "isError": False,
                },
            }
        except (SpecCompileError, SpecExpressionError, ValueError) as exc:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": json.dumps({"error": str(exc)})}],
                    "isError": True,
                },
            }

    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


def run_stdio_server(stdin: TextIO | None = None, stdout: TextIO | None = None) -> None:
    """Serve newline-delimited JSON-RPC 2.0 MCP requests over stdio."""
    in_stream = stdin if stdin is not None else sys.stdin
    out_stream = stdout if stdout is not None else sys.stdout
    for raw_line in in_stream:
        line = raw_line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            err = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": "Parse error"},
            }
            out_stream.write(json.dumps(err) + "\n")
            out_stream.flush()
            continue
        resp = handle_mcp_request(req)
        if resp is not None:
            out_stream.write(json.dumps(resp) + "\n")
            out_stream.flush()
