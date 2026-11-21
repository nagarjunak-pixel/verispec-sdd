"""Model Context Protocol (MCP) stdio JSON-RPC 2.0 server for VeriSpec-SDD."""

from __future__ import annotations

from verispec.mcp.server import handle_mcp_request, run_stdio_server

__all__ = ["handle_mcp_request", "run_stdio_server"]
