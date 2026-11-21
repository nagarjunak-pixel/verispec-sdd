import React, { useEffect, useState } from "react";

interface DiagnosticItem {
  code: string;
  severity: "ERROR" | "WARNING";
  state: string | null;
  event: string | null;
  message: string;
}

interface StateRow {
  state: string;
  is_initial: boolean;
  is_terminal: boolean;
  hops_from_initial: number;
  hops_to_terminal: number;
  scc_id: number;
  in_cycle: boolean;
  out_degree: number;
}

interface VerificationReport {
  spec_name: string;
  passed: boolean;
  backend: string;
  num_states: number;
  num_transitions: number;
  reachable_states: number;
  deadlock_states: string[];
  livelock_states: string[];
  unreachable_states: string[];
  nondeterministic_pairs: string[];
  shortest_witness_to_terminal: string[];
  diagnostics: DiagnosticItem[];
  state_table: StateRow[];
}

const DEFAULT_SPEC = `# Spec: GovernedEnterpriseOrderFlow
- **Version**: 1.0.0
- **Initial**: \`CREATED\`
- **Terminal**: \`COMPLETED\`, \`CANCELLED\`

## States
- \`CREATED\`: Order initialized [invariant: amount >= 0]
- \`PAYMENT_AUTHORIZED\`: Payment captured [invariant: paid == True]
- \`RISK_HOLD\`: High-risk order awaiting supervisor review
- \`FULFILLED\`: Warehouse shipment confirmed [invariant: paid == True]
- \`COMPLETED\`: Settlement finalized [invariant: paid == True]
- \`CANCELLED\`: Order cancelled or rejected

## Transitions
| From | Event | To | Guard | Postcondition |
| --- | --- | --- | --- | --- |
| CREATED | authorize_payment | PAYMENT_AUTHORIZED | amount > 0 and fraud_score < 80 | paid == True |
| CREATED | route_risk_hold | RISK_HOLD | fraud_score >= 80 | - |
| CREATED | cancel_order | CANCELLED | - | - |
| RISK_HOLD | approve_override | PAYMENT_AUTHORIZED | manual_override == True | paid == True |
| RISK_HOLD | reject_risk | CANCELLED | manual_override == False | - |
| PAYMENT_AUTHORIZED | confirm_shipment | FULFILLED | paid == True | - |
| PAYMENT_AUTHORIZED | cancel_order | CANCELLED | - | - |
| FULFILLED | finalize_settlement | COMPLETED | paid == True | - |

## Invariants
- \`non_negative_amount\`: \`amount >= 0\`
- \`completed_requires_payment\` [COMPLETED]: \`paid == True\`
`;

