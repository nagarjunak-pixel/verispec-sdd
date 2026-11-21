"""End-to-end tests for FastAPI REST endpoints, Typer CLI commands, and stdio MCP server."""

from __future__ import annotations

import io
import json
from pathlib import Path

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from verispec.api import create_app
from verispec.cli import app as cli_app
from verispec.demo import BUGGY_ERP_ORDER_SPEC, GOVERNED_ERP_ORDER_SPEC
from verispec.mcp import handle_mcp_request, run_stdio_server

runner = CliRunner()


def test_fastapi_endpoints_and_prometheus_metrics() -> None:
    client = TestClient(create_app())

    h = client.get("/health")
    assert h.status_code == 200
    assert h.json()["status"] == "ok"

    c = client.post("/api/v1/compile", json={"markdown": GOVERNED_ERP_ORDER_SPEC})
    assert c.status_code == 200
    assert c.json()["name"] == "GovernedEnterpriseOrderFlow"

    bad = client.post("/compile", json={"markdown": "# EmptyOnly\nNo states."})
    assert bad.status_code == 422

    v_fail = client.post("/api/v1/verify", json={"markdown": BUGGY_ERP_ORDER_SPEC})
    assert v_fail.status_code == 200
    assert v_fail.json()["passed"] is False

    v_pass = client.post("/verify", json={"markdown": GOVERNED_ERP_ORDER_SPEC})
    assert v_pass.status_code == 200
    assert v_pass.json()["passed"] is True

    s_ts = client.post(
        "/api/v1/synth", json={"markdown": GOVERNED_ERP_ORDER_SPEC, "target": "typescript"}
    )
    assert s_ts.status_code == 200
    assert s_ts.json()["target"] == "typescript"

    tr = client.post(
        "/api/v1/check-trace",
        json={
            "markdown": GOVERNED_ERP_ORDER_SPEC,
            "steps": [{"event": "cancel_order", "context": {"amount": 100}}],
            "require_terminal": True,
        },
    )
    assert tr.status_code == 200
    assert tr.json()["valid"] is True

    demo = client.get("/api/v1/demo")
    assert demo.status_code == 200
    assert demo.json()["governed_spec"]["report"]["passed"] is True

    bench = client.get("/api/v1/bench?num_states=80&trace_len=500")
    assert bench.status_code == 200
    assert bench.json()["num_states"] == 80

    metrics = client.get("/metrics")
    assert metrics.status_code == 200
    assert "verispec_verifications_total 2" in metrics.text
    assert "verispec_failed_specs_total 1" in metrics.text


def test_typer_cli_commands(tmp_path: Path) -> None:
    gov_file = tmp_path / "governed.spec.md"
    gov_file.write_text(GOVERNED_ERP_ORDER_SPEC, encoding="utf-8")
    buggy_file = tmp_path / "buggy.spec.md"
    buggy_file.write_text(BUGGY_ERP_ORDER_SPEC, encoding="utf-8")

    r_comp = runner.invoke(cli_app, ["compile", str(gov_file)])
    assert r_comp.exit_code == 0
    assert "GovernedEnterpriseOrderFlow" in r_comp.stdout

    r_ver_ok = runner.invoke(cli_app, ["verify", str(gov_file)])
    assert r_ver_ok.exit_code == 0

    r_ver_json = runner.invoke(cli_app, ["verify", str(buggy_file), "--json"])
    assert r_ver_json.exit_code == 1
    assert '"passed": false' in r_ver_json.stdout

    r_ver_pretty_fail = runner.invoke(cli_app, ["verify", str(buggy_file)])
    assert r_ver_pretty_fail.exit_code == 1
    assert "DEADLOCK_STATE" in r_ver_pretty_fail.stdout

    out_test = tmp_path / "generated_test.py"
    r_synth_file = runner.invoke(
        cli_app, ["synth", str(gov_file), "-t", "python", "-o", str(out_test)]
    )
    assert r_synth_file.exit_code == 0
    assert out_test.exists()

    r_synth_stdout = runner.invoke(cli_app, ["synth", str(gov_file), "-t", "typescript"])
    assert r_synth_stdout.exit_code == 0
    assert "stepContract" in r_synth_stdout.stdout

    r_synth_bad = runner.invoke(cli_app, ["synth", str(gov_file), "-t", "golang"])
    assert r_synth_bad.exit_code != 0

    trace_ok = tmp_path / "trace_ok.json"
    trace_ok.write_text(
        json.dumps([{"event": "cancel_order", "context": {"amount": 25}}]), encoding="utf-8"
    )
    r_tr_ok = runner.invoke(
        cli_app, ["check-trace", str(gov_file), str(trace_ok), "--require-terminal"]
    )
    assert r_tr_ok.exit_code == 0

    trace_bad = tmp_path / "trace_bad.json"
    trace_bad.write_text(
        json.dumps([{"event": "finalize_settlement", "context": {"amount": 25}}]), encoding="utf-8"
    )
    r_tr_bad = runner.invoke(cli_app, ["check-trace", str(gov_file), str(trace_bad)])
    assert r_tr_bad.exit_code == 1

    r_demo = runner.invoke(cli_app, ["demo"])
    assert r_demo.exit_code == 0
    assert "VeriSpec-SDD" in r_demo.stdout

    r_demo_json = runner.invoke(cli_app, ["demo", "--json"])
    assert r_demo_json.exit_code == 0
    assert '"kernel_benchmark"' in r_demo_json.stdout

    r_bench = runner.invoke(cli_app, ["bench", "--states", "60", "--trace-len", "400"])
    assert r_bench.exit_code == 0
    assert '"num_states": 60' in r_bench.stdout


