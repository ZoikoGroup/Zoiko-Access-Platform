import { useEffect, useState } from "react";
import { getIncoming, getOrderSystem, incomingStep, setOrderSystem, type OrderSystem } from "../api";
import { ValuesEditor, toValues, type ValueRow } from "./ResultValues";
import type { Incoming, IncomingState } from "../types";
import { Card, Pill } from "../ui";
import { Gate, useLoad } from "../useLoad";

const KIND: Record<string, string> = {
  lab_order: "Laboratory or imaging order", pharmacy_request: "Pharmacy request", homecare_referral: "Home-care referral",
};
const STATE: Record<IncomingState, { text: string; kind: "ok" | "warn" | "info" | "bad" }> = {
  requested: { text: "New", kind: "warn" }, accepted: { text: "Accepted", kind: "info" },
  declined: { text: "Declined", kind: "bad" }, in_progress: { text: "In progress", kind: "info" },
  result_available: { text: "Delivered", kind: "ok" }, reviewed: { text: "Delivered and reviewed", kind: "ok" },
  cancelled: { text: "Cancelled by the clinic", kind: "info" },
};

function Row({ r, onChanged }: { r: Incoming; onChanged: () => void }) {
  const [note, setNote] = useState("");
  const [result, setResult] = useState("");
  const [summary, setSummary] = useState("");
  const [flag, setFlag] = useState("normal");
  const [correcting, setCorrecting] = useState(false);
  const [rows, setRows] = useState<ValueRow[]>([]);
  const [correction, setCorrection] = useState("corrected");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function run(step: "accept" | "decline" | "start" | "deliver" | "correct") {
    setBusy(true); setError("");
    try {
      await incomingStep(r.id, step, step === "decline" ? { note }
        : step === "deliver" ? { result_ref: result, ...(summary.trim() || toValues(rows).length ? { flag } : {}), ...(summary.trim() ? { summary } : {}), ...(toValues(rows).length ? { values: toValues(rows) } : {}) }
        : step === "correct" ? { result_ref: result, summary, flag, correction, values: toValues(rows) } : {});
      onChanged();
    } catch (err) {
      const code = (err as { code?: string }).code;
      setError(code === "VALIDATION_FAILED" ? (step === "decline" ? "Give a reason (3 to 200 characters)." : "Give the reference of your result (3 to 40 letters, digits or . _ : -).")
        : code === "CONFLICT_VERSION" ? "That step is not possible now: the request has moved on."
        : code === "POLICY_DENIED" ? "Your role does not handle this kind of request."
        : err instanceof Error ? err.message : "That did not work");
    } finally { setBusy(false); }
  }
  const state = STATE[r.state];
  return (
    <div className="appt" style={{ flexDirection: "column", alignItems: "stretch" }}>
      <div className="info">
        <b>{r.label} <Pill kind={state.kind}>{state.text}</Pill>{r.urgency === "urgent" && <> <Pill kind="warn">urgent</Pill></>}{r.on_hold && <> <Pill kind="warn">on hold</Pill></>}</b>
        <small>{KIND[r.kind]} from {r.from} · order {r.token} · {r.ref_no}</small>
        {r.state === "declined" && r.decision_note && <small>Your reason: {r.decision_note}</small>}
        {r.result_ref && <small>Your result reference: {r.result_ref}{(r.result_version ?? 0) > 1 ? ` · version ${r.result_version} (${r.result_correction})` : ""}</small>}
        {r.identity && <small>Patient (shared with their agreement, for this request only): <b>{r.identity.name}</b>{r.identity.birth_date ? `, born ${r.identity.birth_date}` : ""}. Do not keep or use it for anything else.</small>}
        {!r.identity && r.consent !== "declined" && r.consent !== "withdrawn" && <small>Waiting for the patient to agree to tell you who they are. You cannot accept or start until they do.</small>}
        {(r.consent === "withdrawn" || r.consent === "declined") && <small>The patient did not agree, or took their agreement back. Nothing about them is shown.</small>}
        {r.on_hold && <small>This request is on hold for now. Nothing is needed from you until it is released.</small>}
        {error && <div className="error" role="alert">{error}</div>}
      </div>
      {r.state === "requested" && !r.on_hold && r.consent === "granted" && (
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <button className="btn" disabled={busy} onClick={() => void run("accept")}>Accept</button>
          <input className="field" aria-label={`Reason for declining ${r.token}`} placeholder="Reason, if you decline" value={note} onChange={(e) => setNote(e.target.value)} style={{ flex: 1, minWidth: 200 }} />
          <button className="btn ghost" disabled={busy || note.trim().length < 3} onClick={() => void run("decline")}>Decline</button>
        </div>
      )}
      {r.state === "accepted" && !r.on_hold && r.consent === "granted" && <div><button className="btn" disabled={busy} onClick={() => void run("start")}>Start work</button></div>}
      {r.state === "in_progress" && !r.on_hold && r.consent === "granted" && (
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <input className="field" aria-label={`Result reference for ${r.token}`} placeholder="Your reference for the result" value={result} onChange={(e) => setResult(e.target.value)} style={{ flex: 1, minWidth: 200 }} />
          <label style={{ flexBasis: "100%" }}>Short summary of the result (optional, up to 2000 characters)
            <textarea className="field" rows={4} maxLength={2000} value={summary} onChange={(e) => setSummary(e.target.value)} style={{ width: "100%" }} />
          </label>
          <label>Rating{" "}
            <select value={flag} onChange={(e) => setFlag(e.target.value)} aria-label={`Rating for ${r.token}`}>
              <option value="normal">Normal</option><option value="abnormal">Abnormal</option><option value="critical">Critical</option>
            </select>
          </label>
          <ValuesEditor rows={rows} onChange={setRows} label={`Measured values for ${r.token}`} />
          <small style={{ flexBasis: "100%" }}>Only the ordering clinic's treating doctor can read the summary and the values. A critical rating stands out to them. Do not put the patient's name in them.</small>
          <button className="btn" disabled={busy || result.trim().length < 3} onClick={() => void run("deliver")}>Mark the result as available</button>
        </div>
      )}
      {(r.state === "result_available" || r.state === "reviewed") && !correcting && (
        <div><button className="btn ghost" onClick={() => { setCorrecting(true); setResult(r.result_ref); }}>Send a correction</button></div>
      )}
      {(r.state === "result_available" || r.state === "reviewed") && correcting && (
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }} role="region" aria-label={`Correction for ${r.token}`}>
          <label>What changed{" "}
            <select value={correction} onChange={(e) => setCorrection(e.target.value)} aria-label={`Kind of correction for ${r.token}`}>
              <option value="corrected">Corrected (the result was wrong)</option>
              <option value="amended">Amended (changed after more work)</option>
              <option value="appended">Added to (more information)</option>
            </select>
          </label>
          <input className="field" aria-label={`New result reference for ${r.token}`} placeholder="Your reference for the new version" value={result} onChange={(e) => setResult(e.target.value)} style={{ flex: 1, minWidth: 200 }} />
          <label style={{ flexBasis: "100%" }}>The full new summary (it replaces the old one, which stays on record)
            <textarea className="field" rows={4} maxLength={2000} value={summary} onChange={(e) => setSummary(e.target.value)} style={{ width: "100%" }} />
          </label>
          <label>Rating{" "}
            <select value={flag} onChange={(e) => setFlag(e.target.value)} aria-label={`New rating for ${r.token}`}>
              <option value="normal">Normal</option><option value="abnormal">Abnormal</option><option value="critical">Critical</option>
            </select>
          </label>
          <ValuesEditor rows={rows} onChange={setRows} label={`New measured values for ${r.token}`} />
          <small style={{ flexBasis: "100%" }}>The doctor is asked to review the result again. The earlier version, with its values, is kept exactly as you sent it.</small>
          <button className="btn" disabled={busy || result.trim().length < 3} onClick={() => void run("correct")}>Send the correction</button>
          <button className="btn ghost" disabled={busy} onClick={() => setCorrecting(false)}>Cancel</button>
        </div>
      )}
    </div>
  );
}

