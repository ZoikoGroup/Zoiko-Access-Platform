import { useCallback, useEffect, useState } from "react";
import { deleteClaimAttachment, downloadClaimAttachmentSent, getPatients, listClaimPayers, listClaims, sendClaim, uploadClaimAttachment, withdrawClaim } from "../api";
import type { Claim, ClaimState, PatientRow } from "../types";
import { Card, Pill, type PillKind } from "../ui";

const STATE: Record<ClaimState, { text: string; kind: PillKind }> = {
  submitted: { text: "Sent, waiting for the payer", kind: "info" }, approved: { text: "Approved", kind: "ok" },
  declined: { text: "Declined", kind: "bad" }, paid: { text: "Paid", kind: "ok" }, withdrawn: { text: "Withdrawn", kind: "info" },
};
const REASON: Record<string, string> = {
  not_covered: "not covered", member_not_found: "member not found", duplicate: "duplicate of another claim",
  missing_information: "information missing", other: "other",
};
const money = (minor: number, currency: string) => `${(minor / 100).toFixed(2)} ${currency}`;
const today = () => new Date().toISOString().slice(0, 10);

function Row({ c, onDone }: { c: Claim; onDone: () => Promise<void> }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [uploading, setUploading] = useState(false);

  async function withdraw() {
    setBusy(true); setError("");
    try { await withdrawClaim(c.id); await onDone(); }
    catch (e) { setError(e instanceof Error ? e.message : "That did not work"); }
    finally { setBusy(false); }
  }

  async function handleUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploading(true); setError("");
    try {
      await uploadClaimAttachment(c.id, file);
      e.target.value = "";
      await onDone();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Upload failed");
    } finally {
      setUploading(false);
    }
  }

  async function handleDeleteAttachment(attId: string) {
    setBusy(true); setError("");
    try {
      await deleteClaimAttachment(c.id, attId);
      await onDone();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Delete failed");
    } finally {
      setBusy(false);
    }
  }

  const state = STATE[c.state];
  return (
    <div className="appt" style={{ flexDirection: "column", alignItems: "stretch" }}>
      <div className="info">
        <b>{c.label} <Pill kind={state.kind}>{state.text}</Pill></b>
        <small>{c.payer} · {c.patient_name || c.patient} · member …{c.member_tail} · {money(c.amount_minor, c.currency)} · service on {c.service_date} · {c.claim_no}</small>
        {c.state === "declined" && c.decision_reason && <small>Reason given: {REASON[c.decision_reason] ?? c.decision_reason}</small>}
        {c.state === "paid" && c.payment_ref && <small>Payment reference {c.payment_ref}{c.amount_paid_minor != null ? `, paid ${(c.amount_paid_minor / 100).toFixed(2)} ${c.currency}${c.amount_paid_minor < c.amount_minor ? " (less than claimed)" : ""}` : ""}{c.paid_on ? ` on ${c.paid_on}` : ""}</small>}
        {c.remittance_advice && <small><b>Remittance advice:</b> {c.remittance_advice}</small>}
        {c.attachments && c.attachments.length > 0 && (
          <div style={{ marginTop: 6 }}>
            <b>Supporting documents ({c.attachments.length})</b>
            <ul style={{ margin: "4px 0" }}>
              {c.attachments.map((a) => (
                <li key={a.id} style={{ margin: "2px 0" }}>
                  {a.scan === "clean" ? (
                    <button className="btn ghost" disabled={busy} onClick={() => void downloadClaimAttachmentSent(c.id, a).catch((e: unknown) => setError(e instanceof Error ? e.message : "Download failed"))}>
                      Download {a.filename}
                    </button>
                  ) : (
                    <span>{a.filename} ({a.scan === "pending" ? "waiting for virus scan" : "virus found; blocked"})</span>
                  )}
                  <small> ({Math.ceil(a.size / 1024)} KB)</small>
                  {c.state === "submitted" && (
                    <button className="btn ghost" style={{ marginLeft: 8 }} disabled={busy} onClick={() => void handleDeleteAttachment(a.id)}>Remove</button>
                  )}
                </li>
              ))}
            </ul>
          </div>
        )}
        {error && <div className="error" role="alert">{error}</div>}
      </div>
      <div style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 4 }}>
        {c.state === "submitted" && (
          <>
            {(c.attachments?.length ?? 0) < 3 && (
              <label className="btn ghost" style={{ cursor: busy || uploading ? "default" : "pointer" }}>
                {uploading ? "Uploading…" : "Add supporting document (PDF, PNG, JPEG)"}
                <input type="file" accept=".pdf,image/png,image/jpeg" disabled={busy || uploading} onChange={(e) => void handleUpload(e)} style={{ display: "none" }} />
              </label>
            )}
            <button className="btn ghost" disabled={busy || uploading} onClick={() => void withdraw()}>Withdraw the claim</button>
          </>
        )}
      </div>
    </div>
  );
}