def test_mcp_stdio_jsonrpc_server() -> None:
    assert handle_mcp_request({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None

    init_resp = handle_mcp_request({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert init_resp is not None and init_resp["result"]["serverInfo"]["name"] == "verispec-sdd"

    ping_resp = handle_mcp_request({"jsonrpc": "2.0", "id": 2, "method": "ping"})
    assert ping_resp == {"jsonrpc": "2.0", "id": 2, "result": {}}

    list_resp = handle_mcp_request({"jsonrpc": "2.0", "id": 3, "method": "tools/list"})
    assert list_resp is not None and len(list_resp["result"]["tools"]) == 4

    cases: list[tuple[str, dict[str, object]]] = [
        ("verispec_compile_spec", {}),
        ("verispec_verify_spec", {}),
        ("verispec_synthesize_tests", {"target": "typescript"}),
        (
            "verispec_check_trace",
            {
                "steps": [{"event": "cancel_order", "context": {"amount": 10}}],
                "require_terminal": True,
            },
        ),
    ]
    for idx, (tool_name, extra_args) in enumerate(cases, start=10):
        resp = handle_mcp_request(
            {
                "jsonrpc": "2.0",
                "id": idx,
                "method": "tools/call",
                "params": {
                    "name": tool_name,
                    "arguments": {"markdown": GOVERNED_ERP_ORDER_SPEC, **extra_args},
                },
            }
        )
        assert resp is not None
        assert resp["result"]["isError"] is False

    err_tool = handle_mcp_request(
        {
            "jsonrpc": "2.0",
            "id": 99,
            "method": "tools/call",
            "params": {"name": "unknown_tool", "arguments": {"markdown": GOVERNED_ERP_ORDER_SPEC}},
        }
    )
    assert err_tool is not None and err_tool["result"]["isError"] is True

    not_found = handle_mcp_request({"jsonrpc": "2.0", "id": 100, "method": "resources/list"})
    assert not_found is not None and not_found["error"]["code"] == -32601

    raw_in = io.StringIO(
        "\n"
        "not-json\n"
        + json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"})
        + "\n"
        + json.dumps({"jsonrpc": "2.0", "id": 7, "method": "ping"})
        + "\n"
    )
    raw_out = io.StringIO()
    run_stdio_server(stdin=raw_in, stdout=raw_out)
    lines = [json.loads(line) for line in raw_out.getvalue().strip().splitlines()]
    assert len(lines) == 2
    assert lines[0]["error"]["code"] == -32700
    assert lines[1]["id"] == 7

    r_mcp_cli = runner.invoke(
        cli_app, ["mcp"], input=json.dumps({"jsonrpc": "2.0", "id": 8, "method": "ping"}) + "\n"
    )
    assert r_mcp_cli.exit_code == 0
    assert '"id": 8' in r_mcp_cli.stdout