export function Requests() {
  const [n, setN] = useState(0);              // changing the key loads everything again
  return <RequestsInner key={n} onChanged={() => setN(n + 1)} />;
}

function RequestsInner({ onChanged }: { onChanged: () => void }) {
  const list = useLoad(getIncoming, 30_000);
  return (
    <>
      <div className="hero"><div>
        <h1>Incoming requests</h1>
        <p>Requests clinics have sent you. You are told an order number, what is asked and how urgent it is: never who the patient is. A result stays with you: you only say that it is available and give your own reference for it.</p>
      </div></div>
      <OrderSystemCard />
      <div className="grid">
        <Card title="Requests">
          <Gate result={list}>
            {(rows) => rows.length === 0 ? <div className="empty">No requests yet.</div> : rows.map((r) => <Row key={r.id} r={r} onChanged={onChanged} />)}
          </Gate>
        </Card>
      </div>
    </>
  );
}


/** Which system this partner receives coded orders in (DEV-323). Only systems Zoiko has put live. */
export function OrderSystemCard() {
  const [data, setData] = useState<OrderSystem | null>(null);
  const [error, setError] = useState("");
  const load = () => getOrderSystem().then(setData).catch((e: unknown) => setError(e instanceof Error ? e.message : "Could not load"));
  useEffect(() => { void load(); }, []);
  async function choose(system: string) {
    setError("");
    try { setData(await setOrderSystem(system)); }
    catch (e) { setError(e instanceof Error ? e.message : "That did not work"); }
  }
  return (
    <div className="card" style={{ marginBottom: 12 }}>
      <label>Receive coded orders in{" "}
        <select aria-label="Order system" value={data?.system ?? ""} onChange={(e) => void choose(e.target.value)}>
          <option value="">This app only</option>
          {(data?.choices ?? []).map((c) => <option key={c.key} value={c.key}>{c.key}{c.simulated ? " (pretend, development only)" : ""}</option>)}
        </select>
      </label>
      <small style={{ display: "block" }}>An order is sent to your system only when it has a SNOMED CT code and the patient has agreed. It never carries the patient's name.</small>
      {error && <div className="error" role="alert">{error}</div>}
    </div>
  );
}