function Send({ payers, patients, onDone }: { payers: { id: string; name: string }[]; patients: PatientRow[]; onDone: () => Promise<void> }) {
  const [f, setF] = useState({ payer: payers[0]?.id ?? "", patient: patients[0]?.id ?? "", member_id: "", category: "general_physician", label: "", amount: "", currency: "GBP", service_date: today() });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const set = (k: string, v: string) => setF((cur) => ({ ...cur, [k]: v }));
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setError("");
    const pence = Math.round(Number(f.amount) * 100);
    try {
      await sendClaim({ ...f, amount: pence });
      setF((cur) => ({ ...cur, label: "", amount: "", member_id: "" })); await onDone();
    } catch (err) {
      const code = (err as { code?: string }).code;
      setError(code === "VALIDATION_FAILED" ? "Please check the member id, the service, the amount and the date."
        : code === "CONFLICT_VERSION" ? "A claim for this patient, service and day is already open."
        : code === "POLICY_DENIED" || code === "SUBJECT_LINK_UNVERIFIED" ? "You cannot claim for this patient."
        : err instanceof Error ? err.message : "That did not work");
    } finally { setBusy(false); }
  }
  return (
    <form onSubmit={(e) => void submit(e)} style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <label>Payer
        <select value={f.payer} onChange={(e) => set("payer", e.target.value)}>
          {payers.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
        </select>
      </label>
      <label>Patient
        <select value={f.patient} onChange={(e) => set("patient", e.target.value)}>
          {patients.map((p) => <option key={p.id} value={p.id}>{p.patient_name || p.id}</option>)}
        </select>
      </label>
      <label>Member id (the payer's number for the patient)<input value={f.member_id} onChange={(e) => set("member_id", e.target.value)} required maxLength={40} /></label>
      <label>Kind of service (as the plan lists it)<input value={f.category} onChange={(e) => set("category", e.target.value)} required maxLength={40} /></label>
      <label>What was done (a short plain description)<input value={f.label} onChange={(e) => set("label", e.target.value)} required maxLength={80} /></label>
      <label>Amount<input type="number" min="0.01" step="0.01" value={f.amount} onChange={(e) => set("amount", e.target.value)} required /></label>
      <label>Currency<input value={f.currency} onChange={(e) => set("currency", e.target.value.toUpperCase())} required maxLength={3} /></label>
      <label>Date of the service<input type="date" value={f.service_date} max={today()} onChange={(e) => set("service_date", e.target.value)} required /></label>
      <small>The payer sees only the member id, the service, the amount and the date. It never sees the patient's name or any clinical detail. Do not write one in the description.</small>
      {error && <div className="error" role="alert">{error}</div>}
      <div><button className="btn" type="submit" disabled={busy || !f.payer || !f.patient || !f.member_id.trim() || !f.label.trim() || !f.amount}>{busy ? "Sending…" : "Send claim"}</button></div>
    </form>
  );
}

export function Claims() {
  const [rows, setRows] = useState<Claim[] | null>(null);
  const [payers, setPayers] = useState<{ id: string; name: string }[] | null>(null);
  const [patients, setPatients] = useState<PatientRow[] | null>(null);
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    try {
      const [r, p, pt] = await Promise.all([listClaims(), listClaimPayers(), getPatients()]);
      setRows(r); setPayers(p); setPatients(pt); setError("");
    } catch (e) { setError(e instanceof Error ? e.message : "Could not load"); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  return (
    <>
      <div className="hero"><div>
        <h1>Claims</h1>
        <p>Ask a payer to cover something you have done for a patient, and see where each claim stands. The payer sees only the member id, the service, the amount and the date.</p>
      </div></div>
      {error && <div className="error" role="alert">{error}</div>}
      <div className="grid">
        <Card title={`Your claims (${rows?.length ?? 0})`}>
          {rows === null ? <div className="empty">Loading…</div> : rows.length === 0
            ? <div className="empty">No claims sent yet.</div>
            : rows.map((c) => <Row key={c.id} c={c} onDone={load} />)}
        </Card>
        <Card title="Send a claim">
          {payers === null || patients === null ? <div className="empty">Loading…</div>
            : payers.length === 0 ? <div className="empty">No payer is available to claim from yet.</div>
            : <Send payers={payers} patients={patients} onDone={load} />}
        </Card>
      </div>
    </>
  );
}
