import { useCallback, useEffect, useState } from "react";
import {
  ApiError, acceptHazard, assessHazard, getHazards, retireHazard, reviewHazard, type HazardRow,
} from "../api";
import { Card, Pill } from "../ui";

const STATUS = {
  OPEN: { text: "Open", kind: "info" },
  VERIFIED: { text: "Verified", kind: "ok" },
  REJECTED: { text: "Rejected", kind: "bad" },
  ACCEPTED: { text: "Risk accepted", kind: "ok" },
  RETIRED: { text: "Retired", kind: "warn" },
} as const;
const SCALE = ["1", "2", "3", "4", "5"];
const SEVERITY_WORDS = ["", "negligible", "minor", "serious", "critical", "catastrophic"];
const PROBABILITY_WORDS = ["", "rare", "unlikely", "possible", "likely", "frequent"];

function friendly(e: unknown): string {
  const code = e instanceof ApiError ? e.code : "";
  return code === "POLICY_DENIED" ? "You cannot do that: the person who assessed a hazard may not review or accept it, and only safety officers decide."
    : code === "CONFLICT_VERSION" ? "That hazard is not in a state where this step is allowed."
    : code === "VALIDATION_FAILED" ? "Check the form: severity and probability from 1 to 5, a remaining risk, a few words on how you judged it, and a note where one is required. A HIGH remaining risk cannot be accepted: reduce it and assess again."
    : e instanceof Error ? e.message : "That did not work";
}

