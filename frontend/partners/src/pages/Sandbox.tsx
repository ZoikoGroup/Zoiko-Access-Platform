import { useEffect, useState } from "react";
import { checkSandboxMessage, getSandbox, type SandboxCheck, type SandboxPatient } from "../api";
import { Card, Pill } from "../ui";

const SCENARIO: Record<string, string> = {
  normal: "A normal result", abnormal: "An abnormal result", critical: "A critical result (alerts the doctor)",
  corrected: "A corrected result (a new version)", held: "A message the intake holds (no SNOMED CT code)",
};

/** A developer's sandbox (DEV-329): synthetic patients, example messages, and a message checker. Nothing is real or stored. */
export function Sandbox() {
  const [patients, setPatients] = useState<SandboxPatient[] | null>(null);
  const [open, setOpen] = useState<string>("");
  const [text, setText] = useState("");
  const [check, setCheck] = useState<SandboxCheck | null>(null);
  const [error, setError] = useState("");
  useEffect(() => { getSandbox().then(setPatients).catch((e: unknown) => setError(e instanceof Error ? e.message : "Could not load")); }, []);
  async function run() {
    setError(""); setCheck(null);
    try { setCheck(await checkSandboxMessage(text)); }
    catch (e) { setError((e as { code?: string }).code === "VALIDATION_FAILED" ? "The message is too large (64 KB at most)." : e instanceof Error ? e.message : "That did not work"); }
  }
  return (
    <>
      <Card title="Sandbox: synthetic patients">
        <p>Made-up patients for building and testing your connection. None is a real person and nothing here is stored. Each comes with the FHIR order Zoiko would send you and an example report you could send back, built with the same rules as the real intake (FHIR R4, UK Core).</p>
        {error && <div className="error" role="alert">{error}</div>}
        {(patients ?? []).map((p) => (
          <div key={p.id} className="appt" style={{ flexDirection: "column", alignItems: "stretch" }}>
            <div className="info">
              <b>{p.name} <Pill kind={p.expect === "held" ? "warn" : "ok"}>{p.expect === "held" ? "would be held" : "would be accepted"}</Pill></b>
              <small>{p.id} · born {p.birth_date} · {SCENARIO[p.scenario] ?? p.scenario}</small>
            </div>
            {open === p.id ? (
              <div>
                <b>Order Zoiko sends (ServiceRequest)</b>
                <pre style={{ whiteSpace: "pre-wrap", fontSize: 12 }}>{JSON.stringify(p.order, null, 2)}</pre>
                <b>Example report back (DiagnosticReport)</b>
                <pre style={{ whiteSpace: "pre-wrap", fontSize: 12 }}>{JSON.stringify(p.report, null, 2)}</pre>
                <button className="btn ghost" onClick={() => setText(JSON.stringify(p.report, null, 2))}>Copy the report into the checker</button>{" "}
                <button className="btn ghost" onClick={() => setOpen("")}>Hide</button>
              </div>
            ) : <div><button className="btn ghost" onClick={() => setOpen(p.id)}>Show the messages</button></div>}
          </div>
        ))}
      </Card>
      <Card title="Check a message">
        <p>Paste a FHIR resource your system produces. You will see whether Zoiko's intake would accept it or hold it for a person, and why. It is checked, then forgotten. Use synthetic data only.</p>
        <textarea className="field" aria-label="FHIR resource to check" rows={10} value={text} onChange={(e) => setText(e.target.value)} style={{ width: "100%", fontFamily: "monospace" }} />
        <div style={{ marginTop: 8 }}><button className="btn" disabled={!text.trim()} onClick={() => void run()}>Check it</button></div>
        {check && (
          <div role="status" style={{ marginTop: 8 }}>
            <Pill kind={check.result === "accepted" ? "ok" : "warn"}>{check.result === "accepted" ? "Would be accepted" : "Would be held"}</Pill>
            {check.issues.length > 0 && <ul>{check.issues.map((i, n) => <li key={n}><code>{i.path || "(resource)"}</code>: {i.text}</li>)}</ul>}
          </div>
        )}
      </Card>
    </>
  );
}
