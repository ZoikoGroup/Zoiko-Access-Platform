import { useCallback, useEffect, useState } from "react";
import { ApiError, acknowledgeIncident, addIncident, assessIncident, assignIncident, getIncidentTimeline, getIncidents, getOnCallStaff, recordHandoff, resolveIncident, type IncidentRow, type OnCallPerson, type TimelineEntry } from "../api";
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
  unknown: { text: "Not yet assessed", kind: "warn", means: "Stays open until a safety officer evaluates it" },
} as const;

const EMPTY_FORM = () => ({ title: "", severity: "sev2", happened_on: today(), details: "", safety_impact: false });

function friendly(e: unknown): string {
  const code = e instanceof ApiError ? e.code : "";
  return code === "CONFLICT_VERSION" ? "That incident is already closed."
    : code === "VALIDATION_FAILED" ? "Check the form: title up to 120 characters, details up to 2000, a severity, a note of up to 1000 characters to close, and a date that is not in the future."
    : code === "POLICY_DENIED" ? "Your account may not change the incident log."
    : e instanceof Error ? e.message : "That did not work";
}

/** The platform's hand-written incident log, on GHE-20's SEV-0 to SEV-3 scale (DEV-118). */
export function Incidents({ canWrite, canAssess = false }: { canWrite: boolean; canAssess?: boolean }) {
  const [rows, setRows] = useState<IncidentRow[] | null>(null);
  const [error, setError] = useState("");
  const [done, setDone] = useState("");
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState(EMPTY_FORM());
  const [closing, setClosing] = useState<{ id: string; note: string; notice: string } | null>(null);
  const [staff, setStaff] = useState<OnCallPerson[]>([]);
  const [assigning, setAssigning] = useState<{ id: string; owner: string; deputy: string } | null>(null);
  const [shown, setShown] = useState<{ id: string; entries: TimelineEntry[] } | null>(null);
  const [assessing, setAssessing] = useState<{ id: string; severity: string; note: string } | null>(null);
  const [handing, setHanding] = useState<{ id: string; note: string } | null>(null);

  const load = useCallback(async () => {
    try { setRows(await getIncidents()); setError(""); }
    catch (e) { setError(friendly(e)); setRows([]); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => { getOnCallStaff().then(setStaff).catch(() => setStaff([])); }, []);
  async function toggleTimeline(id: string) {
    if (shown?.id === id) { setShown(null); return; }
    try { setShown({ id, entries: await getIncidentTimeline(id) }); } catch (e) { setError(friendly(e)); }
  }

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
              <label style={{ display: "flex", gap: 8, alignItems: "center" }}>
                <input type="checkbox" checked={form.safety_impact} onChange={(e) => setForm({ ...form, safety_impact: e.target.checked })} />
                This touches patient safety (it cannot be closed until a safety officer records the hand-off)
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
                  <span style={{ whiteSpace: "pre-wrap" }}>Closed {r.resolved_on} by {r.resolved_by}: {r.resolution}{r.restoration_notice && ` Restoration notice: ${r.restoration_notice}`}</span>
                )}
                {r.assessment_note && <span className="sub">Assessed by {r.assessed_by}: {r.assessment_note}</span>}
                <span className="sub">
                  {r.owner ? <>On call: {r.owner} (deputy {r.deputy}).{" "}
                    {r.acknowledged_at ? <Pill kind="ok">acknowledged</Pill> : r.escalated_at ? <Pill kind="bad">escalated to the deputy</Pill> : <Pill kind="warn">not yet acknowledged</Pill>}</>
                    : r.status === "open" ? "Nobody is on call for it yet." : ""}
                  {" "}<button className="btn ghost" onClick={() => void toggleTimeline(r.id)}>{shown?.id === r.id ? "Hide timeline" : "Timeline"}</button>
                </span>
                {shown?.id === r.id && (
                  <ol className="sub">{shown.entries.map((e, i) => <li key={i}>{new Date(e.at).toLocaleString()}: {e.kind} by {e.by}{e.note && ` (${e.note})`}</li>)}</ol>
                )}
                {r.status === "open" && r.owner && !r.acknowledged_at && (
                  <span><button className="btn" disabled={busy} onClick={() => void run(() => acknowledgeIncident(r.id), "Acknowledged.")}>I have it</button></span>
                )}
                {canWrite && r.status === "open" && (assigning?.id === r.id ? (
                  <span className="form-row">
                    <select aria-label={`Owner for ${r.title}`} value={assigning.owner} onChange={(e) => setAssigning({ ...assigning, owner: e.target.value })}>
                      <option value="">Owner…</option>{staff.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
                    </select>
                    <select aria-label={`Deputy for ${r.title}`} value={assigning.deputy} onChange={(e) => setAssigning({ ...assigning, deputy: e.target.value })}>
                      <option value="">Deputy…</option>{staff.filter((p) => p.id !== assigning.owner).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
                    </select>
                    <button className="btn" disabled={busy || !assigning.owner || !assigning.deputy}
                      onClick={() => void run(() => assignIncident(r.id, assigning.owner, assigning.deputy), "On call named.", () => setAssigning(null))}>Name them</button>
                    <button className="btn ghost" onClick={() => setAssigning(null)}>Cancel</button>
                  </span>
                ) : (
                  <span><button className="btn ghost" onClick={() => setAssigning({ id: r.id, owner: r.owner_ref, deputy: r.deputy_ref })}>{r.owner ? "Change who is on call" : "Name who is on call"}</button></span>
                ))}
                {r.safety_impact && <span className="sub"><Pill kind="bad">safety impact</Pill>{" "}{r.handoff_note ? `Hand-off recorded by ${r.handoff_by}: ${r.handoff_note}` : "It can be closed only after a safety officer records the hand-off or fallback."}</span>}
                {canAssess && r.status === "open" && r.safety_impact && !r.handoff_note && (handing?.id === r.id ? (
                  <span className="form-row">
                    <input aria-label={`Hand-off for ${r.title}`} placeholder="What covers this meanwhile? (no patient details)" maxLength={300}
                      value={handing.note} onChange={(e) => setHanding({ id: r.id, note: e.target.value })} />
                    <button className="btn" disabled={busy || handing.note.trim().length < 3}
                      onClick={() => void run(() => recordHandoff(r.id, handing.note), "Hand-off recorded.", () => setHanding(null))}>
                      Record hand-off
                    </button>
                    <button className="btn ghost" onClick={() => setHanding(null)}>Cancel</button>
                  </span>
                ) : (
                  <span><button className="btn" onClick={() => setHanding({ id: r.id, note: "" })}>Record the hand-off</button></span>
                ))}
                {r.status === "open" && r.severity === "unknown" && (
                  <span className="sub">It can be closed only after a safety officer gives it a level.</span>
                )}
                {canAssess && r.status === "open" && r.severity === "unknown" && (assessing?.id === r.id ? (
                  <span className="form-row">
                    <select aria-label={`Level for ${r.title}`} value={assessing.severity}
                      onChange={(e) => setAssessing({ ...assessing, severity: e.target.value })}>
                      {(["sev0", "sev1", "sev2", "sev3"] as const).map((k) => <option key={k} value={k}>{SEVERITY[k].text}: {SEVERITY[k].means}</option>)}
                    </select>
                    <input aria-label={`What you evaluated for ${r.title}`} placeholder="What did you evaluate? (no patient details)" maxLength={300}
                      value={assessing.note} onChange={(e) => setAssessing({ ...assessing, note: e.target.value })} />
                    <button className="btn" disabled={busy || assessing.note.trim().length < 3}
                      onClick={() => void run(() => assessIncident(r.id, assessing.severity, assessing.note), "Assessment recorded.", () => setAssessing(null))}>
                      Record assessment
                    </button>
                    <button className="btn ghost" onClick={() => setAssessing(null)}>Cancel</button>
                  </span>
                ) : (
                  <span><button className="btn" onClick={() => setAssessing({ id: r.id, severity: "sev2", note: "" })}>Assess this incident</button></span>
                ))}
                {canWrite && r.status === "open" && r.severity !== "unknown" && !(r.safety_impact && !r.handoff_note) && (closing?.id === r.id ? (
                  <span className="form-row">
                    <textarea aria-label="How it ended" placeholder="How did it end? (no patient details)" rows={2} maxLength={1000}
                      value={closing.note} onChange={(e) => setClosing({ ...closing, note: e.target.value })} />
                    {(r.severity === "sev0" || r.severity === "sev1") && (
                      <textarea aria-label="Restoration notice" placeholder="What were customers and partners told when service came back? (required for SEV-0 and SEV-1)" rows={2} maxLength={1000}
                        value={closing.notice} onChange={(e) => setClosing({ ...closing, notice: e.target.value })} />
                    )}
                    <button className="btn" disabled={busy || !closing.note.trim() || ((r.severity === "sev0" || r.severity === "sev1") && closing.notice.trim().length < 10)}
                      onClick={() => void run(() => resolveIncident(r.id, closing.note, closing.notice), "Incident closed.", () => setClosing(null))}>
                      Close it
                    </button>
                    <button className="btn ghost" onClick={() => setClosing(null)}>Cancel</button>
                  </span>
                ) : (
                  <span><button className="btn ghost" onClick={() => setClosing({ id: r.id, note: "", notice: "" })}>Close this incident</button></span>
                ))}
              </div>
            </div>
          ))}
        </Card>
      </div>
    </>
  );
}