export function App(): React.JSX.Element {
  const [markdown, setMarkdown] = useState<string>(DEFAULT_SPEC);
  const [report, setReport] = useState<VerificationReport | null>(null);
  const [synthCode, setSynthCode] = useState<string>("");
  const [target, setTarget] = useState<"python" | "typescript">("python");
  const [error, setError] = useState<string | null>(null);
  const [presets, setPresets] = useState<{ buggy: string; governed: string } | null>(null);

  useEffect(() => {
    void fetch("/api/v1/demo")
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (data?.presets) {
          setPresets({
            buggy: data.presets.buggy_markdown,
            governed: data.presets.governed_markdown
          });
          setReport(data.governed_spec.report);
          setSynthCode(data.governed_spec.python_preview);
        }
      })
      .catch(() => null);
  }, []);

  async function runVerifyAndSynth(specMd: string, nextTarget: "python" | "typescript"): Promise<void> {
    setError(null);
    try {
      const [vRes, sRes] = await Promise.all([
        fetch("/api/v1/verify", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ markdown: specMd })
        }),
        fetch("/api/v1/synth", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ markdown: specMd, target: nextTarget })
        })
      ]);
      if (!vRes.ok) {
        const detail = await vRes.json();
        throw new Error(detail.detail ?? "Spec verification failed");
      }
      setReport((await vRes.json()) as VerificationReport);
      if (sRes.ok) {
        const sData = (await sRes.json()) as { code: string };
        setSynthCode(sData.code);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to reach VeriSpec-SDD API (:8200)");
    }
  }

  return (
    <div className="shell">
      <header className="topbar">
        <div>
          <span className="eyebrow">VeriSpec-SDD v1.0.0 · Port 8200 · C++20 CSR Automaton Prover</span>
          <h1>Executable SPEC.md State-Machine Verifier &amp; Contract Synthesizer</h1>
        </div>
        <div className="preset-actions">
          {presets && (
            <>
              <button
                type="button"
                className="btn-secondary"
                onClick={() => {
                  setMarkdown(presets.buggy);
                  void runVerifyAndSynth(presets.buggy, target);
                }}
              >
                Load Buggy Agent Spec
              </button>
              <button
                type="button"
                className="btn-secondary"
                onClick={() => {
                  setMarkdown(presets.governed);
                  void runVerifyAndSynth(presets.governed, target);
                }}
              >
                Load Governed Spec
              </button>
            </>
          )}
          <button
            type="button"
            className="btn-primary"
            onClick={() => void runVerifyAndSynth(markdown, target)}
          >
            Verify &amp; Synthesize
          </button>
        </div>
      </header>

      {error && <div className="alert-error">{error}</div>}

      <main className="grid">
        <section className="panel">
          <div className="panel-head">
            <h2>Executable SPEC.md Contract</h2>
            <div className="seg">
              <button
                type="button"
                className={target === "python" ? "active" : ""}
                onClick={() => {
                  setTarget("python");
                  void runVerifyAndSynth(markdown, "python");
                }}
              >
                Hypothesis (Python)
              </button>
              <button
                type="button"
                className={target === "typescript" ? "active" : ""}
                onClick={() => {
                  setTarget("typescript");
                  void runVerifyAndSynth(markdown, "typescript");
                }}
              >
                Vitest (TypeScript)
              </button>
            </div>
          </div>
          <textarea
            className="editor"
            value={markdown}
            onChange={(e) => setMarkdown(e.target.value)}
            spellCheck={false}
          />
        </section>

        <section className="panel">
          <div className="panel-head">
            <h2>C++20 Reachability, Co-Reachability &amp; Tarjan SCC Proof</h2>
            {report && (
              <span className={report.passed ? "badge pass" : "badge fail"}>
                {report.passed ? "FORMALLY SOUND" : "DEFECTS DETECTED"}
              </span>
            )}
          </div>

          {report ? (
            <>
              <div className="kpis">
                <div className="kpi">
                  <span>Reachable</span>
                  <strong>
                    {report.reachable_states}/{report.num_states}
                  </strong>
                </div>
                <div className="kpi">
                  <span>Deadlocks</span>
                  <strong>{report.deadlock_states.length}</strong>
                </div>
                <div className="kpi">
                  <span>Livelock Traps</span>
                  <strong>{report.livelock_states.length}</strong>
                </div>
                <div className="kpi">
                  <span>Kernel Backend</span>
                  <strong>{report.backend}</strong>
                </div>
              </div>

              {report.diagnostics.length > 0 && (
                <ul className="diag-list">
                  {report.diagnostics.map((d, idx) => (
                    <li key={idx} className={d.severity === "ERROR" ? "err" : "warn"}>
                      <strong>[{d.code}]</strong> {d.message}
                    </li>
                  ))}
                </ul>
              )}

              <table className="state-table">
                <thead>
                  <tr>
                    <th>State</th>
                    <th>Hops (Init)</th>
                    <th>Hops (Term)</th>
                    <th>SCC</th>
                    <th>Cycle</th>
                    <th>Out°</th>
                  </tr>
                </thead>
                <tbody>
                  {report.state_table.map((row) => (
                    <tr key={row.state}>
                      <td>
                        <code>{row.state}</code>
                        {row.is_initial ? " (init)" : ""}
                        {row.is_terminal ? " (term)" : ""}
                      </td>
                      <td>{row.hops_from_initial}</td>
                      <td>{row.hops_to_terminal}</td>
                      <td>#{row.scc_id}</td>
                      <td>{row.in_cycle ? "Yes" : "No"}</td>
                      <td>{row.out_degree}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          ) : (
            <p className="muted">Click Verify &amp; Synthesize to run the C++20 automaton prover.</p>
          )}

          <h3>Synthesized Property-Test Suite ({target})</h3>
          <pre className="code-box">{synthCode || "// Synthesized suite will appear here"}</pre>
        </section>
      </main>
    </div>
  );
}
