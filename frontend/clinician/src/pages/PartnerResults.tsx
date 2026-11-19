import { useEffect, useState } from "react";
import { getPartnerRequests, getPartners, getPatients, getResultSummary, placeOrder, reviewPartnerResult, type PartnerChoice } from "../api";
import type { PartnerRequest, PatientRow, ResultSummary, ResultValue } from "../types";
import { Card, Pill } from "../ui";
import { Gate, useLoad } from "../useLoad";

const KIND: Record<string, string> = {
  lab_order: "Laboratory or imaging order", pharmacy_request: "Pharmacy request", homecare_referral: "Home-care referral",
};
const STATE: Record<PartnerRequest["state"], { text: string; kind: "ok" | "warn" | "info" | "bad" }> = {
  requested: { text: "Sent, waiting for the partner", kind: "info" }, accepted: { text: "Accepted", kind: "info" },
  declined: { text: "Declined", kind: "bad" }, in_progress: { text: "In progress", kind: "info" },
  result_available: { text: "Result ready: waiting for you", kind: "warn" },
  reviewed: { text: "Reviewed", kind: "ok" }, cancelled: { text: "Cancelled", kind: "info" },
};

function Row({ r, onChanged }: { r: PartnerRequest; onChanged: () => void }) {
  const [busy, setBusy] = useState(false);
  const [summary, setSummary] = useState<ResultSummary | null>(null);
  async function open() {
    setBusy(true); setError("");
    try { setSummary(await getResultSummary(r.id)); }
    catch (err) { setError(err instanceof Error ? err.message : "Could not open the result"); }
    finally { setBusy(false); }
  }
  const [error, setError] = useState("");
  async function review() {
    setBusy(true); setError("");
    try { await reviewPartnerResult(r.id); onChanged(); }
    catch (err) {
      const code = (err as { code?: string }).code;
      setError(code === "POLICY_DENIED" ? "You can only review while you are treating this patient (not when following up read only)."
        : code === "CONFLICT_VERSION" ? "There is no ready result to review."
        : err instanceof Error ? err.message : "That did not work");
    } finally { setBusy(false); }
  }
  const state = STATE[r.state];
  return (
    <div className="appt" style={{ flexDirection: "column", alignItems: "stretch" }}>
      <div className="info">
        <b>{r.label} <Pill kind={state.kind}>{state.text}</Pill>{r.urgency === "urgent" && <> <Pill kind="warn">urgent</Pill></>}{r.result_flag === "critical" && <> <Pill kind="bad">CRITICAL RESULT</Pill></>}{r.result_flag === "abnormal" && <> <Pill kind="warn">abnormal</Pill></>}{(r.result_version ?? 0) > 1 && <> <Pill kind="warn">{r.result_correction ?? "corrected"}: version {r.result_version}</Pill></>}</b>
        <small>{KIND[r.kind]} · {r.partner} · {r.patient_name || r.patient} · order {r.token}</small>
        {r.result_ref && <small>The partner holds the result under reference {r.result_ref}. Opening it is done with the partner, outside this screen.</small>}
        {r.state === "declined" && r.decision_note && <small>Reason given: {r.decision_note}</small>}
        {summary && (
          <div role="region" aria-label="Result summary">
            <b>Lab summary ({summary.flag}){(summary.version ?? 0) > 1 ? `, version ${summary.version} (${summary.correction})` : ""}:</b>
            {summary.summary && <pre style={{ whiteSpace: "pre-wrap", margin: "4px 0 0", fontFamily: "inherit" }}>{summary.summary}</pre>}
            <Values values={summary.values} />
            {(summary.earlier ?? []).map((v) => (
              <details key={v.version} style={{ marginTop: 6 }}>
                <summary>Earlier version {v.version} ({v.flag}), replaced {v.replaced_at.slice(0, 10)}{v.reviewed ? ", it had been reviewed" : ""}</summary>
                <pre style={{ whiteSpace: "pre-wrap", margin: "4px 0 0", fontFamily: "inherit" }}>{v.summary}</pre>
                <Values values={v.values} />
              </details>
            ))}
          </div>
        )}
        {error && <div className="error" role="alert">{error}</div>}
      </div>
      {(r.has_summary || r.has_values) && !summary && (r.state === "result_available" || r.state === "reviewed") && (
        <div><button className="btn ghost" disabled={busy} onClick={() => void open()}>Read the lab's summary</button>
          <small style={{ display: "block", marginTop: 4 }}>Opening it is recorded.</small></div>
      )}
      {r.state === "result_available" && (
        <div>
          <button className="btn" disabled={busy} onClick={() => void review()}>I have reviewed this result</button>
          <small style={{ display: "block", marginTop: 4 }}>This records that you, a clinician, have reviewed it. A result being ready is never treated as reviewed.</small>
        </div>
      )}
    </div>
  );
}

