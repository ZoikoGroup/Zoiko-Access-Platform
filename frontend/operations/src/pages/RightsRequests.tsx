import { useCallback, useEffect, useState } from "react";
import { listRightsCases, rightsStep } from "../api";
import type { RightsCase } from "../types";
import { Card, Patient, Pill, type PillKind } from "../ui";

const WORDS: Record<string, string> = {
  access: "See the data held about them", correction: "Correct their data",
  portability: "Give them their data to take elsewhere", restriction: "Limit how their data is used",
  objection: "They object to how their data is used", consent_withdrawal: "They withdraw consent",
  erasure: "Delete their data",
};
const STATE: Record<RightsCase["state"], { text: string; kind: PillKind }> = {
  received: { text: "New", kind: "warn" }, in_progress: { text: "Being looked at", kind: "info" },
  completed: { text: "Done", kind: "ok" }, declined: { text: "Declined", kind: "bad" },
  withdrawn: { text: "Withdrawn by the patient", kind: "info" },
};

function Row({ c, canClose, onDone }: { c: RightsCase; canClose: boolean; onDone: () => Promise<void> }) {
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const open = c.state === "received" || c.state === "in_progress";
  async function run(work: () => Promise<unknown>) {
    setBusy(true); setError("");
    try { await work(); setNote(""); await onDone(); }
    catch (e) { setError(e instanceof Error ? e.message : "That did not work"); }
    finally { setBusy(false); }
  }
  return (
    <div className="appt" style={{ flexDirection: "column", alignItems: "stretch" }}>
      <div className="info">
        <b>{c.ref} <Pill kind={STATE[c.state].kind}>{STATE[c.state].text}</Pill> <Patient name={c.patient_name} id={c.patient} /></b>
        <small>{WORDS[c.type] ?? c.type}. Sent {c.created_at.slice(0, 10)}.</small>
        {c.due_at && open && (
          <small>Answer by {c.due_at.slice(0, 10)}{Date.parse(c.due_at) < Date.now() ? " (overdue)" : ""} ({c.due_basis}).</small>
        )}
        {c.note && <small>Their words: {c.note}</small>}
        <small>{c.identity_checked_at ? `Identity checked ${c.identity_checked_at.slice(0, 10)}.` : "Identity not checked yet."}</small>
        {c.decision_note && <small>Told the patient: {c.decision_note}</small>}
        {error && <small style={{ color: "var(--bad)" }}>{error}</small>}
      </div>
      {open && (
        <div className="form-row">
          {c.state === "received" && <button className="btn ghost" disabled={busy} onClick={() => void run(() => rightsStep(c.id, "start"))}>Start</button>}
          {!c.identity_checked_at && <button className="btn ghost" disabled={busy} onClick={() => void run(() => rightsStep(c.id, "verify"))}>I have checked who is asking</button>}
          {canClose ? (
            <>
              <input aria-label={`Note to the patient for ${c.ref}`} placeholder="What the patient is told (needed to decline)" value={note} maxLength={300} onChange={(e) => setNote(e.target.value)} />
              <button className="btn" disabled={busy || !c.identity_checked_at} title={c.identity_checked_at ? "" : "Check who is asking first"}
                onClick={() => void run(() => rightsStep(c.id, "complete", note))}>Mark done</button>
              <button className="btn ghost" disabled={busy || note.trim().length < 3} onClick={() => void run(() => rightsStep(c.id, "decline", note))}>Decline</button>
            </>
          ) : <small>Only your clinic administrator can close a request.</small>}
        </div>
      )}
    </div>
  );
}

/** Patients' formal requests about their own data. The clinic does the work; this tracks it. */
export function RightsRequests() {
  const [rows, setRows] = useState<RightsCase[] | null>(null);
  const [canClose, setCanClose] = useState(false);
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    try { const d = await listRightsCases(); setRows(d.requests); setCanClose(d.can_close); setError(""); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not load the requests"); }
  }, []);
  useEffect(() => {
    void load();
    const timer = setInterval(() => void load(), 15_000);
    return () => clearInterval(timer);
  }, [load]);
  const waiting = (rows ?? []).filter((r) => r.state === "received" || r.state === "in_progress").length;
  return (
    <>
      <div className="hero"><div>
        <h1>Data requests</h1>
        <p>Patients asking about their own data: to see it, correct it, take it elsewhere, limit it, object, withdraw consent, or delete it. Check who is asking first. You do the work yourselves (a correction, a deletion); this keeps the record and tells the patient the outcome. A clinic administrator closes each request. A deadline appears on a request only when Zoiko has an approved country pack for your country; otherwise none is shown. {waiting > 0 ? `${waiting} waiting.` : ""}</p>
      </div></div>
      {error && <div className="error" role="alert">{error}</div>}
      <div className="grid">
        <Card title={`${rows?.length ?? 0} request${rows?.length === 1 ? "" : "s"}`}>
          {rows === null ? <div className="empty">Loading…</div> : rows.length === 0
            ? <div className="empty">No patient has made a data request.</div>
            : rows.map((c) => <Row key={c.id} c={c} canClose={canClose} onDone={load} />)}
        </Card>
      </div>
    </>
  );
}
