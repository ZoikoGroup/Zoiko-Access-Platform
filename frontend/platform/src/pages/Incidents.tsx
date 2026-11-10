import { useCallback, useEffect, useState } from "react";
import { ApiError, addIncident, getIncidents, resolveIncident, type IncidentRow } from "../api";
import { Card, Pill } from "../ui";

function today(): string {
  const d = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

// GHE-20's proposed internal scale. It does not replace the legal definition of a notifiable incident.
const SEVERITY = {
  sev0: { text: "SEV-0", kind: "bad", means: "Patient safety, a large unlawful disclosure, or a systemic compromise" },
  sev1: { text: "SEV-1", kind: "bad", means: "A major regional loss or a critical workflow failure" },
  sev2: { text: "SEV-2", kind: "warn", means: "A partial service degradation" },
  sev3: { text: "SEV-3", kind: "info", means: "A minor operational issue" },
} as const;

const EMPTY_FORM = () => ({ title: "", severity: "sev2", happened_on: today(), details: "" });

function friendly(e: unknown): string {
  const code = e instanceof ApiError ? e.code : "";
  return code === "CONFLICT_VERSION" ? "That incident is already closed."
    : code === "VALIDATION_FAILED" ? "Check the form: title up to 120 characters, details up to 2000, a severity, a note of up to 1000 characters to close, and a date that is not in the future."
    : code === "POLICY_DENIED" ? "Your account may not change the incident log."
    : e instanceof Error ? e.message : "That did not work";
}

/** The platform's hand-written incident log, on GHE-20's SEV-0 to SEV-3 scale (DEV-118). */
export function Incidents({ canWrite }: { canWrite: boolean }) {
  const [rows, setRows] = useState<IncidentRow[] | null>(null);
  const [error, setError] = useState("");
  const [done, setDone] = useState("");
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState(EMPTY_FORM());
  const [closing, setClosing] = useState<{ id: string; note: string } | null>(null);

  const load = useCallback(async () => {
    try { setRows(await getIncidents()); setError(""); }
    catch (e) { setError(friendly(e)); setRows([]); }
  }, []);
  useEffect(() => { void load(); }, [load]);

  async function run(work: () => Promise<unknown>, success: string, after?: () => void) {
    setBusy(true); setError(""); setDone("");
    try { await work(); setDone(success); after?.(); await load(); }
    catch (e) { setError(friendly(e)); }
    finally { setBusy(false); }
  }

  const open = rows?.filter((r) => r.status === "open").length ?? 0;
  return (
    <>
      <div className="hero">
        <div>
          <h1>Incidents</h1>
          <p>A log, written by hand, of what went wrong and how it ended. It raises no alarm and fixes nothing. Severity follows the specification's proposed scale, SEV-0 (worst) to SEV-3; the owners can still choose a different one.</p>
        </div>
      </div>
      <div className="grid">
        {canWrite && (
          <Card title="Record an incident" sub="Needs support rights. Each entry is also written to the platform audit log.">
            <div className="notice">Never write patient names, emails or any health detail here: the text is not checked.</div>
            <form className="add-form" onSubmit={(e) => {
              e.preventDefault();
              void run(() => addIncident(form), "Incident recorded.", () => setForm(EMPTY_FORM()));
            }}>
              <label>Title
                <input value={form.title} maxLength={120} onChange={(e) => setForm({ ...form, title: e.target.value })} required />
              </label>
              <label>Severity
                <select value={form.severity} onChange={(e) => setForm({ ...form, severity: e.target.value })}>
                  {(Object.keys(SEVERITY) as (keyof typeof SEVERITY)[]).map((k) => (
                    <option key={k} value={k}>{SEVERITY[k].text}: {SEVERITY[k].means}</option>
                  ))}
                </select>
              </label>
              <label>Happened on
                <input type="date" value={form.happened_on} max={today()} onChange={(e) => setForm({ ...form, happened_on: e.target.value })} required />
              </label>
              <label>Details (optional)
                <textarea value={form.details} maxLength={2000} rows={4} onChange={(e) => setForm({ ...form, details: e.target.value })} />
              </label>
              {error && <div className="error" role="alert">{error}</div>}
              {done && <div className="success" role="status">{done}</div>}
              <div>
                <button className="btn" type="submit" disabled={busy || !form.title.trim() || !form.happened_on}>
                  {busy ? "Saving…" : "Add to the log"}
                </button>
              </div>
            </form>
          </Card>
        )}
        <Card title={`${rows?.length ?? 0} incident${rows?.length === 1 ? "" : "s"}, ${open} open`} sub="Open ones first, then newest">
          {!canWrite && error && <div className="error" role="alert">{error}</div>}
          {rows === null ? <div className="empty">Loading…</div> : rows.length === 0 ? <div className="empty">Nothing recorded yet.</div> : rows.map((r) => (
            <div key={r.id} className="appt">
              <div className="info">
                <b>
                  {r.title} <Pill kind={SEVERITY[r.severity].kind}>{SEVERITY[r.severity].text}</Pill>{" "}
                  <Pill kind={r.status === "open" ? "warn" : "ok"}>{r.status === "open" ? "Open" : "Closed"}</Pill>
                </b>
                <span className="sub">Happened {r.happened_on} - recorded by {r.recorded_by}</span>
                {r.details && <span className="sub" style={{ whiteSpace: "pre-wrap" }}>{r.details}</span>}
                {r.status === "resolved" && (
                  <span style={{ whiteSpace: "pre-wrap" }}>Closed {r.resolved_on} by {r.resolved_by}: {r.resolution}</span>
                )}
                {canWrite && r.status === "open" && (closing?.id === r.id ? (
                  <span className="form-row">
                    <textarea aria-label="How it ended" placeholder="How did it end? (no patient details)" rows={2} maxLength={1000}
                      value={closing.note} onChange={(e) => setClosing({ id: r.id, note: e.target.value })} />
                    <button className="btn" disabled={busy || !closing.note.trim()}
                      onClick={() => void run(() => resolveIncident(r.id, closing.note), "Incident closed.", () => setClosing(null))}>
                      Close it
                    </button>
                    <button className="btn ghost" onClick={() => setClosing(null)}>Cancel</button>
                  </span>
                ) : (
                  <span><button className="btn ghost" onClick={() => setClosing({ id: r.id, note: "" })}>Close this incident</button></span>
                ))}
              </div>
            </div>
          ))}
        </Card>
      </div>
    </>
  );
}