function NewOrder({ onSent }: { onSent: (message: string) => void }) {
  const [partners, setPartners] = useState<PartnerChoice[]>([]);
  const [patients, setPatients] = useState<PatientRow[]>([]);
  const [f, setF] = useState({ patient: "", partner: "", kind: "lab_order", label: "", urgency: "routine", order_code: "" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    getPartners().then(setPartners).catch(() => setPartners([]));
    getPatients().then(setPatients).catch(() => setPatients([]));
  }, []);
  const set = (k: string, v: string) => setF((cur) => ({ ...cur, [k]: v }));
  async function send() {
    setBusy(true); setError("");
    const token = `DR-${new Date().toISOString().slice(2, 10).replace(/-/g, "")}-${Math.random().toString(36).slice(2, 6).toUpperCase()}`;
    try {
      await placeOrder({ ...f, token });
      onSent("Order sent. The patient has been asked to agree to share their name with the service; it cannot start until they do.");
    } catch (e) { setError(e instanceof Error ? e.message : "That did not work"); }
    finally { setBusy(false); }
  }
  const ready = f.patient && f.partner && f.label.trim().length >= 3;
  return (
    <Card title="Place an order">
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        <label>Patient{" "}
          <select aria-label="Order patient" value={f.patient} onChange={(e) => set("patient", e.target.value)}>
            <option value="">Choose a patient</option>
            {patients.map((p) => <option key={p.id} value={p.id}>{p.patient_name || p.id}</option>)}
          </select>
        </label>
        <label>Send to{" "}
          <select aria-label="Order service" value={f.partner} onChange={(e) => set("partner", e.target.value)}>
            <option value="">Choose a service</option>
            {partners.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
        </label>
        <label>Kind{" "}
          <select value={f.kind} onChange={(e) => set("kind", e.target.value)}>
            <option value="lab_order">Laboratory or imaging order</option>
            <option value="pharmacy_request">Pharmacy request</option>
            <option value="homecare_referral">Home-care referral</option>
          </select>
        </label>
        <label>What is asked <input className="field" value={f.label} maxLength={80} onChange={(e) => set("label", e.target.value)} placeholder="For example: Full blood count" /></label>
        <label>SNOMED CT code (optional) <input className="field" value={f.order_code} maxLength={18} onChange={(e) => set("order_code", e.target.value.trim())} placeholder="For example: 26604007" /></label>
        <small>With a code, the order also goes to the lab's own system once the patient agrees; without one, the lab works in the partner app.</small>
        <label>Urgency{" "}
          <select value={f.urgency} onChange={(e) => set("urgency", e.target.value)}>
            <option value="routine">Routine</option><option value="urgent">Urgent</option>
          </select>
        </label>
        <small>Write only what is asked. Do not put a name, a number that identifies the patient, or clinical details here.</small>
        {error && <div className="error" role="alert">{error}</div>}
        <div><button className="btn" disabled={busy || !ready} onClick={() => void send()}>Send order</button></div>
      </div>
    </Card>
  );
}

export function PartnerResults() {
  const [n, setN] = useState(0);              // changing the key loads everything again
  const [notice, setNotice] = useState("");   // kept here so it survives the reload
  return <PartnerResultsInner key={n} notice={notice} onChanged={() => setN(n + 1)}
    onOrdered={(message) => { setNotice(message); setN(n + 1); }} />;
}

function PartnerResultsInner({ onChanged, onOrdered, notice }: { onChanged: () => void; onOrdered: (message: string) => void; notice: string }) {
  const list = useLoad(getPartnerRequests, 30_000);
  const waiting = (list.data ?? []).filter((r) => r.state === "result_available").length;
  return (
    <>
      <div className="hero"><div>
        <h1>Orders &amp; results</h1>
        <p>Requests your clinic sent to laboratories, pharmacies and home-care partners for your patients, and where each stands. {waiting > 0 ? `${waiting} ready for you to review.` : ""}</p>
      </div></div>
      <div className="grid">
        {notice && <div className="notice" role="status">{notice}</div>}
        <NewOrder onSent={onOrdered} />
        <Card title="Requests for your patients">
          <Gate result={list}>
            {(rows) => rows.length === 0 ? <div className="empty">Nothing sent for your patients yet.</div> : rows.map((r) => <Row key={r.id} r={r} onChanged={onChanged} />)}
          </Gate>
        </Card>
      </div>
    </>
  );
}


const FLAG_KIND: Record<string, "ok" | "warn" | "bad"> = { normal: "ok", high: "warn", low: "warn", abnormal: "warn", critical: "bad" };

/** The lab's measured values (DEV-330): value, unit, normal range and flag. */
function Values({ values }: { values?: ResultValue[] }) {
  if (!values || values.length === 0) return null;
  return (
    <table aria-label="Measured values" style={{ width: "100%", fontSize: 13, marginTop: 6 }}>
      <thead><tr><th align="left">Test</th><th align="right">Value</th><th align="left">Unit</th><th align="left">Normal range</th><th align="left">Flag</th></tr></thead>
      <tbody>
        {values.map((v, i) => (
          <tr key={i}>
            <td>{v.name}</td>
            <td align="right"><b>{String(v.value)}</b></td>
            <td>{v.unit ?? ""}</td>
            <td>{v.low != null || v.high != null ? `${v.low ?? ""} to ${v.high ?? ""}` : ""}</td>
            <td>{v.flag ? <Pill kind={FLAG_KIND[v.flag] ?? "warn"}>{v.flag}</Pill> : ""}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
