import { useCallback, useEffect, useState } from "react";
import { ApiError, addReference, getReferences, withdrawReference, type RecordReference } from "../api";
import { Card, Pill } from "../ui";

const KIND: Record<string, string> = {
  consultation_note: "Consultation note", result: "Result", letter: "Letter", imaging: "Imaging", other: "Other record",
};
const REASON: Record<string, string> = {
  entered_in_error: "Entered in error", wrong_patient: "Recorded for the wrong patient",
  removed_at_source: "No longer held in the clinic's system", other: "Other",
};
const REASON_SHOWN: Record<string, string> = { ...REASON, retention_period: "Erased after the retention period" };

function why(e: unknown): string {
  const code = e instanceof ApiError ? e.code : "";
  return code === "POLICY_DENIED" ? "You can only do this while treating this patient (not when following up read only), for your own clinic's records."
    : code === "VALIDATION_FAILED" ? "Check the details: the system is a short code (small letters, digits, - or _), the record number has no spaces and includes a digit, and the date is not in the future."
    : code === "CONFLICT_VERSION" ? "That record is already listed."
    : e instanceof Error ? e.message : "That did not work";
}

function Row({ r, onDone }: { r: RecordReference; onDone: () => Promise<void> }) {
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function withdraw() {
    setBusy(true); setError("");
    try { await withdrawReference(r.id, reason); await onDone(); }
    catch (e) { setError(why(e)); }
    finally { setBusy(false); }
  }
  return (
    <div className="appt">
      <div className="info">
        <b>{KIND[r.kind] ?? r.kind} <Pill kind={r.status === "active" ? "ok" : "warn"}>{r.status === "active" ? "Held" : "Withdrawn"}</Pill></b>
        <small>Dated {r.refers_to} · {r.organization.name} · system {r.system} · record {r.external_id}</small>
        {r.withdrawn_reason && <small>Withdrawn: {REASON_SHOWN[r.withdrawn_reason] ?? r.withdrawn_reason}</small>}
        {error && <div className="error" role="alert">{error}</div>}
      </div>
      {r.status === "active" && (
        <div className="row">
          <select aria-label={`Why withdraw record ${r.external_id}`} value={reason} onChange={(e) => setReason(e.target.value)}>
            <option value="">Why withdraw?</option>
            {Object.entries(REASON).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
          <button className="btn ghost" disabled={busy || !reason} onClick={() => void withdraw()}>Withdraw</button>
        </div>
      )}
    </div>
  );
}

/** Pointers to one patient's records in a clinic's own system. Never the record itself. */
export function RecordReferences({ person, name, onClose }: { person: string; name: string; onClose: () => void }) {
  const [rows, setRows] = useState<RecordReference[] | null>(null);
  const [off, setOff] = useState(false);
  const [error, setError] = useState("");
  const [form, setForm] = useState({ system: "", external_id: "", kind: "result", refers_to: "" });
  const [busy, setBusy] = useState(false);
  const set = (k: string, v: string) => setForm((f) => ({ ...f, [k]: v }));

  const load = useCallback(async () => {
    try { setRows(await getReferences(person)); setError(""); }
    catch (e) {
      if (e instanceof ApiError && e.code === "NOT_FOUND") setOff(true);
      else setError(why(e));
    }
  }, [person]);
  useEffect(() => { setRows(null); setOff(false); void load(); }, [load]);

  async function add(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setError("");
    try { await addReference(person, form); setForm({ system: "", external_id: "", kind: "result", refers_to: "" }); await load(); }
    catch (err) { setError(why(err)); }
    finally { setBusy(false); }
  }

  return (
    <Card title={`Records held elsewhere: ${name}`} sub="Where this patient's records are kept. No text, values or files are stored here."
      action={<button className="btn ghost" onClick={onClose}>Close</button>}>
      {off ? <div className="notice" role="status">Record references are not switched on here.</div> : (
        <>
          {rows === null ? <div className="empty">{error ? "Could not load the list." : "Loading…"}</div>
            : rows.length === 0 ? <div className="empty">Nothing listed yet.</div>
            : rows.map((r) => <Row key={r.id} r={r} onDone={load} />)}
          {error && <div className="error" role="alert">{error}</div>}
          <form className="ref-form" onSubmit={(e) => void add(e)} style={{ marginTop: 16 }}>
            <label>Kind of record
              <select value={form.kind} onChange={(e) => set("kind", e.target.value)}>
                {Object.entries(KIND).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </select>
            </label>
            <label>System it is held in (a short code, such as emis)
              <input value={form.system} maxLength={40} onChange={(e) => set("system", e.target.value.toLowerCase())} required />
            </label>
            <label>Record number in that system
              <input value={form.external_id} maxLength={64} onChange={(e) => set("external_id", e.target.value.trim())} required />
            </label>
            <label>Date of the record
              <input type="date" value={form.refers_to} max={new Date().toISOString().slice(0, 10)} onChange={(e) => set("refers_to", e.target.value)} required />
            </label>
            <div><button className="btn" type="submit" disabled={busy || !form.system || !form.external_id || !form.refers_to}>{busy ? "Saving…" : "Add reference"}</button></div>
          </form>
        </>
      )}
    </Card>
  );
}
