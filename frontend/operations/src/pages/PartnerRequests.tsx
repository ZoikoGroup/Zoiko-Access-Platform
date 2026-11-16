import { useCallback, useEffect, useState } from "react";
import { cancelExchangeRequest, getPatients, listExchangePartners, listExchangeRequests, sendExchangeRequest } from "../api";
import type { ExchangeRequest, ExchangeState, PatientRow } from "../types";
import { Card, Pill, type PillKind } from "../ui";

const KIND: Record<string, string> = {
  lab_order: "Laboratory or imaging order", pharmacy_request: "Pharmacy request", homecare_referral: "Home-care referral",
};
const STATE: Record<ExchangeState, { text: string; kind: PillKind }> = {
  requested: { text: "Sent, waiting for the partner", kind: "info" }, accepted: { text: "Accepted", kind: "info" },
  declined: { text: "Declined", kind: "bad" }, in_progress: { text: "In progress", kind: "info" },
  result_available: { text: "Result available: waiting for a doctor to review", kind: "warn" },
  reviewed: { text: "Reviewed by a doctor", kind: "ok" }, cancelled: { text: "Cancelled", kind: "info" },
};

function Send({ partners, patients, onDone }: { partners: { id: string; name: string }[]; patients: PatientRow[]; onDone: () => Promise<void> }) {
  const [partner, setPartner] = useState(partners[0]?.id ?? "");
  const [kind, setKind] = useState("lab_order");
  const [patient, setPatient] = useState(patients[0]?.id ?? "");
  const [label, setLabel] = useState("");
  const [token, setToken] = useState("");
  const [urgency, setUrgency] = useState("routine");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setError("");
    try {
      await sendExchangeRequest({ partner, kind, label, token, urgency, patient });
      setLabel(""); setToken("");
      await onDone();
    } catch (err) {
      setError((err as { code?: string }).code === "VALIDATION_FAILED"
        ? "Check the order number (3 to 40 letters, digits or . _ : -) and the description (plain words, no personal details)."
        : err instanceof Error ? err.message : "Could not send");
    } finally { setBusy(false); }
  }
  if (partners.length === 0) return <div className="empty">No partners are set up yet. Zoiko support adds a laboratory, pharmacy or home-care partner.</div>;
  return (
    <form className="profile-form" onSubmit={(e) => void submit(e)}>
      <label>Partner<select value={partner} onChange={(e) => setPartner(e.target.value)}>{partners.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label>
      <label>Kind of request<select value={kind} onChange={(e) => setKind(e.target.value)}>{Object.entries(KIND).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>
      <label>Patient (you keep this link: the partner is never told who it is)
        <select value={patient} onChange={(e) => setPatient(e.target.value)}>
          {patients.map((p) => <option key={p.id} value={p.id}>{p.patient_name || p.id}</option>)}
        </select>
      </label>
      <label>Order number (yours, not the patient's name)<input value={token} onChange={(e) => setToken(e.target.value)} required maxLength={40} /></label>
      <label>What is asked, in plain words<input value={label} onChange={(e) => setLabel(e.target.value)} required maxLength={80} placeholder="Full blood count" /></label>
      <label>How urgent<select value={urgency} onChange={(e) => setUrgency(e.target.value)}><option value="routine">Routine</option><option value="urgent">Urgent</option></select></label>
      <small>Only the order number, what is asked and how urgent it is travel to the partner: never a name, a diagnosis or a result.</small>
      {error && <div className="error" role="alert">{error}</div>}
      <div><button className="btn" type="submit" disabled={busy || !patient || !token.trim() || !label.trim()}>{busy ? "Sending…" : "Send request"}</button></div>
    </form>
  );
}

function Row({ r, onDone }: { r: ExchangeRequest; onDone: () => Promise<void> }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const state = STATE[r.state];
  async function cancel() {
    setBusy(true); setError("");
    try { await cancelExchangeRequest(r.id); await onDone(); }
    catch (e) { setError(e instanceof Error ? e.message : "That did not work"); }
    finally { setBusy(false); }
  }
  return (
    <div className="appt" style={{ flexDirection: "column", alignItems: "stretch" }}>
      <div className="info">
        <b>{r.label} <Pill kind={state.kind}>{state.text}</Pill>{r.urgency === "urgent" && <> <Pill kind="warn">urgent</Pill></>}{r.on_hold && <> <Pill kind="warn">on hold</Pill></>}</b>
        <small>{KIND[r.kind]} to {r.partner} · order {r.token} · {r.patient_name || r.patient} · {r.ref_no}</small>
        {r.state === "declined" && r.decision_note && <small>Reason given: {r.decision_note}</small>}
        {r.state === "result_available" && <small>Result reference {r.result_ref}. A result being ready is not the same as a doctor having read it.</small>}
        {r.on_hold && (r.state === "requested" || r.state === "accepted" || r.state === "in_progress") && <small>The partner cannot take this at the moment. Cancel it and ask elsewhere, or wait: it carries on if the partner comes back. {r.hold_mine ? "This one is yours to decide." : r.hold_owner_name ? `${r.hold_owner_name} sent it and decides.` : ""}</small>}
        {error && <div className="error" role="alert">{error}</div>}
      </div>
      {(r.state === "requested" || r.state === "accepted" || r.state === "in_progress") && (
        <div><button className="btn ghost" disabled={busy} onClick={() => void cancel()}>Cancel the request</button></div>
      )}
    </div>
  );
}

export function PartnerRequests() {
  const [rows, setRows] = useState<ExchangeRequest[] | null>(null);
  const [partners, setPartners] = useState<{ id: string; name: string }[] | null>(null);
  const [patients, setPatients] = useState<PatientRow[] | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      const [r, p, pt] = await Promise.all([listExchangeRequests(), listExchangePartners(), getPatients()]);
      setRows(r); setPartners(p); setPatients(pt); setError("");
    } catch (e) { setError(e instanceof Error ? e.message : "Could not load"); }
  }, []);
  useEffect(() => { void load(); }, [load]);

  return (
    <>
      <div className="hero"><div>
        <h1>Partner requests</h1>
        <p>Requests you send to laboratories, pharmacies and home-care partners, and where each one stands. Only an order number, a short description and the urgency leave the clinic; a result stays with the partner and only its reference comes back. A doctor marks a result as reviewed in the doctor app.</p>
      </div></div>
      {error && <div className="error" role="alert">{error}</div>}
      <div className="grid">
        <Card title={`Your requests (${rows?.length ?? 0})`}>
          {rows === null ? <div className="empty">Loading…</div> : rows.length === 0
            ? <div className="empty">Nothing sent yet.</div>
            : rows.map((r) => <Row key={r.id} r={r} onDone={load} />)}
        </Card>
        <Card title="Send a request">
          {partners === null || patients === null ? <div className="empty">Loading…</div>
            : <Send partners={partners} patients={patients} onDone={load} />}
        </Card>
      </div>
    </>
  );
}
