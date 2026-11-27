import { useCallback, useEffect, useState } from "react";
import { ApiError, getPurposes, proposePurpose, purposeStep, type PurposeRow } from "../api";
import { Card, Pill } from "../ui";

const CLASS = {
  administrative: "Administrative: booking, messages, paperwork",
  clinical_support: "Clinical support: informs a person, decides nothing",
  regulated: "Regulated: may be a medical device (needs a legal manufacturer)",
} as const;
const STATUS = {
  draft: { text: "Proposed", kind: "warn" }, approved: { text: "Approved", kind: "ok" },
  superseded: { text: "Replaced", kind: "info" }, retired: { text: "Withdrawn", kind: "bad" },
} as const;

function friendly(e: unknown): string {
  const code = e instanceof ApiError ? e.code : "";
  return code === "VALIDATION_FAILED" ? "Check the form: a statement of 20 to 500 characters, a class, who it is for, and for a regulated one a legal manufacturer before approval."
    : code === "POLICY_DENIED" ? "You cannot do that: only a safety officer approves or withdraws, and nobody approves their own statement."
    : code === "CONFLICT_VERSION" ? "That statement has already moved on. Refresh the page."
    : e instanceof Error ? e.message : "That did not work";
}

/** What each capability is for. A capability with no approved statement cannot go live in a release. */
export function Purposes({ canPropose, canDecide }: { canPropose: boolean; canDecide: boolean }) {
  const [rows, setRows] = useState<PurposeRow[] | null>(null);
  const [caps, setCaps] = useState<string[]>([]);
  const [error, setError] = useState("");
  const [done, setDone] = useState("");
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState({ capability: "", statement: "", classification: "", intended_users: "", legal_manufacturer: "" });
  const [reason, setReason] = useState<{ id: string; value: string } | null>(null);

  const load = useCallback(async () => {
    try { const r = await getPurposes(); setRows(r.purposes); setCaps(r.capabilities); setError(""); }
    catch (e) { setError(friendly(e)); setRows([]); }
  }, []);
  useEffect(() => { void load(); }, [load]);

  async function run(work: () => Promise<unknown>, success: string, after?: () => void) {
    setBusy(true); setError(""); setDone("");
    try { await work(); setDone(success); after?.(); await load(); }
    catch (e) { setError(friendly(e)); }
    finally { setBusy(false); }
  }

  return (
    <>
      <div className="hero"><div>
        <h1>Intended purposes</h1>
        <p>A written statement of what each capability is for. A capability with no approved statement cannot go live in a release, and there is no default class: someone must choose one. Approval needs a safety officer who did not write it.</p>
      </div></div>
      <div className="grid">
        {canPropose && (
          <Card title="Propose a statement">
            <form className="profile-form" onSubmit={(e) => { e.preventDefault(); void run(() => proposePurpose(form), "Proposed. A safety officer now needs to approve it.", () => setForm({ ...form, statement: "" })); }}>
              <label>Capability
                <select value={form.capability} onChange={(e) => setForm({ ...form, capability: e.target.value })} required>
                  <option value="">Choose one</option>{caps.map((c) => <option key={c} value={c}>{c}</option>)}
                </select>
              </label>
              <label>Class
                <select value={form.classification} onChange={(e) => setForm({ ...form, classification: e.target.value })} required>
                  <option value="">Choose one</option>
                  {(Object.keys(CLASS) as (keyof typeof CLASS)[]).map((k) => <option key={k} value={k}>{CLASS[k]}</option>)}
                </select>
              </label>
              <label>What it is for (20 to 500 characters)
                <textarea rows={3} maxLength={500} value={form.statement} onChange={(e) => setForm({ ...form, statement: e.target.value })} required />
              </label>
              <label>Who uses it<input maxLength={200} value={form.intended_users} onChange={(e) => setForm({ ...form, intended_users: e.target.value })} required /></label>
              <label>Legal manufacturer (needed to approve a regulated one)<input maxLength={120} value={form.legal_manufacturer} onChange={(e) => setForm({ ...form, legal_manufacturer: e.target.value })} /></label>
              <div><button className="btn" type="submit" disabled={busy}>Propose</button></div>
            </form>
          </Card>
        )}
        <Card title="Statements">
          {error && <div className="error" role="alert">{error}</div>}
          {done && <div className="success" role="status">{done}</div>}
          {rows === null ? <div className="empty">Loading…</div> : rows.length === 0 ? <div className="empty">Nothing proposed yet. Until a statement is approved, no capability can go live.</div> : rows.map((r) => (
            <div key={r.public_id} className="appt"><div className="info">
              <b>{r.capability} <small>version {r.version}</small> <Pill kind={STATUS[r.status].kind}>{STATUS[r.status].text}</Pill> <Pill kind={r.classification === "regulated" ? "bad" : "info"}>{r.classification.replace("_", " ")}</Pill>{r.reclassifies && <> <Pill kind="warn">changes the class</Pill></>}</b>
              <span style={{ whiteSpace: "pre-wrap" }}>{r.statement}</span>
              {r.status === "draft" && r.reclassifies && <span className="sub">This changes what the capability is classed as, so two different safety officers must review it. Reviews so far: {r.reviews} of {r.reviews_needed}.</span>}
              <span className="sub">For {r.intended_users}.{r.legal_manufacturer ? ` Legal manufacturer: ${r.legal_manufacturer}.` : ""}{r.note ? ` Withdrawn: ${r.note}` : ""}</span>
              {canDecide && (r.status === "draft" || r.status === "approved") && (reason?.id === r.public_id ? (
                <span className="form-row">
                  <input aria-label={`Why withdraw ${r.capability} version ${r.version}`} placeholder="Why? (3 to 300 characters)" maxLength={300} value={reason.value} onChange={(e) => setReason({ id: r.public_id, value: e.target.value })} />
                  <button className="btn" disabled={busy || reason.value.trim().length < 3} onClick={() => void run(() => purposeStep(r.public_id, "retire", reason.value), "Withdrawn.", () => setReason(null))}>Withdraw it</button>
                  <button className="btn ghost" onClick={() => setReason(null)}>Cancel</button>
                </span>
              ) : (
                <span className="form-row">
                  {r.status === "draft" && <button className="btn" disabled={busy} onClick={() => void run(() => purposeStep(r.public_id, "approve"), "Approved.")}>{r.reclassifies ? "Review" : "Approve"} {r.capability} version {r.version}</button>}
                  <button className="btn ghost" onClick={() => setReason({ id: r.public_id, value: "" })}>Withdraw…</button>
                </span>
              ))}
            </div></div>
          ))}
        </Card>
      </div>
    </>
  );
}