/** The hazard register (docs: safety.hazard). Writes only for safety officers; nobody reviews their own work. */
export function Hazards() {
  const [rows, setRows] = useState<HazardRow[] | null>(null);
  const [canDecide, setCanDecide] = useState(false);
  const [error, setError] = useState("");
  const [done, setDone] = useState("");
  const [busy, setBusy] = useState(false);
  const [library, setLibrary] = useState<"product" | "ai">("product");
  const [open, setOpen] = useState("");
  const [form, setForm] = useState({ severity: "3", probability: "3", residual_risk: "medium", basis_method: "", hazardous_situation: "" });
  const [note, setNote] = useState("");

  const load = useCallback(async () => {
    try { const d = await getHazards(); setRows(d.hazards); setCanDecide(d.can_decide); setError(""); }
    catch (e) { setError(friendly(e)); setRows([]); }
  }, []);
  useEffect(() => { void load(); }, [load]);

  async function run(work: () => Promise<unknown>, success: string) {
    setBusy(true); setError(""); setDone("");
    try { await work(); setDone(success); setNote(""); await load(); }
    catch (e) { setError(friendly(e)); }
    finally { setBusy(false); }
  }

  const shown = (rows ?? []).filter((h) => h.library === library);
  const chosen = (rows ?? []).find((h) => h.id === open);
  const count = (s: string) => (rows ?? []).filter((h) => h.library === library && h.status === s).length;

  return (
    <>
      <div className="hero"><div>
        <h1>Hazards</h1>
        <p>Ways the product could hurt someone, from the specification's list. A safety officer assesses each one; a <b>different</b> safety officer reviews it; only then can the remaining risk be accepted (never when it is high).
          {canDecide ? " You can decide." : " You can read; only safety officers decide."} Never put patient details in a note.</p>
      </div></div>
      {error && <div className="error" role="alert">{error}</div>}
      {done && <div className="success" role="status">{done}</div>}
      <div className="grid">
        <Card title={`${library === "product" ? "Product hazards (H01 to H20)" : "AI hazards (HAI-01 to HAI-16)"}: ${shown.length}`}
          sub={`${count("OPEN")} open, ${count("VERIFIED")} verified, ${count("ACCEPTED")} accepted, ${count("REJECTED")} rejected, ${count("RETIRED")} retired`}>
          <div style={{ display: "flex", gap: 8, marginBottom: 10 }}>
            <button className={`btn ${library === "product" ? "" : "ghost"}`} aria-pressed={library === "product"} onClick={() => { setLibrary("product"); setOpen(""); }}>Product hazards</button>
            <button className={`btn ${library === "ai" ? "" : "ghost"}`} aria-pressed={library === "ai"} onClick={() => { setLibrary("ai"); setOpen(""); }}>AI hazards</button>
          </div>
          {rows === null ? <div className="empty">Loading…</div> : shown.map((h) => (
            <div key={h.id} className="appt">
              <div className="info">
                <b>{h.ref_no} <Pill kind={STATUS[h.status].kind}>{STATUS[h.status].text}</Pill></b>
                <small>{h.title}</small>
                <small>{h.assessed
                  ? `Severity ${h.severity} (${SEVERITY_WORDS[h.severity]}), probability ${h.probability} (${PROBABILITY_WORDS[h.probability]}), remaining risk ${h.residual_risk}. Assessed by ${h.assessed_by}${h.reviewer ? `, reviewed by ${h.reviewer}` : ""}.`
                  : "Not assessed yet."}</small>
              </div>
              <button className="btn ghost" aria-label={`Open ${h.ref_no}`} onClick={() => setOpen(open === h.id ? "" : h.id)}>{open === h.id ? "Close" : "Open"}</button>
            </div>
          ))}
        </Card>
        {chosen && (
          <Card title={`${chosen.ref_no}: ${chosen.title}`}>
            {chosen.basis_method && <p><b>How it was judged:</b> {chosen.basis_method}</p>}
            {chosen.hazardous_situation && <p><b>The situation:</b> {chosen.hazardous_situation}</p>}
            {canDecide && chosen.status !== "RETIRED" && (
              <>
                <h3 style={{ fontSize: 14, margin: "12px 0 6px" }}>Assess (or assess again)</h3>
                <div className="form-row">
                  <label>Severity <select aria-label="Severity" value={form.severity} onChange={(e) => setForm({ ...form, severity: e.target.value })}>{SCALE.map((n) => <option key={n} value={n}>{n} {SEVERITY_WORDS[Number(n)]}</option>)}</select></label>
                  <label>Probability <select aria-label="Probability" value={form.probability} onChange={(e) => setForm({ ...form, probability: e.target.value })}>{SCALE.map((n) => <option key={n} value={n}>{n} {PROBABILITY_WORDS[Number(n)]}</option>)}</select></label>
                  <label>Remaining risk <select aria-label="Remaining risk" value={form.residual_risk} onChange={(e) => setForm({ ...form, residual_risk: e.target.value })}>
                    <option value="low">low</option><option value="medium">medium</option><option value="high">high</option></select></label>
                  <input aria-label="How you judged it" placeholder="How you judged it (5 to 300 characters)" maxLength={300} value={form.basis_method} onChange={(e) => setForm({ ...form, basis_method: e.target.value })} />
                  <input aria-label="The hazardous situation" placeholder="The situation (optional, no patient details)" maxLength={500} value={form.hazardous_situation} onChange={(e) => setForm({ ...form, hazardous_situation: e.target.value })} />
                  <button className="btn" disabled={busy || form.basis_method.trim().length < 5}
                    onClick={() => void run(() => assessHazard(chosen.id, { severity: Number(form.severity), probability: Number(form.probability), residual_risk: form.residual_risk, basis_method: form.basis_method, hazardous_situation: form.hazardous_situation }), "Assessment saved. It now needs a different safety officer to review it.")}>Save assessment</button>
                </div>
                <h3 style={{ fontSize: 14, margin: "14px 0 6px" }}>Review, accept or retire</h3>
                <div className="form-row">
                  <input aria-label="Note" placeholder="Note (needed to reject or retire)" maxLength={300} value={note} onChange={(e) => setNote(e.target.value)} />
                  {chosen.status === "OPEN" && chosen.assessed && (
                    <>
                      <button className="btn" disabled={busy} onClick={() => void run(() => reviewHazard(chosen.id, "verified", note), "Verified.")}>Verify (independent review)</button>
                      <button className="btn ghost" disabled={busy || !note.trim()} onClick={() => void run(() => reviewHazard(chosen.id, "rejected", note), "Rejected: the assessor can fix it and assess again.")}>Reject</button>
                    </>
                  )}
                  {chosen.status === "VERIFIED" && (
                    <button className="btn" disabled={busy} onClick={() => void run(() => acceptHazard(chosen.id, note), "Remaining risk accepted.")}>Accept the remaining risk</button>
                  )}
                  <button className="btn ghost" disabled={busy || note.trim().length < 3} onClick={() => void run(() => retireHazard(chosen.id, note), "Retired.")}>Retire</button>
                </div>
              </>
            )}
          </Card>
        )}
      </div>
    </>
  );
}
